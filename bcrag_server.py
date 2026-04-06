"""bcrag HTTP server: index registry + GET /{index}?query= for isolated search."""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bcrag_registry import IndexRegistry, safe_index_name


def _registry_path(root: Path) -> Path:
    env = os.environ.get("BCRAG_REGISTRY")
    if env:
        return Path(env).expanduser().resolve()
    return (root / "bcrag_registry.json").resolve()


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = os.environ.get("BCRAG_API_TOKEN")
    if not token:
        return True
    auth = handler.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def _send_json(handler: BaseHTTPRequestHandler, code: int, obj: object) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_text(handler: BaseHTTPRequestHandler, code: int, text: str) -> None:
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler_class(registry: IndexRegistry):
    class BcragHTTPRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

        def _require_auth(self) -> bool:
            if _auth_ok(self):
                return True
            _send_json(self, 401, {"ok": False, "error": "Unauthorized (set Authorization: Bearer <token>)"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            qs = parse_qs(parsed.query)

            if not parts:
                _send_json(
                    self,
                    200,
                    {
                        "service": "bcrag",
                        "search": "GET /{index_name}?query=...",
                        "registry": "GET /indexes — list registered indexes",
                        "indexing": "CLI only: python bcrag.py index --dir ... --register-as <name>",
                    },
                )
                return

            if parts[0].lower() == "health" and len(parts) == 1:
                _send_json(self, 200, {"ok": True})
                return

            if parts[0].lower() == "indexes" and len(parts) == 1:
                entries = registry.list_entries()
                _send_json(self, 200, {"ok": True, "indexes": entries})
                return

            if len(parts) == 1:
                self._handle_search(parts[0], qs)
                return

            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def _handle_search(self, index_name: str, qs: dict[str, list[str]]) -> None:
            try:
                safe_index_name(index_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return

            q_list = qs.get("query") or qs.get("q")
            if not q_list or not (q_list[0] or "").strip():
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "Missing query parameter: ?query= or ?q="},
                )
                return
            query = q_list[0].strip()

            k = int((qs.get("k") or ["10"])[0])
            threshold = float((qs.get("threshold") or ["0.3"])[0])
            top_n = int((qs.get("top_n") or qs.get("top-n") or ["5"])[0])

            db = registry.get_path(index_name)
            if db is None:
                _send_json(
                    self,
                    404,
                    {"ok": False, "error": f"Unknown index: {index_name!r} (not in registry)"},
                )
                return
            if not db.is_file():
                _send_json(
                    self,
                    404,
                    {"ok": False, "error": f"SQLite missing for index {index_name!r}: {db}"},
                )
                return

            from bcrag_rag import search_db

            try:
                result = search_db(
                    db,
                    query,
                    k=k,
                    score_threshold=threshold,
                    rerank_top_n=top_n,
                )
            except Exception as e:
                _send_json(self, 500, {"ok": False, "error": str(e)})
                return

            want_text = (qs.get("format") or ["json"])[0].lower() == "text"
            if want_text:
                _send_text(self, 200, result)
                return
            _send_json(
                self,
                200,
                {
                    "ok": True,
                    "index": index_name,
                    "db_path": str(db),
                    "query": query,
                    "result": result,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            _send_json(
                self,
                405,
                {
                    "ok": False,
                    "error": (
                        "POST is not allowed. Build and register indexes on the host only: "
                        "python bcrag.py index --dir <corpus> --register-as <name>"
                    ),
                },
            )

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            _send_json(
                self,
                405,
                {
                    "ok": False,
                    "error": "DELETE is not allowed. The HTTP API is read-only except for registry listing.",
                },
            )

    return BcragHTTPRequestHandler


def run_server(*, host: str, port: int, repo_root: Path | None = None) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    root = (repo_root or Path(__file__).resolve().parent).resolve()
    reg_path = _registry_path(root)
    registry = IndexRegistry(reg_path)
    registry.load()

    handler_cls = make_handler_class(registry)
    server = ThreadingHTTPServer((host, int(port)), handler_cls)
    print(f"bcrag server http://{host}:{port}/  registry={reg_path}", flush=True)
    print("GET /{index}?query=...   GET /indexes   (POST/DELETE disabled; index via CLI)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()
        return 0
    return 0
