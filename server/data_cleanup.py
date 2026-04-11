"""Remove KB SQLite files on disk that are no longer referenced by the app DB or index registry."""
from __future__ import annotations

from pathlib import Path

from ragret.registry import IndexRegistry

from server.store.protocol import AppStore


def _unlink_sqlite_cluster(path: Path) -> None:
    if path.suffix.lower() != ".sqlite":
        path.unlink(missing_ok=True)
        return
    stem = str(path)
    for tail in ("-wal", "-shm", "-journal"):
        Path(stem + tail).unlink(missing_ok=True)
    path.unlink(missing_ok=True)


def _referenced_sqlite_paths(registry: IndexRegistry, app_store: AppStore) -> set[Path]:
    ref: set[Path] = set()
    app_p = getattr(app_store, "_path", None)
    if app_p is not None:
        ref.add(Path(app_p).resolve())
    for p in registry.all_registered_db_paths():
        ref.add(p.resolve())
    for p in app_store.all_kb_db_paths():
        ref.add(p.resolve())
    return ref


def cleanup_orphan_kb_sqlite_files(repo_root: Path, *, registry: IndexRegistry, app_store: AppStore) -> int:
    """Delete orphan ``*.sqlite`` under ``runtime/data`` and legacy ``data/``. Returns number of DB files removed."""
    ref = _referenced_sqlite_paths(registry, app_store)
    dirs: list[Path] = []
    rd = (repo_root / "runtime" / "data").resolve()
    legacy = (repo_root / "data").resolve()
    dirs.append(rd)
    if legacy != rd and legacy.is_dir():
        dirs.append(legacy)
    removed = 0
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            names = list(d.iterdir())
        except OSError:
            continue
        for f in names:
            if not f.is_file():
                continue
            if f.suffix.lower() != ".sqlite":
                continue
            try:
                resolved = f.resolve()
            except OSError:
                continue
            if resolved in ref:
                continue
            try:
                _unlink_sqlite_cluster(f)
                removed += 1
            except OSError:
                pass
    return removed
