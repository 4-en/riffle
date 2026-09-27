"""FastAPI server: JSON API, thumbnails/previews, and the built Svelte UI.

At startup it loads the embedding matrix; the CLIP text encoder loads in the
background (the first start downloads it), so the UI is usable at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .config import Config, load_config, set_location_history, set_sources
from .embed import embedding_paths, load_embeddings
from .filters import (
    FILENAME_EXPR,
    GROUP_KEYS,
    LOCATION_KEYS,
    NAME_SORTS,
    PLACE_NAME,
    PhotoFilter,
    facets,
    location_labels,
    photo_filter,
)
from . import selections
from .export import CONTENT, STRUCTURE, ExportError, check_destination, run_export
from .jobs import BackgroundJob, index_job
from .selections import EXPORTED_EXPR, FLAG_EXPR, flag_expr

log = logging.getLogger(__name__)

# The built UI (`npm run build` in web/ writes it here; package data in the wheel).
WEB_DIST = Path(__file__).resolve().parent / "web"

TextEncoder = Callable[[list[str]], np.ndarray]


class Index:
    """The in-memory embedding matrix, reloaded when the files on disk change."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stamp = None
        self.E = np.zeros((0, 0), np.float32)
        self.ids = np.zeros((0,), np.int64)
        self.row = {}
        self.refresh()

    def refresh(self) -> None:
        vec_path, ids_path = embedding_paths(self.cfg)
        try:
            stamp = (vec_path.stat().st_mtime_ns, ids_path.stat().st_mtime_ns)
        except FileNotFoundError:
            stamp = None
        if stamp == self.stamp:
            return
        with self.lock:
            E, ids = load_embeddings(self.cfg)
            self.E, self.ids, self.stamp = E, ids, stamp
            self.row = {int(pid): i for i, pid in enumerate(ids)}


class FolderIn(BaseModel):
    path: str


class CurateIn(BaseModel):
    n: int = 12
    variety: float = 0.4
    time_spread: float = 0.5
    place_spread: float = 0.5
    styles: dict[str, float] = {}
    include_rejects: bool = False
    locked: list[int] = []
    removed: list[int] = []
    look: dict[str, int | str | None] = {}  # colour and light: see curate.look_scores
    query: str = ""  # a text search that scores the candidates (query.py syntax)


class AlternativesIn(CurateIn):
    id: int


class CustomTagIn(BaseModel):
    name: str
    photo_ids: list[int]
    strictness: str = "normal"


class CustomTagEdit(BaseModel):
    name: str | None = None
    strictness: str | None = None
    add: list[int] = []
    remove: list[int] = []


class CustomTagPreview(BaseModel):
    photo_ids: list[int]
    strictness: str = "normal"


class FlagOp(BaseModel):
    ids: list[int]
    flag: str | None  # "pick", "reject", or null to clear


class FlagsIn(BaseModel):
    ops: list[FlagOp]


class ResetIn(BaseModel):
    scope: str = "filtered"  # "all": every flag; "filtered": photos within the query-string filters


class ExportIn(BaseModel):
    folder: str  # parent folder
    name: str = ""  # subfolder to create in it (optional)
    content: str = "images"  # images | images_raws | raws
    raw_fallback: bool = True  # with "raws": copy the image when a photo has no RAW
    structure: str = "flat"  # flat | folders
    scope: str = "all"  # all picks, or "filtered": picks within the query-string filters
    add_location: bool = True  # write timeline positions into copies of photos without GPS
    only_new: bool = False  # skip photos that were exported before
    photo_ids: list[int] | None = None  # export exactly these photos (e.g. a Curate draft)


COLLAPSE = ("dupes", "stacks", "none")


def require_json(request: Request) -> None:
    # Mutating endpoints only accept JSON, which a cross-site form post cannot send
    # without a CORS preflight (and this server grants none).
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "expected application/json")


class TasteStore:
    """The taste model (taste.py): trained only when the user calibrates it, saved
    next to the embeddings, loaded at startup. Scores for the indexed photos are
    recomputed (cheaply, without retraining) when the embeddings change."""

    def __init__(self, cfg: Config):
        from . import taste

        self.cfg = cfg
        self.path = cfg.embeddings_dir / f"{cfg.model.model_id}.taste.npz"
        self.lock = threading.Lock()
        self.model = taste.TasteModel.load(self.path)
        self.scores: dict[int, float] = {}
        self.scored_stamp = None

    def _refresh_scores(self, index: Index) -> None:
        if self.scored_stamp == index.stamp:
            return
        scores = {}
        if self.model and self.model.enabled and self.model.w is not None and index.E.size:
            if index.E.shape[1] == len(self.model.w):
                scores = dict(zip((int(i) for i in index.ids), self.model.score(index.E).tolist()))
        self.scores, self.scored_stamp = scores, index.stamp

    def calibrate(self, index: Index):
        from . import taste

        conn = db.connect_readonly(self.cfg.db_path, self.cfg.selections_path)
        try:
            model = taste.train(conn, index.E, index.ids)
        finally:
            conn.close()
        model.save(self.path)
        with self.lock:
            self.model, self.scored_stamp = model, None
            self._refresh_scores(index)
        return model

    def current(self, index: Index):
        with self.lock:
            self._refresh_scores(index)
            return self.model

    def score_of(self, photo_id: int) -> float | None:
        return self.scores.get(photo_id)


@dataclass
class AutoExit:
    """Stop the server once no browser tab is connected any more (the one-click
    launch only): ``idle_seconds`` after the last tab closed, or
    ``first_connect_seconds`` if none ever connected, but never within
    ``min_uptime_seconds`` of starting (a slow browser start, a tab closed right
    away) and never while an index or export is running. ``stop`` asks the
    server to shut down."""

    stop: Callable[[], None]
    idle_seconds: float = 30.0
    first_connect_seconds: float = 300.0
    min_uptime_seconds: float = 60.0


