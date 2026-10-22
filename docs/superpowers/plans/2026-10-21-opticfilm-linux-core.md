# opticfilm-linux Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested Python package and CLI (`ofscan`) that takes one whole-holder sweep from a four-slide 35mm holder, splits it into slides, enhances each, and writes finished files, all runnable without the scanner, plus read-only detection of the real device.

**Architecture:** A `Scanner` protocol hides the hardware; `FakeScanner` replays stored sweeps. A pure-function pipeline (split, dust removal, exposure and colour restoration, grain reduction, sharpening) turns a `RawSweep` into per-slide arrays; an output writer saves them. Real scanning is deliberately not implemented here: the community driver has no slide-holder sweep yet (see spec), so the real backend raises `NotSupportedYet`.

**Tech Stack:** Python 3.11+, `uv`, numpy, Pillow, tifffile, opencv-python-headless, pytest + pytest-cov, ruff, argparse (stdlib).

**Spec:** `docs/superpowers/specs/2026-10-21-plustek-slide-scanner-design.md` (approved by the owner 2026-10-21; the owner scans mounted 35mm slides in four-slide holders).

## Global Constraints

- Package `opticfilm_linux` under `src/`, CLI entry point `ofscan`; Python managed with `uv`.
- Licence GPL-3.0-or-later.
- Immutable data flow: every pipeline step returns a new array and never modifies its input; dataclasses are `frozen=True`.
- Tests first (RED, then GREEN); coverage gate 80% (`--cov-fail-under=80`).
- Functions under 50 lines, files under 800 lines, no `print()` outside `cli.py`.
- No vendor material in the repo: no Plustek code, binaries, firmware, ICC profiles, documentation, config values, or captures. `.local-research/` is git-ignored and must stay so.
- Hardware tests carry `@pytest.mark.hardware` and are excluded from the default run (`-m "not hardware"`) and from CI.
- Processing is per-slide and memory-bounded: sweeps load memory-mapped and read-only; the default working resolution is 3600 dpi, 7200 dpi is opt-in.
- This plan sends nothing to the scanner. Device detection reads sysfs only.
- No `git push` and no GitHub repo creation without the owner's explicit instruction (Task 11).

## Review Focus

Failure modes the spec implies that a user will meet (each has a test in the owning task):

1. **Partly loaded holder** (blank windows): blank slides are detected and skipped, never written as white files. Task 4, Task 9.
2. **Dense or very dark slide** (and an all-black frame): no NaN, no division by zero, no mass clipping. Task 5.
3. **Sweep without an infrared plane** (infrared off): dust removal is skipped silently, splitting falls back to luminance. Tasks 4, 6.
4. **Re-running into the same folder**: existing files are never overwritten. Task 8.
5. **Truncated or corrupt stored sweep** (interrupted scan): a clear error message and exit code 2, not a traceback. Tasks 2, 9.

---

### Task 1: Project skeleton

