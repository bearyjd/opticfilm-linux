import dataclasses

import pytest

from opticfilm_linux.batch.model import SlideRecord, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner


@pytest.mark.parametrize("stale", [False, True])
def test_resume_queues_blank_missing_fingerprint(tmp_path, monkeypatch, stale):
    store = BatchStore.create(tmp_path / "batch", name="T")
    blank = SlideRecord(1, status=SlideStatus.BLANK, preview_stale=stale)
    tray = TrayRecord(1, TrayStage.REVIEW, slides=(blank,))
    store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
    runner = BatchRunner(store, FakeScanner(tmp_path))
    assert runner._next_stale(frozenset()) == (1, 1)
    runner.close()
