"""Regression tests for the contributor guide."""

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "CONTRIBUTING.md"


def test_development_commands_match_repository_tooling():
    guide = GUIDE.read_text(encoding="utf-8")

    expected_commands = (
        "python -m pip install -r requirements.txt -r requirements-dev.txt",
        "python -m pytest -q tests",
        "ruff check src tests",
    )
    stale_instructions = (
        'pip install -e ".[dev]"',
        "black src/",
        "flake8 src/",
        "mypy src/",
        "src/ghost/",
        "├── cli.py",
        "\ninstall/",
        "\nscripts/",
        "--cov=src/ghost",
        "docs/SPEED_GUIDE.md",
    )

    assert all(command in guide for command in expected_commands)
    assert all(instruction not in guide for instruction in stale_instructions)


def test_local_markdown_links_resolve():
    guide = GUIDE.read_text(encoding="utf-8")
    local_targets = re.findall(r"\[[^]]+\]\((?!https?://|mailto:)([^)]+)\)", guide)

    for raw_target in local_targets:
        target = unquote(raw_target.split("#", 1)[0])
        if target:
            assert (ROOT / target).exists(), f"Missing local link target: {raw_target}"
