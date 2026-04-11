"""HTTP API process: auth, knowledge-base ACL, static UI. Depends on ``ragret`` for RAG + registry."""
from __future__ import annotations

from server.httpd import run_server

__all__ = ["run_server"]
