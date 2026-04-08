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

from bcecli.registry import IndexRegistry, safe_index_name

from server.passwords import hash_password
from server.store import create_app_store
from server.store.protocol import AppStore, KBRecord

REPO_ROOT = Path(__file__).resolve().parent.parent

_SESSION_TTL_SECONDS = int(os.environ.get("BCECLI_SESSION_TTL", str(30 * 24 * 3600)))
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{24}$")
_AVATAR_MAX_BYTES = int(os.environ.get("BCECLI_AVATAR_MAX_BYTES", str(2 * 1024 * 1024)))
_ALLOWED_AVATAR_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def _sniff_image_mime(data: bytes) -> str | None:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    app_store: AppStore,
    upload_base: Path,
    index_name: str,
    description: str,
    upload_id: str,
    owner_user_id: int,
    *,
    is_public: bool = False,
    icon: str = "book",
) -> None:
    from bcecli.rag import index_workdir
    from bcecli.registry import resolve_db_path

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

        is_public_job = bool(meta.get("is_public", is_public))

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
        try:
            app_store.create_knowledge_base(
                name=key,
                description=description,
                readme_md=str(meta.get("readme_md") or ""),
                db_path=str(db_path.resolve()),
                owner_id=int(owner_user_id),
                is_public=is_public_job,
                icon=str(icon or "book"),
            )
        except Exception as e:
            registry.remove(key)
            try:
                if db_path.is_file():
                    db_path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"Register in app database failed: {e}") from e
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
    env = os.environ.get("BCECLI_REGISTRY")
    if env:
        return Path(env).expanduser().resolve()
    return (root / "bcecli_registry.json").resolve()


def _bearer_raw(handler: BaseHTTPRequestHandler) -> str:
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _api_key_raw(handler: BaseHTTPRequestHandler) -> str:
    xk = (handler.headers.get("X-API-Key") or "").strip()
    if xk:
        return xk
    tok = _bearer_raw(handler)
    if tok.startswith("sk-"):
        return tok
    return ""


def _api_super_token() -> str | None:
    t = os.environ.get("BCECLI_API_TOKEN")
    return t.strip() if t else None


def _is_superuser_bearer(handler: BaseHTTPRequestHandler) -> bool:
    tok = _api_super_token()
    if not tok:
        return False
    return _bearer_raw(handler) == tok


def _session_user_id(handler: BaseHTTPRequestHandler, app_store: AppStore) -> int | None:
    return app_store.get_session_user_id(_bearer_raw(handler))


def _resolve_actor(handler: BaseHTTPRequestHandler, app_store: AppStore) -> tuple[str, int | None]:
    """Returns (kind, user_id) where kind is 'superuser' | 'user' | 'api_key' | 'anon'."""
    if _is_superuser_bearer(handler):
        return "superuser", None
    uid = _session_user_id(handler, app_store)
    if uid is not None:
        return "user", int(uid)
    uid_by_key = app_store.get_api_key_owner_user_id(_api_key_raw(handler))
    if uid_by_key is not None:
        return "api_key", int(uid_by_key)
    return "anon", None


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


def _serialize_kb(rec: KBRecord) -> dict[str, Any]:
    assert isinstance(rec, KBRecord)
    p = rec.permission
    lc = int(rec.list_color_idx)
    lc = max(0, min(4, lc))
    return {
        "name": rec.name,
        "description": rec.description,
        "sqlite_exists": Path(rec.db_path).is_file(),
        "is_public": bool(rec.is_public),
        "list_color_idx": lc,
        "icon": str(rec.icon or "book"),
        "owner": {
            "id": rec.owner_id,
            "username": rec.owner_username,
            "has_avatar": rec.owner_has_avatar,
        },
        "permission": {
            "can_read": p.can_read,
            "can_write": p.can_write,
            "can_delete": p.can_delete,
            "is_owner": p.is_owner,
        },
    }


