"""Single-threaded global queue for corpus upload / index build jobs."""
from __future__ import annotations

import errno
import gc
import json
import multiprocessing
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
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from ragret.rag import BuildCancelledError, index_workdir, try_incremental_update_workdir
from ragret.registry import IndexRegistry, safe_sqlite_basename
from dulwich import porcelain
from dulwich.errors import NotGitRepository

from server.archive_util import is_tar_archive_filename, safe_extract_tar_archive
from server.runtime_paths import runtime_data_dir, runtime_webhook_dir

_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{24}$")


def is_http_git_clone_url(url: str) -> bool:
    """True if URL is suitable for Dulwich HTTP(S) clone (not SSH, not a bare secret token)."""
    u = str(url or "").strip()
    if len(u) < 12:
        return False
    ul = u.lower()
    if not (ul.startswith("http://") or ul.startswith("https://")):
        return False
    after_scheme = u.split("://", 1)[1]
    return "/" in after_scheme and not after_scheme.startswith("//")

_queue_wake = threading.Event()


def _gitlab_ref_to_branch(ref: str) -> str:
    s = str(ref or "").strip()
    if s.startswith("refs/heads/"):
        return s[len("refs/heads/") :]
    return s


def _webhook_tmp_base(root: Path) -> Path:
    return runtime_webhook_dir(root)


def cleanup_webhook_temp_directories(repo_root: Path, *, primary_clone: Path | None = None) -> None:
    """One cleanup pass: remove primary clone dir (if set), then all ragret-webhook-* under webhook/ and repo root."""
    root_r = repo_root.resolve()

    def _rm_tree(path: Path, ctx: str) -> None:
        if not path.is_dir():
            return
        # Dulwich / Windows may keep packfile mmap handles briefly; retry with gc + backoff.
        delays_s = (0.0, 0.08, 0.15, 0.25, 0.4, 0.65, 1.0, 1.5, 2.0, 2.5, 3.0)
        last_err: OSError | None = None
        for delay in delays_s:
            if delay > 0:
                time.sleep(delay)
            gc.collect()
            try:
                shutil.rmtree(path)
                return
            except OSError as e:
                last_err = e
                if sys.platform == "win32" and getattr(e, "winerror", None) == 32:
                    continue
                sys.stderr.write(f"ragret-webhook-cleanup: rmtree failed ({ctx})\n  path={path}\n  error={e}\n")
                traceback.print_exc(file=sys.stderr)
                return
        if last_err is not None:
            sys.stderr.write(
                f"ragret-webhook-cleanup: rmtree exhausted retries ({ctx})\n  path={path}\n  error={last_err}\n"
            )
            traceback.print_exc(file=sys.stderr)

    if primary_clone is not None:
        pc = Path(primary_clone).resolve()
        if pc.is_dir():
            _rm_tree(pc, "primary webhook clone")

    wb = runtime_webhook_dir(root_r)
    for base, label in ((wb, "runtime/webhook/"), (root_r, "repo root")):
        try:
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and child.name.startswith("ragret-webhook-"):
                    _rm_tree(child, f"{label} temp {child.name}")
        except OSError as e:
            sys.stderr.write(f"ragret-webhook-cleanup: listdir failed ({label})\n  path={base}\n  error={e}\n")
            traceback.print_exc(file=sys.stderr)


def _https_url_strip_credentials(repo_url: str) -> str:
    """Remove userinfo from HTTP(S) URL so Dulwich can use username= / password= instead."""
    raw = str(repo_url or "").strip()
    if not is_http_git_clone_url(raw):
        return raw
    p = urlsplit(raw)
    if p.scheme not in ("http", "https"):
        return raw
    netloc = p.netloc
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def _dulwich_clone_auth(repo_url: str, pat: str, *, is_github: bool) -> tuple[str, str | None, str | None]:
    """Return (clean_url, username, password). Dulwich often ignores credentials embedded in the URL."""
    clean = _https_url_strip_credentials(repo_url)
    tok = str(pat or "").strip()
    if not tok:
        return clean, None, None
    if is_github:
        # GitHub HTTPS + PAT: official pattern is username "git" and PAT as password.
        return clean, "git", tok
    return clean, "oauth2", tok


