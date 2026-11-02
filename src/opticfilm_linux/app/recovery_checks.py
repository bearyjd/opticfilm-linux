"""Keep raw decoding out of the periodic window refresh worker."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock


class RecoveryChecks:
    def __init__(self):
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raw-recovery")
        self._future = None
        self._key = None
        self._available = frozenset()
        self._lock = Lock()
        self._closed = False

    def poll(self, session, manifest, pending, check):
        """Return completed evidence while a separate worker checks the current files.

        Availability is advisory; the recovery command always rechecks its evidence.
        Never reuse results for a different session or manifest.
        """
        with self._lock:
            if self._closed:
                return frozenset()
            return self._poll(session, manifest, pending, check)

    def _poll(self, session, manifest, pending, check):
        key = (id(session), manifest, pending)
        if self._future is not None and self._future.done():
            try:
                result = self._future.result()
            except Exception:
                result = frozenset()
            self._available = result if key == self._key else frozenset()
            self._future = None
        if key != self._key:
            self._available = frozenset()
        if self._future is None:
            self._key = key
            self._future = self._worker.submit(check, session, manifest, pending)
        return self._available

    def close(self):
        with self._lock:
            self._closed = True
        self._worker.shutdown(wait=True, cancel_futures=True)
