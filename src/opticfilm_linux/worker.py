"""Best-effort scheduling for CPU-heavy background workers, without Qt dependencies."""

import logging
import os
import sys

log = logging.getLogger(__name__)


def lower_worker_priority() -> None:
    """Lower the calling Linux worker thread's priority, leaving other threads alone.

    Linux niceness is per thread. Other platforms can apply niceness to the whole
    process, so leave their scheduling unchanged. A scheduling failure must never
    prevent the executor from starting or running work.
    """
    if sys.platform != "linux":
        return
    try:
        os.nice(5)
    except Exception:
        log.warning("Could not lower background worker priority", exc_info=True)
