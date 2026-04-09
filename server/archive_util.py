"""Safe tar extraction for staged uploads (shared by HTTP handler and build worker)."""
from __future__ import annotations

import os
import re
import sys
import tarfile
from pathlib import Path

_TAR_SUFFIX_RE = re.compile(r"\.(tar\.gz|tar\.bz2|tar\.xz|tgz|tbz2|txz|tar)$", re.IGNORECASE)


def is_tar_archive_filename(name: str) -> bool:
    return bool(_TAR_SUFFIX_RE.search(name))


def safe_extract_tar_archive(tf: tarfile.TarFile, dest: Path) -> None:
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
