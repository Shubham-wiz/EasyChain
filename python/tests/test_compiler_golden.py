"""Golden tests: each flow compiles to exactly the expected LangGraph code.

Regenerate after an intended change with ``UPDATE_GOLDEN=1 uv run pytest tests/test_compiler_golden.py``
and review the diff like any other code change.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from easychain.compiler import compile_flow
from easychain.spec import load_spec

from .conftest import TEMPLATES

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted((GOLDEN / "cases").glob("*.flow.yaml")) + sorted(TEMPLATES.glob("*.flow.yaml"))


def _name(path: Path) -> str:
    prefix = "template_" if path.parent == TEMPLATES else ""
    return prefix + path.name.removesuffix(".flow.yaml").replace("-", "_")


@pytest.mark.parametrize("path", CASES, ids=_name)
def test_golden(path: Path):
    compiled = compile_flow(load_spec(path))
    expected_path = GOLDEN / "expected" / f"{_name(path)}.py"
    if os.environ.get("UPDATE_GOLDEN") or not expected_path.exists():
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(compiled.source)
        if not os.environ.get("UPDATE_GOLDEN"):
            pytest.fail(f"Wrote new golden file {expected_path.name}; review it and re-run.")
    assert compiled.source == expected_path.read_text()


@pytest.mark.parametrize("path", CASES, ids=_name)
def test_generated_code_compiles_and_is_lint_clean(path: Path, tmp_path: Path):
    compiled = compile_flow(load_spec(path))
    compile(compiled.source, compiled.module_name, "exec")
    ruff = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
    target = tmp_path / f"{compiled.module_name}.py"
    target.write_text(compiled.source)
    # F: undefined names, unused imports, redefinitions. E9: syntax errors. B: likely bugs.
    result = subprocess.run(
        [
            ruff,
            "check",
            "--no-cache",
            "--isolated",
            "--select",
            "F,E9,B,I",
            "--target-version",
            "py312",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
