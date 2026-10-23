from opticfilm_linux.cli import main


def _new(path, *extra):
    return main(["batch", "new", str(path), "--name", "Trips", "--dpi", "600", *extra])


def _scan(path, sweep_dir, *extra):
    return main(["batch", "scan", str(path), "--fake", str(sweep_dir), *extra])


def test_new_creates_a_batch(tmp_path):
    assert _new(tmp_path / "b", "--album", "Family", "--date", "1975-06") == 0
    assert (tmp_path / "b" / "batch.json").is_file()


def test_new_twice_gives_exit_2(tmp_path, capsys):
    assert _new(tmp_path / "b") == 0
    assert _new(tmp_path / "b") == 2
    assert capsys.readouterr().err.startswith("error:")


def test_scan_fake_runs_to_review_and_status_lists_trays(tmp_path, synthetic_sweep_dir, capsys):
    _new(tmp_path / "b")
    assert _scan(tmp_path / "b", synthetic_sweep_dir, "--count", "2") == 0
    capsys.readouterr()
    assert main(["batch", "status", str(tmp_path / "b")]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("tray")]
    assert len(lines) == 2
    assert all("review" in ln and "4 accepted, 0 needs a look, 0 blank" in ln for ln in lines)


def test_accept_all_then_export_writes_files(tmp_path, synthetic_sweep_dir, capsys):
    _new(tmp_path / "b")
    _scan(tmp_path / "b", synthetic_sweep_dir)
    assert main(["batch", "accept-all", str(tmp_path / "b")]) == 0
    capsys.readouterr()
    out = tmp_path / "out"
    assert main(["batch", "export", str(tmp_path / "b"), "--out", str(out)]) == 0
    assert "4 slides written" in capsys.readouterr().out
    assert len(list(out.glob("*.tif"))) == 4


def test_export_twice_writes_zero_new_files(tmp_path, synthetic_sweep_dir, capsys):
    _new(tmp_path / "b")
    _scan(tmp_path / "b", synthetic_sweep_dir)
    out = tmp_path / "out"
    main(["batch", "export", str(tmp_path / "b"), "--out", str(out)])
    capsys.readouterr()
    assert main(["batch", "export", str(tmp_path / "b"), "--out", str(out)]) == 0
    assert "0 slides written" in capsys.readouterr().out
    assert len(list(out.glob("*.tif"))) == 4


def test_export_format_option(tmp_path, synthetic_sweep_dir):
    _new(tmp_path / "b")
    _scan(tmp_path / "b", synthetic_sweep_dir)
    out = tmp_path / "out"
    assert (
        main(["batch", "export", str(tmp_path / "b"), "--out", str(out), "--format", "jpeg"]) == 0
    )
    assert len(list(out.glob("*.jpg"))) == 4


def test_corrupt_manifest_gives_exit_2_and_message(tmp_path, capsys):
    _new(tmp_path / "b")
    (tmp_path / "b" / "batch.json").write_text("{garbage")
    assert main(["batch", "status", str(tmp_path / "b")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err


def test_missing_batch_gives_exit_2(tmp_path, capsys):
    assert main(["batch", "status", str(tmp_path / "nope")]) == 2
    assert "not found" in capsys.readouterr().err


def test_scan_without_fake_and_without_the_driver_says_what_is_missing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(tmp_path / "nowhere"))
    _new(tmp_path / "b")
    assert main(["batch", "scan", str(tmp_path / "b")]) == 2
    assert "OPTICFILM_DRIVER_DIR" in capsys.readouterr().err


def test_scan_exit_code_is_2_when_a_tray_failed(tmp_path, synthetic_sweep_dir, capsys):
    main(["batch", "new", str(tmp_path / "b"), "--name", "T", "--dpi", "3600"])
    assert _scan(tmp_path / "b", synthetic_sweep_dir) == 2
    captured = capsys.readouterr()
    assert "failed" in captured.out and "600" in captured.out
    assert captured.err.startswith("error:")


def test_second_writer_is_refused_while_a_batch_is_open(tmp_path, capsys):
    from opticfilm_linux.batch.store import BatchStore

    _new(tmp_path / "b")
    with BatchStore.open(tmp_path / "b"):
        assert main(["batch", "accept-all", str(tmp_path / "b")]) == 2
        assert "in use" in capsys.readouterr().err
        assert main(["batch", "status", str(tmp_path / "b")]) == 0  # reading is always allowed


def test_ctrl_c_stops_after_the_scan_in_progress(
    tmp_path, synthetic_sweep_dir, monkeypatch, capsys
):
    from opticfilm_linux.batch.runner import BatchRunner
    from opticfilm_linux.batch.store import BatchStore

    def interrupted(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(BatchRunner, "wait_scans_idle", interrupted)
    _new(tmp_path / "b")
    assert _scan(tmp_path / "b", synthetic_sweep_dir, "--count", "3") == 130
    assert "interrupted" in capsys.readouterr().err
    assert len(BatchStore.open(tmp_path / "b", readonly=True).snapshot().trays) == 1


def test_status_of_an_empty_batch(tmp_path, capsys):
    _new(tmp_path / "b")
    assert main(["batch", "status", str(tmp_path / "b")]) == 0
    assert "no trays" in capsys.readouterr().out
