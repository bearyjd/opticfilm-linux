"""Mixins reach run_export, AutoPipeline and run_free_space through ``controller``.

Tests patch those names on ``opticfilm_linux.app.controller``; a mixin that imported them
directly would bypass the patch point.
"""

import ast
from pathlib import Path

import opticfilm_linux.app as app_pkg
from opticfilm_linux.app import controller

PATCHED = {"run_export", "AutoPipeline", "run_free_space"}
MIXINS = (
    "commands",
    "selection_commands",
    "lifecycle_commands",
    "load_and_scan",
    "holder",
    "first_load",
    "scan_openings",
)


def test_mixins_do_not_import_patch_points_directly():
    root = Path(app_pkg.__file__).parent
    offenders = []
    for name in MIXINS:
        tree = ast.parse((root / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                offenders += [f"{name}: {a.name}" for a in node.names if a.name in PATCHED]
    assert offenders == []


def test_controller_keeps_patch_point_reexports():
    for name in PATCHED:
        assert hasattr(controller, name)


def test_export_directory_check_is_not_reexported_by_controller():
    assert not hasattr(controller, "_check_export_directory")
