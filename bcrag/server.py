"""HTTP server with API + optional static frontend hosting."""
from __future__ import annotations

import cgi
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bcrag.registry import IndexRegistry, safe_index_name

REPO_ROOT = Path(__file__).resolve().parent.parent

_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{24}$")


def _job_patch(job_id: str, **kwargs: Any) -> None:
    with _JOB_LOCK:
        rec = _JOBS.setdefault(job_id, {"created": time.time()})
        rec.update(kwargs)


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def _run_index_job(
    job_id: str,
    root: Path,
    registry: IndexRegistry,
    upload_base: Path,
    index_name: str,
    description: str,
    upload_id: str,
) -> None:
    from bcrag.rag import index_workdir
    from bcrag.registry import resolve_db_path

    last_pct = [0]

    def bump(phase: str, pct: int, detail: str | None = None) -> None:
        last_pct[0] = max(last_pct[0], pct)
        _job_patch(
            job_id,
            status="running",
            phase=phase,
            percent=last_pct[0],
            detail=(detail or ""),
        )

    try:
        bump("extract", 4, "staging")
        if not _UPLOAD_ID_RE.match(upload_id):
            raise ValueError("Invalid upload_id")
        staging = (upload_base / "staging" / upload_id).resolve()
        upload_base_r = upload_base.resolve()
        try:
            staging.relative_to(upload_base_r)
        except ValueError as e:
            raise ValueError("Invalid staging path") from e
        if not staging.is_dir():
            raise FileNotFoundError("Upload not found or expired")
        meta_path = staging / "meta.json"
        blob_path = staging / "blob"
        if not meta_path.is_file() or not blob_path.is_file():
            raise FileNotFoundError("Incomplete upload")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        archive_name = str(meta.get("original_name") or "")
        if not archive_name or not _is_tar_filename(archive_name):
            raise ValueError("Expected a tar archive (.tar, .tar.gz, .tgz, …)")

        bump("extract", 8, archive_name)
        extract_dir = (staging / "extracted").resolve()
        try:
            extract_dir.relative_to(staging)
        except ValueError as e:
            raise ValueError("Invalid extract path") from e
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        try:
            with tarfile.open(blob_path, "r:*") as tf:
                _safe_extract_tar(tf, extract_dir)
        except (tarfile.TarError, OSError) as e:
            raise ValueError(f"Invalid or unreadable tar: {e}") from e

        bump("extract", 14, "extracted")

        def rag_progress(phase: str, pct: int, detail: str | None) -> None:
            bump(phase, max(last_pct[0], pct), detail)

        _, db_path = resolve_db_path(extract_dir, index_name)
        db_path = (root / "data" / db_path.name).resolve()
        index_workdir(extract_dir, db_path, progress=rag_progress)

        bump("register", 99, None)
        key = registry.add(index_name, db_path, description=description)
        _job_patch(
            job_id,
            status="done",
            phase="done",
            percent=100,
            detail="",
            error=None,
            result={"name": key, "description": description},
        )
    except Exception as e:
        _job_patch(
            job_id,
            status="error",
            phase="error",
            percent=last_pct[0],
            error=str(e),
            detail="",
        )
    finally:
        try:
            sid_dir = (upload_base / "staging" / upload_id).resolve()
            base_r = upload_base.resolve()
            if sid_dir.is_dir():
                try:
                    sid_dir.relative_to(base_r)
                    shutil.rmtree(sid_dir)
                except ValueError:
                    pass
        except OSError:
            pass


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


def _send_bytes(
    handler: BaseHTTPRequestHandler,
    code: int,
    body: bytes,
    content_type: str,
) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


_TAR_SUFFIX_RE = re.compile(r"\.(tar\.gz|tar\.bz2|tar\.xz|tgz|tbz2|txz|tar)$", re.IGNORECASE)


def _is_tar_filename(name: str) -> bool:
    return bool(_TAR_SUFFIX_RE.search(name))


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if sys.version_info >= (3, 12):
        tf.extractall(dest, filter="data")
        return
    abs_dest = os.path.abspath(dest)
    for member in tf.getmembers():
        abs_target = os.path.abspath(dest / member.name)
        if abs_target != abs_dest and not abs_target.startswith(abs_dest + os.sep):
            continue
        tf.extract(member, path=dest, set_attrs=False)


