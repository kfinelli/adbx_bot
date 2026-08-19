"""
Regression tests for the models ↔ engine circular import (issue #188).

models.py must never import engine.* at module level: importing any engine
submodule runs engine/__init__.py, which does `from models import ...` —
if models was imported first, it is only partially initialized and the
import crashes. Registry/class access in models.py is function-level for
this reason.

These run in fresh subprocesses because the pytest process itself cannot
reproduce the cycle: conftest.py imports engine before any test module.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_fresh(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_import_models_first():
    """`import models` alone must not trigger the engine package."""
    r = _run_fresh("import models")
    assert r.returncode == 0, r.stderr


def test_import_models_then_engine():
    """models-first order: engine/__init__'s `from models import ...` must succeed."""
    r = _run_fresh("import models; import engine")
    assert r.returncode == 0, r.stderr


def test_models_first_lazy_registry_access():
    """Registry-backed Character properties work in a models-first process."""
    r = _run_fresh(
        "import models; "
        "from models import Character, GameState; "
        "c = Character(); "
        "assert c.defense == 0 and c.resistance == 0; "
        "assert c.equipped_weapons() == []"
    )
    assert r.returncode == 0, r.stderr


def test_import_engine_first_still_works():
    """The historical order (conftest) must keep working."""
    r = _run_fresh("import engine; import models")
    assert r.returncode == 0, r.stderr