def _webhook_git_http_timeout_s() -> tuple[float, float]:
    """(connect_timeout_s, read_timeout_s) for Dulwich HTTP(S) webhook clones."""
    try:
        connect = float(os.environ.get("RAGRET_GIT_HTTP_CONNECT_TIMEOUT_S", "20"))
    except ValueError:
        connect = 20.0
    try:
        read = float(os.environ.get("RAGRET_GIT_HTTP_READ_TIMEOUT_S", "30"))
    except ValueError:
        read = 30.0
    return max(1.0, connect), max(5.0, read)


def _webhook_git_clone_wall_timeout_s() -> float:
    """Wall-clock cap for the whole Dulwich clone (many HTTP requests). Defaults to read timeout."""
    raw = os.environ.get("RAGRET_GIT_CLONE_WALL_TIMEOUT_S")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    _, read_s = _webhook_git_http_timeout_s()
    return max(5.0, read_s)


class _WebhookUrllib3Pool:
    """Dulwich often passes timeout=None / retries=None; force our connect/read limits and no retries."""

    __slots__ = ("_inner", "_timeout", "_no_retry")

    def __init__(self, inner: Any, *, timeout: Any, no_retry: Any) -> None:
        self._inner = inner
        self._timeout = timeout
        self._no_retry = no_retry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        # Dulwich may pass its own timeout; always apply ours so read/connect limits are real.
        kwargs["timeout"] = self._timeout
        kwargs["retries"] = self._no_retry
        return self._inner.request(method, url, *args, **kwargs)


def _webhook_clone_pool_manager():
    """urllib3 PoolManager for Dulwich webhook clones: direct connections only.

    Timeouts: RAGRET_GIT_HTTP_CONNECT_TIMEOUT_S, RAGRET_GIT_HTTP_READ_TIMEOUT_S (per HTTP request).
    Whole-clone wall clock: RAGRET_GIT_CLONE_WALL_TIMEOUT_S (default = read timeout).
    """
    try:
        import urllib3
        from urllib3.util import Retry
    except ImportError as e:
        raise RuntimeError("urllib3 is required for webhook Git clone") from e
    connect_s, read_s = _webhook_git_http_timeout_s()
    tmo = urllib3.Timeout(connect=connect_s, read=read_s)
    try:
        no_retry = Retry(total=0)
    except TypeError:
        no_retry = False
    try:
        inner = urllib3.PoolManager(
            num_pools=8,
            maxsize=8,
            block=True,
            timeout=tmo,
            retries=no_retry,
        )
    except TypeError:
        inner = urllib3.PoolManager(num_pools=8, maxsize=8, block=True, timeout=tmo)
    nr = no_retry if no_retry is not False else False
    return _WebhookUrllib3Pool(inner, timeout=tmo, no_retry=nr)


def _dulwich_clone_error_is_timeout(exc: BaseException) -> bool:
    """True if this or its __cause__ chain is a network read/connect timeout (→ cancel job)."""
    if isinstance(exc, TimeoutError):
        return True
    try:
        import urllib3.exceptions as u3e
    except ImportError:
        return False
    err_it: BaseException | None = exc
    for _ in range(12):
        if err_it is None:
            break
        if isinstance(err_it, (u3e.ConnectTimeoutError, u3e.ReadTimeoutError)):
            return True
        if isinstance(err_it, OSError) and getattr(err_it, "errno", None) == errno.ETIMEDOUT:
            return True
        if isinstance(err_it, u3e.MaxRetryError):
            r = getattr(err_it, "reason", None)
            if isinstance(r, (u3e.ConnectTimeoutError, u3e.ReadTimeoutError)):
                return True
        err_it = err_it.__cause__ if isinstance(err_it.__cause__, BaseException) else None
    return False


def _run_dulwich_clone_into(
    td: Path,
    *,
    repo_url: str,
    branch: str | None,
    username: str | None,
    password: str | None,
) -> None:
    br = str(branch or "").strip() or None
    clone_kw: dict[str, Any] = {
        "source": str(repo_url),
        "target": str(td),
        "depth": 1,
        "checkout": True,
        "errstream": sys.stderr.buffer,
        "outstream": sys.stdout.buffer,
        "branch": br,
    }
    if username is not None:
        clone_kw["username"] = username
        clone_kw["password"] = password if password is not None else ""
    clone_kw["pool_manager"] = _webhook_clone_pool_manager()
    try:
        repo_obj = porcelain.clone(**clone_kw)
        try:
            if repo_obj is not None:
                closer = getattr(repo_obj, "close", None)
                if callable(closer):
                    closer()
        except OSError as e:
            sys.stderr.write(f"ragret-webhook-clone: repo close warning\n  path={td}\n  error={e}\n")
        gc.collect()
    except Exception as e:
        sys.stderr.write(
            "ragret-webhook-clone: clone failed\n"
            f"  repo_url={repo_url}\n"
            f"  branch={br or '(default)'}\n"
            f"  error={e}\n"
        )
        traceback.print_exc(file=sys.stderr)
        if _dulwich_clone_error_is_timeout(e):
            raise BuildCancelledError("clone timed out") from e
        detail = str(e).strip()
        if isinstance(e, NotGitRepository):
            detail = detail or "repository is not a git repository"
        raise RuntimeError(f"git clone failed: {detail or 'unknown error'}") from e


