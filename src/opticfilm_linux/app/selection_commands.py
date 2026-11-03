"""Selection gestures and bulk review commands; storage work stays on workers."""

import dataclasses
from collections import Counter
from collections.abc import Mapping

from opticfilm_linux.app import selection
from opticfilm_linux.app.state import Banner
from opticfilm_linux.batch.model import SlideSettings, SlideStatus


class SelectionCommands:
    def select_slide(self, number: int, position: int, control=False, shift=False) -> None:
        key = (number, position)
        with self._lock:
            if key not in self._selection_order:
                return
            if shift:
                self._selection = selection.extend(
                    self._selection, key, self._selection_order, additive=control
                )
            elif control:
                self._selection = selection.toggle(self._selection, key)
            else:
                self._selection = selection.click(self._selection, key)
            self._selected = number

    def select_all(self) -> None:
        with self._lock:
            self._selection = selection.select_all(self._selection_order)

    def clear_selection(self) -> None:
        with self._lock:
            self._selection = selection.clear()

    def select_tray_slides(self, number: int, additive: bool = False) -> None:
        with self._lock:
            self._selection = selection.select_tray(
                self._selection, number, self._selection_order, additive
            )
            self._selected = number

    def move_focus(self, delta: int, extend: bool = False) -> None:
        with self._lock:
            self._selection = selection.move_focus(
                self._selection, delta, self._selection_order, extend
            )
            if self._selection.focus:
                self._selected = self._selection.focus[0]

    def _selection_keys(self):
        with self._lock:
            return self._selection.keys

    def set_selection_status(self, status: SlideStatus) -> None:
        keys = self._selection_keys()

        def job(runner):
            result = runner.edit_many(keys, status=status)
            self._bulk_result(result)
            with self._lock:
                self._auto_due.update(number for number, _ in result.before)
            self._submit_auto_due()

        self._run_writer(job, "Could not change selected slides")

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

        def job(runner):
            edits = changes
            if callable(changes):
                edits = {
                    (tray.number, slide.position): changes(slide.settings)
                    for tray in self._open_store().snapshot().trays
                    for slide in tray.slides
                    if (tray.number, slide.position) in keys
                }
            result = runner.edit_many(keys, edits)
            self._bulk_result(result)

        self._run_writer(job, "Could not adjust selected slides")

    def _bulk_result(self, result) -> None:
        if not result.skipped:
            return
        reasons = {
            "raw_removed": "their raw scans were removed",
            "blank": "they are blank",
            "not_editable": "their trays are not ready for review",
            "missing": "they no longer exist",
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
