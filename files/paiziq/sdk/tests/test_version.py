"""Version consistency (PZ-041): the single source of truth is
sdk/pyproject.toml; paiziq.__version__ must mirror it and the release
workflow refuses tags that disagree."""

from __future__ import annotations

import re
from pathlib import Path

import paiziq

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject_version() -> str:
    match = re.search(r'^version = "([^"]+)"', _PYPROJECT.read_text(), re.M)
    assert match, "version line missing from pyproject.toml"
    return match.group(1)


def test_package_version_matches_pyproject():
    assert paiziq.__version__ == _pyproject_version()


def test_version_is_semver_like():
    assert re.fullmatch(r"\d+\.\d+\.\d+", paiziq.__version__)