def _webhook_dulwich_clone_child(
    child_conn: Any,
    td_s: str,
    repo_url: str,
    branch: str,
    username: str | None,
    password: str | None,
) -> None:
    """Runs inside a multiprocessing child (spawn). Top-level for pickling on Windows."""
    try:
        td = Path(td_s)
        _run_dulwich_clone_into(
            td,
            repo_url=repo_url,
            branch=branch or None,
            username=username,
            password=password,
        )
        child_conn.send(("ok", None))
    except BuildCancelledError as e:
        try:
            child_conn.send(("cancel", str(e)))
        except Exception:
            pass
    except BaseException as e:
        tb = traceback.format_exc()
        if len(tb) > 48000:
            tb = tb[:48000] + "\n...(truncated)\n"
        try:
            child_conn.send(("err", type(e).__name__, str(e), tb))
        except Exception:
            pass
    finally:
        try:
            child_conn.close()
        except Exception:
            pass


def _terminate_dulwich_clone_process(proc: Any) -> None:
    if not proc.is_alive():
        proc.join(timeout=0.5)
        return
    proc.terminate()
    proc.join(timeout=25)
    if proc.is_alive():
        kill = getattr(proc, "kill", None)
        if callable(kill):
            kill()
            proc.join(timeout=15)
        else:
            proc.join(timeout=5)


def _clone_webhook_repo(
    *,
    repo_url: str,
    branch: str,
    work_dir: Path,
    username: str | None = None,
    password: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_clone_tick: Callable[[], None] | None = None,
) -> Path:
    wb = _webhook_tmp_base(work_dir)
    td = Path(tempfile.mkdtemp(prefix="ragret-webhook-", dir=str(wb)))
    br = str(branch or "").strip()
    if not br:
        shutil.rmtree(td, ignore_errors=True)
        raise ValueError("Webhook clone requires a non-empty branch name")
    try:
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=_webhook_dulwich_clone_child,
            args=(child_conn, str(td.resolve()), repo_url, br, username, password),
            name="ragret-dulwich-clone",
        )
        proc.start()
        try:
            child_conn.close()
        except OSError:
            pass
        try:
            wall_s = _webhook_git_clone_wall_timeout_s()
            t_wall0 = time.monotonic()
            while proc.is_alive():
                if cancel_check is not None and cancel_check():
                    _terminate_dulwich_clone_process(proc)
                    raise BuildCancelledError("cancelled")
                if time.monotonic() - t_wall0 > wall_s:
                    _terminate_dulwich_clone_process(proc)
                    raise BuildCancelledError("clone timed out")
                if on_clone_tick is not None:
                    on_clone_tick()
                proc.join(timeout=0.35)
            proc.join()
            if parent_conn.poll(30.0):
                kind, *rest = parent_conn.recv()
            else:
                ec = getattr(proc, "exitcode", None)
                raise RuntimeError(f"git clone child exited without result (exitcode={ec!r})")
            if kind == "ok":
                if cancel_check is not None and cancel_check():
                    raise BuildCancelledError("cancelled")
            elif kind == "cancel":
                raise BuildCancelledError(rest[0] if rest else "cancelled")
            elif kind == "err":
                ename, emsg, tb_s = rest[0], rest[1], rest[2]
                sys.stderr.write(str(tb_s))
                if ename == "NotGitRepository":
                    raise RuntimeError(
                        f"git clone failed: {emsg or 'repository is not a git repository'}"
                    ) from None
                raise RuntimeError(f"git clone failed: {emsg}") from None
            else:
                raise RuntimeError(f"git clone failed: unexpected child message {kind!r}")
        finally:
            try:
                parent_conn.close()
            except OSError:
                pass
    except Exception:
        shutil.rmtree(td, ignore_errors=True)
        raise
    sys.stderr.write("ragret-webhook-clone: clone finished\n" f"  path={td}\n")
    return td


