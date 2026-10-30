# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jorge Bravo-Abad
"""The version is written once, in ``cadaques.__version__``, and everything else agrees."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

import pytest

import cadaques

ROOT = Path(__file__).resolve().parents[1]


def test_installed_metadata_matches_module():
    assert importlib.metadata.version("cadaques") == cadaques.__version__


def test_citation_cff_matches_module():
    cff = ROOT / "CITATION.cff"
    if not cff.exists():
        pytest.skip("CITATION.cff not available (not a source checkout)")
    match = re.search(r"^version:\s*['\"]?([^'\"\s]+)['\"]?\s*$", cff.read_text(encoding="utf-8"), re.M)
    assert match is not None
    assert match.group(1) == cadaques.__version__
