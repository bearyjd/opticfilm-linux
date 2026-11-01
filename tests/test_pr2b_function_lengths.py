"""Keep PR2b functions below the documented 50-line limit."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", ["app/controller.py", "batch/export.py", "provenance.py"])
def test_pr2b_function_lengths(module):
    source = Path(__file__).resolve().parents[1] / "src" / "opticfilm_linux" / module
    tree = ast.parse(source.read_text())
    oversized = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Controller.__init__ predates PR2b and is unchanged by this PR.
        if module == "app/controller.py" and node.name == "__init__":
            continue
        length = node.end_lineno - node.lineno + 1
        if length >= 50:
            oversized.append(f"{node.name}: {length} lines")
    assert not oversized, f"{module}: {', '.join(oversized)}"