def create_app(
    cfg: Config | None = None,
    text_encoder: TextEncoder | None = None,
    index_runner: Callable | None = None,
    auto_exit: AutoExit | None = None,
) -> FastAPI:
    cfg = cfg or load_config()  # --config / $RIFFLE_CONFIG / ./config.yaml / the user's config
    model_id = cfg.model.model_id
    # clients: open /api/events streams (browser tabs); seen_client: one ever connected.
    # stopping: the server is shutting down, so the event streams should end now.
    state: dict = {
        "encoder": text_encoder,
        # The CLIP text encoder loads in the background (the first start downloads it):
        # "loading" -> "ready" | "failed" (with model_error). Browsing works meanwhile.
        "model": "ready" if text_encoder else "loading",
        "model_error": "",
        "clients": 0,
        "seen_client": False,
        "stopping": False,
    }
    job = index_job(cfg, run=index_runner)
    taste_store = TasteStore(cfg)
    export_job = BackgroundJob(cfg, run_export)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Make sure the catalogue exists so read-only connections work before the first index.
        db.connect(cfg.db_path).close()
        selections.ensure(cfg.selections_path)
        state["index"] = Index(cfg)
        if state["encoder"] is None:
            threading.Thread(target=load_encoder, name="riffle-model", daemon=True).start()
        watchdog = asyncio.create_task(watch_clients()) if auto_exit else None
        yield
        if watchdog:
            watchdog.cancel()

    def load_encoder() -> None:
        try:
            from .embed import Clip

            state["encoder"] = Clip(cfg.model, text_only=True).encode_text
            state["model"] = "ready"
        except Exception as e:  # noqa: BLE001 - browsing still works without text search
            log.error("Could not load the text encoder: %s", e)
            state["model_error"] = str(e) or type(e).__name__
            state["model"] = "failed"

    def model_status() -> dict:
        from . import frozen

        log_file = frozen.log_path()
        return {
            "model": state["model"],
            "model_error": state["model_error"],
            "log": str(log_file) if log_file else None,
        }

    async def watch_clients():
        """Stop when no tab has been connected for a while (see AutoExit)."""
        started = idle_since = time.monotonic()
        while True:
            await asyncio.sleep(0.5)
            young = time.monotonic() - started < auto_exit.min_uptime_seconds
            if young or state["clients"] > 0 or job.running or export_job.running:
                idle_since = time.monotonic()
                continue
            limit = auto_exit.idle_seconds if state["seen_client"] else auto_exit.first_connect_seconds
            if time.monotonic() - idle_since >= limit:
                log.info("no browser tab connected for %.0f s: stopping", limit)
                auto_exit.stop()
                return

    app = FastAPI(title="Riffle", lifespan=lifespan)
    app.state.end_streams = lambda: state.__setitem__("stopping", True)

    def get_conn():
        conn = db.connect_readonly(cfg.db_path, cfg.selections_path)
        try:
            yield conn
        finally:
            conn.close()

    def get_index() -> Index:
        index: Index = state["index"]
        index.refresh()
        return index

    # ---- custom tags (example photos; custom_tags.py) ------------------------------

    ctag_cache: dict[int, tuple] = {}  # tag id -> (key, member ids)

    def custom_tag_list(conn) -> list[dict]:
        """Every custom tag with the ids of its examples that are in the library."""
        tags = [dict(r) for r in conn.execute(
            "SELECT id, name, strictness, updated_at FROM sel.custom_tags ORDER BY name COLLATE NOCASE"
        )]
        examples: dict[int, list[int]] = {}
        for r in conn.execute(
            """SELECT e.tag_id, p.id FROM sel.custom_tag_examples e
               JOIN photos p ON p.sha256 = e.sha256 AND p.status = 'ok' ORDER BY e.added_at, p.id"""
        ):
            if r[1] not in examples.setdefault(r[0], []):
                examples[r[0]].append(r[1])
        for t in tags:
            t["examples"] = examples.get(t["id"], [])
        return tags

    def custom_tag_members(conn, index: Index, tags: list[dict] | None = None) -> dict[int, list[int]]:
        """Member photo ids per custom tag, cached until the tag or the embeddings change."""
        from . import custom_tags

        out = {}
        for t in tags if tags is not None else custom_tag_list(conn):
            key = (t["updated_at"], t["strictness"], tuple(t["examples"]), index.stamp, cfg.stacks.min_similarity)
            hit = ctag_cache.get(t["id"])
            if hit is None or hit[0] != key:
                rows = custom_tags.example_rows(index.row, t["examples"])
                limit = custom_tags.threshold(cfg.stacks.min_similarity, t["strictness"])
                ids, _ = custom_tags.members(index.E, index.ids, rows, limit)
                ctag_cache[t["id"]] = hit = (key, ids)
            out[t["id"]] = hit[1]
        return out

    def resolved_filter(flt: PhotoFilter = Depends(photo_filter), conn=Depends(get_conn), index: Index = Depends(get_index)) -> PhotoFilter:
        """The query-string filter with custom tags resolved to their members."""
        if flt.ctags or flt.exclude_ctags:
            members = custom_tag_members(conn, index)
            flt.ctag_members = [members.get(t, []) for t in flt.ctags]
            flt.ctag_excluded = sorted({i for t in flt.exclude_ctags for i in members.get(t, [])})
        return flt

    # ---- helpers -------------------------------------------------------------

    def items_for(conn, ids: list[int], scores: dict[int, float] | None = None) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT p.id, p.source, p.rel_path, p.width, p.height, p.taken_at, p.dupe_group,
                       p.stack_id, p.sharpness, p.clip_highlights, p.clip_shadows, {FLAG_EXPR} AS flag,
                       {EXPORTED_EXPR} AS exported,
                       EXISTS (SELECT 1 FROM raws r WHERE r.photo_id = p.id) AS has_raw,
                       (SELECT COUNT(*) FROM photos d
                         WHERE d.dupe_group = p.dupe_group AND d.status = 'ok') AS dupe_count,
                       (SELECT COUNT(*) FROM photos s
                         WHERE s.stack_id = p.stack_id AND s.status = 'ok') AS stack_count
                FROM photos p WHERE p.id IN ({marks})""",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        out = []
        for pid in ids:
            r = by_id.get(pid)
            if r is None:
                continue
            item = {
                "id": r["id"],
                "rel_path": r["rel_path"],
                "width": r["width"],
                "height": r["height"],
                "taken_at": r["taken_at"],
                "has_raw": bool(r["has_raw"]),
                "dupe_group": r["dupe_group"],
                "dupe_count": r["dupe_count"],
                "stack_id": r["stack_id"],
                "stack_count": r["stack_count"],
                "sharpness": r["sharpness"],
                "clip_highlights": r["clip_highlights"],
                "clip_shadows": r["clip_shadows"],
                "flag": r["flag"],
                "exported": bool(r["exported"]),
                "taste": taste_store.score_of(r["id"]),
                "thumb": f"/thumbs/{r['id']}.jpg",
            }
            if scores is not None:
                item["score"] = round(scores[pid], 4)
            out.append(item)
        return out

    def collapse_mode(collapse: str | None, dupes: str) -> str:
        """``collapse`` (dupes, stacks, none); ``dupes=collapse|all`` is the older spelling."""
        mode = collapse or ("dupes" if dupes == "collapse" else "none")
        if mode not in COLLAPSE:
            raise HTTPException(400, f"collapse must be one of {', '.join(COLLAPSE)}")
        return mode

    def rank(conn, index: Index, query: np.ndarray, flt: PhotoFilter, exclude: int | None,
             collapse: str, offset: int, limit: int) -> dict:
        if index.E.size == 0:
            return {"total": 0, "items": []}
        where, params = flt.where(model_id)
        allowed = {r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where}", params)}
        allowed.discard(exclude)
        mask = np.array([int(p) in allowed for p in index.ids], dtype=bool)
        if query.ndim == 2:  # text search: the best of its alternatives
            from .query import score

            scores = score(index.E, query)
        else:
            scores = index.E @ query
        scores[~mask] = -np.inf
        order = np.argsort(-scores)[: int(mask.sum())]
        ranked = [int(index.ids[i]) for i in order]

        if collapse != "none":
            column = "stack_id" if collapse == "stacks" else "dupe_group"
            groups = dict(conn.execute(
                f"SELECT id, {column} FROM photos WHERE {column} IS NOT NULL"
            ).fetchall())
            # The query photo counts as shown, so its own duplicates are hidden too.
            seen, kept = {groups.get(exclude)} - {None}, []
            for pid in ranked:
                g = groups.get(pid)
                if g is not None:
                    if g in seen:
                        continue
                    seen.add(g)
                kept.append(pid)
            ranked = kept

        page = ranked[offset : offset + limit]
        score_map = {pid: float(scores[index.row[pid]]) for pid in page}
        return {"total": len(ranked), "items": items_for(conn, page, score_map)}

    # ---- API -----------------------------------------------------------------

    def listing(flt: PhotoFilter, collapse: str, sort: str, group: str | None):
        """SQL pieces for the grid listing: (cte, shown, order, params).
        ``{cte} SELECT ... {shown}`` selects the listed photos as ``p``."""
        where, params = flt.where(model_id)
        if group and group not in GROUP_KEYS:
            raise HTTPException(400, f"group must be one of {', '.join(GROUP_KEYS)}")
        order = {
            "taken_at": "p.taken_at IS NULL, p.taken_at, p.source, p.rel_path",
            "-taken_at": "p.taken_at IS NULL, p.taken_at DESC, p.source, p.rel_path",
            "path": "p.source, p.rel_path",
            "name": f"{FILENAME_EXPR} COLLATE NOCASE, p.source, p.rel_path",
            "-name": f"{FILENAME_EXPR} COLLATE NOCASE DESC, p.source, p.rel_path",
            # by place name; photos without a location last either way
            "place": f"{PLACE_NAME} IS NULL, {PLACE_NAME} COLLATE NOCASE, p.taken_at IS NULL, p.taken_at",
            "-place": f"{PLACE_NAME} IS NULL, {PLACE_NAME} COLLATE NOCASE DESC, p.taken_at IS NULL, p.taken_at",
            "id": "p.id",
        }.get(sort)
        if order is None:
            raise HTTPException(400, "sort must be taken_at, -taken_at, name, -name, place, -place, path, id, taste or -taste")
        condition = ""
        if collapse == "dupes":
            # One representative per duplicate group: its lowest id within the filtered set.
            condition = """WHERE f.dupe_group IS NULL
                OR f.id = (SELECT MIN(g.id) FROM filtered g WHERE g.dupe_group = f.dupe_group)"""
        elif collapse == "stacks":
            # One per stack: its pick if there is one (your choice shows), else the first.
            condition = f"""WHERE f.stack_id IS NULL
                OR f.id = (SELECT g.id FROM filtered g WHERE g.stack_id = f.stack_id
                           ORDER BY {flag_expr('g')} = 'pick' DESC, g.taken_at, g.id LIMIT 1)"""
        cte = f"WITH filtered AS (SELECT p.* FROM photos p WHERE {where})"
        shown = f"FROM filtered p WHERE p.id IN (SELECT f.id FROM filtered f {condition})"
        return cte, shown, order, params

    @app.get("/api/photos")
    def list_photos(
        flt: PhotoFilter = Depends(resolved_filter),
        dupes: str = "collapse",
        collapse: str | None = None,
        sort: str = "taken_at",
        group: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
    ):
        """Paged listing. With ``group`` (day, month, year) photos come in date
        order, each item carries its group key ("" = undated, last), and
        ``groups`` lists every non-empty group of the filtered set with its count.
        ``sort=taste`` / ``-taste``: likely keepers / likely rejects first (taste model)."""
        if group and sort not in ("taken_at", "-taken_at") and sort not in NAME_SORTS.get(group, ()):
            sort = "taken_at"  # groups must be contiguous
        if sort in ("taste", "-taste"):
            return taste_listing(flt, collapse_mode(collapse, dupes), sort, offset, limit, conn)
        cte, shown, order, params = listing(flt, collapse_mode(collapse, dupes), sort, group)
        key = GROUP_KEYS[group] if group else "NULL"
        total = conn.execute(f"{cte} SELECT COUNT(*) {shown}", params).fetchone()[0]
        direction = "DESC" if sort == "-taken_at" else ""
        by_location = group in LOCATION_KEYS
        groups = group_summary(conn, cte, shown, params, group, direction, sort) if group else None
        if group and sort in NAME_SORTS.get(group, ()):
            # Groups by name (place or folder), in the order of the summary; the sort within them.
            if groups:
                cte += ", grank(k, r) AS (VALUES " + ", ".join("(?, ?)" for _ in groups) + ")"
                params = [*params, *(x for r, g in enumerate(groups) for x in (g["key"], r))]
                order = f"(SELECT r FROM grank WHERE k = COALESCE(grp, '')), {order}"
        elif by_location:
            # Location groups in trip order: by each group's first photo, unknown last.
            order = f"grp IS NULL, MIN(p.taken_at) OVER (PARTITION BY grp) {direction}, grp, {order}"
        elif group == "folder":
            # Folders in path order (each folder's photos together), by date within a folder.
            order = f"grp, {order}"
        rows = conn.execute(
            f"{cte} SELECT p.id, {key} AS grp {shown} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = items_for(conn, [r[0] for r in rows])
        result = {"total": total, "items": items}
        if group:
            keys = {r[0]: r[1] or "" for r in rows}
            for item in items:
                item["group"] = keys[item["id"]]
            result["groups"] = groups
        return result

    def taste_listing(flt, collapse, sort, offset, limit, conn) -> dict:
        model = taste_store.current(get_index())
        if model is None or not model.enabled:
            raise HTTPException(
                409, model.reason if model else "Not calibrated yet: Library → Your taste → Calibrate."
            )
        cte, shown, order, params = listing(flt, collapse, "taken_at", None)
        ids = [r[0] for r in conn.execute(f"{cte} SELECT p.id {shown} ORDER BY {order}", params)]
        sign = -1.0 if sort == "taste" else 1.0
        missing = 2.0  # photos without an embedding go last either way
        ids.sort(key=lambda i: sign * s if (s := taste_store.score_of(i)) is not None else missing)
        return {"total": len(ids), "items": items_for(conn, ids[offset : offset + limit])}

    def folder_label(key: str | None) -> str:
        """ "/photos/Sweden/" + "day 1/" -> "Sweden / day 1" (the source folder's name, then the subfolders)."""
        if not key:
            return "Unknown folder"
        for s in sorted(cfg.sources, key=lambda p: -len(str(p))):
            prefix = f"{s}/"
            if key.startswith(prefix):
                rest = key[len(prefix):].strip("/")
                return " / ".join([s.name, *rest.split("/")]) if rest else s.name
        return key.rstrip("/")

    def group_summary(conn, cte: str, shown: str, params: list, group: str, direction: str = "", sort: str = "") -> list[dict]:
        """Every non-empty group of the listing, in display order, with its count,
        first/last capture time, and a cover photo (a pick if the group has one,
        else its first photo). Location groups add a label and a centre point.
        With a name sort (``NAME_SORTS``), groups are ordered by their label, unknown last."""
        key = GROUP_KEYS[group]
        by_location = group in LOCATION_KEYS
        group_order = f"MIN(p.taken_at) {direction}, grp" if by_location else f"grp {direction}"
        extra = ""
        if by_location:
            lat = "(SELECT l.lat FROM photo_locations l WHERE l.photo_id = p.id)"
            lon = "(SELECT l.lon FROM photo_locations l WHERE l.photo_id = p.id)"
            extra = f", AVG({lat}), AVG({lon})"
        rows = conn.execute(
            f"""{cte} SELECT {key} AS grp, COUNT(*), MIN(p.taken_at), MAX(p.taken_at){extra} {shown}
                GROUP BY grp ORDER BY grp IS NULL, {group_order}""",
            params,
        ).fetchall()
        covers = dict(
            conn.execute(
                f"""{cte} SELECT grp, id FROM (
                      SELECT {key} AS grp, p.id AS id, ROW_NUMBER() OVER (
                        PARTITION BY {key} ORDER BY {FLAG_EXPR} = 'pick' DESC, p.taken_at, p.id) AS rn
                      {shown}) WHERE rn = 1""",
                params,
            ).fetchall()
        )
        labels = location_labels(conn)[group] if by_location else {}
        out = []
        for r in rows:
            k = r[0]
            g = {"key": k or "", "count": r[1], "first": r[2], "last": r[3], "cover": covers.get(k)}
            if group == "folder":
                g["label"] = folder_label(k)
            if by_location:
                g["label"] = labels.get(k, "Unknown location") if k else "Unknown location"
                g["lat"], g["lon"] = (r[4], r[5]) if k else (None, None)
            out.append(g)
        if sort in NAME_SORTS.get(group, ()):
            known = sorted((g for g in out if g["key"]), key=lambda g: g["label"].casefold(), reverse=sort.startswith("-"))
            out = known + [g for g in out if not g["key"]]
        return out

    @app.get("/api/groups")
    def list_groups(
        group: str,
        flt: PhotoFilter = Depends(resolved_filter),
        dupes: str = "collapse",
        collapse: str | None = None,
        conn=Depends(get_conn),
    ):
        """Only the groups of a grouped listing (for the calendar and map overviews)."""
        if group not in GROUP_KEYS:
            raise HTTPException(400, f"group must be one of {', '.join(GROUP_KEYS)}")
        cte, shown, _, params = listing(flt, collapse_mode(collapse, dupes), "taken_at", group)
        return {"group": group, "groups": group_summary(conn, cte, shown, params, group)}

    @app.get("/api/photos/{photo_id}")
    def photo_detail(photo_id: int, conn=Depends(get_conn), index: Index = Depends(get_index)):
        r = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "photo not found")
        photo = dict(r)
        photo.pop("hues", None)  # binary (colors.py); only Curate uses it
        photo["path"] = str(Path(r["source"]) / r["rel_path"])
        photo["thumb"] = f"/thumbs/{photo_id}.jpg"
        photo["preview"] = f"/previews/{photo_id}.jpg"
        photo["tags"] = [
            dict(t)
            for t in conn.execute(
                """SELECT t.id, t.family, t.name, pt.prob, pt.sim FROM photo_tags pt
                   JOIN tags t ON t.id = pt.tag_id
                   WHERE pt.photo_id = ? AND pt.model_id = ?
                   ORDER BY t.family, pt.prob DESC""",
                (photo_id, model_id),
            )
        ]
        ctags = custom_tag_list(conn)
        members = custom_tag_members(conn, index, ctags)
        photo["custom_tags"] = [{"id": t["id"], "name": t["name"]} for t in ctags if photo_id in members[t["id"]]]
        photo["raws"] = [
            {"path": str(Path(x["source"]) / x["rel_path"]), "size_bytes": x["size_bytes"]}
            for x in conn.execute(
                "SELECT source, rel_path, size_bytes FROM raws WHERE photo_id = ? ORDER BY rel_path",
                (photo_id,),
            )
        ]
        photo["duplicates"] = []
        if r["dupe_group"] is not None:
            ids = [
                x[0]
                for x in conn.execute(
                    "SELECT id FROM photos WHERE dupe_group = ? AND status = 'ok' AND id != ? ORDER BY id",
                    (r["dupe_group"], photo_id),
                )
            ]
            photo["duplicates"] = items_for(conn, ids)
        loc = conn.execute("SELECT * FROM photo_locations WHERE photo_id = ?", (photo_id,)).fetchone()
        if loc is None:
            photo["location"] = None
        else:
            labels = location_labels(conn)
            cc, region, place = loc["country_code"], loc["region"], loc["place"]
            photo["location"] = {
                "lat": loc["lat"],
                "lon": loc["lon"],
                "source": loc["source"],
                "accuracy_m": loc["accuracy_m"],
                "gap_s": loc["gap_s"],
                "label": labels["place"].get(f"{cc}|{region}|{place}", ""),
                "place_key": f"{cc}|{region}|{place}",
                "region_key": f"{cc}|{region}",
                "country_key": cc,
            }
        photo["export"] = selections.export_info(cfg.selections_path, r["sha256"]) if r["sha256"] else None
        photo["taste"] = taste_store.score_of(photo_id)
        photo["flag"] = conn.execute(
            f"SELECT {FLAG_EXPR} FROM photos p WHERE p.id = ?", (photo_id,)
        ).fetchone()[0]
        photo["stack"] = [
            x[0]
            for x in conn.execute(
                """SELECT id FROM photos WHERE stack_id = ? AND status = 'ok'
                   ORDER BY taken_at IS NULL, taken_at, source, rel_path""",
                (r["stack_id"],),
            )
        ] if r["stack_id"] is not None else []
        return photo

    @app.get("/api/search/text")
    def search_text(
        q: str = Query(..., min_length=1),
        flt: PhotoFilter = Depends(resolved_filter),
        dupes: str = "collapse",
        collapse: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
        index: Index = Depends(get_index),
    ):
        encoder = state["encoder"]
        if encoder is None:
            if state["model"] == "loading":
                raise HTTPException(503, "The AI model is still loading; search works once it is ready.")
            raise HTTPException(503, "text encoder not available")
        from .query import query_vectors

        query = query_vectors(q, encoder)  # "a | b" alternatives, "-term" excludes (query.py)
        if query is None:
            raise HTTPException(400, "the search has no words")
        return rank(conn, index, query, flt, None, collapse_mode(collapse, dupes), offset, limit)

    @app.get("/api/search/similar/{photo_id}")
    def search_similar(
        photo_id: int,
        flt: PhotoFilter = Depends(resolved_filter),
        dupes: str = "collapse",
        collapse: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
        index: Index = Depends(get_index),
    ):
        row = index.row.get(photo_id)
        if row is None:
            raise HTTPException(404, "photo has no embedding")
        return rank(conn, index, index.E[row], flt, photo_id, collapse_mode(collapse, dupes), offset, limit)

    @app.get("/api/tags")
    def list_tags(flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn), index: Index = Depends(get_index)):
        """Tags with photo counts within the current filter (tags and EXIF).
        Tags that no matching photo carries are omitted, except the selected ones."""
        selected = flt.tags
        where, params = flt.where(model_id)
        counts = {
            r[0]: r[1]
            for r in conn.execute(
                f"""SELECT pt.tag_id, COUNT(*) FROM photo_tags pt
                    WHERE pt.model_id = ? AND pt.photo_id IN (SELECT p.id FROM photos p WHERE {where})
                    GROUP BY pt.tag_id""",
                [model_id, *params],
            )
        }
        families: dict[str, list] = {}
        # Families and tags come back in vocabulary order (tag ids follow it), tags by count.
        for r in conn.execute("SELECT id, family, name FROM tags ORDER BY id"):
            count = counts.get(r["id"], 0)
            excluded = r["id"] in flt.exclude_tags
            if count or r["id"] in selected or excluded:  # keep chosen tags visible to switch them off
                families.setdefault(r["family"], []).append(
                    {"id": r["id"], "name": r["name"], "count": count, "excluded": excluded}
                )
        for items in families.values():
            items.sort(key=lambda t: -t["count"])
        photos = conn.execute("SELECT COUNT(*) FROM photos WHERE status = 'ok'").fetchone()[0]
        unmatched = conn.execute("SELECT COUNT(*) FROM raws WHERE photo_id IS NULL").fetchone()[0]
        flag_counts = dict(
            conn.execute(
                f"SELECT {FLAG_EXPR}, COUNT(*) FROM photos p WHERE p.status = 'ok' GROUP BY 1"
            ).fetchall()
        )
        picks, rejects = flag_counts.get("pick", 0), flag_counts.get("reject", 0)
        exported = conn.execute(
            f"SELECT COUNT(*) FROM photos p WHERE p.status = 'ok' AND {EXPORTED_EXPR}"
        ).fetchone()[0]
        ctags = custom_tag_list(conn)
        members = custom_tag_members(conn, index, ctags)
        custom = [
            {
                "id": t["id"],
                "name": t["name"],
                "strictness": t["strictness"],
                "examples": t["examples"],
                "excluded": t["id"] in flt.exclude_ctags,
                "count": conn.execute(
                    f"SELECT COUNT(*) FROM photos p WHERE {where} AND p.id IN (SELECT value FROM json_each(?))",
                    [*params, json.dumps(members[t["id"]])],
                ).fetchone()[0],
            }
            for t in ctags
        ]
        return {
            "families": families,
            "custom": custom,
            "photos": photos,
            "picks": picks,
            "rejects": rejects,
            "exported": exported,
            "location_history": bool(cfg.location_history),
            "unmatched_raws": unmatched,
            "model_id": model_id,
            "text_search": state["encoder"] is not None,
        }

    @app.get("/api/facets")
    def list_facets(flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn)):
        """EXIF filter options (date range, cameras, lenses, focal length, aperture,
        ISO, orientation, GPS, folder) within the other active filters."""
        out = facets(conn, flt, model_id)
        # Folders labelled like the folder groups, by photo folder in the Library's order.
        order = {f"{src}/": k for k, src in enumerate(cfg.sources)}

        def rank(key: str) -> tuple:
            source = max((s for s in order if key.startswith(s)), key=len, default=None)
            return (order.get(source, len(order)), key)

        out["folder"] = [f | {"label": folder_label(f["value"])} for f in sorted(out["folder"], key=lambda f: rank(f["value"]))]
        return out

    # ---- culling: flags, selection, stacks ---------------------------------------

    @app.post("/api/flags", dependencies=[Depends(require_json)])
    def set_flags(body: FlagsIn, conn=Depends(get_conn)):
        """Apply flag changes in order, atomically. Returns the previous flag of
        every affected photo, which the UI keeps for undo."""
        try:
            previous = selections.set_flags(
                conn, cfg.selections_path, [(op.ids, op.flag) for op in body.ops]
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"previous": {str(k): v for k, v in previous.items()}}

    @app.post("/api/exported/reset", dependencies=[Depends(require_json)])
    def reset_exported(body: ResetIn, flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn)):
        """Forget the export history, of everything or of the photos within the filters."""
        if body.scope == "all":
            n = selections.clear_exported(conn, cfg.selections_path)
        elif body.scope == "filtered":
            where, params = flt.where(model_id)
            ids = [r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where}", params)]
            n = selections.clear_exported(conn, cfg.selections_path, ids)
        else:
            raise HTTPException(400, "scope must be all or filtered")
        return {"cleared": n}

    @app.post("/api/flags/reset", dependencies=[Depends(require_json)])
    def reset_flags(body: ResetIn, flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn)):
        """Unflag everything, or every photo within the filters. Returns the
        previous flags, like /api/flags, so the reset can be undone."""
        if body.scope == "all":
            previous = selections.clear_flags(conn, cfg.selections_path)
        elif body.scope == "filtered":
            where, params = flt.where(model_id)
            ids = [r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where}", params)]
            previous = selections.clear_flags(conn, cfg.selections_path, ids)
        else:
            raise HTTPException(400, "scope must be all or filtered")
        return {"previous": {str(k): v for k, v in previous.items()}}

    @app.get("/api/ids")
    def list_ids(
        flt: PhotoFilter = Depends(resolved_filter),
        dupes: str = "collapse",
        collapse: str | None = None,
        sort: str = "taken_at",
        conn=Depends(get_conn),
    ):
        """Every id of the listing, in order (for select-all)."""
        sort = "taken_at" if sort in ("taste", "-taste") else sort
        cte, shown, order, params = listing(flt, collapse_mode(collapse, dupes), sort, None)
        return {"ids": [r[0] for r in conn.execute(f"{cte} SELECT p.id {shown} ORDER BY {order}", params)]}

    @app.get("/api/stacks")
    def list_stacks(
        flt: PhotoFilter = Depends(resolved_filter),
        unreviewed: bool = False,
        conn=Depends(get_conn),
    ):
        """Stacks with at least one photo matching the filters, in date order.
        ``unreviewed``: only stacks that still have an unflagged photo."""
        where, params = flt.where(model_id)
        having = f"HAVING SUM({flag_expr('s')} IS NULL) > 0" if unreviewed else ""
        rows = conn.execute(
            f"""SELECT s.stack_id, COUNT(*) AS size, MIN(s.taken_at) AS taken_at,
                       SUM({flag_expr('s')} IS NULL) AS unflagged,
                       SUM({flag_expr('s')} = 'pick') AS picked
                FROM photos s
                WHERE s.status = 'ok' AND s.stack_id IN (
                    SELECT p.stack_id FROM photos p WHERE {where} AND p.stack_id IS NOT NULL)
                GROUP BY s.stack_id {having}
                ORDER BY taken_at IS NULL, taken_at, s.stack_id""",
            params,
        ).fetchall()
        return {"stacks": [dict(r) | {"id": r["stack_id"]} for r in rows]}

    @app.get("/api/stacks/{stack_id}")
    def stack_detail(stack_id: int, conn=Depends(get_conn)):
        """All photos of a stack (regardless of filters), in capture order."""
        ids = [
            r[0]
            for r in conn.execute(
                """SELECT id FROM photos WHERE stack_id = ? AND status = 'ok'
                   ORDER BY taken_at IS NULL, taken_at, source, rel_path""",
                (stack_id,),
            )
        ]
        if not ids:
            raise HTTPException(404, "stack not found")
        return {"id": stack_id, "items": items_for(conn, ids)}

    # CLIP-IQA style prompt pairs (Wang et al., 2023): how much more a photo looks
    # like the first than the second of each pair.
    QUALITY_PROMPTS = [("Good photo.", "Bad photo."), ("Sharp photo.", "Blurry photo.")]

    def clip_quality(index: Index, ids: list[int]) -> dict[int, float] | None:
        encoder = state["encoder"]
        if encoder is None or index.E.size == 0:
            return None
        if "quality_text" not in state:
            state["quality_text"] = encoder([t for pair in QUALITY_PROMPTS for t in pair])
        T = state["quality_text"]
        out = {}
        for pid in ids:
            row = index.row.get(pid)
            if row is None:
                continue
            sims = (T @ index.E[row]).reshape(len(QUALITY_PROMPTS), 2) * 100
            good = np.exp(sims[:, 0] - sims.max(axis=1))
            bad = np.exp(sims[:, 1] - sims.max(axis=1))
            out[pid] = float((good / (good + bad)).mean())
        return out

    @app.get("/api/suggest")
    def suggest_keeper(ids: str, conn=Depends(get_conn), index: Index = Depends(get_index)):
        """The suggested keeper among similar photos (e.g. a stack), with the scores
        behind it: sharpness, exposure (clipping) and a CLIP quality score."""
        from .quality import keeper_scores

        try:
            wanted = [int(i) for i in ids.split(",") if i.strip()]
        except ValueError:
            raise HTTPException(400, "ids must be comma-separated photo ids")
        photos = items_for(conn, wanted)
        suggested, scores = keeper_scores(photos, clip_quality(index, wanted))
        return {"suggested": suggested, "scores": {str(k): v for k, v in scores.items()}}

    # ---- curate ------------------------------------------------------------------

    def styles_config():
        from . import curate, paths

        return curate.load_styles(cfg.vocabulary_path, paths.default_file("vocabulary.yaml"))

    def style_scores(index: Index) -> dict[str, dict[int, float]]:
        """Per style, how much more each photo looks like "towards" than "away" (CLIP)."""
        encoder = state["encoder"]
        if encoder is None or index.E.size == 0:
            return {}
        styles = styles_config()
        key = tuple((s.name, tuple(s.towards), tuple(s.away)) for s in styles)
        if state.get("style_key") != key:
            vecs = {}
            for st in styles:
                pos, neg = encoder(st.towards).mean(axis=0), encoder(st.away).mean(axis=0)
                vecs[st.name] = pos / np.linalg.norm(pos) - neg / np.linalg.norm(neg)
            state["style_vecs"], state["style_key"] = vecs, key
        ids = [int(i) for i in index.ids]
        return {name: dict(zip(ids, (index.E @ v).tolist())) for name, v in state["style_vecs"].items()}

    def curate_pool(body: CurateIn, flt: PhotoFilter, conn, index: Index):
        from . import curate

        p = curate.Params(
            n=max(2, min(60, body.n)),
            variety=min(1.0, max(0.0, body.variety)),
            time_spread=min(1.0, max(0.0, body.time_spread)),
            place_spread=min(1.0, max(0.0, body.place_spread)),
            styles={
                k: min(1.0, max(-1.0, float(w)))
                for k, w in body.styles.items()
                if k in {st.name for st in styles_config()}
            },
            include_rejects=body.include_rejects,
            locked=body.locked,
            removed=body.removed,
        )
        where, params = flt.where(model_id)
        model = taste_store.current(index)
        scores = style_scores(index) if any(p.styles.values()) else {}
        look, look_weights, look_labels = curate.look_scores(conn, body.look)
        p.styles.update(look_weights)
        query_scores = None
        if body.query.strip() and state["encoder"] is not None and index.E.size:
            from .query import query_vectors, score

            Q = query_vectors(body.query, state["encoder"])
            if Q is not None:
                query_scores = dict(zip((int(i) for i in index.ids), score(index.E, Q).tolist()))
        pool = curate.build_pool(
            conn, where, params, index.row, index.E, p,
            taste=taste_store.scores if model and model.enabled else None,
            clip_quality=clip_quality(index, [int(i) for i in index.ids]),
            style_scores={**scores, **look},
            place_labels=location_labels(conn)["place"],
            query_scores=query_scores,
        )
        return pool, p, look_labels

    @app.get("/api/hues")
    def list_hues():
        """The colours Curate can lean towards (name and a display colour)."""
        from .colors import HUES

        return [{"name": name, "color": css} for name, (_, css) in HUES.items()]

    def tag_error(e: Exception) -> HTTPException:
        text = str(e)
        return HTTPException(404 if text == "no such tag" else 409 if "already exists" in text else 400, text)

    @app.post("/api/custom-tags", dependencies=[Depends(require_json)])
    def create_custom_tag(body: CustomTagIn, conn=Depends(get_conn)):
        """A tag from example photos (stored with the flags, by content hash)."""
        try:
            tag_id = selections.create_tag(conn, cfg.selections_path, body.name, body.photo_ids, body.strictness)
        except selections.TagError as e:
            raise tag_error(e)
        return {"id": tag_id}

    @app.post("/api/custom-tags/preview", dependencies=[Depends(require_json)])
    def preview_custom_tag(body: CustomTagPreview, conn=Depends(get_conn), index: Index = Depends(get_index)):
        """How many photos a tag with these examples would have at each strictness,
        and the members closest to its edge (the least similar ones still in)."""
        from . import custom_tags

        rows = custom_tags.example_rows(index.row, body.photo_ids)
        s = custom_tags.scores(index.E, rows)
        counts, edge = {}, []
        for level in selections.STRICTNESS:
            limit = custom_tags.threshold(cfg.stacks.min_similarity, level)
            ids, _ = custom_tags.members(index.E, index.ids, rows, limit)
            counts[level] = len(ids)
            if level == body.strictness and rows:
                own = set(rows)
                inside = [k for k in np.argsort(s) if s[k] >= limit and k not in own]
                edge = [int(index.ids[k]) for k in inside[:8]]
        return {"counts": counts, "edge": items_for(conn, edge), "examples": len(rows)}

    @app.post("/api/custom-tags/{tag_id}", dependencies=[Depends(require_json)])
    def edit_custom_tag(tag_id: int, body: CustomTagEdit, conn=Depends(get_conn)):
        try:
            selections.update_tag(
                conn, cfg.selections_path, tag_id,
                name=body.name, strictness=body.strictness, add=body.add, remove=body.remove,
            )
        except selections.TagError as e:
            raise tag_error(e)
        return {"id": tag_id}

    @app.delete("/api/custom-tags/{tag_id}")
    def delete_custom_tag(tag_id: int):
        if not selections.delete_tag(cfg.selections_path, tag_id):
            raise HTTPException(404, "no such tag")
        ctag_cache.pop(tag_id, None)
        return {"deleted": tag_id}

    @app.get("/api/styles")
    def list_styles():
        return [{"name": s.name, "label": s.label, "description": s.description} for s in styles_config()]

    @app.post("/api/curate", dependencies=[Depends(require_json)])
    def curate_draft(body: CurateIn, flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn), index: Index = Depends(get_index)):
        """A draft selection from the photos within the filters (see curate.py)."""
        from . import curate

        pool, p, look_labels = curate_pool(body, flt, conn, index)
        where, params = flt.where(model_id)
        if pool is None:
            return {"items": [], "cover": None, "sections": [], "candidates": 0, "used": {}}
        d = curate.draft(pool, p)
        labels = {s.name: s.label for s in styles_config()} | look_labels
        ids = [it["id"] for it in d["items"]]
        by_id = {i["id"]: i for i in items_for(conn, ids)}
        items = []
        for it in d["items"]:
            j = it["index"]
            if it["id"] in by_id:
                items.append(by_id[it["id"]] | {
                    "q": round(float(pool.q[j]), 3),
                    "reason": curate.reason(pool, j, p, labels),
                    "day": pool.info[j]["day"],
                    "place": pool.info[j]["place"],
                    "similar": len(pool.members[j]),
                    "locked": bool(set(p.locked) & set(pool.members[j])),
                })
        return {
            "items": items,
            "cover": d["cover"],
            "sections": d["sections"],
            "candidates": len(pool.ids),
            "used": {
                "taste": bool(taste_store.scores),
                "locations": bool((pool.loc_w > 0).any()),
                "styles": sorted(k for k, w in p.styles.items() if w),
                # false when a search was given but could not be used (the AI model is not ready)
                "query": pool.query_pct is not None if body.query.strip() else None,
                # candidates whose colours are not analysed yet (the next index does it)
                "colors_missing": conn.execute(
                    f"SELECT COUNT(*) FROM photos p WHERE {where} AND p.brightness IS NULL", params
                ).fetchone()[0],
            },
        }

    @app.post("/api/curate/alternatives", dependencies=[Depends(require_json)])
    def curate_alternatives(body: AlternativesIn, flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn), index: Index = Depends(get_index)):
        """Photos that could take one slot of the draft: its other frames, then similar good ones."""
        from . import curate

        pool, p, _ = curate_pool(body, flt, conn, index)
        if pool is None:
            return {"items": []}
        chosen = {int(pool.ids[j]) for j in curate.select(pool, p)}
        ids = curate.alternatives(pool, body.id, chosen, p)
        return {"items": items_for(conn, ids)}

    # ---- export ------------------------------------------------------------------

    @app.post("/api/export", dependencies=[Depends(require_json)])
    def start_export(body: ExportIn, flt: PhotoFilter = Depends(resolved_filter), conn=Depends(get_conn)):
        """Copy the picks (all, or those within the query-string filters) to a folder."""
        if body.content not in CONTENT:
            raise HTTPException(400, f"content must be one of {', '.join(CONTENT)}")
        if body.structure not in STRUCTURE:
            raise HTTPException(400, f"structure must be one of {', '.join(STRUCTURE)}")
        name = body.name.strip()
        if name and (Path(name).name != name or name in (".", "..")):
            raise HTTPException(400, "the folder name must not contain slashes")
        folder = Path(body.folder.strip()).expanduser()
        if not folder.is_absolute():
            raise HTTPException(400, "use an absolute destination path")
        folder = (folder / name if name else folder).resolve()
        try:
            check_destination(cfg, folder)
        except ExportError as e:
            raise HTTPException(400, str(e))
        scope = flt if body.scope == "filtered" else PhotoFilter()
        where, params = scope.where(model_id)
        if body.photo_ids is not None:
            wanted = list(dict.fromkeys(body.photo_ids))
            present = {r[0] for r in conn.execute(
                f"SELECT id FROM photos WHERE status = 'ok' AND id IN ({','.join('?' * len(wanted))})", wanted
            )} if wanted else set()
            ids = [i for i in wanted if i in present]
            if not ids:
                raise HTTPException(400, "none of these photos can be exported")
        else:
            ids = None
        ids = ids if ids is not None else [
            r[0]
            for r in conn.execute(
                f"""SELECT p.id FROM photos p WHERE {where} AND {FLAG_EXPR} = 'pick'
                    {f"AND NOT {EXPORTED_EXPR}" if body.only_new else ""}
                    ORDER BY p.taken_at IS NULL, p.taken_at, p.source, p.rel_path""",
                params,
            )
        ]
        if not ids:
            raise HTTPException(400, "no new picked photos to export" if body.only_new else "no picked photos to export")
        if body.photo_ids is not None and body.only_new:
            fresh = {r[0] for r in conn.execute(
                f"SELECT p.id FROM photos p WHERE NOT {EXPORTED_EXPR} AND p.id IN ({','.join('?' * len(ids))})", ids
            )}
            ids = [i for i in ids if i in fresh]
            if not ids:
                raise HTTPException(400, "all of these photos were exported before")
        started = export_job.start(
            photo_ids=ids,
            folder=str(folder),
            content=body.content,
            raw_fallback=body.raw_fallback,
            structure=body.structure,
            add_location=body.add_location and bool(cfg.location_history),
        )
        if not started:
            raise HTTPException(409, "an export is already running")
        return export_job.status()

    @app.get("/api/export")
    def export_status():
        return export_job.status()

    @app.get("/api/raws/unmatched")
    def unmatched_raws(conn=Depends(get_conn)):
        return [
            {"path": str(Path(r["source"]) / r["rel_path"]), "size_bytes": r["size_bytes"]}
            for r in conn.execute(
                "SELECT source, rel_path, size_bytes FROM raws WHERE photo_id IS NULL ORDER BY source, rel_path"
            )
        ]

    # ---- library: folders and indexing -----------------------------------------

    def folder_path(raw: str) -> Path:
        path = Path(raw.strip()).expanduser()
        if not path.is_absolute():
            raise HTTPException(400, "use an absolute path")
        return path.resolve()

    @app.get("/api/events")
    async def events():
        """A long-lived stream per open tab (Server-Sent Events). It tells the server
        which tabs are open (for AutoExit) and lets the page notice a stopped server."""

        async def stream():
            state["clients"] += 1
            state["seen_client"] = True  # even a tab that closes before the next watchdog check
            try:
                # The first message says whether the server stops with its last tab; this
                # and later ones carry the model status whenever it changes.
                sent = model_status()
                yield "retry: 2000\ndata: " + json.dumps({"auto_exit": bool(auto_exit), **sent}) + "\n\n"
                # A closed tab only shows up when writing to it fails (Starlette with
                # ASGI 2.4 does not listen for disconnects), so write a tiny comment
                # every 2 s: the stream is then cancelled within ~2 s of the tab closing.
                # On shutdown the stream ends by itself, so a graceful stop does not
                # wait for open tabs (the page reconnects when Riffle is back).
                ticks = 0
                while not state["stopping"]:
                    await asyncio.sleep(0.25)
                    ticks += 1
                    if (now := model_status()) != sent:
                        sent = now
                        yield "data: " + json.dumps({"auto_exit": bool(auto_exit), **now}) + "\n\n"
                    elif ticks % 8 == 0:
                        yield ": keep-alive\n\n"
            finally:
                state["clients"] -= 1

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    def taste_summary(conn, model) -> dict:
        """The model's status plus how many flags / exports changed since calibrating."""
        if model is None:
            return {"enabled": False, "calibrated": False, "reason": "Not calibrated yet.", "changed_since": None}
        since = model.calibrated_at or 0
        changed = conn.execute("SELECT COUNT(*) FROM sel.flags WHERE updated_at > ?", (since,)).fetchone()[0]
        changed += conn.execute("SELECT COUNT(*) FROM sel.exported WHERE last_at > ?", (since,)).fetchone()[0]
        return model.summary() | {"calibrated": True, "changed_since": changed}

    @app.get("/api/taste")
    def taste_status(conn=Depends(get_conn), index: Index = Depends(get_index)):
        """Whether the taste model is available, what it learned from, how well it
        works (checked on scenes it did not learn from), and how stale it is."""
        return taste_summary(conn, taste_store.current(index))

    @app.post("/api/taste/calibrate", dependencies=[Depends(require_json)])
    def taste_calibrate(conn=Depends(get_conn), index: Index = Depends(get_index)):
        """Learn from the current flags and exports (takes a second or two)."""
        return taste_summary(conn, taste_store.calibrate(index))

    @app.get("/api/health")
    def health():
        """Identifies a running Riffle (and which config it serves) for the launcher."""
        from importlib.metadata import PackageNotFoundError, version

        try:
            v = version("riffle")
        except PackageNotFoundError:
            v = "dev"
        return {
            "app": "riffle",
            "version": v,
            "config": str(cfg.path) if cfg.path else None,
            "tabs": state["clients"],  # open browser tabs (live /api/events streams)
            **model_status(),
        }

    @app.get("/api/sources")
    def list_sources(conn=Depends(get_conn)):
        counts = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM photos WHERE status = 'ok' GROUP BY source"
            ).fetchall()
        )
        return {
            "sources": [
                {"path": str(s), "exists": s.is_dir(), "photos": counts.get(str(s), 0)}
                for s in cfg.sources
            ],
            "editable": cfg.path is not None,
            "config": str(cfg.path) if cfg.path else None,
            "selections": str(cfg.selections_path),
            "data_dir": str(cfg.data_dir),
        }

    @app.post("/api/sources", dependencies=[Depends(require_json)])
    def add_source(body: FolderIn):
        if cfg.path is None:
            raise HTTPException(409, "config was not loaded from a file")
        path = folder_path(body.path)
        if not path.is_dir():
            raise HTTPException(400, f"not a folder: {path}")
        if path == cfg.data_dir or path.is_relative_to(cfg.data_dir):
            raise HTTPException(400, "that is Riffle's own data folder")
        for s in cfg.sources:
            if path == s or path.is_relative_to(s):
                raise HTTPException(409, f"already included in {s}")
        # A parent of existing sources replaces them; their photos keep their ids (moves by hash).
        sources = [s for s in cfg.sources if not s.is_relative_to(path)] + [path]
        set_sources(cfg, sources)
        job.start()
        return {"ok": True, "path": str(path)}

    @app.delete("/api/sources", dependencies=[Depends(require_json)])
    def remove_source(body: FolderIn):
        if cfg.path is None:
            raise HTTPException(409, "config was not loaded from a file")
        path = folder_path(body.path)
        if path not in cfg.sources:
            raise HTTPException(404, "not a configured folder")
        set_sources(cfg, [s for s in cfg.sources if s != path])
        job.start()  # marks its photos missing
        return {"ok": True}

    # ---- location history ------------------------------------------------------

    @app.get("/api/location-history")
    def list_location_history(conn=Depends(get_conn)):
        """The referenced history files with what they contain, and how many photos
        are placed by each kind of evidence."""
        from .timeline import HistoryError, load

        files = []
        for p in cfg.location_history:
            entry = {"path": str(p), "exists": p.is_file()}
            if entry["exists"]:
                try:
                    entry |= load([p]).summary()
                except (HistoryError, OSError) as e:
                    entry["error"] = str(e)
            files.append(entry)
        placed = dict(conn.execute("SELECT source, COUNT(*) FROM photo_locations GROUP BY source").fetchall())
        photos = conn.execute("SELECT COUNT(*) FROM photos WHERE status = 'ok'").fetchone()[0]
        return {"files": files, "placed": placed, "photos": photos, "editable": cfg.path is not None}

    @app.post("/api/location-history", dependencies=[Depends(require_json)])
    def add_location_history(body: FolderIn):
        from .timeline import HistoryError, load_file

        if cfg.path is None:
            raise HTTPException(409, "config was not loaded from a file")
        path = folder_path(body.path)
        if not path.is_file():
            raise HTTPException(400, f"not a file: {path}")
        if path in cfg.location_history:
            raise HTTPException(409, "this file is already used")
        try:
            summary = load_file(path).summary()  # validates the format
        except HistoryError as e:
            raise HTTPException(400, str(e))
        set_location_history(cfg, [*cfg.location_history, path])
        job.start()
        return {"ok": True, "path": str(path)} | summary

    @app.delete("/api/location-history", dependencies=[Depends(require_json)])
    def remove_location_history(body: FolderIn):
        if cfg.path is None:
            raise HTTPException(409, "config was not loaded from a file")
        path = folder_path(body.path)
        if path not in cfg.location_history:
            raise HTTPException(404, "not a configured location history file")
        set_location_history(cfg, [p for p in cfg.location_history if p != path])
        job.start()
        return {"ok": True}

    @app.get("/api/fs")
    def browse(path: str | None = None, files: str | None = None):
        p = folder_path(path) if path else Path.home()
        if not p.is_dir():
            raise HTTPException(404, f"not a folder: {p}")
        dirs, images, listed = [], 0, []
        wanted = {"history": (".json", ".gpx")}.get(files or "", ())
        try:
            entries = sorted(os.scandir(p), key=lambda e: e.name.lower())
        except PermissionError:
            raise HTTPException(403, f"permission denied: {p}")
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": str(Path(e.path))})
                else:
                    ext = os.path.splitext(e.name)[1].lower()
                    if ext in cfg.image_extensions:
                        images += 1
                    if ext in wanted:
                        listed.append({"name": e.name, "path": str(Path(e.path)), "size": e.stat().st_size})
            except OSError:
                continue
        within = next((str(s) for s in cfg.sources if p == s or p.is_relative_to(s)), None)
        return {
            "path": str(p),
            "parent": str(p.parent) if p.parent != p else None,
            "dirs": dirs,
            "images": images,
            "files": listed,
            "source": within,
        }

    @app.get("/api/index")
    def index_status():
        return job.status()

    @app.post("/api/index", dependencies=[Depends(require_json)])
    def start_index():
        job.start()
        return job.status()

    # ---- static files ----------------------------------------------------------

    app.mount("/thumbs", StaticFiles(directory=cfg.thumbs_dir, check_dir=False), name="thumbs")
    app.mount("/previews", StaticFiles(directory=cfg.previews_dir, check_dir=False), name="previews")
    if (WEB_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_ui():
            return (
                "<p>The UI has not been built. Run <code>cd web && npm install && npm run build</code>,"
                " or use the Vite dev server (<code>npm run dev</code>).</p>"
            )

    return app


def run_server(app: FastAPI, host: str, port: int, *, on_created: Callable | None = None) -> None:
    """Serve ``app`` until stopped. One Ctrl+C stops it cleanly: the open event
    streams (browser tabs) are ended first, so the graceful shutdown does not wait
    for them, and uvicorn's re-raised KeyboardInterrupt is not shown as a crash.
    ``on_created(server)`` gets the uvicorn server (e.g. for AutoExit)."""
    import uvicorn

    class RiffleServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            app.state.end_streams()
            super().handle_exit(sig, frame)

    server = RiffleServer(
        uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=3)
    )
    if on_created:
        on_created(server)
    try:
        server.run()
    except KeyboardInterrupt:
        pass  # uvicorn re-raises the Ctrl+C it handled; the server has already stopped
    print("Riffle stopped.")
