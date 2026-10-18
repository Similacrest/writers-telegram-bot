"""The version the bot reports (/health, the debug line) is the one in pyproject.toml."""

import tomllib
from pathlib import Path

import writers_bot


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert writers_bot.__version__ == pyproject["project"]["version"]
