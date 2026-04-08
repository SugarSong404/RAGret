"""Application data store for the HTTP server (SQLite today; swap via factory)."""
from __future__ import annotations

from server.store.factory import create_app_store
from server.store.protocol import AppStore, KBPermission, KBRecord, UserRecord

__all__ = [
    "AppStore",
    "KBPermission",
    "KBRecord",
    "UserRecord",
    "create_app_store",
]