def _clone_webhook_repo_default_main_master(
    *,
    repo_url: str,
    work_dir: Path,
    username: str | None = None,
    password: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_clone_tick: Callable[[], None] | None = None,
) -> Path:
    """For manual pull without ref, prefer main then master."""
    last_error: Exception | None = None
    for cand in ("main", "master"):
        try:
            return _clone_webhook_repo(
                repo_url=repo_url,
                branch=cand,
                work_dir=work_dir,
                username=username,
                password=password,
                cancel_check=cancel_check,
                on_clone_tick=on_clone_tick,
            )
        except BuildCancelledError:
            raise
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise last_error
    raise RuntimeError("git clone failed: cannot resolve default branch")


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
    return (runtime_data_dir(root) / f"{safe_sqlite_basename(kb_name)}.sqlite").resolve()


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
            if not is_http_git_clone_url(repo_url):
                raise ValueError(
                    "Stored repository URL is not a valid http(s) Git remote (e.g. https://host/group/repo.git). "
                    "Fix it under knowledge base → webhook / repository settings — do not paste the webhook secret there."
                )
            uid = int(job.get("user_id") or 0)
            rec_wh = app_store.get_kb_record_any_state(kb_name)
            prov = str(rec_wh.webhook_provider or "").strip().lower() if rec_wh else ""
            if prov == "github":
                owner_pat = str(app_store.get_user_github_pat(uid) or "").strip()
            else:
                owner_pat = str(app_store.get_user_gitlab_pat(uid) or "").strip()
            clone_url, auth_user, auth_pass = _dulwich_clone_auth(
                repo_url, owner_pat, is_github=(prov == "github")
            )
            _clone_t0 = time.monotonic()
            _last_clone_ui = [0.0]

            def on_webhook_clone_tick() -> None:
                now = time.monotonic()
                if now - _last_clone_ui[0] < 0.75:
                    return
                _last_clone_ui[0] = now
                elapsed = int(now - _clone_t0)
                bump("git_clone", 8, f"cloning remote… {elapsed}s")

            bump("git_clone", 8, "cloning remote… 0s")
            sys.stderr.write(
                "ragret-webhook-clone: start\n"
                f"  kb_name={kb_name}\n"
                f"  repo_url={repo_url}\n"
                f"  branch={branch or 'main->master'}\n"
                f"  using_pat={'yes' if owner_pat else 'no'}\n"
                f"  backend=dulwich\n"
            )
            if branch:
                webhook_workdir = _clone_webhook_repo(
                    repo_url=clone_url,
                    branch=branch,
                    work_dir=root,
                    username=auth_user,
                    password=auth_pass,
                    cancel_check=cancelled,
                    on_clone_tick=on_webhook_clone_tick,
                )
            else:
                webhook_workdir = _clone_webhook_repo_default_main_master(
                    repo_url=clone_url,
                    work_dir=root,
                    username=auth_user,
                    password=auth_pass,
                    cancel_check=cancelled,
                    on_clone_tick=on_webhook_clone_tick,
                )
            extract_dir = webhook_workdir
            bump("extract", 14, "cloned, then index")
        else:
            raise ValueError(f"Unknown task kind: {task_kind!r}")
        if cancelled():
            raise BuildCancelledError("cancelled")

        if task_kind == "webhook" and extract_dir is not None:
            sys.stderr.write(
                "ragret-webhook-index: starting\n"
                f"  extract_dir={extract_dir}\n"
                f"  op={op}\n",
            )

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
        sys.stderr.write(
            "ragret-build-job: failed\n"
            f"  job_id={job_id}\n"
            f"  kb_name={kb_name}\n"
            f"  task_kind={task_kind}\n"
            f"  op={op}\n"
            f"  error={e!r}\n",
        )
        traceback.print_exc(file=sys.stderr)
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
        if task_kind == "webhook":
            cleanup_webhook_temp_directories(root, primary_clone=webhook_workdir)


def global_build_worker_loop(
    *,
    root: Path,
    registry: IndexRegistry,
    app_store: Any,
    upload_base: Path,
    stop_event: threading.Event,
    tick_s: float = 0.35,
) -> None:
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
            sys.stderr.write("ragret-build-queue: unhandled error:\n")
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
        name="ragret-build-queue",
        daemon=True,
    )
    t.start()
    return t, stop