**Files:**
- Create: `pyproject.toml`, `.gitignore` (extend existing: keep `.local-research/`), `LICENSE` (GPL-3.0 text), `README.md` (status paragraph only), `src/opticfilm_linux/__init__.py`, `src/opticfilm_linux/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `opticfilm_linux.cli.main(argv: list[str] | None = None) -> int`; `__version__: str = "0.1.0"`.

- [ ] **Step 1: Write the failing test** in `tests/test_cli.py`: `test_version_flag_prints_version(capsys)` calls `main(["--version"])` inside `pytest.raises(SystemExit)` with code 0 and asserts `"ofscan 0.1.0" in capsys.readouterr().out`.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `cli.main` with argparse and the `ofscan` script entry in `pyproject.toml`; configure ruff, pytest (`-m "not hardware"` default, `hardware` marker declared) and coverage (`--cov=opticfilm_linux --cov-fail-under=80` in CI only, not the default, so early tasks pass). Run `git init -b main` and make `.gitignore` also ignore `.venv/`, `__pycache__/`, `*.egg-info/`.
- [ ] **Step 4: Run** `uv run pytest -v && uv run ruff check .`. Expected: PASS, no lint errors.
- [ ] **Step 5: Add CI** (`ci.yml`: checkout, `astral-sh/setup-uv`, `uv sync`, `uv run ruff check .`, `uv run pytest --cov=opticfilm_linux --cov-fail-under=80`). Commit: `chore: project skeleton, CLI entry point and CI`.

### Task 2: Data model and sweep storage

**Files:**
- Create: `src/opticfilm_linux/model.py`, `src/opticfilm_linux/sweepstore.py`, `tests/test_model.py`, `tests/test_sweepstore.py`

**Interfaces:**
- Produces (`model.py`):
  - `class HolderKind(str, Enum)`: `SLIDE4 = "slide4"`, `STRIP6 = "strip6"`.
  - `@dataclass(frozen=True) class ScanSettings`: `dpi: int = 3600`, `mode: Literal["positive","negative"] = "positive"`, `infrared: bool = True`.
  - `@dataclass(frozen=True) class Box`: `x: int, y: int, w: int, h: int` (pixels; `y` runs along the sweep).
  - `@dataclass(frozen=True) class RawSweep`: `rgb: np.ndarray` (H, W, 3, uint16; H is the long axis), `ir: np.ndarray | None` (H, W, uint16), `dpi: int`, `holder: HolderKind`. `__post_init__` raises `ValueError` if `rgb` is not (H, W, 3) uint16 or `ir` is not (H, W) uint16.
  - `@dataclass(frozen=True) class Slide`: `index: int, rgb: np.ndarray, ir: np.ndarray | None, box: Box`.
- Produces (`sweepstore.py`): `class SweepFormatError(ValueError)`; `save_sweep(sweep: RawSweep, directory: Path) -> None` (writes `rgb.npy`, optional `ir.npy`, `meta.json` with `dpi` and `holder`); `load_sweep(directory: Path) -> RawSweep` (arrays memory-mapped read-only).

- [ ] **Step 1: Write failing tests**: `test_sweep_roundtrip` (save then load; arrays equal, dtype `uint16`, `dpi` and `holder` equal); `test_loaded_arrays_are_memory_mapped_and_read_only` (`isinstance(loaded.rgb, np.memmap)` and `loaded.rgb.flags.writeable is False`); `test_ir_is_optional` (round-trip with `ir=None` gives `loaded.ir is None`); `test_rawsweep_rejects_wrong_dtype_or_shape` (`pytest.raises(ValueError)` for float32 rgb and for an `ir` of mismatched shape); `test_truncated_rgb_file_raises_sweepformaterror` (cut `rgb.npy` to half its size; message mentions the file name); `test_missing_meta_raises_sweepformaterror`.
- [ ] **Step 2: Run** `uv run pytest tests/test_model.py tests/test_sweepstore.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules; `load_sweep` uses `np.load(mmap_mode="r")` and converts `OSError`/`ValueError`/`json.JSONDecodeError` into `SweepFormatError`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: sweep data model and memory-mapped sweep storage`.

### Task 3: Scanner protocol, fake scanner, synthetic sweeps

**Files:**
- Create: `src/opticfilm_linux/scanner/__init__.py`, `src/opticfilm_linux/scanner/base.py`, `src/opticfilm_linux/scanner/fake.py`, `tests/synthetic.py`, `tests/conftest.py`, `tests/test_fake_scanner.py`

**Interfaces:**
- Consumes: `RawSweep`, `ScanSettings` (Task 2), `save_sweep`, `load_sweep`.
- Produces (`base.py`): `class ScanError(RuntimeError)`; `class NotSupportedYet(ScanError)`; `@dataclass(frozen=True) class DeviceInfo`: `vendor_id: int, product_id: int, name: str, bus: int, address: int`; `class Scanner(Protocol)`: `scan_holder(self, settings: ScanSettings) -> RawSweep`, `eject(self) -> None`.
- Produces (`fake.py`): `class FakeScanner`: `__init__(self, sweep_dir: Path)`; implements `Scanner`; `scan_holder` raises `ScanError` if `settings.dpi != sweep.dpi` (message names both values).
- Produces (`tests/synthetic.py`): `make_sweep(dpi: int = 600, filled: tuple[bool, ...] = (True, True, True, True), dust: int = 0, ir: bool = True, tilt_deg: float = 0.0, seed: int = 0) -> RawSweep`, plus `truth_boxes(dpi: int) -> tuple[Box, ...]` (exact aperture boxes of the generated holder).

Synthetic layout (decisions the tests share): scan width 35 mm and length 226 mm → `W = round(35/25.4*dpi)`, `H = round(226/25.4*dpi)`; four apertures of 22.75 mm along the sweep and 35 mm across... drawn at pitch 62.6 mm, first aperture starting 8 mm from the sweep start; the mount between apertures is opaque (RGB and IR near 0); a filled aperture holds a smooth colour gradient image (RGB mid-range, IR high ~0.9 of full scale because film is transparent in infrared); an unfilled aperture is clear (all channels near full scale); `dust` adds that many dark specks (radius 2–4 px at 600 dpi) to the IR plane and a matching dark speck to the RGB of filled apertures; `tilt_deg` rotates each aperture's content. (These values come from the community driver's measurements on one unit, `docs/holder-geometry.md` upstream, CC BY 4.0; they are test fixtures here, to be replaced by real data.)

- [ ] **Step 1: Write failing tests** in `tests/test_fake_scanner.py`: `test_make_sweep_shape_and_dtype` (`rgb.shape == (round(226/25.4*600), round(35/25.4*600), 3)`, dtype `uint16`, `holder is HolderKind.SLIDE4`); `test_make_sweep_is_deterministic` (same seed, equal arrays); `test_fake_scanner_returns_stored_sweep` (save a synthetic sweep, `FakeScanner(dir).scan_holder(ScanSettings(dpi=600))` equals it); `test_fake_scanner_rejects_dpi_mismatch` (`pytest.raises(ScanError, match="600")` when asking for 3600); `test_fake_scanner_satisfies_protocol` (`isinstance` check via `typing.runtime_checkable` on `Scanner`).
- [ ] **Step 2: Run** `uv run pytest tests/test_fake_scanner.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the three modules and the synthetic generator (numpy only; deterministic via `np.random.default_rng(seed)`). Add a `synthetic_sweep_dir` fixture to `conftest.py` that saves `make_sweep(dpi=600, dust=12)` to `tmp_path`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: scanner protocol, fake scanner and synthetic sweeps`.

### Task 4: Slide splitting and blank detection

**Files:**
- Create: `src/opticfilm_linux/pipeline/__init__.py`, `src/opticfilm_linux/pipeline/split.py`, `tests/test_split.py`

**Interfaces:**
- Consumes: `RawSweep`, `Slide`, `Box` (Task 2); `make_sweep`, `truth_boxes` (Task 3).
- Produces: `class SlideSplitError(ValueError)`; `split_slides(sweep: RawSweep, expected: int = 4) -> tuple[Slide, ...]`; `is_blank(slide: Slide) -> bool`.

Approach (decided): find the openings along the sweep (`y`) from a 1-D profile — the infrared plane when present (opaque mount blocks infrared, film and clear windows pass it), otherwise luminance. Threshold with Otsu, take runs whose length is within ±25 % of the expected aperture length (22.75 mm), and require exactly `expected` runs; otherwise raise `SlideSplitError`. Across the sweep, crop to the full width of the aperture run. Slides are numbered from the sweep start. `is_blank`: a clear window, i.e. mean luminance above 0.85 of full scale and standard deviation below 0.02.

- [ ] **Step 1: Write failing tests**: `test_four_openings_found_at_600dpi` (each returned `box` within 2 px of `truth_boxes(600)` on every edge); `test_works_without_ir` (`make_sweep(ir=False)` still finds four, tolerance 3 px); `test_tilted_content_is_not_clipped` (`tilt_deg=2.0`: each box is at least as large as the truth box minus 2 px in `w` and `h`); `test_partial_holder_flags_blank_slides` (`filled=(True, False, True, True)` gives `[is_blank(s) for s in slides] == [False, True, False, False]`); `test_dark_slide_is_not_blank` (replace one filled aperture with a uniform dark image: `is_blank` is False); `test_too_few_openings_raises` (blank out the IR/luminance of two apertures completely, i.e. zero everything: `pytest.raises(SlideSplitError)`).
- [ ] **Step 2: Run** `uv run pytest tests/test_split.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `split_slides` and `is_blank`; slice views of the sweep arrays (no copies of the whole sweep).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: split a holder sweep into slides and detect blank windows`.

### Task 5: Exposure and colour restoration

**Files:**
- Create: `src/opticfilm_linux/pipeline/tone.py`, `tests/test_tone.py`

**Interfaces:**
- Produces: `auto_exposure(rgb: np.ndarray) -> np.ndarray` (global gain/gamma from luminance percentiles); `restore_color(rgb: np.ndarray, strength: float = 1.0) -> np.ndarray` (per-channel percentile stretch for faded colour, blended by `strength` in [0, 1]); `invert_negative(rgb: np.ndarray) -> np.ndarray` (density inversion with automatic black and white points). All take and return `uint16` (H, W, 3) arrays and never modify the input.

- [ ] **Step 1: Write failing tests**: `test_restore_color_stretches_faded_image` (a gradient squeezed into 0.3–0.7 of full scale: after restoring, the 0.1 and 99.9 percentiles of every channel are within 1 % of 0 and full scale); `test_restore_color_removes_cast` (red channel scaled 1.3×: channel means within 5 % of each other); `test_strength_zero_is_identity`; `test_clipping_is_limited` (at most 0.5 % of values at either extreme after `auto_exposure` then `restore_color` on a normal gradient); `test_all_black_frame_has_no_nan_or_error` (uniform zeros: output is finite, `uint16`, no exception, no warning); `test_very_dark_slide_is_brightened_not_blown_out` (mean 0.05 of full scale: output mean between 0.2 and 0.6, clipped fraction < 1 %); `test_input_is_not_modified` (copy before, equal after); `test_invert_negative_matches_positive` (a gradient inverted by the test, then `invert_negative`: Pearson correlation with the original above 0.95).
- [ ] **Step 2: Run** `uv run pytest tests/test_tone.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with float32 intermediates; guard zero-range channels (return the channel unchanged).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: auto exposure, colour restoration and negative inversion`.

### Task 6: Infrared dust and scratch removal

**Files:**
- Create: `src/opticfilm_linux/pipeline/dust.py`, `tests/test_dust.py`

**Interfaces:**
- Produces: `defect_mask(ir: np.ndarray, sensitivity: float = 0.15, dilate_px: int = 2) -> np.ndarray` (bool, same H, W; a pixel is a defect when the local median of `ir` exceeds it by more than `sensitivity` of full scale); `remove_dust(rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600, tile: int = 1024) -> np.ndarray` (inpaints masked pixels with `cv2.inpaint`, processed in overlapping tiles of `tile` px with a 16 px overlap; `dilate_px` scales with `dpi / 600`; returns `rgb` unchanged, as a new array, when `ir is None`).

- [ ] **Step 1: Write failing tests**: `test_defect_mask_finds_synthetic_specks` (specks from `make_sweep(dust=12)`: at least 95 % of speck centres are masked; masked area outside specks under 0.1 % of the frame); `test_remove_dust_reduces_error_inside_mask` (mean absolute error against the clean synthetic slide inside the mask drops below 30 % of the dusty value); `test_clean_ir_gives_empty_mask_and_identical_output`; `test_no_ir_returns_copy` (`remove_dust(rgb, None)` equals `rgb` and is not the same object; no exception); `test_tiling_leaves_no_seam` (output with `tile=256` and `tile=4096` differs by at most 2 % of full scale anywhere on the tile boundaries); `test_input_is_not_modified`.
- [ ] **Step 2: Run** `uv run pytest tests/test_dust.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** using `cv2.medianBlur` for the local median (on the 8-bit view of `ir`), `cv2.dilate`, and `cv2.inpaint` on an 8-bit working copy blended back at 16-bit only where masked.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: infrared dust and scratch removal`.

### Task 7: Sharpening and grain reduction

**Files:**
- Create: `src/opticfilm_linux/pipeline/detail.py`, `tests/test_detail.py`

**Interfaces:**
- Produces: `sharpen(rgb: np.ndarray, radius_px: float = 1.5, amount: float = 0.5) -> np.ndarray`; `reduce_grain(rgb: np.ndarray, strength: float = 0.3) -> np.ndarray` (edge-preserving, `strength` in [0, 1]; 0 returns an equal copy). Both `uint16` (H, W, 3) in and out, input never modified.

- [ ] **Step 1: Write failing tests**: `test_sharpen_increases_edge_gradient` (step edge: maximum gradient grows by at least 20 %); `test_sharpen_leaves_flat_region_unchanged` (within 1 % of full scale); `test_grain_reduction_cuts_noise_and_keeps_edge` (noisy step image: standard deviation of a flat patch drops by at least 30 % at `strength=0.6`, the edge position moves by at most 1 px); `test_strength_zero_is_identity`; `test_input_is_not_modified`.
- [ ] **Step 2: Run** `uv run pytest tests/test_detail.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** unsharp mask with `cv2.GaussianBlur` and a float32 `cv2.bilateralFilter` per channel scaled by `strength`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: sharpening and grain reduction`.

### Task 8: Output writer

**Files:**
- Create: `src/opticfilm_linux/output.py`, `tests/test_output.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class OutputSpec`: `directory: Path`, `fmt: Literal["tiff16","png8","jpeg"] = "tiff16"`, `album: str = ""`, `date: str | None = None`; `write_slide(rgb: np.ndarray, spec: OutputSpec) -> Path`. File name `{album}_{date}_{seq:04d}.{ext}` (album/date segments omitted when empty); `seq` is the lowest number above every existing matching file in `spec.directory`; the directory is created if missing; an sRGB ICC profile is embedded (TIFF via tifffile `iccprofile`, JPEG/PNG via Pillow `icc_profile`, profile bytes from `PIL.ImageCms`); `tiff16` keeps 16-bit pixels exactly, the other formats scale to 8-bit.

- [ ] **Step 1: Write failing tests**: `test_tiff16_roundtrip_is_lossless` (read back with tifffile: dtype `uint16`, equal to input); `test_icc_profile_embedded_in_tiff_and_jpeg`; `test_sequence_numbers_increase` (three writes give `..._0001`, `_0002`, `_0003`); `test_never_overwrites_existing_file` (pre-create `_0001.tif`; the next write returns `_0002` and `_0001.tif` is byte-identical afterwards); `test_two_runs_into_same_folder_keep_all_files` (two batches of four give eight distinct files); `test_album_and_date_in_name` (`"Family Trips_1975-06_0001.tif"`); `test_jpeg_is_8bit_rgb`.
- [ ] **Step 2: Run** `uv run pytest tests/test_output.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with exclusive-create (`open(path, "xb")` or `O_EXCL`) so a race cannot overwrite.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: output writer with sequence naming and sRGB profile`.

### Task 9: Pipeline orchestration and CLI

**Files:**
- Create: `src/opticfilm_linux/pipeline/run.py`, `tests/test_run.py`, `tests/test_cli_scan.py`
- Modify: `src/opticfilm_linux/cli.py`

**Interfaces:**
- Consumes: every function from Tasks 2–8.
- Produces (`run.py`): `@dataclass(frozen=True) class ProcessOptions`: `mode: Literal["positive","negative"] = "positive"`, `remove_dust: bool = True`, `restore_color: bool = True`, `sharpen_amount: float = 0.5`, `grain_strength: float = 0.3`, `skip_blank: bool = True`; `@dataclass(frozen=True) class RunResult`: `written: tuple[Path, ...]`, `blank_skipped: int`; `process_sweep(sweep: RawSweep, options: ProcessOptions, out: OutputSpec) -> RunResult`. Per slide the order is: dust removal, negative inversion (negative mode only), auto exposure, colour restoration, grain reduction, sharpening, write.
- Produces (CLI): `ofscan scan --fake SWEEP_DIR --out DIR [--album A] [--date D] [--format tiff16|png8|jpeg] [--mode positive|negative] [--dpi N]`; `ofscan process SWEEP_DIR --out DIR` (same options; no scanner object); both print `"N slides written, M blank skipped"` and return 0; any `SweepFormatError` or `ScanError` prints `error: <message>` to stderr and returns 2. `ofscan devices` is added in Task 10.

- [ ] **Step 1: Write failing tests**: in `test_run.py`: `test_four_slides_written` (synthetic sweep, `len(result.written) == 4`, all TIFFs 16-bit); `test_partial_holder_skips_blank_windows` (`filled=(True, False, True, True)`: three files, `blank_skipped == 1`); `test_skip_blank_false_writes_all_four`; `test_no_ir_sweep_still_processes` (`ir=False`: four files, no exception); `test_negative_mode_runs` (a synthetic sweep marked negative produces four files). In `test_cli_scan.py`: `test_scan_fake_end_to_end(synthetic_sweep_dir, tmp_path, capsys)` (return 0, stdout contains `"4 slides written, 0 blank skipped"`); `test_truncated_sweep_gives_exit_2_and_message` (truncate `rgb.npy`: return 2, stderr starts with `error:`, no traceback text); `test_dpi_mismatch_gives_exit_2`.
- [ ] **Step 2: Run** `uv run pytest tests/test_run.py tests/test_cli_scan.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `process_sweep` as a loop over `split_slides` results, composing the step functions; wire the `scan` and `process` subcommands.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit:** `feat: end-to-end processing pipeline and scan/process commands`.

