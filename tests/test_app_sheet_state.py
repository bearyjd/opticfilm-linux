"""The state and preview APIs expose every tray even when another tray is selected."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.selection import Selection
from opticfilm_linux.app.state import build_state
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideSettings,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.scanner.base import ScannerStatus


def _manifest():
    return BatchManifest(
        "Sheet",
        trays=tuple(TrayRecord(n, TrayStage.REVIEW, slides=(SlideRecord(1),)) for n in (1, 2)),
    )


def test_preview_tokens_include_unselected_trays(tmp_path):
    store = Mock()

    def preview_path(number, position, kind):
        return tmp_path / f"{number}-{position}-{kind}.jpg"

    store.preview_path.side_effect = preview_path
    for number in (1, 2):
        preview_path(number, 1, "after").write_bytes(b"preview")
    tokens = Controller._preview_tokens(SimpleNamespace(store=store), _manifest())
    assert {(1, 1), (2, 1)} <= tokens.keys()


def test_state_contains_all_tray_cards_and_distinguishes_mixed_from_auto():
    manifest = _manifest()
    second = manifest.trays[1]
    changed = replace(second.slides[0], settings=SlideSettings(brightness_ev=0.5))
    manifest = replace(manifest, trays=(manifest.trays[0], replace(second, slides=(changed,))))
    state = build_state(
        manifest,
        preview_tokens={(1, 1): 10, (2, 1): 20},
        scanner=ScannerStatus.READY,
        scan_pending=False,
        readonly=False,
        selected=1,
        updating=frozenset(),
        progress={},
        scan_started={},
        now=0.0,
        banner=None,
        export=None,
        free_space=None,
        selection=Selection(frozenset({(1, 1), (2, 1)}), (1, 1), (2, 1)),
    )
    assert [row.slides[0].preview_token for row in state.trays] == [10, 20]
    assert state.selection_summary.trays == (2, 1)
    assert state.selection_summary.mixed == ("brightness_ev",)
    assert dict(state.selection_summary.settings)["color_strength"] is None
