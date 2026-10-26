"""CI workflow of plan §6.9: style gate and unit tests only, without secrets."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/ci.yml"
GATE = [
    "uv sync --locked",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy src",
    "uv run pytest",
]


def run_commands() -> list[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step
    ]


def test_ci_runs_the_style_gate_and_unit_tests_in_order() -> None:
    assert run_commands() == GATE


def test_ci_never_uses_secrets_integration_tests_or_evals() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "secrets." not in text
    assert "integration" not in " ".join(run_commands())
    assert "evals" not in " ".join(run_commands())


def test_ci_runs_on_push_and_pull_request() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    assert set(workflow[True]) == {"push", "pull_request"}
