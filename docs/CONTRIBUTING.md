# Contributing

- **Tests first.** Write the failing test, watch it fail for the right reason, then make it pass.
- **Coverage gate:** 80 % (`uv run pytest --cov=opticfilm_linux --cov-fail-under=80`), plus
  `uv run ruff check .` and `uv run ruff format --check .`. CI runs both.
- **Immutable data flow.** Pipeline steps return new arrays and never modify their input;
  dataclasses are frozen.
- **Hardware tests** carry `@pytest.mark.hardware`, are read-only, and are excluded from the
  default run and from CI. Nothing in this repository may send commands to the scanner without an
  explicit, reviewed design.
- **No vendor material.** Do not commit Plustek code, binaries, firmware, ICC profiles,
  configuration values, documentation or USB captures.
- Small files (under 800 lines), small functions (under 50 lines), conventional commit messages
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **The desktop app** (`opticfilm`): all behaviour lives in the Qt-free controller
  (`opticfilm_linux.app`, tested in the normal run); `opticfilm_linux.app.qt` and the QML pages are
  thin. The Qt layer needs the distribution's PySide6 and Kirigami (on Fedora:
  `dnf install python3-pyside6 kf6-kirigami qt6-qtdeclarative`). Use a separate environment that
  can see them: `uv venv --system-site-packages --python /usr/bin/python3 .venv-qt &&
  UV_PROJECT_ENVIRONMENT=.venv-qt uv sync --python /usr/bin/python3`, then
  `QT_QPA_PLATFORM=offscreen .venv-qt/bin/python -m pytest -m qt tests/qt`. Qt tests carry
  `@pytest.mark.qt`, are excluded from the default run, and run in the optional Fedora CI job.
