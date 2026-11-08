"""Bulk description and date edits: which slides and trays an edit reaches, and how many.

Everything here is pure: ``plan`` turns a manifest and a ``BulkMetaEdit`` into the new manifest,
what an undo needs and the counts the owner is shown before applying; ``apply`` runs one plan in
one ``BatchStore.update``, so a bulk edit is one write and one undo step. Only description and
date values (``meta`` on slides and trays, ``description`` and ``date`` on the batch) are ever
changed: never settings, status, owner decisions or anything the render signature reads.

Scopes (``BulkMetaEdit.scope``):

- ``batch``: the batch default, so every slide still inheriting it changes, in all trays.
- ``from_now_on``: trays that inherit the old value keep it (stamped at tray level), then the
  batch default changes; only trays scanned later see the new value.
- ``slides`` / ``tray``: the given slide keys / one tray's own value.
- ``tray_range``: the tray's own value on every existing tray numbered ``first``..``last``.
- ``empty_only``: slide level, on slides that resolve to nothing for the field and have no value
  at any level (an explicit blank on a slide or tray is an owner decision and is kept; a blank
  batch value cannot be told from none, so it counts as empty).
- ``append`` (description only): ``"; " + text`` added at slide level to what each target
  resolves to (selected ``keys``, one ``tray``, or every slide). Slide level is the one place
  that changes no other slide; a tray or batch level append would also change slides that
  inherit from it and have different text. Refused whole if any target would pass the cap.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from opticfilm_linux.batch.metadata import (
    FIELDS,
    MAX_DESCRIPTION,
    checked_override,
    resolve,
    validate_description,
)
from opticfilm_linux.batch.model import BatchManifest, SlideKey
from opticfilm_linux.batch.review import (
    BeforeMeta,
    MetaEditResult,
    _batch_value,
    _find,
    _slide_metas,
    _with_tray_metas,
    batch_meta_change,
)
from opticfilm_linux.batch.store import BatchStore

SCOPES = ("batch", "from_now_on", "slides", "tray", "tray_range", "empty_only", "append")
APPEND_SEPARATOR = "; "


@dataclass(frozen=True)
class BulkMetaEdit:
    """``value``: text, ``""`` to blank, ``None`` to inherit again (slides, tray, tray_range)."""

    scope: str
    field: str
    value: str | None
    keys: frozenset[SlideKey] = frozenset()
    tray: int | None = None
    first: int | None = None
    last: int | None = None


@dataclass(frozen=True)
class BulkMetaPlan:
    manifest: BatchManifest
    before: BeforeMeta
    skipped: Mapping[SlideKey, str] = field(default_factory=lambda: MappingProxyType({}))
    slides_changed: int = 0  # slides whose resolved value for the field differs afterwards
    trays_changed: int = 0  # trays whose own value is written (stamped, set or reset)
    batch_changed: bool = False

    def summary(self) -> str:
        """What the owner is told before applying."""
        parts = []
        if self.slides_changed:
            parts.append(f"{self.slides_changed} slide{'' if self.slides_changed == 1 else 's'}")
        if self.trays_changed:
            parts.append(f"{self.trays_changed} tray{'' if self.trays_changed == 1 else 's'}")
        if self.batch_changed and not self.slides_changed:
            parts.append("the batch default for trays scanned from now on")
        return "Changes " + " and ".join(parts) if parts else "Changes nothing"


def _text(manifest: BatchManifest, edit_field: str, tray, slide) -> str:
    meta = resolve(manifest, tray, slide)
    if edit_field == "description":
        return meta.description
    return meta.date.text() if meta.date else ""


def _resolved(manifest: BatchManifest, edit_field: str) -> dict[SlideKey, str]:
    return {
        (t.number, s.position): _text(manifest, edit_field, t, s)
        for t in manifest.trays
        for s in t.slides
    }


def _checked(edit: BulkMetaEdit) -> dict[str, str | None]:
    return {edit.field: checked_override(edit.field, edit.value)}


def _plan_batch(manifest: BatchManifest, edit: BulkMetaEdit):
    values = {edit.field: _batch_value(edit.field, edit.value)}
    changed, before = batch_meta_change(manifest, values, edit.scope)
    return changed, before, {}


def _plan_slides(manifest: BatchManifest, edit: BulkMetaEdit):
    if not edit.keys:
        raise ValueError("Select at least one slide first")
    values = _checked(edit)
    new, skipped, before = _slide_metas(
        manifest, edit.keys, lambda key, meta: dataclasses.replace(meta, **values)
    )
    return new, BeforeMeta(slides=before), skipped


def _plan_trays(manifest: BatchManifest, edit: BulkMetaEdit, numbers: list[int]):
    values = _checked(edit)
    metas = {}
    before = {}
    for t in manifest.trays:
        if t.number in numbers:
            meta = dataclasses.replace(t.meta, **values)
            if meta != t.meta:
                metas[t.number], before[t.number] = meta, t.meta
    return _with_tray_metas(manifest, metas), BeforeMeta(trays=before), {}


def _plan_tray(manifest: BatchManifest, edit: BulkMetaEdit):
    if edit.tray is None:
        raise ValueError("Choose a tray first")
    _find(manifest, edit.tray)  # a tray that is not there is refused
    return _plan_trays(manifest, edit, [edit.tray])


def _plan_range(manifest: BatchManifest, edit: BulkMetaEdit):
    if edit.first is None or edit.last is None or edit.first > edit.last:
        raise ValueError("Choose the first and last tray, the first not after the last")
    numbers = [t.number for t in manifest.trays if edit.first <= t.number <= edit.last]
    if not numbers:
        raise ValueError(f"There are no trays between {edit.first} and {edit.last}")
    return _plan_trays(manifest, edit, numbers)


def _needs_text(edit: BulkMetaEdit) -> str:
    if not edit.value or not edit.value.strip():
        raise ValueError("Type the text first: this scope only fills in, it cannot blank or reset")
    return checked_override(edit.field, edit.value) or ""


def _plan_empty(manifest: BatchManifest, edit: BulkMetaEdit):
    value = _needs_text(edit)
    targets = set()
    for t in manifest.trays:
        for s in t.slides:
            meta = resolve(manifest, t, s)
            level = meta.description_level if edit.field == "description" else meta.date_level
            if level == "":  # no value and no deliberate blank at any level
                targets.add((t.number, s.position))
    new, _, before = _slide_metas(
        manifest, targets, lambda key, meta: dataclasses.replace(meta, **{edit.field: value})
    )
    return new, BeforeMeta(slides=before), {}


def _append_targets(manifest: BatchManifest, edit: BulkMetaEdit) -> set[SlideKey]:
    if edit.keys:
        return set(edit.keys)
    if edit.tray is not None:
        tray, _ = _find(manifest, edit.tray)
        return {(tray.number, s.position) for s in tray.slides}
    return {(t.number, s.position) for t in manifest.trays for s in t.slides}


def _plan_append(manifest: BatchManifest, edit: BulkMetaEdit):
    if edit.field != "description":
        raise ValueError("Add to description works on the description only")
    addition = validate_description(edit.value or "")
    if not addition:
        raise ValueError("Type the text to add first")
    now = _resolved(manifest, "description")
    joined: dict[SlideKey, str] = {}
    for key in _append_targets(manifest, edit):
        old = now.get(key)
        if old is None or old.endswith(addition):
            continue  # not there, or already says it: adding twice would only repeat it
        joined[key] = f"{old}{APPEND_SEPARATOR}{addition}" if old else addition
    over = sorted(k for k, text in joined.items() if len(text) > MAX_DESCRIPTION)
    if over:
        tray, position = over[0]
        raise ValueError(
            f"{len(over)} description{'' if len(over) == 1 else 's'} would pass "
            f"{MAX_DESCRIPTION} characters (first: tray {tray}, slide {position}); "
            "nothing was changed. Use shorter text."
        )
    new, _, before = _slide_metas(
        manifest, set(joined), lambda key, meta: dataclasses.replace(meta, description=joined[key])
    )
    return new, BeforeMeta(slides=before), {}


_PLANNERS = {
    "batch": _plan_batch,
    "from_now_on": _plan_batch,
    "slides": _plan_slides,
    "tray": _plan_tray,
    "tray_range": _plan_range,
    "empty_only": _plan_empty,
    "append": _plan_append,
}


def plan(manifest: BatchManifest, edit: BulkMetaEdit) -> BulkMetaPlan:
    """The result of ``edit`` on ``manifest`` without writing anything; raises ValueError (or
    NoSuchSlide) with a plain reason when the edit is refused."""
    if edit.scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, not {edit.scope!r}")
    if edit.field not in FIELDS:
        raise ValueError(f"metadata field must be one of {', '.join(FIELDS)}, not {edit.field!r}")
    new, before, skipped = _PLANNERS[edit.scope](manifest, edit)
    old_text, new_text = _resolved(manifest, edit.field), _resolved(new, edit.field)
    return BulkMetaPlan(
        new,
        before,
        MappingProxyType(dict(skipped)),
        slides_changed=sum(1 for key, text in new_text.items() if old_text.get(key) != text),
        trays_changed=len(before.trays),
        batch_changed=before.batch is not None,
    )


def apply(store: BatchStore, edit: BulkMetaEdit) -> tuple[MetaEditResult, BulkMetaPlan]:
    """Write ``edit`` in one update: all of it or, when refused, none of it."""
    planned: list[BulkMetaPlan] = []

    def update(manifest: BatchManifest) -> BatchManifest:
        planned[:] = [plan(manifest, edit)]
        return planned[0].manifest

    store.update(update)
    return MetaEditResult(planned[0].skipped, planned[0].before), planned[0]
