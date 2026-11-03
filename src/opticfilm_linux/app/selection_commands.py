"""Selection gestures and bulk review commands; storage work stays on workers."""

import dataclasses
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from opticfilm_linux.app import selection
from opticfilm_linux.app.state import Banner, selection_fields
from opticfilm_linux.batch import review
from opticfilm_linux.batch.events import SlideUpdated
from opticfilm_linux.batch.export import clear_exported
from opticfilm_linux.batch.model import SlideSettings, SlideStatus, TrayStage


class SelectionCommands:
    """Mixin for Controller: relies on its _lock, _state, _selected, _manifest and writers."""

    def select_slide(self, number: int, position: int, control=False, shift=False) -> None:
        key = (number, position)
        with self._lock:
            if key not in self._selection_order:
                return
            if shift:
                new = selection.extend(
                    self._selection, key, self._selection_order, additive=control
                )
            elif control:
                new = selection.toggle(self._selection, key)
            else:
                new = selection.click(self._selection, key)
            self._publish_selection(new, number)
        self._kick_refresh()

    def select_all(self) -> None:
        with self._lock:
            self._publish_selection(selection.select_all(self._selection_order))
        self._kick_refresh()

    def clear_selection(self) -> None:
        with self._lock:
            self._publish_selection(selection.clear())
        self._kick_refresh()

    def select_tray_slides(self, number: int, additive: bool = False, shift: bool = False) -> None:
        with self._lock:
            order = self._selection_order
            if not any(key[0] == number for key in order):
                return
            anchor = self._selection.anchor
            if shift and anchor in order:
                low, high = sorted((anchor[0], number))
                keys = frozenset(key for key in order if low <= key[0] <= high)
                focus = next(key for key in order if key[0] == number)
                new = selection.Selection(
                    keys | self._selection.keys if additive else keys, anchor, focus
                )
            else:
                new = selection.select_tray(self._selection, number, order, additive)
            self._publish_selection(new, number)
        self._kick_refresh()

    def select_where(self, name: str) -> None:
        if name not in ("flagged", "failed"):
            return
        with self._lock:
            matches = self._filter_selection_keys(name)
            new = selection.select_where(self._selection_order, matches.__contains__)
            self._publish_selection(new, new.focus[0] if new.focus else None)
        self._kick_refresh()

    def _filter_selection_keys(self, name):
        matches = set()
        for tray in self._manifest.trays if self._manifest else ():
            for slide in tray.slides:
                flagged = (
                    bool(slide.flags)
                    or slide.position in tray.auto_flagged
                    or slide.status is SlideStatus.BLANK
                )
                failed = (
                    slide.position in tray.auto_failed
                    or tray.stage is TrayStage.FAILED
                    or (tray.auto == "failed" and not tray.auto_failed)
                )
                if flagged if name == "flagged" else failed:
                    matches.add((tray.number, slide.position))
        return matches

    def move_focus(self, delta: int, extend: bool = False) -> None:
        with self._lock:
            new = selection.move_focus(self._selection, delta, self._selection_order, extend)
            self._publish_selection(new, new.focus[0] if new.focus else None)
        self._kick_refresh()

    def _publish_selection(self, new, tray: int | None = None) -> None:
        """Swap the selection and patch the published state from the cached manifest.

        Called with the lock held: in-memory work only, no store or disk access.
        """
        self._selection = new
        if tray is not None:
            self._selected = tray
        self._state = dataclasses.replace(
            self._state,
            selected=self._selected,
            **selection_fields(self._manifest, new),
        )

    def _selection_keys(self):
        with self._lock:
            return self._selection.keys

    def set_selection_status(self, status: SlideStatus) -> None:
        keys = self._selection_keys()
        if not keys:
            return

        def job(runner):
            result = runner.edit_many(keys, status=status)
            self._record_bulk_result(result, "Change acceptance")
            with self._lock:
                self._auto_due.update(number for number, _ in result.before)
            self._submit_auto_due()

        self._run_selection_writer(job, "Could not change selected slides")

    def adjust_selection(self, changes: Mapping[str, object]) -> None:
        self._edit_selection(dict(changes))

    def reset_selection(self) -> None:
        self._edit_selection(SlideSettings())

    def rotate_selection(self, quarter_turns: int) -> None:
        def rotate(settings):
            step = -90 if settings.mirror else 90
            return dataclasses.replace(
                settings, rotation=(settings.rotation + step * quarter_turns) % 360
            )

        self._edit_selection(rotate)

    def mirror_selection(self) -> None:
        self._edit_selection(
            lambda settings: dataclasses.replace(settings, mirror=not settings.mirror)
        )

    def _edit_selection(self, changes) -> None:
        keys = self._selection_keys()
        if not keys:
            return

        def job(runner):
            result = runner.edit_many(keys, changes)
            self._record_bulk_result(result, "Adjust selected slides")

        self._run_selection_writer(job, "Could not adjust selected slides")

    def toggle_selection_accepted(self) -> None:
        keys = self._selection_keys()
        if not keys:
            return

        def job(runner):
            slides = (
                slide
                for tray in runner._store.snapshot().trays
                for slide in tray.slides
                if (tray.number, slide.position) in keys
            )
            accepted = all(slide.status is SlideStatus.ACCEPTED for slide in slides)
            status = SlideStatus.PENDING if accepted else SlideStatus.ACCEPTED
            result = runner.edit_many(keys, status=status)
            self._record_bulk_result(result, "Change acceptance")
            with self._lock:
                self._auto_due.update(number for number, _ in result.before)
            self._submit_auto_due()

        self._run_selection_writer(job, "Could not change selected slides")

    def _run_selection_writer(self, job, message):
        requested = self._session

        def guarded(runner):
            if self._session is requested:
                job(runner)

        return self._run_writer(guarded, message)

    def _selection_undo_label(self):
        if not hasattr(self, "_undo") or self._undo_session is not self._session:
            self._clear_selection_undo()
        return self._undo[-1].label if self._undo else ""

    def _clear_selection_undo(self):
        self._undo_session = self._session
        self._undo = ()

    def _record_bulk_result(self, result, label):
        with self._lock:
            self._selection_undo_label()
            if result.before:
                self._undo = selection.push_undo(
                    self._undo, selection.UndoEntry(label, result.before)
                )
        self._bulk_result(result)

    def undo(self) -> None:
        def job(runner):
            with self._lock:
                self._selection_undo_label()
                entry = self._undo[-1] if self._undo else None
            if entry is None:
                return
            result = review.restore_many(runner._store, entry.before)
            runner._queue_stale_previews()
            for number, position in result.before:
                runner._emit(SlideUpdated(number, position))
            with self._lock:
                self._undo, _ = selection.pop_undo(self._undo)
                self._auto_due.update(number for number, _ in result.before)
            self._bulk_result(result)
            self._submit_auto_due()

        self._run_selection_writer(job, "Could not undo selected slides")

    def reexport_selection(self) -> None:
        from opticfilm_linux.app import controller

        requested = self._session
        keys = self._selection_keys()
        if not keys:
            return

        def job(_runner):
            if self._session is not requested:
                return
            store = requested.store
            manifest = store.snapshot()
            defaults = manifest.defaults
            if not defaults.export_dir:
                self._set(_banner=Banner("Choose an export folder before re-exporting."))
                return
            chosen, skipped = self._reexport_keys(manifest, keys)
            self._bulk_result(SimpleNamespace(skipped=skipped))
            if not chosen:
                return
            clear_exported(store, chosen)
            controller.run_export(
                store,
                Path(defaults.export_dir),
                defaults.export_format,
                self._cancel_export,
                lambda view: self._set(_export=view),
                keys=chosen,
                jpeg_size=defaults.jpeg_size,
            )

        if not self._closing.is_set():
            self._cancel_export.clear()
        self._after_pending_commands(job, "Could not re-export selected slides")

    @staticmethod
    def _reexport_keys(manifest, keys):
        chosen, skipped = set(), dict.fromkeys(keys, "missing")
        for tray in manifest.trays:
            for slide in tray.slides:
                key = tray.number, slide.position
                if key not in keys:
                    continue
                if tray.raw_removed:
                    skipped[key] = "raw_removed"
                elif tray.stage not in (TrayStage.REVIEW, TrayStage.EXPORTED):
                    skipped[key] = "not_editable"
                elif slide.status is not SlideStatus.ACCEPTED:
                    skipped[key] = "not_accepted"
                else:
                    chosen.add(key)
                    del skipped[key]
        return frozenset(chosen), skipped

    def _bulk_result(self, result) -> None:
        if not result.skipped:
            return
        reasons = {
            "raw_removed": "their raw scans were removed",
            "blank": "they are blank",
            "not_editable": "their trays are not ready for review",
            "missing": "they no longer exist",
            "not_accepted": "they are not accepted",
        }
        groups = Counter(result.skipped.values())
        detail = "; ".join(
            f"{count}: {reasons[reason]}" for reason, count in sorted(groups.items())
        )
        trays = ", ".join(map(str, sorted({number for number, _ in result.skipped})))
        self._set(
            _banner=Banner(
                f"{len(result.skipped)} selected slides left unchanged in trays {trays}: {detail}."
            )
        )
