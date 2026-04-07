"""
bcecli — CLI entry (thin shim).

Implementation lives in the ``bcecli`` package: ``bcecli.cli``, ``bcecli.rag``, ``bcecli.server``, …
"""
from __future__ import annotations

import sys

from bcecli.cli import main

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
