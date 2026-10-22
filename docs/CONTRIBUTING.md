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
