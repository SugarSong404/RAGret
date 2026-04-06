"""Named SQLite index registry (JSON file) for the bcrag HTTP server and optional CLI --register-as."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_RESERVED_NAMES = frozenset(
    {"indexes", "health", "favicon.ico"},
)


def safe_index_name(name: str) -> str:
    s = name.strip()
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    if not s:
        raise ValueError("Index name is empty after sanitization.")
    if s.lower() in _RESERVED_NAMES:
        raise ValueError(f"Index name is reserved: {s!r}")
    return s


class IndexRegistry:
    """Maps logical index id (URL segment) to absolute .sqlite path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._indexes: dict[str, str] = {}

    def load(self) -> None:
        if not self.path.is_file():
            self._indexes = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        idx = raw.get("indexes")
        if not isinstance(idx, dict):
            self._indexes = {}
            return
        self._indexes = {str(k): str(v) for k, v in idx.items()}

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"indexes": dict(sorted(self._indexes.items()))}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)

    def add(self, name: str, db_path: Path) -> str:
        key = safe_index_name(name)
        resolved = str(db_path.resolve())
        with self._lock:
            self.load()
            self._indexes[key] = resolved
            self._save_unlocked()
        return key

    def remove(self, name: str) -> bool:
        key = safe_index_name(name)
        with self._lock:
            self.load()
            if key not in self._indexes:
                return False
            del self._indexes[key]
            self._save_unlocked()
        return True

    def get_path(self, name: str) -> Path | None:
        key = safe_index_name(name)
        with self._lock:
            self.load()
            p = self._indexes.get(key)
        return Path(p) if p else None

    def list_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            self.load()
            items = list(self._indexes.items())
        out: list[dict[str, Any]] = []
        for n, p in sorted(items, key=lambda x: x[0].lower()):
            path = Path(p)
            out.append(
                {
                    "name": n,
                    "db_path": p,
                    "sqlite_exists": path.is_file(),
                }
            )
        return out


def safe_sqlite_basename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name or "bcrag_index"


def resolve_db_path(work_path: Path, name: str | None) -> tuple[Path, Path]:
    work_path = work_path.resolve()
    if not work_path.exists():
        raise FileNotFoundError(work_path)
    if work_path.is_dir():
        parent = work_path.parent
        default = work_path.name
    else:
        parent = work_path.parent
        default = work_path.stem
    base = safe_sqlite_basename(name or default)
    return work_path, parent / f"{base}.sqlite"
