"""cleanup_recovery and runner share cleanup state through cleanup_state, not export internals."""

import ast
from pathlib import Path

import opticfilm_linux.batch.cleanup_recovery as cleanup_recovery
import opticfilm_linux.batch.cleanup_state as cleanup_state
import opticfilm_linux.batch.export as export
import opticfilm_linux.batch.runner as runner

MODULES = (cleanup_recovery, runner)


def _tree(module) -> ast.AST:
    return ast.parse(Path(module.__file__).read_text())


def test_no_private_export_attribute_access():
    for module in MODULES:
        private = [
            node.lineno
            for node in ast.walk(_tree(module))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "export"
            and node.attr.startswith("_")
        ]
        assert not private, f"{module.__name__} uses export private names at lines {private}"


def test_cleanup_recovery_does_not_import_export():
    for node in ast.walk(_tree(cleanup_recovery)):
        if isinstance(node, ast.ImportFrom):
            names = {a.name for a in node.names}
            assert node.module != "opticfilm_linux.batch.export"
            assert not (node.module == "opticfilm_linux.batch" and "export" in names)
        if isinstance(node, ast.Import):
            assert all(a.name != "opticfilm_linux.batch.export" for a in node.names)


def test_single_shared_lock_and_pending_registry():
    assert export._CLEANUP_LOCK is cleanup_state.CLEANUP_LOCK
    assert export._PENDING_CLEANUP_RESETS is cleanup_state.PENDING_CLEANUP_RESETS
    assert export.CleanupRefused is cleanup_state.CleanupRefused
    assert export.ExportVerificationFailed is cleanup_state.ExportVerificationFailed
