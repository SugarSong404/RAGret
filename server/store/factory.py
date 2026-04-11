"""Construct the application store implementation from environment."""
from __future__ import annotations

import os
from pathlib import Path

from server.runtime_paths import default_app_sqlite_path
from server.store.protocol import AppStore
from server.store.sqlite_store import SqliteAppStore


def create_app_store(repo_root: Path) -> AppStore:
    """Create store backend. ``RAGRET_APP_STORE=sqlite`` (default); others later (e.g. mysql)."""
    backend = (os.environ.get("RAGRET_APP_STORE") or "sqlite").strip().lower()
    if backend == "sqlite":
        raw = os.environ.get("RAGRET_APP_DB")
        db_path = (
            Path(raw).expanduser().resolve()
            if raw
            else default_app_sqlite_path(repo_root)  # runtime/data/ragret_app.sqlite
        )
        return SqliteAppStore(db_path)
    raise ValueError(f"Unsupported RAGRET_APP_STORE={backend!r} (supported: sqlite)")
