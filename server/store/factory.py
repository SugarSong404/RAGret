"""Construct the application store implementation from environment."""
from __future__ import annotations

import os
from pathlib import Path

from server.store.protocol import AppStore
from server.store.sqlite_store import SqliteAppStore


def create_app_store(repo_root: Path) -> AppStore:
    """Create store backend. ``BCECLI_APP_STORE=sqlite`` (default); others later (e.g. mysql)."""
    backend = (os.environ.get("BCECLI_APP_STORE") or "sqlite").strip().lower()
    if backend == "sqlite":
        raw = os.environ.get("BCECLI_APP_DB")
        db_path = (
            Path(raw).expanduser().resolve()
            if raw
            else (repo_root / "data" / "bcecli_app.sqlite").resolve()
        )
        return SqliteAppStore(db_path)
    raise ValueError(f"Unsupported BCECLI_APP_STORE={backend!r} (supported: sqlite)")
