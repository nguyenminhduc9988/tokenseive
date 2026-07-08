"""Shared pytest fixtures for TokenSeive tests.

No third-party dependencies required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the in-tree package importable without installation.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def sample_prompt() -> str:
    """A verbose prompt with code, identity, and critical keywords."""
    return (
        "You are Atlas Agent, a senior engineer. Your name is Atlas.\n"
        "\n"
        "It is important to note that you must always validate input.\n"
        "In order to process data, please note that you really should be careful.\n"
        "It goes without saying that you do not ignore warnings. This is critical.\n"
        "\n"
        "```python\n"
        "def hello(name):\n"
        "    print(f'hello {name}')\n"
        "```\n"
        "\n"
        "<system>You are the orchestrator</system>\n"
    )


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    """A tiny throwaway repository for mapper tests."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        '"""Core module."""\n'
        "from myapp.utils import helper\n"
        "\n"
        "def process(data):\n"
        "    result = helper(data)\n"
        "    return result\n"
        "\n"
        "class Service:\n"
        "    def run(self, x):\n"
        "        return process(x)\n",
        encoding="utf-8",
    )
    (pkg / "utils.py").write_text(
        '"""Util module."""\n'
        "def helper(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    return tmp_path
