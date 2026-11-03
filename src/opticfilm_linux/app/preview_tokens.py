"""Event-driven preview versions; file metadata is read only by status workers."""

import threading


class PreviewTokens:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._known = frozenset()
        self._dirty = set()
        self._tokens = {}

    def invalidate(self, number: int, position: int) -> None:
        with self._lock:
            self._dirty.add((number, position))

    def read(self, store, manifest) -> dict:
        keys = frozenset(
            (tray.number, slide.position) for tray in manifest.trays for slide in tray.slides
        )
        with self._lock:
            pending = (keys - self._known) | (self._dirty & keys)
            self._known = keys
            self._dirty.difference_update(pending)
        versions = {key: self._read_slide(store, *key) for key in pending}
        with self._lock:
            self._tokens = {key: value for key, value in self._tokens.items() if key[:2] in keys}
            for key, tokens in versions.items():
                self._tokens.pop(key, None)
                self._tokens.pop((*key, "before"), None)
                self._tokens.update(tokens)
            return dict(self._tokens)

    @staticmethod
    def _read_slide(store, number, position) -> dict:
        tokens = {}
        for kind in ("before", "after"):
            key = (number, position) if kind == "after" else (number, position, kind)
            try:
                tokens[key] = store.preview_path(number, position, kind).stat().st_mtime_ns
            except OSError:
                continue
        return tokens
