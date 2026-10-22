import tifffile

from opticfilm_linux.output import OutputSpec
from opticfilm_linux.pipeline.run import ProcessOptions, process_sweep
from synthetic import make_sweep, truth_boxes


def test_four_slides_written(tmp_path):
    sweep = make_sweep(dpi=600, dust=12)
    result = process_sweep(sweep, ProcessOptions(), OutputSpec(directory=tmp_path))
    assert len(result.written) == 4 and result.blank_skipped == 0
    for path, box in zip(result.written, truth_boxes(600), strict=True):
        image = tifffile.imread(path)
        assert image.dtype == "uint16" and image.shape[2] == 3
        assert abs(image.shape[0] - box.h) <= 2


def test_partial_holder_skips_blank_windows(tmp_path):
    sweep = make_sweep(dpi=600, filled=(True, False, True, True))
    result = process_sweep(sweep, ProcessOptions(), OutputSpec(directory=tmp_path))
    assert len(result.written) == 3 and result.blank_skipped == 1


def test_skip_blank_false_writes_all_four(tmp_path):
    sweep = make_sweep(dpi=600, filled=(True, False, True, True))
    options = ProcessOptions(skip_blank=False)
    result = process_sweep(sweep, options, OutputSpec(directory=tmp_path))
    assert len(result.written) == 4 and result.blank_skipped == 0


def test_no_ir_sweep_still_processes(tmp_path):
    sweep = make_sweep(dpi=600, ir=False)
    result = process_sweep(sweep, ProcessOptions(), OutputSpec(directory=tmp_path))
    assert len(result.written) == 4


def test_negative_mode_runs(tmp_path):
    result = process_sweep(
        make_sweep(dpi=600), ProcessOptions(mode="negative"), OutputSpec(directory=tmp_path)
    )
    assert len(result.written) == 4
