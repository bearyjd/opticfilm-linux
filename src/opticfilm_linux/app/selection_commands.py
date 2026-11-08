"""Selection gestures and bulk review commands; storage work stays on workers."""

import dataclasses
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from opticfilm_linux.app import selection
from opticfilm_linux.app.state import Banner, selection_fields
from opticfilm_linux.batch import meta_bulk, review
from opticfilm_linux.batch.export import clear_exported
from opticfilm_linux.batch.metadata import checked_override
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
            new = selection.select_all(self._selection_order)
            self._publish_selection(new, new.focus[0] if new.focus else None)
        self._kick_refresh()

    def clear_selection(self) -> None:
        with self._lock:
            self._publish_selection(selection.clear())
        self._kick_refresh()

    def select_tray_slides(self, number: int, additive: bool = False, shift: bool = False) -> None:
        with self._lock:
            order = self._selection_order
            if not any(key[0] == number for key in order):
                if not self._manifest or not any(
                    tray.number == number for tray in self._manifest.trays
                ):
                    return
                trays = self._selection.trays | {number}
                new = (
                    dataclasses.replace(self._selection, trays=trays)
                    if additive
                    else selection.Selection(trays=frozenset({number}))
                )
            elif shift and self._selection.anchor in order:
                anchor = self._selection.anchor
                low, high = sorted((anchor[0], number))
                keys = frozenset(key for key in order if low <= key[0] <= high)
                focus = next(key for key in order if key[0] == number)
                new = selection.Selection(
                    keys | self._selection.keys if additive else keys,
                    anchor,
                    focus,
                    self._selection.trays if additive else frozenset(),
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
            empty = (
                selection.failed_empty_trays(self._manifest.trays)
                if name == "failed" and self._manifest
                else frozenset()
            )
            new = dataclasses.replace(new, trays=empty)
            self._publish_selection(new, new.focus[0] if new.focus else next(iter(empty), None))
        self._kick_refresh()

    def _filter_selection_keys(self, name):
        return (
            {
                (tray.number, slide.position)
                for tray in self._manifest.trays
                for slide in tray.slides
                if selection.filter_matches(tray, slide, name)
            }
            if self._manifest
            else set()
        )

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
            result = runner.toggle_many_accepted(keys)
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
        """Undo the latest render, status or metadata edit, in the order they were made."""

        def job(runner):
            with self._lock:
                self._selection_undo_label()
                entry = self._undo[-1] if self._undo else None
            if isinstance(entry, selection.MetaUndoEntry):
                self._undo_meta(runner, entry)
            elif entry is not None:
                self._undo_render(runner, entry)

        self._run_selection_writer(job, "Could not undo selected slides")

    def _undo_render(self, runner, entry) -> None:
        result = runner.restore_many(entry.before)
        with self._lock:
            self._undo, _ = selection.pop_undo(self._undo)
            self._auto_due.update(number for number, _ in result.before)
        self._bulk_result(result)
        self._submit_auto_due()

    def _undo_meta(self, runner, entry) -> None:
        # metadata needs no previews, renders or automatic work: only the manifest changes
        result = review.restore_meta_many(runner.store, entry.before)
        with self._lock:
            self._undo, _ = selection.pop_undo(self._undo)
        self._bulk_result(result)
        self._requeue_verified(runner, result.before)
        self._queue_metadata_update_if_due()
        self._kick_refresh()

    def _requeue_verified(self, runner, before) -> None:
        """Hand verified trays the edit touched back to automatic work, so a raw cleanup that
        gave up because of the edit is tried again rather than waiting for a reopen."""
        touched = {number for number, _ in before.slides} | set(before.trays)
        due = {
            t.number
            for t in runner.store.snapshot().trays
            if t.auto == "verified" and (before.batch is not None or t.number in touched)
        }
        if due:
            with self._lock:
                self._auto_due.update(due)
            self._submit_auto_due()

    def _check_meta_target(self, tray: int | None = None) -> None:
        """Refuse at once, before queueing, what could not start."""
        with self._lock:
            session, manifest = self._session, self._manifest
        if session is None:
            raise ValueError("Open a batch first")
        if session.runner is None:
            raise ValueError("This batch is open read-only")
        if tray is not None and manifest and all(t.number != tray for t in manifest.trays):
            raise ValueError(f"There is no tray {tray} in this batch")

    def set_selection_meta(self, field: str, value: str | None) -> None:
        """Set one field on every selected slide: text, ``""`` blank, ``None`` inherit.

        A value that breaks the rules, or nothing to edit, raises ValueError at once.
        """
        self.set_slides_meta(self._selection_keys(), field, value)

    def set_slides_meta(self, keys, field: str, value: str | None) -> None:
        """Like ``set_selection_meta`` for explicit ``(tray, position)`` keys: the strip passes
        the slides selected when the owner started typing, not whatever is selected now."""
        change = {field: checked_override(field, value)}
        self._check_meta_target()
        keys = frozenset((int(tray), int(position)) for tray, position in keys)
        if not keys:
            raise ValueError("Select at least one slide first")
        with self._lock:
            manifest = self._manifest
        known = (
            {(t.number, s.position) for t in manifest.trays for s in t.slides}
            if manifest
            else set()
        )
        if not keys <= known:
            raise ValueError("Some of those slides are no longer in this batch")
        self._run_meta_edit(lambda store: review.edit_meta_many(store, keys, change))

    def reset_selection_meta(self, field: str) -> None:
        self.set_selection_meta(field, None)

    def set_tray_meta(self, number: int, field: str, value: str | None) -> None:
        change = {field: checked_override(field, value)}
        self._check_meta_target(number)
        self._run_meta_edit(lambda store: review.edit_tray_meta(store, number, change))

    def set_batch_meta(self, field: str, value: str | None, scope: str = "inheriting") -> None:
        """Change the batch default; ``inheriting`` (the default, "Entire batch") changes every
        slide that still inherits it, ``from_now_on`` keeps existing trays as they are."""
        if scope not in review.META_SCOPES:
            raise ValueError(f"scope must be one of {', '.join(review.META_SCOPES)}")
        checked_override(field, value)
        self._check_meta_target()
        change = {field: value}
        self._run_meta_edit(lambda store: review.edit_batch_meta(store, change, scope=scope))

    def _run_meta_edit(self, edit) -> None:
        def job(runner):
            result = edit(runner.store)
            with self._lock:
                self._selection_undo_label()
                if not result.before.empty:
                    entry = selection.MetaUndoEntry("Edit metadata", result.before)
                    self._undo = selection.push_undo(self._undo, entry)
            self._bulk_result(result)
            self._requeue_verified(runner, result.before)
            if not result.before.empty:
                self._queue_metadata_update_if_due()
            self._kick_refresh()

        self._run_selection_writer(job, "Could not change the description or date")

    def _bulk_meta(self, scope, field, value, keys, tray, first, last) -> meta_bulk.BulkMetaEdit:
        return meta_bulk.BulkMetaEdit(
            scope,
            field,
            value,
            keys=frozenset((int(t), int(p)) for t, p in keys),
            tray=None if tray is None else int(tray),
            first=None if first is None else int(first),
            last=None if last is None else int(last),
        )

    def preview_meta_edit(
        self, scope, field, value, keys=(), tray=None, first=None, last=None
    ) -> str:
        """What a bulk description/date edit would change, e.g. "Changes 148 slides", before it
        is applied. Raises ValueError with the reason when it would be refused."""
        edit = self._bulk_meta(scope, field, value, keys, tray, first, last)
        with self._lock:
            manifest = self._manifest
        if manifest is None:
            raise ValueError("Open a batch first")
        try:
            return meta_bulk.plan(manifest, edit).summary()
        except LookupError as exc:  # a tray or slide that is not in the batch
            raise ValueError(str(exc)) from exc

    def apply_meta_edit(
        self, scope, field, value, keys=(), tray=None, first=None, last=None
    ) -> None:
        """A bulk description/date edit (see ``batch.meta_bulk`` for the scopes) as one write and
        one undo step. Refused at once, before anything is queued, when it cannot apply."""
        edit = self._bulk_meta(scope, field, value, keys, tray, first, last)
        self._check_meta_target(edit.tray)
        self.preview_meta_edit(scope, field, value, keys, tray, first, last)
        self._run_meta_edit(lambda store: meta_bulk.apply(store, edit)[0])

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
            self._bulk_result(review.BulkEditResult(skipped, {}))
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
            "skipped": "their openings were not scanned",
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
