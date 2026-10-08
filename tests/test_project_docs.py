"""README の開発手順と、版・CHANGELOG の整合."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_test_command_runs_the_whole_suite():
    # unittest の discover は pytest 形式の関数を拾わず、一部しか走らない
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "unittest discover" not in readme
    assert re.search(r"^pytest\b", readme, re.M)


def test_version_is_not_released_while_unreleased_changes_exist():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)  # tomllib は 3.11 から
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
    assert version not in released, f"{version} is already released; bump to the next .dev version"
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    if unreleased.strip():
        assert ".dev" in version, "Unreleased has entries: the version must be a .dev pre-release"
