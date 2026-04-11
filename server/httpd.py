"""HTTP server with API + optional static frontend hosting."""
from __future__ import annotations

import cgi
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ragret.registry import IndexRegistry, safe_index_name

from server.archive_util import is_tar_archive_filename, safe_extract_tar_archive
from server.data_cleanup import cleanup_orphan_kb_sqlite_files
from server.build_queue import (
    cleanup_upload_staging,
    is_http_git_clone_url,
    start_global_build_worker,
    wake_build_worker,
)
from server.passwords import hash_password
from server.runtime_paths import default_registry_path, kb_sqlite_path, runtime_upload_dir
from server.store import create_app_store
from server.store.protocol import AppStore, KBRecord

REPO_ROOT = Path(__file__).resolve().parent.parent

_SESSION_TTL_SECONDS = int(os.environ.get("RAGRET_SESSION_TTL", str(30 * 24 * 3600)))
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{24}$")
_MAX_USER_UPLOAD_JOBS = 3


def _provider_webhook_kb_from_path(path: str) -> tuple[str, str] | None:
    """Match /(api/)?webhooks?/(gitlab|github)/<kb> (optional /api prefix). Returns (provider, kb_name)."""
    m = re.search(
        r"(?i)/(?:api/)?(?:webhooks?)/(?P<prov>gitlab|github)/(?P<kb>[^/?#]+)", path or ""
    )
    if not m:
        return None
    return (m.group("prov").lower(), unquote(m.group("kb")))


def _github_signature256_valid(secret: str, payload: bytes, sig_header: str) -> bool:
    if not secret or not sig_header:
        return False
    sh = sig_header.strip()
    if not sh.startswith("sha256="):
        return False
    want = sh[7:].strip().lower()
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, want)