### Task 10: Read-only device detection

**Files:**
- Create: `src/opticfilm_linux/scanner/usb.py`, `src/opticfilm_linux/scanner/community.py`, `tests/test_usb.py`, `tests/test_hardware.py`
- Modify: `src/opticfilm_linux/cli.py`

**Interfaces:**
- Consumes: `DeviceInfo`, `NotSupportedYet` (Task 3).
- Produces (`usb.py`): `KNOWN_DEVICES: dict[tuple[int, int], str] = {(0x07B3, 0x1436): "Plustek OpticFilm 135i"}`; `find_devices(sysfs_root: Path = Path("/sys/bus/usb/devices")) -> list[DeviceInfo]` (reads `idVendor`, `idProduct`, `busnum`, `devnum` and the `product` string; skips unreadable or incomplete entries; never writes or opens device nodes).
- Produces (`community.py`): `class CommunityDriverScanner`: `__init__(self, device: DeviceInfo)`; `scan_holder` and `eject` raise `NotSupportedYet` with a message that the slide-holder sweep depends on upstream `opticfilm135i-linux` support (roadmap item C2).
- CLI: `ofscan devices` lists detected known devices (`"07b3:1436 Plustek OpticFilm 135i  bus 3 address 54"`) or `"no supported scanner found"`, return 0.

