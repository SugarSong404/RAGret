"""Repository root and Hugging Face cache layout helpers (safe before rag import / offline env).

`default_hf_models_dir()` resolves to ``<repo>/models``. The ``bcecli`` package lives in
``<repo>/bcecli/``, so ``BCECLI_REPO_ROOT`` is the parent of this package directory.
"""
from __future__ import annotations

import os
from pathlib import Path

BCECLI_REPO_ROOT = Path(__file__).resolve().parent.parent


def default_hf_models_dir() -> Path:
    return (BCECLI_REPO_ROOT / "models").resolve()


def snapshot_has_weights(snap: Path) -> bool:
    """True if ``snap`` looks like a full model snapshot (not tokenizer-only)."""
    try:
        for p in snap.rglob("*"):
            if not p.is_file():
                continue
            if ".incomplete" in p.name:
                continue
            if p.suffix == ".safetensors" or p.name == "pytorch_model.bin":
                return True
    except OSError:
        return False
    return False


def resolve_hf_snapshot_dir(
    repo_id: str,
    hf_home: str | Path | None = None,
    *,
    require_weights: bool = True,
) -> Path | None:
    """Latest usable snapshot for ``repo_id`` under hub or flat ``models--…`` layout."""
    if hf_home is None:
        raw = os.environ.get("HF_HOME")
        root = Path(raw).expanduser().resolve() if raw else default_hf_models_dir()
    else:
        root = Path(hf_home).expanduser().resolve()
    safe = "models--" + repo_id.replace("/", "--")
    for base in (root / "hub", root):
        snaps = base / safe / "snapshots"
        if not snaps.is_dir():
            continue
        for child in sorted((p for p in snaps.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
            try:
                if not any(child.iterdir()):
                    continue
            except OSError:
                continue
            if require_weights and not snapshot_has_weights(child):
                continue
            return child
    return None