def make_handler_class(registry: IndexRegistry, root: Path):
    static_dir = (root / "bcrag" / "static").resolve()
    upload_base = (root / "upload").resolve()

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
                if self._serve_static_file("index.html"):
                    return
                _send_json(self, 200, {"service": "bcrag", "api": "/api/indexes | /api/search/{index}?query=..."})
                return

            if parts[0].lower() == "health" and len(parts) == 1:
                _send_json(self, 200, {"ok": True})
                return

            if parts[0].lower() == "indexes" and len(parts) == 1:
                entries = registry.list_entries()
                _send_json(self, 200, {"ok": True, "indexes": entries})
                return

            if parts[0].lower() == "api":
                self._handle_api_get(parts[1:], qs)
                return

            if self._serve_static_path(parsed.path):
                return

            # Backward compatibility: GET /{index}?query=...
            if len(parts) == 1:
                self._handle_search(parts[0], qs)
                return

            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def _handle_api_get(self, parts: list[str], qs: dict[str, list[str]]) -> None:
            if not parts:
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "bcrag",
                        "endpoints": {
                            "list": "GET /api/indexes",
                            "search": "GET /api/search/{index}?query=...",
                            "upload": "POST /api/upload (multipart: file=tar)",
                            "build": "POST /api/indexes/build (JSON: name, description, upload_id)",
                            "job": "GET /api/jobs/{job_id}",
                            "delete_index": "DELETE /api/indexes/{name}",
                        },
                    },
                )
                return
            if parts[0].lower() == "indexes" and len(parts) == 1:
                entries = registry.list_entries()
                _send_json(self, 200, {"ok": True, "indexes": entries})
                return
            if parts[0].lower() == "search" and len(parts) == 2:
                self._handle_search(parts[1], qs)
                return
            if parts[0].lower() == "jobs" and len(parts) == 2:
                snap = _job_snapshot(parts[1])
                if snap is None:
                    _send_json(self, 404, {"ok": False, "error": "Unknown job"})
                    return
                _send_json(self, 200, {"ok": True, **snap})
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

            from bcrag.rag import search_db

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
                    "query": query,
                    "result": result,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            if len(parts) == 2 and parts[0].lower() == "api" and parts[1].lower() == "upload":
                self._handle_stage_archive_upload()
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "indexes"
                and parts[2].lower() == "build"
            ):
                self._handle_start_build_job()
                return
            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            qs = parse_qs(parsed.query)
            if len(parts) == 3 and parts[0].lower() == "api" and parts[1].lower() == "indexes":
                self._handle_delete_index(parts[2], qs)
                return
            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def _handle_stage_archive_upload(self) -> None:
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                _send_json(self, 400, {"ok": False, "error": "Content-Type must be multipart/form-data"})
                return
            try:
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
                )
            except Exception as e:
                _send_json(self, 400, {"ok": False, "error": f"Invalid multipart payload: {e}"})
                return

            item = form["file"] if "file" in form else None
            if isinstance(item, list):
                item = item[0] if item else None
            if item is None:
                _send_json(self, 400, {"ok": False, "error": "Missing form field: file"})
                return

            archive_name = Path(getattr(item, "filename", "") or "").name
            if not archive_name:
                _send_json(self, 400, {"ok": False, "error": "Missing archive filename"})
                return
            if not _is_tar_filename(archive_name):
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "Expected a tar archive (.tar, .tar.gz, .tgz, …)"},
                )
                return

            upload_id = secrets.token_hex(12)
            upload_base.mkdir(parents=True, exist_ok=True)
            staging = (upload_base / "staging" / upload_id).resolve()
            try:
                staging.relative_to(upload_base.resolve())
            except ValueError:
                _send_json(self, 400, {"ok": False, "error": "Invalid staging path"})
                return
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "meta.json").write_text(
                json.dumps({"original_name": archive_name}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with (staging / "blob").open("wb") as out:
                shutil.copyfileobj(item.file, out)
            _send_json(self, 200, {"ok": True, "upload_id": upload_id})

        def _handle_start_build_job(self) -> None:
            ctype = self.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                _send_json(self, 415, {"ok": False, "error": "Content-Type must be application/json"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(n) if n > 0 else b"{}"
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                _send_json(self, 400, {"ok": False, "error": "Invalid JSON body"})
                return

            name_raw = data.get("name") or data.get("index")
            desc_raw = str(data.get("description") or "").strip()
            upload_id = data.get("upload_id")
            if not name_raw or not upload_id or not desc_raw:
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "JSON must include non-empty name, description and upload_id"},
                )
                return
            try:
                index_name = safe_index_name(str(name_raw))
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            upload_id = str(upload_id).strip()
            if not _UPLOAD_ID_RE.match(upload_id):
                _send_json(self, 400, {"ok": False, "error": "Invalid upload_id"})
                return
            staging = (upload_base / "staging" / upload_id).resolve()
            try:
                staging.relative_to(upload_base.resolve())
            except ValueError:
                _send_json(self, 400, {"ok": False, "error": "Invalid upload_id"})
                return
            if not staging.is_dir():
                _send_json(self, 404, {"ok": False, "error": "Upload not found; upload the archive first"})
                return

            job_id = secrets.token_hex(12)
            _job_patch(
                job_id,
                status="queued",
                phase="queued",
                percent=0,
                detail="",
                error=None,
                result=None,
            )
            threading.Thread(
                target=_run_index_job,
                kwargs={
                    "job_id": job_id,
                    "root": root,
                    "registry": registry,
                    "upload_base": upload_base,
                    "index_name": index_name,
                    "description": desc_raw,
                    "upload_id": upload_id,
                },
                daemon=True,
                name=f"bcrag-build-{job_id[:8]}",
            ).start()
            _send_json(self, 202, {"ok": True, "job_id": job_id})

        def _handle_delete_index(self, name: str, qs: dict[str, list[str]]) -> None:
            try:
                safe_name = safe_index_name(name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            db = registry.get_path(safe_name)
            removed = registry.remove(safe_name)
            if not removed:
                _send_json(self, 404, {"ok": False, "error": f"Unknown index: {safe_name}"})
                return

            delete_sqlite = (qs.get("delete_sqlite") or ["1"])[0] not in ("0", "false", "False")
            deleted_file = False
            if delete_sqlite and db is not None and db.is_file():
                try:
                    db.unlink()
                    deleted_file = True
                except OSError:
                    deleted_file = False

            _send_json(
                self,
                200,
                {"ok": True, "removed": safe_name, "sqlite_deleted": deleted_file},
            )

        def _serve_static_path(self, raw_path: str) -> bool:
            if not static_dir.is_dir():
                return False
            path = unquote(raw_path).lstrip("/")
            if not path:
                path = "index.html"
            if path.startswith("api/"):
                return False
            return self._serve_static_file(path)

        def _serve_static_file(self, rel: str) -> bool:
            if not static_dir.is_dir():
                return False
            candidate = (static_dir / rel).resolve()
            if static_dir not in candidate.parents and candidate != static_dir:
                return False
            if candidate.is_dir():
                candidate = candidate / "index.html"
            if not candidate.is_file():
                # SPA fallback.
                fallback = static_dir / "index.html"
                if fallback.is_file():
                    candidate = fallback
                else:
                    return False
            ctype, _ = mimetypes.guess_type(str(candidate))
            _send_bytes(self, 200, candidate.read_bytes(), ctype or "application/octet-stream")
            return True

    return BcragHTTPRequestHandler


def run_server(*, host: str, port: int, repo_root: Path | None = None) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
    root = (repo_root or REPO_ROOT).resolve()
    if "HF_HOME" not in os.environ:
        from bcrag.paths import default_hf_models_dir

        d = default_hf_models_dir()
        os.environ["HF_HOME"] = str(d)
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(d)
        d.mkdir(parents=True, exist_ok=True)
    reg_path = _registry_path(root)
    registry = IndexRegistry(reg_path)
    registry.load()

    handler_cls = make_handler_class(registry, root)
    server = ThreadingHTTPServer((host, int(port)), handler_cls)
    print(f"bcrag server http://{host}:{port}/  registry={reg_path}", flush=True)
    print("API: GET /api/indexes | GET /api/search/{index}?query=... | POST/DELETE /api/indexes", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()
        return 0
    return 0
