"""Default locations for runtime state (uploads, webhooks, registry, SQLite data)."""
from __future__ import annotations

from pathlib import Path

from ragret.registry import safe_sqlite_basename


def runtime_root(repo_root: Path) -> Path:
    return (repo_root / "runtime").resolve()


def runtime_data_dir(repo_root: Path, *, create: bool = True) -> Path:
    p = (runtime_root(repo_root) / "data").resolve()
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_upload_dir(repo_root: Path, *, create: bool = True) -> Path:
    p = (runtime_root(repo_root) / "upload").resolve()
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_webhook_dir(repo_root: Path, *, create: bool = True) -> Path:
    p = (runtime_root(repo_root) / "webhook").resolve()
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def default_registry_path(repo_root: Path) -> Path:
    return (runtime_root(repo_root) / "ragret_registry.json").resolve()


def default_app_sqlite_path(repo_root: Path) -> Path:
    return (runtime_data_dir(repo_root) / "ragret_app.sqlite").resolve()


def kb_sqlite_path(repo_root: Path, kb_name: str) -> Path:
    return (runtime_data_dir(repo_root) / f"{safe_sqlite_basename(kb_name)}.sqlite").resolve()
