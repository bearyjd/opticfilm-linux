"""``opticfilm``: start the desktop app (``--fake SWEEP_DIR`` runs it on the fake scanner)."""

import argparse
import os
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.messages import load_command
from opticfilm_linux.scanner.base import Scanner
from opticfilm_linux.scanner.driver import (
    DRIVER_ENV,
    DriverConfig,
    DriverScanner,
    install_signal_cleanup,
)
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.select import eject_button_enabled, next_strip_enabled
from opticfilm_linux.sweepstore import SweepFormatError, sweep_dpi

EXIT_MISSING_QT = 3
EXIT_BAD_ARGS = 2
APP_ID = "io.github.bearyjd.OpticFilmLinux"
APP_NAME = "opticfilm"
DISPLAY_NAME = "OpticFilm Linux"
STYLE = "org.kde.desktop"
STYLE_ENV_VAR = "QT_QUICK_CONTROLS_STYLE"
LOAD_COMMAND_ENV = "OPTICFILM_LOAD_COMMAND"  # replaces the shown first-load terminal command
MISSING_QT_MESSAGE = (
    "opticfilm: the desktop app needs the distribution's PySide6 and Kirigami (the PyPI PySide6 "
    "wheel bundles its own Qt and cannot load Kirigami).\n"
    "On Fedora: sudo dnf install python3-pyside6 kf6-kirigami\n"
    "`ofscan` needs neither."
)


def qml_main() -> Path:
    return Path(str(files("opticfilm_linux.app") / "qml" / "Main.qml"))


def scanner_factory(fake_sweep: Path | None):
    """Builds the scanner for a batch; demo mode replays a stored sweep."""

    def build(_dpi: int) -> Scanner:
        if fake_sweep is not None:
            return FakeScanner(fake_sweep)
        return DriverScanner(DriverConfig.from_env())

    return build


def load_command_text() -> str:
    """The first-load command the help dialog shows; set ``OPTICFILM_LOAD_COMMAND`` to fix it."""
    custom = os.environ.get(LOAD_COMMAND_ENV, "").strip()
    if custom:
        return custom
    directory = os.environ.get(DRIVER_ENV)
    return load_command(Path(directory) if directory else None)


def create_engine(bridge: Any, provider: Any) -> tuple[Any, list[str]]:
    """The QML engine with the window loaded, and the list its warnings are collected in."""
    from PySide6.QtCore import QUrl  # noqa: PLC0415 - Qt is imported only when it runs
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: PLC0415

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
    engine.addImageProvider("preview", provider)
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(qml_main())))
    return engine, warnings


def configure_style() -> None:
    """Use the KDE desktop style when installed; a style the user chose is left alone."""
    from PySide6.QtQml import QQmlEngine  # noqa: PLC0415
    from PySide6.QtQuickControls2 import QQuickStyle  # noqa: PLC0415

    if os.environ.get(STYLE_ENV_VAR) or QQuickStyle.name() == STYLE:
        return
    paths = QQmlEngine().importPathList()
    if any(Path(p, "org", "kde", "desktop").is_dir() for p in paths if "://" not in p):
        QQuickStyle.setStyle(STYLE)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Scan and review film slides.")
    parser.add_argument("--fake", type=Path, metavar="SWEEP_DIR", help="demo mode: replay a sweep")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    demo_dpi = None
    if args.fake is not None:
        try:
            demo_dpi = sweep_dpi(args.fake)
        except SweepFormatError as exc:
            print(f"error: {exc}", file=sys.stderr)  # noqa: T201
            return EXIT_BAD_ARGS
    try:
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
    except ImportError:
        print(MISSING_QT_MESSAGE, file=sys.stderr)  # noqa: T201
        return EXIT_MISSING_QT
    from opticfilm_linux.app.qt.bridge import Bridge  # noqa: PLC0415
    from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: PLC0415
    from opticfilm_linux.app.qt.settings import QSettingsPrefs  # noqa: PLC0415

    app = QApplication.instance() or QApplication(sys.argv[:1])
    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setOrganizationName("bearyjd")
    QGuiApplication.setApplicationDisplayName(DISPLAY_NAME)
    QGuiApplication.setDesktopFileName(APP_ID)
    configure_style()
    install_signal_cleanup()  # the driver runs in its own session: stop it if the app is killed
    controller = Controller(
        scanner_factory(args.fake),
        next_strip=next_strip_enabled(),
        eject_button=eject_button_enabled(),
    )
    bridge = Bridge(controller, QSettingsPrefs(), demo_dpi, load_command_text=load_command_text())
    provider = PreviewProvider(lambda n, p, kind: controller.preview_path(n, p, kind))
    engine, warnings = create_engine(bridge, provider)
    if not engine.rootObjects():
        print("opticfilm: could not load the window:\n" + "\n".join(warnings), file=sys.stderr)  # noqa: T201
        return 1
    app.aboutToQuit.connect(bridge.shutdown)
    bridge.start()
    return app.exec()
