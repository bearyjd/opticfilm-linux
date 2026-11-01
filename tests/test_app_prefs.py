from pathlib import Path

from opticfilm_linux.app.prefs import (
    DEFAULT_DPI,
    DPI_CHOICES,
    MemoryPrefs,
    RecentBatch,
    batch_subfolder,
    export_defaults,
    forget_batch,
    recent_batches,
    remember_batch,
    remember_export,
)


def test_remember_puts_newest_first_and_dedupes(tmp_path):
    prefs = MemoryPrefs()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    remember_batch(prefs, a)
    remember_batch(prefs, b)
    remember_batch(prefs, a)
    assert recent_batches(prefs) == (RecentBatch(a, True), RecentBatch(b, True))


def test_recent_is_capped_at_ten(tmp_path):
    prefs = MemoryPrefs()
    for i in range(12):
        remember_batch(prefs, tmp_path / f"b{i}")
    found = recent_batches(prefs)
    assert len(found) == 10 and found[0].path == tmp_path / "b11"


def test_missing_path_is_listed_as_not_existing(tmp_path):
    prefs = MemoryPrefs()
    remember_batch(prefs, tmp_path / "gone")
    assert recent_batches(prefs) == (RecentBatch(tmp_path / "gone", False),)


def test_forget_removes_only_that_batch(tmp_path):
    prefs = MemoryPrefs()
    remember_batch(prefs, tmp_path / "a")
    remember_batch(prefs, tmp_path / "b")
    forget_batch(prefs, tmp_path / "a")
    assert [r.path for r in recent_batches(prefs)] == [tmp_path / "b"]


def test_corrupt_stored_list_is_treated_as_empty():
    prefs = MemoryPrefs()
    prefs.set("recent_batches", "{ not json")
    assert recent_batches(prefs) == ()
    prefs.set("recent_batches", '{"a": 1}')
    assert recent_batches(prefs) == ()


def test_subfolder_stays_inside_parent():
    parent = Path("/x")
    assert batch_subfolder(parent, "../../etc") == parent / "etc"
    assert batch_subfolder(parent, "Trips 2026") == parent / "Trips 2026"
    assert batch_subfolder(parent, "") == parent / "batch"
    assert batch_subfolder(parent, "..") == parent / "batch"
    assert batch_subfolder(parent, ".hidden") == parent / "hidden"


def test_dpi_choices_match_the_spec():
    assert DPI_CHOICES == (600, 1200, 2400, 3600, 7200) and DEFAULT_DPI == 7200


def test_export_preferences_default_to_jpeg_full_size():
    defaults = export_defaults(MemoryPrefs())
    assert (defaults.folder, defaults.format, defaults.jpeg_size) == ("", "jpeg", "full")


def test_remember_export_preserves_folder_format_and_size():
    prefs = MemoryPrefs()
    remember_export(prefs, "file:///photos", "png8", "3600")
    defaults = export_defaults(prefs)
    assert (defaults.folder, defaults.format, defaults.jpeg_size) == (
        "file:///photos",
        "png8",
        "3600",
    )


def test_export_preferences_keep_legacy_choices_and_ignore_corrupt_enums():
    prefs = MemoryPrefs()
    prefs.set("export_folder", "file:///legacy")
    prefs.set("export_format", "tiff16")
    defaults = export_defaults(prefs)
    assert (defaults.folder, defaults.format, defaults.jpeg_size) == (
        "file:///legacy",
        "tiff16",
        "full",
    )
    prefs.set("export_format", "unknown")
    prefs.set("export_jpeg_size", "unknown")
    defaults = export_defaults(prefs)
    assert (defaults.format, defaults.jpeg_size) == ("jpeg", "full")
