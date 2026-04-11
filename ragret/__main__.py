"""Allow ``python -m ragret`` from repo root (same as ``python ragret.py``)."""
from __future__ import annotations

import sys

from ragret.cli import main

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
