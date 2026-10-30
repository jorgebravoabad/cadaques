# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jorge Bravo-Abad
"""Every script in examples/ runs from a clone and exits cleanly."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
NEEDS_BO = {"pc1d.py", "chemistry_recommend.py"}  # examples that need the [bo] extra (scikit-learn)
SCRIPTS = sorted(EXAMPLES.glob("*.py"))


def test_examples_exist():
    assert {"pc1d.py", "external_reserve_settle.py"} <= {p.name for p in SCRIPTS}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_example_runs(script):
    if script.name in NEEDS_BO and importlib.util.find_spec("sklearn") is None:
        pytest.skip("needs the [bo] extra (scikit-learn)")
    done = subprocess.run([sys.executable, str(script)], check=False, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
