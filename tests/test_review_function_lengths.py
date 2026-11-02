"""Inventory every function added or edited by PR4, including its tests."""

import ast
import subprocess
from pathlib import Path

import pytest

# PR4's merge base after merging main; app/auto.py was first added after this commit.
PR4_BASE = "5a3420113c43cba5d6f6d8ba2eea5cf49465d0f5"
ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True)


def _functions(source):
    functions = {}
    lines = source.splitlines(keepends=True)
    occurrences = {}

    def visit(node, parents=()):
        for child in ast.iter_child_nodes(node):
            scope = parents
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = (*parents, child.name)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = ".".join(scope)
                occurrence = occurrences.get(name, 0)
                occurrences[name] = occurrence + 1
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                functions[(name, occurrence)] = (
                    "".join(lines[start - 1 : child.end_lineno]),
                    child.end_lineno - child.lineno + 1,
                )
            visit(child, scope)

    visit(ast.parse(source))
    return functions


def _oversized_changes(path, before, after):
    previous = _functions(before)
    return [
        f"{path}:{key[0]}: {length} lines"
        for key, (source, length) in _functions(after).items()
        if length >= 50 and source != previous.get(key, (None, 0))[0]
    ]


def test_pr4_changed_function_lengths():
    changed = _git("diff", "--name-only", PR4_BASE, "--", "src", "tests").splitlines()
    added = _git("ls-files", "--others", "--exclude-standard", "--", "src", "tests")
    baseline_paths = set(_git("ls-tree", "-r", "--name-only", PR4_BASE).splitlines())
    oversized = []
    for path in sorted(set(changed + added.splitlines())):
        source = ROOT / path
        if source.suffix != ".py" or not source.is_file():
            continue
        before = _git("show", f"{PR4_BASE}:{path}") if path in baseline_paths else ""
        oversized.extend(_oversized_changes(path, before, source.read_text()))
    assert not oversized, "PR4 functions must be below 50 lines:\n" + "\n".join(oversized)


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("src/opticfilm_linux/output.py", "write_slide"),
        ("tests/test_app_auto_controller.py", "test_manual_first_hold_release_resumes_backlog"),
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_inventory_rejects_long_new_and_modified_functions(path, name, existing):
    before = f"def {name}():\n    pass\n" if existing else ""
    after = f"def {name}():\n" + "    pass\n" * 49
    assert _oversized_changes(path, before, after) == [f"{path}:{name}: 50 lines"]


def test_inventory_includes_methods_nested_and_async_functions():
    source = "class Example:\n    async def run(self):\n        def nested():\n"
    source += "            pass\n" * 49
    assert {line.split(":")[1] for line in _oversized_changes("example.py", "", source)} == {
        "Example.run",
        "Example.run.nested",
    }


def test_inventory_allows_unchanged_long_functions_and_short_edits():
    unchanged = "def legacy():\n" + "    pass\n" * 60
    short = "def edited():\n" + "    pass\n" * 48
    assert _oversized_changes("example.py", unchanged, unchanged + short) == []


def test_inventory_detects_decorator_edits_and_repeated_names():
    first = "@old\ndef repeated():\n" + "    pass\n" * 49
    second = "\ndef repeated():\n    pass\n"
    assert _oversized_changes(
        "example.py", first + second, first.replace("@old", "@new") + second
    ) == ["example.py:repeated: 50 lines"]
