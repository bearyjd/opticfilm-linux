"""The hardware ladder is the one place that says what unlocks each off-by-default switch."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = ROOT / "docs" / "hardware-checklist.md"
ANCHOR = "hardware-ladder-for-off-by-default-switches"
LINKING_DOCS = ("ROADMAP.md", "ENV.md", "PRODUCTION.md")


def test_ladder_lists_every_rung_and_switch():
    text = CHECKLIST.read_text()
    assert "## Hardware ladder for off-by-default switches" in text
    for rung in ("H0", "H1", "H1b", "H2", "H3", "H4", "H5", "H6", "H7", "H8"):
        assert re.search(rf"^\| {rung} \|", text, re.M), rung
    for switch in (
        "OPTICFILM_SKIP_EMPTY",
        "OPTICFILM_SKIP_GAPS",
        "OPTICFILM_AUTO_EJECT",
        "OPTICFILM_LOAD_AND_SCAN",
    ):
        assert switch in text, switch


def test_docs_link_to_ladder_not_to_local_plan():
    for name in LINKING_DOCS:
        text = (ROOT / "docs" / name).read_text()
        assert f"hardware-checklist.md#{ANCHOR}" in text, name
        assert "empty-slots plan" not in text, name
    select = (ROOT / "src/opticfilm_linux/scanner/select.py").read_text()
    assert "docs/hardware-checklist.md" in select
    assert "plan rungs" not in select
