"""FastAPI server: JSON API, thumbnails/previews, and the built Svelte UI.

At startup it loads the embedding matrix and the CLIP text encoder only.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .config import Config, load_config, set_sources
from .embed import embedding_paths, load_embeddings
from .jobs import IndexJob

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

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


def _parse_tags(tags: str | None) -> list[int]:
    if not tags:
        return []
    try:
        return sorted({int(t) for t in tags.split(",") if t.strip()})
    except ValueError:
        raise HTTPException(400, "tags must be comma-separated tag ids")


class FolderIn(BaseModel):
    path: str


def require_json(request: Request) -> None:
    # Mutating endpoints only accept JSON, which a cross-site form post cannot send
    # without a CORS preflight (and this server grants none).
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "expected application/json")


def create_app(
    cfg: Config | None = None,
    text_encoder: TextEncoder | None = None,
    index_runner: Callable | None = None,
) -> FastAPI:
    cfg = cfg or load_config(os.environ.get("ARCHIVE_CONFIG", "config.yaml"))
    model_id = cfg.model.model_id
    state: dict = {"encoder": text_encoder}
    job = IndexJob(cfg, run=index_runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Make sure the catalogue exists so read-only connections work before the first index.
        db.connect(cfg.db_path).close()
        state["index"] = Index(cfg)
        if state["encoder"] is None:
            try:
                from .embed import Clip

                state["encoder"] = Clip(cfg.model, text_only=True).encode_text
            except Exception as e:  # noqa: BLE001 - browsing still works without text search
                log.error("Could not load text encoder: %s", e)
        yield

    app = FastAPI(title="Photo Archive", lifespan=lifespan)

    def get_conn():
        conn = db.connect_readonly(cfg.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def get_index() -> Index:
        index: Index = state["index"]
        index.refresh()
        return index

    # ---- helpers -------------------------------------------------------------

    def tag_filter_ids(conn, tag_ids: list[int]) -> set[int] | None:
        if not tag_ids:
            return None
        marks = ",".join("?" * len(tag_ids))
        rows = conn.execute(
            f"""SELECT photo_id FROM photo_tags
                WHERE model_id = ? AND tag_id IN ({marks})
                GROUP BY photo_id HAVING COUNT(DISTINCT tag_id) = ?""",
            [model_id, *tag_ids, len(tag_ids)],
        )
        return {r[0] for r in rows}

    def items_for(conn, ids: list[int], scores: dict[int, float] | None = None) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT p.id, p.source, p.rel_path, p.width, p.height, p.taken_at, p.dupe_group,
                       EXISTS (SELECT 1 FROM raws r WHERE r.photo_id = p.id) AS has_raw,
                       (SELECT COUNT(*) FROM photos d
                         WHERE d.dupe_group = p.dupe_group AND d.status = 'ok') AS dupe_count
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
                "thumb": f"/thumbs/{r['id']}.jpg",
            }
            if scores is not None:
                item["score"] = round(scores[pid], 4)
            out.append(item)
        return out

    def rank(conn, index: Index, query: np.ndarray, tag_ids: list[int], exclude: int | None,
             dupes: str, offset: int, limit: int) -> dict:
        if index.E.size == 0:
            return {"total": 0, "items": []}
        ok = {r[0] for r in conn.execute("SELECT id FROM photos WHERE status = 'ok'")}
        allowed = tag_filter_ids(conn, tag_ids)
        mask = np.array(
            [int(p) in ok and (allowed is None or int(p) in allowed) and int(p) != exclude
             for p in index.ids],
            dtype=bool,
        )
        scores = index.E @ query
        scores[~mask] = -np.inf
        order = np.argsort(-scores)[: int(mask.sum())]
        ranked = [int(index.ids[i]) for i in order]

        if dupes == "collapse":
            groups = dict(conn.execute(
                "SELECT id, dupe_group FROM photos WHERE dupe_group IS NOT NULL"
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

    @app.get("/api/photos")
    def list_photos(
        tags: str | None = None,
        dupes: str = "collapse",
        sort: str = "taken_at",
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
    ):
        tag_ids = _parse_tags(tags)
        params: list = []
        where = "p.status = 'ok'"
        if tag_ids:
            marks = ",".join("?" * len(tag_ids))
            where += f""" AND p.id IN (
                SELECT photo_id FROM photo_tags WHERE model_id = ? AND tag_id IN ({marks})
                GROUP BY photo_id HAVING COUNT(DISTINCT tag_id) = ?)"""
            params += [model_id, *tag_ids, len(tag_ids)]
        order = {
            "taken_at": "p.taken_at IS NULL, p.taken_at, p.source, p.rel_path",
            "-taken_at": "p.taken_at IS NULL, p.taken_at DESC, p.source, p.rel_path",
            "path": "p.source, p.rel_path",
            "id": "p.id",
        }.get(sort)
        if order is None:
            raise HTTPException(400, "sort must be taken_at, -taken_at, path or id")
        collapse = ""
        if dupes == "collapse":
            # One representative per duplicate group: its lowest id within the filtered set.
            collapse = """WHERE f.dupe_group IS NULL
                OR f.id = (SELECT MIN(g.id) FROM filtered g WHERE g.dupe_group = f.dupe_group)"""
        cte = f"WITH filtered AS (SELECT p.* FROM photos p WHERE {where})"
        total = conn.execute(f"{cte} SELECT COUNT(*) FROM filtered f {collapse}", params).fetchone()[0]
        rows = conn.execute(
            f"""{cte} SELECT p.id FROM filtered p
                WHERE p.id IN (SELECT f.id FROM filtered f {collapse})
                ORDER BY {order} LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return {"total": total, "items": items_for(conn, [r[0] for r in rows])}

    @app.get("/api/photos/{photo_id}")
    def photo_detail(photo_id: int, conn=Depends(get_conn)):
        r = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "photo not found")
        photo = dict(r)
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
        return photo

    @app.get("/api/search/text")
    def search_text(
        q: str = Query(..., min_length=1),
        tags: str | None = None,
        dupes: str = "collapse",
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
        index: Index = Depends(get_index),
    ):
        encoder = state["encoder"]
        if encoder is None:
            raise HTTPException(503, "text encoder not available")
        query = encoder([q])[0]
        return rank(conn, index, query, _parse_tags(tags), None, dupes, offset, limit)

    @app.get("/api/search/similar/{photo_id}")
    def search_similar(
        photo_id: int,
        tags: str | None = None,
        dupes: str = "collapse",
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        conn=Depends(get_conn),
        index: Index = Depends(get_index),
    ):
        row = index.row.get(photo_id)
        if row is None:
            raise HTTPException(404, "photo has no embedding")
        return rank(conn, index, index.E[row], _parse_tags(tags), photo_id, dupes, offset, limit)

    @app.get("/api/tags")
    def list_tags(tags: str | None = None, conn=Depends(get_conn)):
        """Tags with photo counts within the current tag filter. Tags that no
        matching photo carries are omitted, except the selected ones."""
        selected = _parse_tags(tags)
        params: list = [model_id]
        where = "pt.model_id = ?"
        if selected:
            marks = ",".join("?" * len(selected))
            where += f""" AND pt.photo_id IN (
                SELECT photo_id FROM photo_tags WHERE model_id = ? AND tag_id IN ({marks})
                GROUP BY photo_id HAVING COUNT(DISTINCT tag_id) = ?)"""
            params += [model_id, *selected, len(selected)]
        counts = {
            r[0]: r[1]
            for r in conn.execute(
                f"""SELECT pt.tag_id, COUNT(*) FROM photo_tags pt
                    JOIN photos p ON p.id = pt.photo_id AND p.status = 'ok'
                    WHERE {where} GROUP BY pt.tag_id""",
                params,
            )
        }
        families: dict[str, list] = {}
        # Families and tags come back in vocabulary order (tag ids follow it), tags by count.
        for r in conn.execute("SELECT id, family, name FROM tags ORDER BY id"):
            count = counts.get(r["id"], 0)
            if count or r["id"] in selected:
                families.setdefault(r["family"], []).append(
                    {"id": r["id"], "name": r["name"], "count": count}
                )
        for items in families.values():
            items.sort(key=lambda t: -t["count"])
        photos = conn.execute("SELECT COUNT(*) FROM photos WHERE status = 'ok'").fetchone()[0]
        unmatched = conn.execute("SELECT COUNT(*) FROM raws WHERE photo_id IS NULL").fetchone()[0]
        return {
            "families": families,
            "photos": photos,
            "unmatched_raws": unmatched,
            "model_id": model_id,
            "text_search": state["encoder"] is not None,
        }

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
        }

    @app.post("/api/sources", dependencies=[Depends(require_json)])
    def add_source(body: FolderIn):
        if cfg.path is None:
            raise HTTPException(409, "config was not loaded from a file")
        path = folder_path(body.path)
        if not path.is_dir():
            raise HTTPException(400, f"not a folder: {path}")
        if path == cfg.data_dir or path.is_relative_to(cfg.data_dir):
            raise HTTPException(400, "that is the archive's own data folder")
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

    @app.get("/api/fs")
    def browse(path: str | None = None):
        p = folder_path(path) if path else Path.home()
        if not p.is_dir():
            raise HTTPException(404, f"not a folder: {p}")
        dirs, images = [], 0
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
                elif os.path.splitext(e.name)[1].lower() in cfg.image_extensions:
                    images += 1
            except OSError:
                continue
        within = next((str(s) for s in cfg.sources if p == s or p.is_relative_to(s)), None)
        return {
            "path": str(p),
            "parent": str(p.parent) if p.parent != p else None,
            "dirs": dirs,
            "images": images,
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
