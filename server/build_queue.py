"""Single-threaded global queue for corpus upload / index build jobs."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from bcecli.rag import BuildCancelledError, index_workdir, try_incremental_update_workdir
from bcecli.registry import IndexRegistry, safe_sqlite_basename
from dulwich import porcelain
from dulwich.errors import NotGitRepository

from server.archive_util import is_tar_archive_filename, safe_extract_tar_archive

_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{24}$")

_queue_wake = threading.Event()


def _gitlab_ref_to_branch(ref: str) -> str:
    s = str(ref or "").strip()
    if s.startswith("refs/heads/"):
        return s[len("refs/heads/") :]
    return s


def _webhook_tmp_base(root: Path) -> Path:
    p = (root / "webhook").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cleanup_legacy_webhook_tmp_dirs(root: Path) -> None:
    """Best-effort cleanup for old temp dirs created under repo root."""
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("bcecli-webhook-"):
                shutil.rmtree(child, ignore_errors=True)
    except OSError:
        pass


def _clone_webhook_repo(*, repo_url: str, branch: str, work_dir: Path) -> Path:
    td = Path(tempfile.mkdtemp(prefix="bcecli-webhook-", dir=str(_webhook_tmp_base(work_dir))))
    br = str(branch or "").strip() or None
    try:
        porcelain.clone(
            source=str(repo_url),
            target=str(td),
            depth=1,
            checkout=True,
            errstream=sys.stderr.buffer,
            outstream=sys.stdout.buffer,
            branch=br,
        )
    except Exception as e:
        sys.stderr.write(
            "bcecli-webhook-clone: clone failed\n"
            f"  repo_url={repo_url}\n"
            f"  branch={br or '(default)'}\n"
            f"  error={e}\n"
        )
        traceback.print_exc(file=sys.stderr)
        try:
            shutil.rmtree(td, ignore_errors=True)
        except OSError:
            pass
        detail = str(e).strip()
        if isinstance(e, NotGitRepository):
            detail = detail or "repository is not a git repository"
        raise RuntimeError(f"git clone failed: {detail or 'unknown error'}") from e
    return td


def _clone_webhook_repo_default_main_master(*, repo_url: str, work_dir: Path) -> Path:
    """For manual pull without ref, prefer main then master."""
    last_error: Exception | None = None
    for cand in ("main", "master"):
        try:
            return _clone_webhook_repo(repo_url=repo_url, branch=cand, work_dir=work_dir)
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise last_error
    raise RuntimeError("git clone failed: cannot resolve default branch")


def _inject_gitlab_pat_http_url(repo_url: str, pat: str) -> str:
    token = str(pat or "").strip()
    raw = str(repo_url or "").strip()
    if not token or not raw:
        return raw
    p = urlsplit(raw)
    if p.scheme not in ("http", "https"):
        return raw
    netloc = p.netloc
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    userinfo = f"oauth2:{quote(token, safe='')}"
    return urlunsplit((p.scheme, f"{userinfo}@{netloc}", p.path, p.query, p.fragment))


def wake_build_worker() -> None:
    """Notify the global build worker that a job may be available (reduces enqueue→claim latency)."""
    _queue_wake.set()


def _finalize_and_drop_job(app_store: Any, job_id: str, **fields: Any) -> None:
    """Persist terminal fields for one client read cycle, then remove the row."""
    if fields:
        app_store.update_build_job_fields(job_id, **fields)
    app_store.delete_build_job(job_id)


def _sync_job_progress(
    app_store: Any,
    job_id: str,
    *,
    phase: str,
    pct: int,
    detail: str = "",
) -> None:
    app_store.update_build_job_fields(
        job_id,
        phase=phase,
        percent=max(0, min(100, int(pct))),
        detail=str(detail or ""),
    )


def cleanup_upload_staging(upload_base: Path, upload_id: str) -> None:
    """Remove a staged upload directory (safe path checks). Public for HTTP cancel path."""
    _cleanup_staging(upload_base, upload_id)


def _cleanup_staging(upload_base: Path, upload_id: str) -> None:
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


def _final_sqlite_path(root: Path, kb_name: str) -> Path:
    data_dir = (root / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return (data_dir / f"{safe_sqlite_basename(kb_name)}.sqlite").resolve()


def run_one_build_job(
    job: dict[str, Any],
    *,
    root: Path,
    registry: IndexRegistry,
    app_store: Any,
    upload_base: Path,
) -> None:
    job_id = str(job["job_id"])
    kb_name = str(job["kb_name"])
    upload_id = str(job["upload_id"])
    op = str(job["op"])
    payload = job.get("payload") or {}
    task_kind = str(job.get("task_kind") or "upload")
    description = str(payload.get("description") or "").strip()
    readme_md = str(payload.get("readme_md") or "").strip()
    is_public = bool(payload.get("is_public", False))
    icon_key = str(payload.get("icon") or "book").strip() or "book"

    last_pct = [0]
    final_db = _final_sqlite_path(root, kb_name)
    building_path: Path | None = None
    webhook_workdir: Path | None = None

    def bump(phase: str, pct: int, detail: str | None = None) -> None:
        last_pct[0] = max(last_pct[0], pct)
        _sync_job_progress(
            app_store,
            job_id,
            phase=phase,
            pct=last_pct[0],
            detail=(detail or ""),
        )

    def cancelled() -> bool:
        return bool(app_store.build_job_cancel_requested(job_id))

    extract_dir: Path | None = None
    try:
        bump("extract", 4, "prepare")
        meta: dict[str, Any] = {}
        if task_kind == "upload":
            if cancelled():
                raise BuildCancelledError("cancelled")
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
            if not archive_name or not is_tar_archive_filename(archive_name):
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
                    safe_extract_tar_archive(tf, extract_dir)
            except (tarfile.TarError, OSError) as e:
                raise ValueError(f"Invalid or unreadable tar: {e}") from e
            bump("extract", 14, "extracted")
        elif task_kind == "webhook":
            repo_url = str(payload.get("repo_url") or "").strip()
            branch = _gitlab_ref_to_branch(str(payload.get("ref") or ""))
            if not repo_url:
                raise ValueError("Webhook payload missing repository URL")
            owner_pat = str(app_store.get_user_gitlab_pat(int(job.get("user_id") or 0)) or "").strip()
            clone_url = _inject_gitlab_pat_http_url(repo_url, owner_pat)
            bump("extract", 8, "clone repository")
            sys.stderr.write(
                "bcecli-webhook-clone: start\n"
                f"  kb_name={kb_name}\n"
                f"  repo_url={repo_url}\n"
                f"  branch={branch or 'main->master'}\n"
                f"  using_pat={'yes' if owner_pat else 'no'}\n"
            )
            if branch:
                webhook_workdir = _clone_webhook_repo(repo_url=clone_url, branch=branch, work_dir=root)
            else:
                webhook_workdir = _clone_webhook_repo_default_main_master(repo_url=clone_url, work_dir=root)
            extract_dir = webhook_workdir
            bump("extract", 14, "cloned")
        else:
            raise ValueError(f"Unknown task kind: {task_kind!r}")
        if cancelled():
            raise BuildCancelledError("cancelled")

        def rag_progress(phase: str, pct: int, detail: str | None) -> None:
            bump(phase, max(last_pct[0], pct), detail)

        is_public_job = bool(meta.get("is_public", is_public))
        readme_effective = str(meta.get("readme_md") or readme_md)

        if op == "create":
            if cancelled():
                raise BuildCancelledError("cancelled")
            try:
                index_workdir(
                    extract_dir,
                    final_db,
                    progress=rag_progress,
                    cancel_check=cancelled,
                )
            except BuildCancelledError:
                raise
            except Exception:
                if final_db.is_file():
                    try:
                        final_db.unlink()
                    except OSError:
                        pass
                raise
            bump("register", 99, None)
            if cancelled():
                raise BuildCancelledError("cancelled")
            key = registry.add(kb_name, final_db, description=description)
            try:
                app_store.finalize_knowledge_base_ready(key)
                app_store.update_knowledge_base_description(key, description)
                app_store.update_knowledge_base_readme(key, readme_effective)
                app_store.update_knowledge_base_public(key, is_public_job)
                app_store.update_knowledge_base_icon(key, icon_key)
                if task_kind == "webhook":
                    app_store.update_knowledge_base_webhook_source(
                        key,
                        repo_url=str(payload.get("repo_url") or "").strip(),
                        ref=str(payload.get("ref") or "").strip(),
                    )
            except Exception as e:
                registry.remove(key)
                try:
                    if final_db.is_file():
                        final_db.unlink()
                except OSError:
                    pass
                app_store.delete_knowledge_base(key)
                raise RuntimeError(f"Register in app database failed: {e}") from e
            _finalize_and_drop_job(
                app_store,
                job_id,
                status="done",
                phase="done",
                percent=100,
                detail="",
                error=None,
                result={"name": key, "description": description},
                finished_at=time.time(),
            )
            return

        if op == "update":
            live = Path(str(app_store.resolve_kb_db_path(kb_name) or "")).resolve()
            if not live.is_file():
                raise FileNotFoundError("Live index database missing")
            building_path = live.parent / f"{live.name}.building"
            if building_path.exists():
                try:
                    building_path.unlink()
                except OSError:
                    pass
            shutil.copy2(live, building_path)
            if cancelled():
                building_path.unlink(missing_ok=True)
                raise BuildCancelledError("cancelled")
            try:
                inc = try_incremental_update_workdir(
                    extract_dir,
                    building_path,
                    progress=rag_progress,
                    cancel_check=cancelled,
                )
                if not inc:
                    index_workdir(
                        extract_dir,
                        building_path,
                        progress=rag_progress,
                        cancel_check=cancelled,
                    )
            except BuildCancelledError:
                building_path.unlink(missing_ok=True)
                raise
            except Exception:
                building_path.unlink(missing_ok=True)
                raise
            if cancelled():
                building_path.unlink(missing_ok=True)
                raise BuildCancelledError("cancelled")
            os.replace(str(building_path), str(live))
            building_path = None
            bump("register", 99, None)
            key = registry.add(kb_name, live, description=description)
            app_store.update_knowledge_base_description(key, description)
            app_store.update_knowledge_base_readme(key, readme_effective)
            app_store.update_knowledge_base_public(key, is_public_job)
            if task_kind == "webhook":
                app_store.update_knowledge_base_webhook_source(
                    key,
                    repo_url=str(payload.get("repo_url") or "").strip(),
                    ref=str(payload.get("ref") or "").strip(),
                )
            _finalize_and_drop_job(
                app_store,
                job_id,
                status="done",
                phase="done",
                percent=100,
                detail="",
                error=None,
                result={"name": key, "description": description},
                finished_at=time.time(),
            )
            return

        raise ValueError(f"Unknown job op: {op!r}")
    except BuildCancelledError:
        _finalize_and_drop_job(
            app_store,
            job_id,
            status="cancelled",
            phase="cancelled",
            percent=last_pct[0],
            detail="",
            error="Cancelled",
            finished_at=time.time(),
        )
        if op == "create":
            app_store.delete_knowledge_base(kb_name)
            registry.remove(kb_name)
            try:
                final_db.unlink(missing_ok=True)
            except OSError:
                pass
        elif op == "update" and building_path is not None:
            building_path.unlink(missing_ok=True)
    except Exception as e:
        _finalize_and_drop_job(
            app_store,
            job_id,
            status="error",
            phase="error",
            percent=last_pct[0],
            detail="",
            error=str(e),
            finished_at=time.time(),
        )
        if op == "create":
            app_store.delete_knowledge_base(kb_name)
            registry.remove(kb_name)
            try:
                final_db.unlink(missing_ok=True)
            except OSError:
                pass
        elif op == "update":
            livep = Path(str(app_store.resolve_kb_db_path(kb_name) or "")).resolve()
            if livep.is_file():
                (livep.parent / f"{livep.name}.building").unlink(missing_ok=True)
    finally:
        if task_kind == "upload":
            _cleanup_staging(upload_base, upload_id)
        if webhook_workdir is not None:
            try:
                shutil.rmtree(webhook_workdir, ignore_errors=True)
            except OSError:
                pass


def global_build_worker_loop(
    *,
    root: Path,
    registry: IndexRegistry,
    app_store: Any,
    upload_base: Path,
    stop_event: threading.Event,
    tick_s: float = 0.35,
) -> None:
    _cleanup_legacy_webhook_tmp_dirs(root)
    while not stop_event.is_set():
        try:
            job = app_store.claim_next_queued_build_job()
            if job is None:
                if _queue_wake.wait(timeout=tick_s):
                    _queue_wake.clear()
                continue
            jid = str(job["job_id"])
            if app_store.build_job_cancel_requested(jid):
                _finalize_and_drop_job(
                    app_store,
                    jid,
                    status="cancelled",
                    phase="cancelled",
                    finished_at=time.time(),
                    error="Cancelled",
                )
                if str(job.get("op")) == "create":
                    app_store.delete_knowledge_base(str(job.get("kb_name") or ""))
                    registry.remove(str(job.get("kb_name") or ""))
                    fd = _final_sqlite_path(root, str(job.get("kb_name") or ""))
                    fd.unlink(missing_ok=True)
                _cleanup_staging(upload_base, str(job.get("upload_id") or ""))
                continue
            run_one_build_job(
                job,
                root=root,
                registry=registry,
                app_store=app_store,
                upload_base=upload_base,
            )
        except Exception:
            sys.stderr.write("bcecli-build-queue: unhandled error:\n")
            traceback.print_exc(file=sys.stderr)
            time.sleep(1.0)


def start_global_build_worker(
    *,
    root: Path,
    registry: IndexRegistry,
    app_store: Any,
    upload_base: Path,
) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(
        target=global_build_worker_loop,
        kwargs={
            "root": root,
            "registry": registry,
            "app_store": app_store,
            "upload_base": upload_base,
            "stop_event": stop,
        },
        name="bcecli-build-queue",
        daemon=True,
    )
    t.start()
    return t, stop
