"""Secrets, caches and local eval reports stay out of git."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def is_ignored_by_git(relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative_path],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    # Exit 1 means "not ignored"; anything else is a git failure that must not pass silently.
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".cache/parsed/entry.json",
        ".venv/bin/python",
        "src/__pycache__/main.cpython-314.pyc",
        "outputs/eval_report.json",
        "outputs/calibration_run1.json",
    ],
)
def test_path_is_ignored(relative_path: str) -> None:
    assert is_ignored_by_git(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    # Each single blind validation run is kept as evidence (decisions 92 and 101).
    [
        ".env.example",
        "outputs/baseline.json",
        "outputs/validation_run1.json",
        "outputs/validation_run2.json",
    ],
)
def test_path_is_tracked(relative_path: str) -> None:
    assert not is_ignored_by_git(relative_path)
