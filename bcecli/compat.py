"""stdlib / dependency quirks (run before heavy imports that use multiprocess)."""
from __future__ import annotations

import os


def patch_multiprocess_resource_tracker_py312() -> None:
    """Py3.12: RLock has no _recursion_count; skip check when absent (multiprocess/datasets)."""
    try:
        from multiprocess.resource_tracker import ResourceTracker
    except ImportError:
        return
    if getattr(ResourceTracker, "_bcecli_py312_fixed", False):
        return

    def _stop_locked(
        self,
        close=os.close,
        waitpid=os.waitpid,
        waitstatus_to_exitcode=os.waitstatus_to_exitcode,
    ):
        lock = getattr(self, "_lock", None)
        if lock is not None:
            fn = getattr(lock, "_recursion_count", None)
            if callable(fn):
                try:
                    if fn() > 1:
                        return self._reentrant_call_error()
                except Exception:
                    pass
        if self._fd is None:
            return
        if self._pid is None:
            return
        close(self._fd)
        self._fd = None
        waitpid(self._pid, 0)
        self._pid = None

    ResourceTracker._stop_locked = _stop_locked  # type: ignore[assignment]
    ResourceTracker._bcecli_py312_fixed = True


patch_multiprocess_resource_tracker_py312()
