"""Infrared dips along strong edges of the colour image are not dust.

On real scans the infrared plane dips by 15-50% on the one or two pixels where the colour image
steps from dark to bright, while the infrared on both sides of the step is level. The median
detector flags those pixels, and repairing them paints '+'-shaped spikes along bright edges.
These frames reproduce that with synthetic data only: a step edge whose transition pixels dip in
the infrared, a scaled copy of the step leaking into the infrared, and real 1-3 px specks.
"""

import time
import tracemalloc

import cv2
import numpy as np
import pytest

from opticfilm_linux.pipeline import dust
from opticfilm_linux.pipeline.dust import defect_mask, defect_mask_for, remove_dust

FULL = 65535
DARK, BRIGHT = 0.02, 0.6  # colour levels on either side of the edges (linear, as scanned)
IR_LEVEL = 0.65
LEVEL_LEAK = 0.1  # a scaled copy of the colour step in the infrared
EDGE_DIP = 0.3  # infrared dip at the transition pixels, as a fraction of full scale
FRAME = 360
SPECK_SPACING = 30
FAR_PX = 4  # a masked pixel further than this from every speck is a false positive


def _edge_frame(
    *, dip: float = EDGE_DIP, specks: bool = True, near_edges: bool = True, seed: int = 0
):
    """Colour image, infrared plane and speck centres for a frame of bright panels on dark.

    Panels: a vertical step, a bright rectangle with corners and a diagonal edge. The colour step
    is softened by a sub-pixel blur, as the optics do; the infrared leaks a scaled copy of the step
    and dips where the colour image changes fastest. Specks (1-3 px) darken the infrared to 35% and
    the colour by 10% (faint) or 50% (dense), in flat areas and 3-4 px beside the edges.
    """
    rng = np.random.default_rng(seed)
    level = np.full((FRAME, FRAME), DARK, np.float32)
    level[:, 120:] = BRIGHT
    level[40:110, 20:90] = BRIGHT
    yy, xx = np.mgrid[0:FRAME, 0:FRAME]
    level[(yy > 200) & (xx < yy - 200)] = BRIGHT
    level = cv2.GaussianBlur(level, (0, 0), 0.6)
    gx = cv2.Sobel(level, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(level, cv2.CV_32F, 0, 1, ksize=3) / 8
    slope = np.hypot(gx, gy)
    ir = IR_LEVEL + LEVEL_LEAK * level - dip * slope / max(float(slope.max()), 1e-6)
    rgb = np.repeat(level[..., None], 3, axis=2) * (1.0, 0.9, 0.8)
    rgb = rgb + rng.normal(0, 0.003, rgb.shape)
    ir = ir + rng.normal(0, 0.004, ir.shape)
    centers = []
    if specks:
        spots = [(y, x) for y in range(15, FRAME - 10, SPECK_SPACING) for x in (60, 200, 300)]
        clear = cv2.distanceTransform((slope < 0.01 * slope.max()).astype(np.uint8), cv2.DIST_L2, 3)
        if near_edges:
            spots += [(y, 120 + side) for y in range(140, 190, 12) for side in (-4, 3, 4)]
            spots += [(30, 55), (115, 55), (75, 15), (75, 95)]  # 4-5 px outside the rectangle
        else:  # only specks well clear of every edge
            spots = [(y, x) for y, x in spots if clear[y, x] > 2 * FAR_PX + 3]
        for k, (y, x) in enumerate(spots):
            size = 1 + k % 3
            darken = 0.5 if k % 2 else 0.9
            cell = (slice(y, y + size), slice(x, x + size))
            rgb[cell] *= darken
            ir[cell] *= 0.35
            centers.append((y + size // 2, x + size // 2))
    rgb16 = (np.clip(rgb, 0, 1) * FULL).astype(np.uint16)
    ir16 = (np.clip(ir, 0, 1) * FULL).astype(np.uint16)
    return rgb16, ir16, centers


def _near(shape, centers, radius):
    marks = np.zeros(shape, np.uint8)
    for y, x in centers:
        cv2.circle(marks, (x, y), radius, 1, -1)
    return marks.astype(bool)


def _false_and_recall(mask, centers):
    false = int((mask & ~_near(mask.shape, centers, FAR_PX)).sum())
    recall = sum(bool(mask[y, x]) for y, x in centers) / len(centers)
    return false, recall


@pytest.mark.parametrize("dpi", [600, 3600])
def test_edge_dips_are_not_masked_and_specks_still_are(dpi):
    rgb, ir, centers = _edge_frame()
    false, recall = _false_and_recall(defect_mask_for(ir, dpi, visible=rgb), centers)
    assert false <= 40  # about 4400 without the visible image
    assert recall >= 0.9


@pytest.mark.parametrize("dpi", [600, 3600])
def test_edges_with_no_speck_nearby_are_not_masked_at_all(dpi):
    rgb, ir, centers = _edge_frame(near_edges=False)
    assert _false_and_recall(defect_mask_for(ir, dpi), centers)[0] > 1000
    false, recall = _false_and_recall(defect_mask_for(ir, dpi, visible=rgb), centers)
    assert false == 0
    assert recall == 1.0


@pytest.mark.parametrize("dpi", [600, 3600])
def test_edge_frame_without_a_visible_plane_still_flags_the_edges(dpi):
    """The frame really reproduces the problem: the infrared alone masks the edges."""
    rgb, ir, centers = _edge_frame()
    false, recall = _false_and_recall(defect_mask_for(ir, dpi), centers)
    assert false > 1000
    assert recall >= 0.9


def test_raw_edge_candidates_are_dropped_before_dilation():
    rgb, ir, centers = _edge_frame()
    before = _false_and_recall(defect_mask(ir, dilate_px=0), centers)[0]
    after = _false_and_recall(defect_mask(ir, dilate_px=0, visible=rgb), centers)[0]
    assert before > 1000
    assert after <= 10


def test_a_level_copy_of_the_edge_alone_is_not_masked_either_way():
    rgb, ir, _ = _edge_frame(dip=0.0, specks=False)
    assert not defect_mask_for(ir, 600).any()
    assert not defect_mask_for(ir, 600, visible=rgb).any()


def test_flat_frame_mask_is_unchanged_by_the_visible_plane():
    rng = np.random.default_rng(2)
    rgb = rng.normal(0.4 * FULL, 0.004 * FULL, (300, 300, 3))
    ir = rng.normal(0.85 * FULL, 0.005 * FULL, (300, 300))
    for k, (y, x) in enumerate((y, x) for y in range(20, 290, 25) for x in range(20, 290, 25)):
        size = 1 + k % 3
        rgb[y : y + size, x : x + size] *= 0.1 if k % 2 else 0.9
        ir[y : y + size, x : x + size] *= 0.35
    rgb, ir = rgb.astype(np.uint16), ir.astype(np.uint16)
    plain = defect_mask_for(ir, 600)
    assert plain.sum() > 0
    assert np.array_equal(defect_mask_for(ir, 600, visible=rgb), plain)


def test_a_dense_speck_on_a_bright_area_is_kept():
    """Real dust on a bright area darkens the colour by half or more: a local minimum, not an
    edge, however strong the step around it."""
    rgb = np.full((80, 80, 3), int(0.6 * FULL), np.uint16)
    ir = np.full((80, 80), int(0.7 * FULL), np.uint16)
    for size, (y, x) in zip((1, 2, 3), ((20, 20), (40, 40), (60, 60)), strict=True):
        rgb[y : y + size, x : x + size] = int(0.06 * FULL)
        ir[y : y + size, x : x + size] = int(0.2 * FULL)
    assert np.array_equal(defect_mask_for(ir, 600, visible=rgb), defect_mask_for(ir, 600))


def test_visible_none_is_byte_identical_to_the_infrared_only_mask():
    _, ir, _ = _edge_frame()
    assert np.array_equal(defect_mask_for(ir, 600, visible=None), defect_mask_for(ir, 600))
    assert np.array_equal(defect_mask(ir, visible=None), defect_mask(ir))


def test_remove_dust_leaves_bright_edges_alone():
    rgb, ir, centers = _edge_frame()
    out = remove_dust(rgb, ir, dpi=600)
    far = ~_near(ir.shape, centers, FAR_PX + dust.INPAINT_RADIUS_PX + dust.DILATE_PX)
    assert np.array_equal(out[far], rgb[far])
    assert not np.array_equal(out, rgb)  # the specks are still repaired


def test_remove_dust_without_infrared_is_still_a_copy():
    rgb, _, _ = _edge_frame()
    out = remove_dust(rgb, None, dpi=600)
    assert np.array_equal(out, rgb) and out is not rgb


def test_visible_plane_of_another_size_is_refused():
    rgb, ir, _ = _edge_frame()
    with pytest.raises(ValueError, match="visible"):
        defect_mask(ir, visible=rgb[:-1])


@pytest.mark.parametrize("shape", [(1, 9), (9, 1), (2, 2), (1, 1), (3, 5)])
def test_tiny_frames_do_not_crash(shape):
    ir = np.full(shape, int(0.8 * FULL), np.uint16)
    ir[0, 0] = int(0.1 * FULL)
    rgb = np.full((*shape, 3), int(0.5 * FULL), np.uint16)
    rgb[..., -1:, :] = int(0.02 * FULL)
    mask = defect_mask(ir, visible=rgb)
    assert mask.shape == shape


def test_grey_and_non_contiguous_visible_planes_are_accepted():
    rgb, ir, centers = _edge_frame()
    grey = np.ascontiguousarray(rgb[..., 1])
    strided = np.zeros((rgb.shape[0], rgb.shape[1] * 2, 3), np.uint16)
    strided[:, ::2] = rgb
    view = strided[:, ::2]  # a non-contiguous view of the same pixels
    assert not view.flags.c_contiguous
    assert np.array_equal(defect_mask(ir, visible=view), defect_mask(ir, visible=rgb))
    assert _false_and_recall(defect_mask(ir, dilate_px=0, visible=grey), centers)[0] <= 10


def _big_edge_frame(size: int):
    rgb, ir, _ = _edge_frame()
    reps = -(-size // FRAME)
    return (
        np.tile(rgb, (reps, reps, 1))[:size, :size],
        np.tile(ir, (reps, reps))[:size, :size],
    )


def test_edge_check_memory_is_bounded_by_strips():
    """No full-size float temporaries: a float32 copy of this frame alone would be 64 MB."""
    rgb, ir = _big_edge_frame(4000)
    candidates = np.ones(ir.shape, bool)  # worst case: every pixel a candidate
    tracing = tracemalloc.is_tracing()  # leave a caller's tracing as it was
    if not tracing:
        tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        dust._on_visible_edges(rgb, candidates)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        if not tracing:
            tracemalloc.stop()
    assert peak < ir.size + 24 * 1024 * 1024


def _best_of_3(run) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    return min(times)


@pytest.mark.parametrize("dpi", [3600, 7200])
def test_edge_check_costs_little_next_to_the_median(dpi):
    rgb, ir = _big_edge_frame(2400)
    plain = _best_of_3(lambda: defect_mask_for(ir, dpi))
    with_visible = _best_of_3(lambda: defect_mask_for(ir, dpi, visible=rgb))
    assert with_visible < 2 * plain + 1.0


@pytest.mark.parametrize("rows", [1, 7, FRAME])
def test_mask_does_not_depend_on_the_strip_height(rows, monkeypatch):
    rgb, ir, _ = _edge_frame()
    expected = defect_mask(ir, dilate_px=0, visible=rgb)
    monkeypatch.setattr(dust, "EDGE_STRIP_ROWS", rows)
    assert np.array_equal(defect_mask(ir, dilate_px=0, visible=rgb), expected)
    assert np.array_equal(
        dust._on_visible_edges(rgb, np.ones(ir.shape, bool)),
        _edges_in_one_strip(rgb, monkeypatch),
    )


def _edges_in_one_strip(rgb, monkeypatch):
    with monkeypatch.context() as whole:
        whole.setattr(dust, "EDGE_STRIP_ROWS", rgb.shape[0])
        return dust._on_visible_edges(rgb, np.ones(rgb.shape[:2], bool))


def _ir_specks_only(visible: np.ndarray) -> np.ndarray:
    """A level infrared plane with 1 px dips that leave ``visible`` untouched (a scratch in the
    base, say): whatever the visible image does there, they are defects."""
    ir = np.full(visible.shape[:2], int(0.7 * FULL), np.uint16)
    ir[20:-20:15, 20:-20:15] = int(0.2 * FULL)
    return ir


def test_specks_on_a_steep_low_contrast_ramp_are_kept():
    """1% of full scale a pixel is steep, but far from the 2x contrast of an edge."""
    xx = np.arange(80, dtype=np.float64)[None, :] * np.ones((80, 1))
    visible = ((0.4 + 0.01 * xx) * FULL).astype(np.uint16)
    ir = _ir_specks_only(visible)
    assert np.array_equal(defect_mask(ir, visible=visible), defect_mask(ir))


def test_specks_on_near_black_texture_are_kept():
    """Near black, noise and grain double the level within a pixel or two, yet the step stays
    below EDGE_FLOOR: no edge (a sawtooth of x1.5 a pixel, from 100 to 1708 of 65535)."""
    xx = np.arange(80)[None, :] * np.ones((80, 1), int)
    visible = (100 * 1.5 ** (xx % 8)).astype(np.uint16)
    ir = _ir_specks_only(visible)
    assert np.array_equal(defect_mask(ir, visible=visible), defect_mask(ir))


@pytest.mark.parametrize("line", [0.05, 0.9], ids=["darker-line", "brighter-line"])
def test_a_line_beside_an_edge_that_is_not_between_its_sides_is_kept(line):
    """A one-pixel line running along a step, darker or brighter than both sides, is a visible
    feature of its own, not the transition: an infrared dip on it is kept."""
    visible = np.full((80, 80), int(0.2 * FULL), np.uint16)
    visible[:, :40] = int(0.6 * FULL)
    visible[:, 40] = int(line * FULL)
    ir = np.full((80, 80), int(0.7 * FULL), np.uint16)
    ir[40, 40] = int(0.2 * FULL)
    assert defect_mask(ir, dilate_px=0, visible=visible)[40, 40]


@pytest.mark.parametrize("block", [(4, 4), (6, 6), (4, 50), (50, 4), (12, 12)])
@pytest.mark.parametrize("dpi", [600, 3600, 7200])
def test_dense_plateau_specks_and_scratches_are_kept_whole(block, dpi):
    """A speck or scratch that darkens the colour evenly is a plateau, not a single-pixel
    minimum: every pixel has a dark neighbour inside it. Its pixels must all stay masked."""
    rgb = np.full((140, 140, 3), 40_000, np.uint16)
    ir = np.full((140, 140), 45_000, np.uint16)
    rows, cols = slice(40, 40 + block[0]), slice(40, 40 + block[1])
    rgb[rows, cols] = 5_000
    ir[rows, cols] = 10_000
    plain = defect_mask_for(ir, dpi)
    assert plain[rows, cols].all()
    assert np.array_equal(defect_mask_for(ir, dpi, visible=rgb), plain)


def test_a_speck_beside_an_edge_is_kept():
    """One pixel into the bright side: darker than both its neighbours, so a speck."""
    visible = np.full((80, 80), 40_000, np.uint16)
    visible[:, :40] = 2_000
    visible[40, 41] = 20_000
    ir = np.full((80, 80), 45_000, np.uint16)
    ir[40, 41] = 10_000
    assert defect_mask(ir, dilate_px=0, visible=visible)[40, 41]


def test_a_speck_on_the_transition_pixel_itself_is_given_up():
    """Known limit: dust sitting exactly on a strong edge's transition pixel looks, in the
    colour image, like the transition itself, so it is left alone, as the edge dips are."""
    visible = np.full((80, 80), 40_000, np.uint16)
    visible[:, :40] = 2_000
    visible[40, 40] = 20_000
    ir = np.full((80, 80), 45_000, np.uint16)
    ir[40, 40] = 10_000
    assert defect_mask(ir, dilate_px=0)[40, 40]
    assert not defect_mask(ir, dilate_px=0, visible=visible)[40, 40]


@pytest.mark.parametrize(
    "visible",
    [
        np.zeros((10, 10), np.uint8),
        np.zeros((10, 10), np.float32),
        np.zeros((10, 10, 4), np.uint16),
        np.zeros((10, 10, 3, 1), np.uint16),
    ],
    ids=["uint8", "float", "4-channel", "4-d"],
)
def test_visible_planes_off_the_16_bit_scale_or_shape_are_refused(visible):
    ir = np.full((10, 10), 45_000, np.uint16)
    with pytest.raises(ValueError, match="visible"):
        defect_mask(ir, visible=visible)


def test_a_faint_speck_two_pixels_from_an_edge_is_kept():
    """5% darker than the bright side, so the plateau rescue does not apply; but darker than
    each of its four neighbours, which is enough for a speck that a reach of 2 px puts on the
    edge."""
    visible = np.full((80, 80), 40_000, np.uint16)
    visible[:, :40] = 2_000
    visible[40, 41] = 38_000
    ir = np.full((80, 80), 45_000, np.uint16)
    ir[40, 41] = 10_000
    assert defect_mask(ir, dilate_px=0, visible=visible)[40, 41]


def _rescued(inside: int, around: int, size: int = 4, widest: int = 21) -> bool:
    """Is a ``size`` square defect of visible level ``inside`` on ``around``, all of it flagged
    as on an edge, rescued as a speck?"""
    visible = np.full((60, 60), around, np.uint16)
    mask = np.zeros((60, 60), np.uint8)
    cell = (slice(20, 20 + size), slice(20, 20 + size))
    visible[cell], mask[cell] = inside, 1
    return bool(dust._in_darker_regions(visible, mask, mask.astype(bool), widest)[cell].all())


def test_only_a_clearly_darker_defect_is_rescued_as_a_speck():
    assert _rescued(34_000, 40_000)  # 15% darker
    assert not _rescued(37_000, 40_000)  # 7.5%: within what noise and shading do
    assert not _rescued(1_500, 2_000)  # 25% darker, but by less than SPECK_FLOOR near black
    assert _rescued(500, 2_000)


def test_defects_wider_than_the_median_window_both_ways_are_not_rescued():
    assert _rescued(5_000, 40_000, size=21, widest=21)
    assert not _rescued(5_000, 40_000, size=22, widest=21)