def make_handler_class(registry: IndexRegistry, root: Path, app_store: AppStore):
    static_dir = (root / "bcecli" / "static").resolve()
    upload_base = (root / "upload").resolve()

    class BcecliHTTPRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

        def _actor(self) -> tuple[str, int | None]:
            return _resolve_actor(self, app_store)

        def _require_actor(self) -> tuple[str, int | None] | None:
            k, uid = self._actor()
            if k == "anon":
                _send_json(
                    self,
                    401,
                    {"ok": False, "error": "Login required (session token) or BCECLI_API_TOKEN"},
                )
                return None
            return k, uid

        def _require_user_id(self) -> int | None:
            """Actions that must be attributed to a real user (e.g. creating a KB)."""
            k, uid = self._actor()
            if k == "user" and uid is not None:
                return int(uid)
            if k == "api_key":
                _send_json(
                    self,
                    403,
                    {
                        "ok": False,
                        "error": "This action requires a signed-in user session, not API key.",
                    },
                )
                return None
            if k == "superuser":
                _send_json(
                    self,
                    403,
                    {
                        "ok": False,
                        "error": "Create and upload require a signed-in user (not only API token).",
                    },
                )
                return None
            _send_json(self, 401, {"ok": False, "error": "Login required"})
            return None

        def _read_json_body(self) -> dict[str, Any] | None:
            ctype = self.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                _send_json(self, 415, {"ok": False, "error": "Content-Type must be application/json"})
                return None
            try:
                n = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(n) if n > 0 else b"{}"
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                _send_json(self, 400, {"ok": False, "error": "Invalid JSON body"})
                return None
            if not isinstance(data, dict):
                _send_json(self, 400, {"ok": False, "error": "JSON body must be an object"})
                return None
            return data

        def _db_for_search(self, index_name: str, k: str, uid: int | None) -> Path | None:
            store_path = app_store.resolve_kb_db_path(index_name)
            if k == "superuser":
                if store_path:
                    return Path(store_path)
                reg = registry.get_path(index_name)
                return reg
            if uid is None:
                return None
            if k == "api_key":
                allowed = {
                    str(r.name)
                    for r in app_store.list_owned_and_subscribed_knowledge_bases_for_user(int(uid))
                }
                if index_name not in allowed:
                    return None
            perm = app_store.permission_for(int(uid), index_name)
            if perm is None or not perm.can_read:
                return None
            if store_path:
                return Path(store_path)
            return None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            qs = parse_qs(parsed.query)

            if not parts:
                if self._serve_static_file("index.html"):
                    return
                _send_json(self, 200, {"service": "bcecli", "api": "/api/…", "auth": "/api/auth/login"})
                return

            if parts[0].lower() == "health" and len(parts) == 1:
                _send_json(self, 200, {"ok": True})
                return

            if parts[0].lower() == "api":
                if not self._api_open_path(parts[1:]) and not self._require_actor():
                    return
                self._handle_api_get(parts[1:], qs)
                return

            if parts[0].lower() == "indexes" and len(parts) == 1:
                if not self._require_actor():
                    return
                self._handle_api_get(["indexes"], qs)
                return

            if self._serve_static_path(parsed.path):
                return

            if len(parts) == 1:
                if not self._require_actor():
                    return
                self._handle_search(parts[0], qs)
                return

            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def _api_open_path(self, parts: list[str]) -> bool:
            if len(parts) >= 2 and parts[0].lower() == "auth":
                return parts[1].lower() in ("register", "login")
            return False

        def _handle_api_get(self, parts: list[str], qs: dict[str, list[str]]) -> None:
            act = self._actor()
            k, uid = act

            if len(parts) >= 2 and parts[0].lower() == "auth" and parts[1].lower() == "me":
                if k == "anon":
                    _send_json(self, 401, {"ok": False, "error": "Not logged in"})
                    return
                if k == "superuser":
                    _send_json(self, 200, {"ok": True, "user": None, "superuser": True})
                    return
                u = app_store.get_user_by_id(int(uid)) if uid is not None else None
                if u is None:
                    _send_json(self, 401, {"ok": False, "error": "Invalid session"})
                    return
                has_avatar = app_store.user_has_avatar(int(uid))
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "user": {"id": u.id, "username": u.username, "has_avatar": has_avatar},
                        "superuser": False,
                    },
                )
                return

            if not parts:
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "bcecli",
                        "endpoints": {
                            "auth": "POST /api/auth/register | /api/auth/login | /api/auth/logout",
                            "list": "GET /api/indexes",
                            "kb": "GET /api/kb/{name}",
                            "members": "GET/POST/DELETE /api/kb/{name}/members",
                            "subscribe": "POST|DELETE /api/kb/{name}/subscribe",
                            "subscriptions": "GET /api/user/subscriptions",
                            "subscribe_indexes": "GET /api/subscribe-indexes (API key only)",
                            "api_keys": "GET/POST/DELETE /api/user/api-keys",
                            "search": "GET /api/search/{index}?query=...",
                            "upload": "POST /api/upload",
                            "build": "POST /api/indexes/build",
                            "job": "GET /api/jobs/{job_id}",
                            "delete_index": "DELETE /api/indexes/{name}",
                        },
                    },
                )
                return

            if parts[0].lower() == "indexes" and len(parts) == 1:
                if k == "superuser":
                    rows = app_store.list_all_knowledge_bases()
                elif k == "api_key":
                    _send_json(self, 403, {"ok": False, "error": "Use /api/subscribe-indexes with API key"})
                    return
                else:
                    rows = app_store.list_knowledge_bases_for_user(int(uid)) if uid is not None else []
                _send_json(self, 200, {"ok": True, "indexes": [_serialize_kb(r) for r in rows]})
                return

            if parts[0].lower() == "subscribe-indexes" and len(parts) == 1:
                if k != "api_key" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Valid API key required"})
                    return
                rows = app_store.list_owned_and_subscribed_knowledge_bases_for_user(int(uid))
                _send_json(self, 200, {"ok": True, "indexes": [_serialize_kb(r) for r in rows]})
                return

            if parts[0].lower() == "user" and len(parts) == 2 and parts[1].lower() == "subscriptions":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                rows = app_store.list_subscribed_knowledge_bases_for_user(int(uid))
                _send_json(self, 200, {"ok": True, "indexes": [_serialize_kb(r) for r in rows]})
                return

            if parts[0].lower() == "user" and len(parts) == 2 and parts[1].lower() == "api-keys":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                rows = app_store.list_api_keys_for_user(int(uid))
                _send_json(self, 200, {"ok": True, "keys": rows})
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

            if parts[0].lower() == "kb" and len(parts) == 2:
                name = parts[1]
                try:
                    safe_index_name(name)
                except ValueError as e:
                    _send_json(self, 400, {"ok": False, "error": str(e)})
                    return
                perm = (
                    None
                    if k == "superuser"
                    else (app_store.permission_for(int(uid), name) if uid is not None else None)
                )
                if k == "api_key" and uid is not None:
                    allowed = {
                        str(r.name)
                        for r in app_store.list_owned_and_subscribed_knowledge_bases_for_user(int(uid))
                    }
                    if name not in allowed:
                        _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                        return
                if k != "superuser" and (perm is None or not perm.can_read):
                    _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                    return
                rec = app_store.get_knowledge_base(name)
                if rec is None and k == "superuser":
                    dbp = registry.get_path(name)
                    if dbp is None:
                        _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                        return
                    _send_json(
                        self,
                        200,
                        {
                            "ok": True,
                            "name": name,
                            "description": registry.get_description(name) or "",
                            "sqlite_exists": dbp.is_file(),
                            "legacy_registry_only": True,
                        },
                    )
                    return
                if rec is None:
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
                body = _serialize_kb(rec)
                body["readme_md"] = str(rec.readme_md or "")
                body["legacy_registry_only"] = False
                if k != "superuser":
                    body["permission"] = {
                        "can_read": perm.can_read,
                        "can_write": perm.can_write,
                        "can_delete": perm.can_delete,
                        "is_owner": perm.is_owner,
                    }
                    if uid is not None:
                        body["subscribed"] = app_store.kb_subscription_get(int(uid), name)
                _send_json(self, 200, {"ok": True, **body})
                return

            if parts[0].lower() == "kb" and len(parts) == 3 and parts[2].lower() == "icon":
                name = parts[1]
                try:
                    safe_index_name(name)
                except ValueError as e:
                    _send_json(self, 400, {"ok": False, "error": str(e)})
                    return
                perm = (
                    None
                    if k == "superuser"
                    else (app_store.permission_for(int(uid), name) if uid is not None else None)
                )
                if k != "superuser" and (perm is None or not perm.can_read):
                    _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                    return
                icon = app_store.load_kb_icon(name)
                if icon is None:
                    _send_json(self, 404, {"ok": False, "error": "No icon"})
                    return
                mime, raw = icon
                _send_bytes(self, 200, raw, mime)
                return

            if parts[0].lower() == "kb" and len(parts) == 3 and parts[2].lower() == "members":
                name = parts[1]
                try:
                    safe_index_name(name)
                except ValueError as e:
                    _send_json(self, 400, {"ok": False, "error": str(e)})
                    return
                roster = app_store.list_members_roster(name)
                if roster is None:
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
                if k != "superuser":
                    if uid is None:
                        _send_json(self, 403, {"ok": False, "error": "Login required"})
                        return
                    perm_roster = app_store.permission_for(int(uid), name)
                    if perm_roster is None or not perm_roster.can_read:
                        _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                        return
                _send_json(self, 200, {"ok": True, "members": roster})
                return

            if len(parts) == 2 and parts[0].lower() == "user" and parts[1].lower() == "avatar":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                av = app_store.load_avatar(int(uid))
                if av is None:
                    _send_json(self, 404, {"ok": False, "error": "No avatar"})
                    return
                mime, raw = av
                _send_bytes(self, 200, raw, mime)
                return

            if (
                len(parts) == 3
                and parts[0].lower() == "users"
                and parts[2].lower() == "avatar"
            ):
                if k == "anon":
                    _send_json(self, 401, {"ok": False, "error": "Login required"})
                    return
                try:
                    target_uid = int(parts[1])
                except ValueError:
                    _send_json(self, 400, {"ok": False, "error": "Invalid user id"})
                    return
                av = app_store.load_avatar(target_uid)
                if av is None:
                    _send_json(self, 404, {"ok": False, "error": "No avatar"})
                    return
                mime, raw = av
                _send_bytes(self, 200, raw, mime)
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

            ak, uid = self._actor()
            db = self._db_for_search(index_name, ak, uid)
            if db is None:
                _send_json(
                    self,
                    404,
                    {"ok": False, "error": f"Unknown or inaccessible index: {index_name!r}"},
                )
                return
            if not db.is_file():
                _send_json(
                    self,
                    404,
                    {"ok": False, "error": f"SQLite missing for index {index_name!r}: {db}"},
                )
                return

            from bcecli.rag import search_db

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
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]

            if len(parts) >= 2 and parts[0].lower() == "api" and parts[1].lower() == "auth":
                sub = parts[2].lower() if len(parts) >= 3 else ""
                if sub == "register":
                    self._handle_auth_register()
                    return
                if sub == "login":
                    self._handle_auth_login()
                    return
                if sub == "logout":
                    if not self._require_actor():
                        return
                    self._handle_auth_logout()
                    return

            if not self._require_actor():
                return

            if len(parts) == 2 and parts[0].lower() == "api" and parts[1].lower() == "upload":
                owner = self._require_user_id()
                if owner is None:
                    return
                self._handle_stage_archive_upload()
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "indexes"
                and parts[2].lower() == "build"
            ):
                owner = self._require_user_id()
                if owner is None:
                    return
                self._handle_start_build_job(owner_user_id=owner)
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "members"
            ):
                self._handle_kb_members_post(parts[2])
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "subscribe"
            ):
                self._handle_kb_subscribe(parts[2], True)
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "avatar"
            ):
                self._handle_user_avatar_upload()
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "icon"
            ):
                self._handle_kb_icon_upload(parts[2])
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "password"
            ):
                self._handle_user_password_change()
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "api-keys"
            ):
                self._handle_api_key_create()
                return

            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            if not self._require_actor():
                return
            if len(parts) == 3 and parts[0].lower() == "api" and parts[1].lower() == "kb":
                self._handle_kb_patch(parts[2])
                return
            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_actor():
                return
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            qs = parse_qs(parsed.query)
            if len(parts) == 3 and parts[0].lower() == "api" and parts[1].lower() == "indexes":
                self._handle_delete_index(parts[2], qs)
                return
            if (
                len(parts) == 5
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "members"
            ):
                self._handle_kb_members_delete(parts[2], parts[4])
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "subscribe"
            ):
                self._handle_kb_subscribe(parts[2], False)
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "avatar"
            ):
                self._handle_user_avatar_delete()
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "icon"
            ):
                self._handle_kb_icon_delete(parts[2])
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "api-keys"
            ):
                self._handle_api_key_delete(parts[3])
                return
            _send_json(self, 404, {"ok": False, "error": "Not found"})

        def _handle_user_avatar_delete(self) -> None:
            uid = self._require_user_id()
            if uid is None:
                return
            app_store.clear_avatar(int(uid))
            _send_json(self, 200, {"ok": True})

        def _handle_user_avatar_upload(self) -> None:
            uid = self._require_user_id()
            if uid is None:
                return
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                _send_json(self, 400, {"ok": False, "error": "Content-Type must be multipart/form-data"})
                return
            try:
                clen = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                clen = 0
            if clen > _AVATAR_MAX_BYTES + 65536:
                _send_json(self, 400, {"ok": False, "error": "File too large"})
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
            raw = item.file.read()
            if len(raw) > _AVATAR_MAX_BYTES:
                _send_json(self, 400, {"ok": False, "error": f"Avatar must be ≤ {_AVATAR_MAX_BYTES} bytes"})
                return
            mime = (getattr(item, "type", None) or "").strip().lower() or ""
            if mime not in _ALLOWED_AVATAR_TYPES:
                mime = _sniff_image_mime(raw) or ""
            if mime not in _ALLOWED_AVATAR_TYPES:
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "Use PNG, JPEG, GIF, or WebP"},
                )
                return
            try:
                app_store.save_avatar(int(uid), mime, raw)
            except OSError as e:
                _send_json(self, 500, {"ok": False, "error": str(e)})
                return
            _send_json(self, 200, {"ok": True})

        def _handle_kb_icon_upload(self, kb_name: str) -> None:
            try:
                safe_name = safe_index_name(kb_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k == "superuser":
                pass
            elif uid is None:
                _send_json(self, 401, {"ok": False, "error": "Login required"})
                return
            else:
                perm = app_store.permission_for(int(uid), safe_name)
                if perm is None or not perm.can_write:
                    _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                    return
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
            raw = item.file.read()
            if len(raw) > _AVATAR_MAX_BYTES:
                _send_json(self, 400, {"ok": False, "error": f"Icon must be ≤ {_AVATAR_MAX_BYTES} bytes"})
                return
            mime = (getattr(item, "type", None) or "").strip().lower() or ""
            if mime not in _ALLOWED_AVATAR_TYPES:
                mime = _sniff_image_mime(raw) or ""
            if mime not in _ALLOWED_AVATAR_TYPES:
                _send_json(self, 400, {"ok": False, "error": "Use PNG, JPEG, GIF, or WebP"})
                return
            try:
                if not app_store.save_kb_icon(safe_name, mime, raw):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
            except OSError as e:
                _send_json(self, 500, {"ok": False, "error": str(e)})
                return
            _send_json(self, 200, {"ok": True})

        def _handle_kb_icon_delete(self, kb_name: str) -> None:
            try:
                safe_name = safe_index_name(kb_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k == "superuser":
                pass
            elif uid is None:
                _send_json(self, 401, {"ok": False, "error": "Login required"})
                return
            else:
                perm = app_store.permission_for(int(uid), safe_name)
                if perm is None or not perm.can_write:
                    _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                    return
            if not app_store.clear_kb_icon(safe_name):
                _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                return
            _send_json(self, 200, {"ok": True})

        def _handle_user_password_change(self) -> None:
            uid = self._require_user_id()
            if uid is None:
                return
            data = self._read_json_body()
            if data is None:
                return
            current = str(data.get("current_password") or "")
            new_pw = str(data.get("new_password") or "")
            if len(new_pw) < 8:
                _send_json(self, 400, {"ok": False, "error": "New password must be at least 8 characters"})
                return
            if not app_store.change_password(int(uid), current, hash_password(new_pw)):
                _send_json(self, 401, {"ok": False, "error": "Current password is incorrect"})
                return
            _send_json(self, 200, {"ok": True})

        def _handle_api_key_create(self) -> None:
            uid = self._require_user_id()
            if uid is None:
                return
            data = self._read_json_body()
            if data is None:
                return
            name = str(data.get("name") or "").strip()
            key_value = "sk-" + secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:36]
            rec = app_store.create_api_key_for_user(int(uid), name=name, key_value=key_value)
            if rec is None:
                _send_json(self, 400, {"ok": False, "error": "You can create at most 5 API keys"})
                return
            _send_json(self, 200, {"ok": True, "key": rec})

        def _handle_api_key_delete(self, key_id_raw: str) -> None:
            uid = self._require_user_id()
            if uid is None:
                return
            try:
                kid = int(key_id_raw)
            except ValueError:
                _send_json(self, 400, {"ok": False, "error": "Invalid key id"})
                return
            if not app_store.delete_api_key_for_user(int(uid), kid):
                _send_json(self, 404, {"ok": False, "error": "API key not found"})
                return
            _send_json(self, 200, {"ok": True})

        def _handle_auth_register(self) -> None:
            data = self._read_json_body()
            if data is None:
                return
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "")
            if not _USERNAME_RE.match(username):
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "Username must be 3–64 chars: letters, digits, . _ -",
                    },
                )
                return
            if len(password) < 8:
                _send_json(self, 400, {"ok": False, "error": "Password must be at least 8 characters"})
                return
            try:
                u = app_store.create_user(username, hash_password(password))
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            token = app_store.create_session(u.id, ttl_seconds=_SESSION_TTL_SECONDS)
            _send_json(
                self,
                200,
                {
                    "ok": True,
                    "token": token,
                    "user": {"id": u.id, "username": u.username, "has_avatar": False},
                },
            )

        def _handle_auth_login(self) -> None:
            data = self._read_json_body()
            if data is None:
                return
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "")
            u = app_store.verify_user_password(username, password)
            if u is None:
                _send_json(self, 401, {"ok": False, "error": "Invalid username or password"})
                return
            token = app_store.create_session(u.id, ttl_seconds=_SESSION_TTL_SECONDS)
            has_avatar = app_store.user_has_avatar(u.id)
            _send_json(
                self,
                200,
                {
                    "ok": True,
                    "token": token,
                    "user": {"id": u.id, "username": u.username, "has_avatar": has_avatar},
                },
            )

        def _handle_auth_logout(self) -> None:
            tok = _bearer_raw(self)
            if tok:
                app_store.delete_session(tok)
            _send_json(self, 200, {"ok": True})

        def _handle_kb_subscribe(self, name: str, subscribed: bool) -> None:
            try:
                key = safe_index_name(name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k != "user" or uid is None:
                _send_json(self, 403, {"ok": False, "error": "Login required"})
                return
            perm = app_store.permission_for(int(uid), key)
            if perm is None or not perm.can_read:
                _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                return
            if not app_store.kb_subscription_set(int(uid), key, subscribed):
                _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                return
            _send_json(self, 200, {"ok": True, "subscribed": subscribed})

        def _handle_kb_patch(self, name: str) -> None:
            try:
                key = safe_index_name(name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k == "superuser":
                data = self._read_json_body()
                if data is None:
                    return
                new_name = None
                if "name" in data:
                    try:
                        new_name = safe_index_name(str(data.get("name") or ""))
                    except ValueError as e:
                        _send_json(self, 400, {"ok": False, "error": str(e)})
                        return
                did = False
                active_key = key
                if new_name is not None and new_name != key:
                    try:
                        if not app_store.rename_knowledge_base(key, new_name):
                            _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                            return
                    except ValueError as e:
                        _send_json(self, 409, {"ok": False, "error": str(e)})
                        return
                    old_path = registry.get_path(key)
                    old_desc = registry.get_description(key) or ""
                    if old_path is not None:
                        registry.remove(key)
                        registry.add(new_name, old_path, description=old_desc)
                    active_key = new_name
                    did = True
                if "description" in data:
                    desc = str(data.get("description") or "").strip()
                    if app_store.update_knowledge_base_description(active_key, desc):
                        cur = registry.get_path(active_key)
                        if cur is not None:
                            registry.add(active_key, cur, description=desc)
                        did = True
                    else:
                        cur = registry.get_path(active_key)
                        if cur is not None:
                            registry.add(active_key, cur, description=desc)
                            did = True
                if "is_public" in data:
                    if app_store.update_knowledge_base_public(active_key, bool(data.get("is_public"))):
                        did = True
                if "readme_md" in data:
                    txt = str(data.get("readme_md") or "").strip()
                    if app_store.update_knowledge_base_readme(active_key, txt):
                        did = True
                if not did and "description" not in data and "is_public" not in data and "name" not in data and "readme_md" not in data:
                    _send_json(self, 400, {"ok": False, "error": "No updates provided"})
                    return
                if not did:
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
                _send_json(self, 200, {"ok": True, "name": active_key})
                return
            if uid is None:
                _send_json(self, 401, {"ok": False, "error": "Login required"})
                return
            perm = app_store.permission_for(int(uid), key)
            if perm is None or not perm.can_read:
                _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                return
            data = self._read_json_body()
            if data is None:
                return
            if "description" not in data and "is_public" not in data and "name" not in data and "readme_md" not in data:
                _send_json(self, 400, {"ok": False, "error": "No updates provided"})
                return
            if "is_public" in data and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can change visibility"})
                return
            if "description" in data and not perm.can_write:
                _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                return
            if "name" in data and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can rename knowledge base"})
                return
            active_key = key
            if "name" in data:
                try:
                    new_name = safe_index_name(str(data.get("name") or ""))
                except ValueError as e:
                    _send_json(self, 400, {"ok": False, "error": str(e)})
                    return
                if new_name != key:
                    try:
                        if not app_store.rename_knowledge_base(key, new_name):
                            _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                            return
                    except ValueError as e:
                        _send_json(self, 409, {"ok": False, "error": str(e)})
                        return
                    old_path = registry.get_path(key)
                    old_desc = registry.get_description(key) or ""
                    if old_path is not None:
                        registry.remove(key)
                        registry.add(new_name, old_path, description=old_desc)
                    active_key = new_name
            if "description" in data:
                desc = str(data.get("description") or "").strip()
                if not app_store.update_knowledge_base_description(active_key, desc):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
                cur = registry.get_path(active_key)
                if cur is not None:
                    registry.add(active_key, cur, description=desc)
            if "is_public" in data:
                if not app_store.update_knowledge_base_public(active_key, bool(data.get("is_public"))):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
            if "readme_md" in data:
                txt = str(data.get("readme_md") or "").strip()
                if not app_store.update_knowledge_base_readme(active_key, txt):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
            _send_json(self, 200, {"ok": True, "name": active_key})

        def _handle_kb_members_post(self, name: str) -> None:
            try:
                safe_index_name(name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k != "user" or uid is None:
                _send_json(self, 403, {"ok": False, "error": "Owner login required"})
                return
            data = self._read_json_body()
            if data is None:
                return
            member_username = str(data.get("username") or "").strip()
            if not member_username:
                _send_json(self, 400, {"ok": False, "error": "Username is required"})
                return
            if app_store.get_user_by_username(member_username) is None:
                _send_json(self, 404, {"ok": False, "error": "User not found"})
                return
            can_write = bool(data.get("can_write", False))
            if not app_store.upsert_member(
                name,
                actor_user_id=int(uid),
                member_username=member_username,
                can_read=True,
                can_write=can_write,
                can_delete=False,
            ):
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "User not found, is owner, or you are not owner"},
                )
                return
            _send_json(self, 200, {"ok": True})

        def _handle_kb_members_delete(self, kb_name: str, member_username: str) -> None:
            try:
                safe_index_name(kb_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            k, uid = self._actor()
            if k != "user" or uid is None:
                _send_json(self, 403, {"ok": False, "error": "Owner login required"})
                return
            if not app_store.remove_member(
                kb_name,
                actor_user_id=int(uid),
                member_username=unquote(member_username),
            ):
                _send_json(self, 404, {"ok": False, "error": "Member not found or not owner"})
                return
            _send_json(self, 200, {"ok": True})

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

        def _handle_start_build_job(self, *, owner_user_id: int) -> None:
            data = self._read_json_body()
            if data is None:
                return

            name_raw = data.get("name") or data.get("index")
            desc_raw = str(data.get("description") or "").strip()
            readme_raw = str(data.get("readme_md") or "").strip()
            upload_id = data.get("upload_id")
            if not name_raw or not upload_id or not desc_raw or not readme_raw:
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "JSON must include non-empty name, description, readme_md and upload_id"},
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

            is_public_flag = bool(data.get("is_public", False))
            icon_key = str(data.get("icon") or "book").strip() or "book"
            meta_path = staging / "meta.json"
            try:
                meta0 = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta0, dict):
                    meta0 = {}
            except (OSError, json.JSONDecodeError):
                meta0 = {}
            meta0["is_public"] = is_public_flag
            meta0["icon"] = icon_key
            meta0["readme_md"] = readme_raw
            meta_path.write_text(json.dumps(meta0, ensure_ascii=False) + "\n", encoding="utf-8")

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
                    "app_store": app_store,
                    "upload_base": upload_base,
                    "index_name": index_name,
                    "description": desc_raw,
                    "upload_id": upload_id,
                    "owner_user_id": int(owner_user_id),
                    "is_public": is_public_flag,
                    "icon": icon_key,
                },
                daemon=True,
                name=f"bcecli-build-{job_id[:8]}",
            ).start()
            _send_json(self, 202, {"ok": True, "job_id": job_id})

        def _handle_delete_index(self, name: str, qs: dict[str, list[str]]) -> None:
            try:
                safe_name = safe_index_name(name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            ak, uid = self._actor()
            if ak == "superuser":
                perm_ok = True
            elif uid is not None:
                p = app_store.permission_for(int(uid), safe_name)
                perm_ok = p is not None and p.can_delete
            else:
                perm_ok = False
            if not perm_ok:
                _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                return

            sp = app_store.resolve_kb_db_path(safe_name)
            db: Path | None = Path(sp) if sp else registry.get_path(safe_name)

            in_store = app_store.delete_knowledge_base(safe_name)
            removed_reg = registry.remove(safe_name)
            if not in_store and not removed_reg:
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

    return BcecliHTTPRequestHandler


def run_server(*, host: str, port: int, repo_root: Path | None = None) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
    root = (repo_root or REPO_ROOT).resolve()
    if "HF_HOME" not in os.environ:
        from bcecli.paths import default_hf_models_dir

        d = default_hf_models_dir()
        os.environ["HF_HOME"] = str(d)
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(d)
        d.mkdir(parents=True, exist_ok=True)
    reg_path = _registry_path(root)
    registry = IndexRegistry(reg_path)
    registry.load()
    app_store = create_app_store(root)

    handler_cls = make_handler_class(registry, root, app_store)
    server = ThreadingHTTPServer((host, int(port)), handler_cls)
    db_path = getattr(app_store, "_path", None)
    extra = f" app_db={db_path}" if db_path is not None else ""
    print(f"bcecli server http://{host}:{port}/  registry={reg_path}{extra}", flush=True)
    print(
        "API: auth /api/auth/* | GET /api/indexes | GET /api/kb/{name} | "
        "GET /api/search/{index}?query=... | POST/DELETE /api/indexes",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()
        return 0
    return 0