def _unwrap_github_logged_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Support delivery logs that wrap the JSON body in summary=payload%3D... (URL-encoded)."""
    summ = str(data.get("summary") or "")
    if not summ.startswith("payload="):
        return data
    try:
        inner = json.loads(unquote(summ[len("payload=") :]))
    except (json.JSONDecodeError, ValueError):
        return data
    return inner if isinstance(inner, dict) else data
_AVATAR_MAX_BYTES = int(os.environ.get("RAGRET_AVATAR_MAX_BYTES", str(2 * 1024 * 1024)))
_ALLOWED_AVATAR_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
def _best_public_host() -> str:
    env = str(os.environ.get("RAGRET_PUBLIC_HOST") or "").strip()
    if env:
        return env
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = str(s.getsockname()[0] or "").strip()
            if ip:
                return ip
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for it in infos:
            ip = str(it[4][0] or "").strip()
            if ip and ip != "127.0.0.1":
                return ip
    except OSError:
        pass
    return "127.0.0.1"



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


def _job_public_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "percent": int(job.get("percent") or 0),
        "detail": str(job.get("detail") or ""),
        "error": job.get("error"),
        "result": job.get("result"),
        "op": job.get("op"),
        "kb_name": job.get("kb_name"),
        "task_kind": job.get("task_kind"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested": bool(job.get("cancel_requested")),
    }


def _registry_path(root: Path) -> Path:
    env = os.environ.get("RAGRET_REGISTRY")
    if env:
        return Path(env).expanduser().resolve()
    return default_registry_path(root)


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
    t = os.environ.get("RAGRET_API_TOKEN")
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


def _is_tar_filename(name: str) -> bool:
    return is_tar_archive_filename(name)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    safe_extract_tar_archive(tf, dest)


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
        "source_type": str(rec.source_type or "tar"),
        "webhook_provider": str(rec.webhook_provider or ""),
        "webhook_secret_len": len(str(rec.webhook_secret or "")),
        "webhook_repo_url": str(rec.webhook_repo_url or ""),
        "webhook_ref": str(rec.webhook_ref or ""),
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
    static_dir = (root / "ragret" / "static").resolve()
    upload_base = runtime_upload_dir(root)

    class RagretHTTPRequestHandler(BaseHTTPRequestHandler):
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
                    {"ok": False, "error": "Login required (session token) or RAGRET_API_TOKEN"},
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

        def _webhook_proto_host(self) -> tuple[str, str]:
            host = _best_public_host()
            port = self.server.server_port
            if port not in (80, 443):
                host = f"{host}:{int(port)}"
            proto = "http"
            xf = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
            if xf in ("http", "https"):
                proto = xf
            return proto, host

        def _webhook_url_for_kb(self, kb_name: str) -> str:
            proto, host = self._webhook_proto_host()
            prov = "gitlab"
            if kb_name:
                rec = app_store.get_kb_record_any_state(kb_name)
                if rec is not None:
                    p = str(rec.webhook_provider or "").strip().lower()
                    if p in ("gitlab", "github"):
                        prov = p
            return f"{proto}://{host}/api/webhooks/{prov}/{kb_name}"

        def _webhook_base_urls(self) -> dict[str, str]:
            proto, host = self._webhook_proto_host()
            base = f"{proto}://{host}/api/webhooks"
            return {"gitlab": f"{base}/gitlab/", "github": f"{base}/github/"}

        def _complete_webhook_push(
            self, safe_name: str, rec: KBRecord, repo_url: str, checkout_sha: str
        ) -> None:
            app_store.update_knowledge_base_webhook_source(safe_name, repo_url=repo_url, ref=None)
            build_ref = str(rec.webhook_ref or "").strip()
            if not build_ref:
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "Branch not configured: set ref (branch) on the knowledge base in the console, then retry.",
                    },
                )
                return
            op = "update" if Path(str(rec.db_path or "")).is_file() else "create"
            job_id = secrets.token_hex(12)
            payload = {
                "description": str(rec.description or ""),
                "readme_md": str(rec.readme_md or ""),
                "is_public": bool(rec.is_public),
                "icon": str(rec.icon or "book"),
                "repo_url": repo_url,
                "ref": build_ref,
                "checkout_sha": str(checkout_sha or ""),
            }
            app_store.enqueue_build_job(
                job_id=job_id,
                user_id=int(rec.owner_id),
                task_kind="webhook",
                op=op,
                kb_name=safe_name,
                upload_id=secrets.token_hex(12),
                payload=payload,
            )
            wake_build_worker()
            _send_json(self, 202, {"ok": True, "job_id": job_id, "op": op})

        def _handle_gitlab_webhook(self, kb_name: str) -> None:
            data = self._read_json_body()
            if data is None:
                return
            if str(data.get("event_name") or data.get("object_kind") or "").lower() != "push":
                _send_json(self, 202, {"ok": True, "ignored": True, "reason": "not_push_event"})
                return
            try:
                safe_name = safe_index_name(kb_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            rec = app_store.get_kb_record_any_state(safe_name)
            if rec is None:
                _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                return
            if str(rec.source_type or "tar") != "webhook" or str(rec.webhook_provider or "") != "gitlab":
                _send_json(self, 400, {"ok": False, "error": "Knowledge base is not configured for GitLab webhook"})
                return
            expected = str(rec.webhook_secret or "").strip()
            got = str(self.headers.get("X-Gitlab-Token") or "").strip()
            if expected and not secrets.compare_digest(expected, got):
                _send_json(self, 403, {"ok": False, "error": "Invalid webhook secret"})
                return
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            repository = data.get("repository") if isinstance(data.get("repository"), dict) else {}
            repo_url = (
                str(project.get("git_http_url") or "").strip()
                or str(repository.get("git_http_url") or "").strip()
                or str(project.get("http_url") or "").strip()
                or str(repository.get("url") or "").strip()
            )
            if not repo_url:
                _send_json(self, 400, {"ok": False, "error": "Missing repository URL in webhook payload"})
                return
            if not is_http_git_clone_url(repo_url):
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "Webhook payload repository URL must be http(s)://… (got a non-URL value; check GitLab project fields).",
                    },
                )
                return
            self._complete_webhook_push(safe_name, rec, repo_url, str(data.get("checkout_sha") or ""))

        def _handle_github_webhook(self, kb_name: str) -> None:
            ctype = self.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                _send_json(self, 415, {"ok": False, "error": "Content-Type must be application/json"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(n) if n > 0 else b"{}"
            except ValueError:
                _send_json(self, 400, {"ok": False, "error": "Invalid Content-Length"})
                return
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                _send_json(self, 400, {"ok": False, "error": "Invalid JSON body"})
                return
            if not isinstance(data, dict):
                _send_json(self, 400, {"ok": False, "error": "JSON body must be an object"})
                return
            try:
                safe_name = safe_index_name(kb_name)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            rec = app_store.get_kb_record_any_state(safe_name)
            if rec is None:
                _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                return
            if str(rec.source_type or "tar") != "webhook" or str(rec.webhook_provider or "") != "github":
                _send_json(self, 400, {"ok": False, "error": "Knowledge base is not configured for GitHub webhook"})
                return
            expected = str(rec.webhook_secret or "").strip()
            if expected:
                sig = self.headers.get("X-Hub-Signature-256") or ""
                if not _github_signature256_valid(expected, raw, sig):
                    _send_json(self, 403, {"ok": False, "error": "Invalid webhook signature"})
                    return
            data = _unwrap_github_logged_payload(data)
            event = (self.headers.get("X-GitHub-Event") or "").strip().lower() or str(
                data.get("event") or ""
            ).strip().lower()
            repo = data.get("repository") if isinstance(data.get("repository"), dict) else {}
            clone_url = str(repo.get("clone_url") or "").strip()
            if not clone_url:
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "Missing repository clone_url in webhook payload"},
                )
                return
            if not is_http_git_clone_url(clone_url):
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "repository.clone_url must be an https://… URL",
                    },
                )
                return
            if event and event != "push":
                _send_json(self, 202, {"ok": True, "ignored": True, "reason": "not_push_event"})
                return
            self._complete_webhook_push(safe_name, rec, clone_url, str(data.get("after") or ""))

        def _handle_kb_webhook_pull(self, name_raw: str) -> None:
            owner = self._require_user_id()
            if owner is None:
                return
            try:
                key = safe_index_name(name_raw)
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return
            perm = app_store.permission_for(int(owner), key)
            if perm is None or not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can trigger a manual pull"})
                return
            rec = app_store.get_kb_record_any_state(key)
            if rec is None:
                _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                return
            prov = str(rec.webhook_provider or "").strip().lower()
            if str(rec.source_type or "tar") != "webhook" or prov not in ("gitlab", "github"):
                _send_json(self, 400, {"ok": False, "error": "Not a GitLab/GitHub webhook knowledge base"})
                return
            repo_url = str(rec.webhook_repo_url or "").strip()
            ref = str(rec.webhook_ref or "").strip()
            if not repo_url:
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "No repository URL stored yet; wait for a push webhook or set repo_url via PATCH",
                    },
                )
                return
            if not is_http_git_clone_url(repo_url):
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "Stored repo_url is not a valid http(s) address. Open manage and set repository URL to https://… (not the webhook secret).",
                    },
                )
                return
            if not ref:
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "Branch (ref) is not set; configure it in knowledge base settings before pulling.",
                    },
                )
                return
            op = "update" if Path(str(rec.db_path or "")).is_file() else "create"
            job_id = secrets.token_hex(12)
            payload = {
                "description": str(rec.description or ""),
                "readme_md": str(rec.readme_md or ""),
                "is_public": bool(rec.is_public),
                "icon": str(rec.icon or "book"),
                "repo_url": repo_url,
                "ref": ref,
                "checkout_sha": "",
            }
            app_store.enqueue_build_job(
                job_id=job_id,
                user_id=int(rec.owner_id),
                task_kind="webhook",
                op=op,
                kb_name=key,
                upload_id=secrets.token_hex(12),
                payload=payload,
            )
            wake_build_worker()
            _send_json(self, 202, {"ok": True, "job_id": job_id, "op": op})

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
                _send_json(self, 200, {"service": "ragret", "api": "/api/…", "auth": "/api/auth/login"})
                return

            if parts[0].lower() == "health" and len(parts) == 1:
                _send_json(self, 200, {"ok": True})
                return

            if _provider_webhook_kb_from_path(parsed.path) is not None:
                _send_json(self, 200, {"ok": True, "accept": "POST"})
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

            if len(parts) == 1 and parts[0].lower() == "webhook-base":
                bases = self._webhook_base_urls()
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "base_url": bases["gitlab"],
                        "bases": bases,
                    },
                )
                return

            if len(parts) == 1 and parts[0].lower() == "skill-md":
                p = (root / "SKILL.md").resolve()
                if not p.is_file():
                    _send_json(self, 404, {"ok": False, "error": "SKILL.md not found"})
                    return
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError as e:
                    _send_json(self, 500, {"ok": False, "error": str(e)})
                    return
                _send_json(self, 200, {"ok": True, "content": text, "filename": "SKILL.md"})
                return

            if len(parts) == 2 and parts[0].lower() == "skill-md" and parts[1].lower() == "download":
                p = (root / "SKILL.md").resolve()
                if not p.is_file():
                    _send_json(self, 404, {"ok": False, "error": "SKILL.md not found"})
                    return
                try:
                    raw = p.read_bytes()
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr("ragret/SKILL.md", raw)
                    body = buf.getvalue()
                except OSError as e:
                    _send_json(self, 500, {"ok": False, "error": str(e)})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="ragret.zip"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if not parts:
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "ragret",
                        "endpoints": {
                            "auth": "POST /api/auth/register | /api/auth/login | /api/auth/logout",
                            "list": "GET /api/indexes",
                            "kb": "GET /api/kb/{name} | PATCH (repo_url, ref for webhook)",
                            "webhook_pull": "POST /api/kb/{name}/webhook-pull (owner, GitLab/GitHub webhook KB)",
                            "members": "GET/POST/DELETE /api/kb/{name}/members",
                            "subscribe": "POST|DELETE /api/kb/{name}/subscribe",
                            "subscriptions": "GET /api/user/subscriptions",
                            "subscribe_indexes": "GET /api/subscribe-indexes (API key only)",
                            "api_keys": "GET/POST/DELETE /api/user/api-keys",
                            "gitlab_pat": "GET/POST /api/user/gitlab-pat",
                            "github_pat": "GET/POST /api/user/github-pat",
                            "search": "GET /api/search/{index}?query=...",
                            "upload": "POST /api/upload",
                            "build": "POST /api/indexes/build",
                            "jobs": "GET /api/jobs | GET /api/jobs/{job_id} | POST /api/jobs/{job_id}/cancel",
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
            if parts[0].lower() == "user" and len(parts) == 2 and parts[1].lower() == "gitlab-pat":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                has_pat = bool(app_store.get_user_gitlab_pat(int(uid)))
                pat = app_store.get_user_gitlab_pat(int(uid))
                _send_json(self, 200, {"ok": True, "has_pat": has_pat, "pat": pat})
                return
            if parts[0].lower() == "user" and len(parts) == 2 and parts[1].lower() == "github-pat":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                has_pat = bool(app_store.get_user_github_pat(int(uid)))
                pat = app_store.get_user_github_pat(int(uid))
                _send_json(self, 200, {"ok": True, "has_pat": has_pat, "pat": pat})
                return
            if parts[0].lower() == "user" and len(parts) == 3 and parts[1].lower() == "webhook-secret" and parts[2].lower() == "generate":
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                _send_json(self, 200, {"ok": True, "secret": secrets.token_urlsafe(24)})
                return

            if parts[0].lower() == "search" and len(parts) == 2:
                self._handle_search(parts[1], qs)
                return

            if parts[0].lower() == "jobs" and len(parts) == 1:
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                jobs = app_store.list_build_jobs_for_user(int(uid))
                _send_json(self, 200, {"ok": True, "jobs": [_job_public_view(j) for j in jobs]})
                return

            if parts[0].lower() == "jobs" and len(parts) == 2:
                jid = parts[1]
                snap = app_store.get_build_job(jid)
                if snap is None:
                    _send_json(self, 404, {"ok": False, "error": "Unknown job"})
                    return
                if k != "superuser" and (uid is None or int(snap["user_id"]) != int(uid)):
                    _send_json(self, 403, {"ok": False, "error": "Forbidden"})
                    return
                _send_json(self, 200, {"ok": True, **_job_public_view(snap)})
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
                body["webhook_url"] = self._webhook_url_for_kb(name)
                body["webhook_secret_masked"] = "*" * int(body.get("webhook_secret_len") or 0)
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
            if parts[0].lower() == "kb" and len(parts) == 3 and parts[2].lower() == "webhook-secret":
                name = parts[1]
                if k != "user" or uid is None:
                    _send_json(self, 403, {"ok": False, "error": "Login required"})
                    return
                perm = app_store.permission_for(int(uid), name)
                if perm is None or not perm.is_owner:
                    _send_json(self, 403, {"ok": False, "error": "Only owner can read webhook secret"})
                    return
                rec = app_store.get_kb_record_any_state(name)
                if rec is None:
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
                _send_json(self, 200, {"ok": True, "secret": str(rec.webhook_secret or "")})
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

            from ragret.rag import search_db

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

            wh = _provider_webhook_kb_from_path(parsed.path)
            if wh is not None:
                prov, wk_kb = wh
                if prov == "gitlab":
                    self._handle_gitlab_webhook(wk_kb)
                else:
                    self._handle_github_webhook(wk_kb)
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
                and parts[1].lower() == "jobs"
                and parts[3].lower() == "cancel"
            ):
                owner = self._require_user_id()
                if owner is None:
                    return
                jid = parts[2]
                err, drop_meta = app_store.request_cancel_build_job(jid, int(owner))
                if err:
                    _send_json(self, 400, {"ok": False, "error": err})
                    return
                if drop_meta and drop_meta.get("dropped_queued"):
                    op_q = str(drop_meta.get("op") or "")
                    kb_n = str(drop_meta.get("kb_name") or "")
                    u_id = str(drop_meta.get("upload_id") or "")
                    if op_q == "create":
                        app_store.delete_knowledge_base(kb_n)
                        registry.remove(kb_n)
                        fd = kb_sqlite_path(root, kb_n)
                        try:
                            if fd.is_file():
                                fd.unlink()
                        except OSError:
                            pass
                    cleanup_upload_staging(upload_base, u_id)
                j = app_store.get_build_job(jid)
                _send_json(
                    self,
                    200,
                    {"ok": True, "job": _job_public_view(j) if j else {}},
                )
                return
            if (
                len(parts) == 4
                and parts[0].lower() == "api"
                and parts[1].lower() == "kb"
                and parts[3].lower() == "webhook-pull"
            ):
                self._handle_kb_webhook_pull(parts[2])
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
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "gitlab-pat"
            ):
                uid = self._require_user_id()
                if uid is None:
                    return
                data = self._read_json_body()
                if data is None:
                    return
                app_store.set_user_gitlab_pat(int(uid), str(data.get("pat") or ""))
                _send_json(self, 200, {"ok": True})
                return
            if (
                len(parts) == 3
                and parts[0].lower() == "api"
                and parts[1].lower() == "user"
                and parts[2].lower() == "github-pat"
            ):
                uid = self._require_user_id()
                if uid is None:
                    return
                data = self._read_json_body()
                if data is None:
                    return
                app_store.set_user_github_pat(int(uid), str(data.get("pat") or ""))
                _send_json(self, 200, {"ok": True})
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
                _send_json(self, 400, {"ok": False, "error": "You can create at most 3 API keys"})
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
                if "webhook_secret" in data:
                    if app_store.update_knowledge_base_webhook_secret(
                        active_key, str(data.get("webhook_secret") or "")
                    ):
                        did = True
                if bool(data.get("regenerate_webhook_secret")):
                    if app_store.update_knowledge_base_webhook_secret(active_key, secrets.token_urlsafe(24)):
                        did = True
                if "repo_url" in data or "ref" in data:
                    if "repo_url" in data:
                        ru = str(data.get("repo_url") or "").strip()
                        if ru and not is_http_git_clone_url(ru):
                            _send_json(
                                self,
                                400,
                                {
                                    "ok": False,
                                    "error": "repo_url must start with http:// or https:// and include a path (clone URL, not a secret token).",
                                },
                            )
                            return
                    if app_store.update_knowledge_base_webhook_source(
                        active_key,
                        repo_url=str(data.get("repo_url") or "").strip() if "repo_url" in data else None,
                        ref=str(data.get("ref") or "").strip() if "ref" in data else None,
                    ):
                        did = True
                if not did and "description" not in data and "is_public" not in data and "name" not in data and "readme_md" not in data and "webhook_secret" not in data and "regenerate_webhook_secret" not in data and "repo_url" not in data and "ref" not in data:
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
            if "description" not in data and "is_public" not in data and "name" not in data and "readme_md" not in data and "webhook_secret" not in data and "regenerate_webhook_secret" not in data and "repo_url" not in data and "ref" not in data:
                _send_json(self, 400, {"ok": False, "error": "No updates provided"})
                return
            if "is_public" in data and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can change visibility"})
                return
            if "webhook_secret" in data and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can update webhook secret"})
                return
            if bool(data.get("regenerate_webhook_secret")) and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can update webhook secret"})
                return
            if ("repo_url" in data or "ref" in data) and not perm.is_owner:
                _send_json(self, 403, {"ok": False, "error": "Only the owner can update webhook repository settings"})
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
            if "webhook_secret" in data:
                if not app_store.update_knowledge_base_webhook_secret(
                    active_key, str(data.get("webhook_secret") or "")
                ):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
            if bool(data.get("regenerate_webhook_secret")):
                if not app_store.update_knowledge_base_webhook_secret(active_key, secrets.token_urlsafe(24)):
                    _send_json(self, 404, {"ok": False, "error": "Unknown knowledge base"})
                    return
            if "repo_url" in data or "ref" in data:
                if "repo_url" in data:
                    ru = str(data.get("repo_url") or "").strip()
                    if ru and not is_http_git_clone_url(ru):
                        _send_json(
                            self,
                            400,
                            {
                                "ok": False,
                                "error": "repo_url must start with http:// or https:// and include a path (clone URL, not a secret token).",
                            },
                        )
                        return
                if not app_store.update_knowledge_base_webhook_source(
                    active_key,
                    repo_url=str(data.get("repo_url") or "").strip() if "repo_url" in data else None,
                    ref=str(data.get("ref") or "").strip() if "ref" in data else None,
                ):
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
            source_type = str(data.get("source_type") or "tar").strip().lower() or "tar"
            webhook_provider = str(data.get("webhook_provider") or "").strip().lower()
            webhook_secret = str(data.get("webhook_secret") or "").strip()
            webhook_repo_url = str(data.get("repo_url") or "").strip()
            webhook_ref = str(data.get("ref") or "").strip()
            if not name_raw or not desc_raw:
                _send_json(self, 400, {"ok": False, "error": "JSON must include non-empty name and description"})
                return
            if source_type not in ("tar", "webhook"):
                _send_json(self, 400, {"ok": False, "error": "source_type must be tar or webhook"})
                return
            if source_type == "tar" and not upload_id:
                _send_json(
                    self,
                    400,
                    {"ok": False, "error": "JSON must include non-empty upload_id for tar build"},
                )
                return
            if source_type == "webhook" and webhook_provider not in ("", "gitlab", "github"):
                _send_json(self, 400, {"ok": False, "error": "webhook_provider must be gitlab or github"})
                return
            if source_type == "webhook" and not webhook_secret:
                webhook_secret = secrets.token_urlsafe(24)
            if source_type == "webhook" and not webhook_repo_url:
                _send_json(self, 400, {"ok": False, "error": "repo_url is required for webhook first build"})
                return
            if source_type == "webhook" and not is_http_git_clone_url(webhook_repo_url):
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "repo_url must be an http(s) Git remote (e.g. https://gitlab.com/group/project.git), not a token or SSH URL.",
                    },
                )
                return
            if source_type == "webhook" and not webhook_ref:
                _send_json(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": "ref is required for webhook builds (branch name, e.g. main or refs/heads/main)",
                    },
                )
                return
            try:
                index_name = safe_index_name(str(name_raw))
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
                return

            if source_type == "tar":
                n_active = app_store.count_user_upload_tasks_active(int(owner_user_id))
                if n_active >= _MAX_USER_UPLOAD_JOBS:
                    _send_json(
                        self,
                        429,
                        {
                            "ok": False,
                            "error": "Too many upload build jobs in queue or running (max 3). "
                            "Finish, cancel one, or wait.",
                        },
                    )
                    return

            existing_ready = app_store.get_knowledge_base(index_name)
            is_update = existing_ready is not None

            if source_type == "webhook":
                if is_update or app_store.knowledge_base_name_taken(index_name):
                    _send_json(self, 409, {"ok": False, "error": "Knowledge base name already taken"})
                    return
                wh_prov = webhook_provider or "gitlab"
                if wh_prov not in ("gitlab", "github"):
                    wh_prov = "gitlab"
                final_sqlite = str(kb_sqlite_path(root, index_name))
                try:
                    app_store.create_pending_knowledge_base(
                        name=index_name,
                        description=desc_raw,
                        readme_md=readme_raw,
                        db_path=final_sqlite,
                        owner_id=int(owner_user_id),
                        is_public=bool(data.get("is_public", False)),
                        icon=str(data.get("icon") or "book").strip() or "book",
                        source_type="webhook",
                        webhook_provider=wh_prov,
                        webhook_secret=webhook_secret,
                        webhook_repo_url=webhook_repo_url,
                        webhook_ref=webhook_ref,
                    )
                except ValueError as e:
                    _send_json(self, 409, {"ok": False, "error": str(e)})
                    return
                job_id = secrets.token_hex(12)
                payload = {
                    "description": desc_raw,
                    "readme_md": readme_raw,
                    "is_public": bool(data.get("is_public", False)),
                    "icon": str(data.get("icon") or "book").strip() or "book",
                    "repo_url": webhook_repo_url,
                    "ref": webhook_ref,
                    "checkout_sha": "",
                }
                try:
                    app_store.enqueue_build_job(
                        job_id=job_id,
                        user_id=int(owner_user_id),
                        task_kind="webhook",
                        op="create",
                        kb_name=index_name,
                        upload_id=secrets.token_hex(12),
                        payload=payload,
                    )
                except Exception as e:
                    app_store.delete_knowledge_base(index_name)
                    _send_json(self, 500, {"ok": False, "error": str(e)})
                    return
                wake_build_worker()
                _send_json(
                    self,
                    202,
                    {
                        "ok": True,
                        "job_id": job_id,
                        "webhook_url": self._webhook_url_for_kb(index_name),
                    },
                )
                return

            if is_update:
                perm = app_store.permission_for(int(owner_user_id), index_name)
                if perm is None or not perm.is_owner:
                    _send_json(
                        self,
                        403,
                        {
                            "ok": False,
                            "error": "Only the owner can rebuild an existing knowledge base",
                        },
                    )
                    return
                op = "update"
            else:
                if app_store.knowledge_base_name_taken(index_name):
                    _send_json(
                        self,
                        409,
                        {"ok": False, "error": "Knowledge base name already taken"},
                    )
                    return
                op = "create"
                final_sqlite = str(kb_sqlite_path(root, index_name))
                try:
                    app_store.create_pending_knowledge_base(
                        name=index_name,
                        description=desc_raw,
                        readme_md=readme_raw,
                        db_path=final_sqlite,
                        owner_id=int(owner_user_id),
                        is_public=bool(data.get("is_public", False)),
                        icon=str(data.get("icon") or "book").strip() or "book",
                        source_type="tar",
                        webhook_provider="",
                        webhook_secret="",
                    )
                except ValueError as e:
                    _send_json(self, 409, {"ok": False, "error": str(e)})
                    return

            upload_id = str(upload_id).strip()
            if not _UPLOAD_ID_RE.match(upload_id):
                if op == "create":
                    app_store.delete_knowledge_base(index_name)
                _send_json(self, 400, {"ok": False, "error": "Invalid upload_id"})
                return
            staging = (upload_base / "staging" / upload_id).resolve()
            try:
                staging.relative_to(upload_base.resolve())
            except ValueError:
                if op == "create":
                    app_store.delete_knowledge_base(index_name)
                _send_json(self, 400, {"ok": False, "error": "Invalid upload_id"})
                return
            if not staging.is_dir():
                if op == "create":
                    app_store.delete_knowledge_base(index_name)
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
            payload = {
                "description": desc_raw,
                "readme_md": readme_raw,
                "is_public": is_public_flag,
                "icon": icon_key,
            }
            try:
                app_store.enqueue_build_job(
                    job_id=job_id,
                    user_id=int(owner_user_id),
                    task_kind="upload",
                    op=op,
                    kb_name=index_name,
                    upload_id=upload_id,
                    payload=payload,
                )
            except Exception as e:
                if op == "create":
                    app_store.delete_knowledge_base(index_name)
                _send_json(self, 500, {"ok": False, "error": str(e)})
                return

            wake_build_worker()
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

    return RagretHTTPRequestHandler


def run_server(*, host: str, port: int, repo_root: Path | None = None) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
    root = (repo_root or REPO_ROOT).resolve()
    if "HF_HOME" not in os.environ:
        from ragret.paths import default_hf_models_dir

        d = default_hf_models_dir()
        os.environ["HF_HOME"] = str(d)
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(d)
        d.mkdir(parents=True, exist_ok=True)
    reg_path = _registry_path(root)
    registry = IndexRegistry(reg_path)
    registry.load()
    app_store = create_app_store(root)

    n_orphan = cleanup_orphan_kb_sqlite_files(root, registry=registry, app_store=app_store)
    if n_orphan:
        print(f"Removed {n_orphan} orphan KB sqlite file(s) under runtime/data or legacy data/.", flush=True)

    upload_base = runtime_upload_dir(root)
    start_global_build_worker(
        root=root,
        registry=registry,
        app_store=app_store,
        upload_base=upload_base,
    )
    wake_build_worker()

    handler_cls = make_handler_class(registry, root, app_store)
    server = ThreadingHTTPServer((host, int(port)), handler_cls)
    db_path = getattr(app_store, "_path", None)
    extra = f" app_db={db_path}" if db_path is not None else ""
    print(f"ragret server http://{host}:{port}/  registry={reg_path}{extra}", flush=True)
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
