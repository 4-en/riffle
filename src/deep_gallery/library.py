"""The library a profile sees: the photos in its own folders (profiles.folders).

The catalogue holds every profile's photos, catalogued under the outermost folders
(profiles.indexed_roots). A profile's folder is usually one of those roots, so its
photos are those with that ``source``; a folder inside another profile's (``Trip``
where another has ``Pictures``) is matched by the start of the photo's path instead.
Everything that lists, counts or compares photos goes through ``Library.sql`` (or
the scoped embedding index built from ``ids``), so no other profile's photos leak in.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Library:
    folders: tuple[Path, ...]  # this profile's folders
    roots: tuple[Path, ...] = ()  # the indexed roots (every profile's, outermost)
    everything: bool = False  # no scope (tests, tools): every photo that is 'ok'
    key: str = field(init=False)

    def __post_init__(self):
        text = "*" if self.everything else "\n".join(sorted(str(f) for f in self.folders))
        object.__setattr__(self, "key", hashlib.sha1(text.encode()).hexdigest()[:16])

    @classmethod
    def all(cls) -> Library:
        return cls((), (), everything=True)

    def sql(self, alias: str = "p") -> tuple[str, list]:
        """(SQL condition, params): the photo is 'ok' and in one of the folders."""
        ok = f"{alias}.status = 'ok'"
        if self.everything:
            return ok, []
        if not self.folders:
            return f"{ok} AND 0", []
        roots = set(self.roots)
        whole = [str(f) for f in self.folders if f in roots or not self.roots]
        inner = [f for f in self.folders if self.roots and f not in roots]
        parts, params = [], []
        if whole:
            parts.append(f"{alias}.source IN ({','.join('?' * len(whole))})")
            params += whole
        for f in inner:
            # The photo's full path starts with "<folder>/" (substr, not LIKE: % and _ are literal).
            prefix = f"{Path(f).as_posix()}/"
            parts.append(f"substr(replace({alias}.source, '\\', '/') || '/' || {alias}.rel_path, 1, ?) = ?")
            params += [len(prefix), prefix]
        return f"{ok} AND ({' OR '.join(parts)})", params

    def ids(self, conn: sqlite3.Connection) -> list[int]:
        where, params = self.sql("p")
        return [r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where} ORDER BY p.id", params)]