- [ ] **Step 1: Write failing tests**: `test_finds_known_scanner_in_fake_sysfs` (build a fake tree under `tmp_path` with the scanner plus a phone and a hub: only the scanner is returned, with `bus` and `address` as ints); `test_ignores_entries_missing_files` (a directory with only `idVendor`: no exception, not returned); `test_empty_sysfs_returns_empty_list`; `test_community_scanner_raises_not_supported_yet` (`pytest.raises(NotSupportedYet, match="C2")`); `test_cli_devices_reports_none_found` (empty fake root via monkeypatched default). In `tests/test_hardware.py`: `@pytest.mark.hardware test_real_scanner_is_visible` asserts `find_devices()` returns at least one device with `product_id == 0x1436` (skipped by default).
- [ ] **Step 2: Run** `uv run pytest tests/test_usb.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules and the `devices` subcommand.
- [ ] **Step 4: Run** `uv run pytest -v && uv run pytest -m hardware -v`. Expected: default run PASS; the hardware run passes only with the scanner plugged in (it is read-only).
- [ ] **Step 5: Commit:** `feat: read-only detection of the OpticFilm 135i`.

### Task 11: Documentation, coverage gate, publication

**Files:**
- Create: `docs/ATTRIBUTION.md`, `docs/CONTRIBUTING.md`
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Write the docs.** `README.md`: what works (fake-scanner pipeline, device detection), what does not (no real scanning yet: depends on upstream slide-holder support), honest status, quick start (`uv sync`, `uv run ofscan scan --fake ...`). `ATTRIBUTION.md`: credit to `cgillinger/opticfilm135i-linux` for the holder geometry used in test fixtures (CC BY 4.0 docs) and for the register-interface knowledge; state that no vendor material is included. `CONTRIBUTING.md`: tests first, coverage gate, hardware marker.
- [ ] **Step 2: Run the full gate:** `uv run ruff check . && uv run pytest --cov=opticfilm_linux --cov-fail-under=80`. Expected: PASS, coverage at least 80 %.
- [ ] **Step 3: Check nothing vendor-derived is tracked:** `git ls-files | grep -i -E "local-research|\.pcap|scaninfo|\.icm"`. Expected: no output.
- [ ] **Step 4: Commit:** `docs: README, attribution and contributing guide`.
- [ ] **Step 5: STOP.** Creating the public GitHub repo (`gh repo create bearyjd/opticfilm-linux --public`) and pushing happen only on the owner's explicit instruction. Ask first.

## Self-review notes

- **Spec coverage:** Phases 1–3 map to Tasks 1–9 (skeleton and fake scanner: 1–3; pipeline MVP: 4, 5, 9; enhancement parity: 5–7; output: 8). Phase 4's real backend is intentionally limited to Task 10 (read-only detection plus an explicit `NotSupportedYet`), as the spec says real slide scanning waits on upstream. The desktop app, Flatpak and hardware tuning (Phases 5–6) need their own plan after real scans exist. ICC handling is limited to embedding sRGB; device-profile handling is deferred until real data.
- **Known gaps by design:** infrared quality, exposure and colour are only validated on synthetic data until real scans exist; the synthetic aperture geometry comes from one community-measured unit and must be re-checked on the owner's unit.
- **Type consistency:** `RawSweep`, `Slide`, `Box`, `ScanSettings`, `OutputSpec` and function names above are used identically in every task that consumes them.
