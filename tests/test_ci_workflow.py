"""CI workflow of plan §6.9 and ticket 16c: the Python gate and the web job, without secrets."""

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/ci.yml"
PACKAGE_JSON = ROOT / "web/package.json"
GATE = [
    "uv sync --locked",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy src",
    "uv run pytest",
]
CONTRACT_FILES = "web/openapi.json web/src/api/schema.d.ts"
# `--no-dev` on the run too: a plain `uv run` would sync the dev group back in.
EXPORT = "PYTHONPATH=src uv run --no-dev python scripts/export_openapi.py"
WEB_GATE = [
    "pnpm install --frozen-lockfile",
    "pnpm lint",
    "pnpm typecheck",
    "pnpm test",
    "pnpm build",
    "uv sync --locked --no-dev",
    EXPORT,
    "pnpm gen:types",
    f"git diff --exit-code -- {CONTRACT_FILES}",
]


# Any: a parsed workflow is navigated key by key; `object` would need a cast per level.
def read_workflow() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow


def run_commands(job: str) -> list[str]:
    steps = read_workflow()["jobs"][job]["steps"]
    return [step["run"] for step in steps if "run" in step]


def all_run_commands() -> str:
    return " ".join(command for job in read_workflow()["jobs"] for command in run_commands(job))


def test_ci_has_exactly_the_python_and_the_web_jobs() -> None:
    assert set(read_workflow()["jobs"]) == {"check", "web"}


def test_the_web_job_type_checks_once() -> None:
    """`pnpm build` is the bundle only: the named type-check step is the one `tsc -b`."""
    scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]

    assert scripts["typecheck"] == "tsc -b"
    assert "tsc" not in scripts["build"]


def test_ci_runs_the_style_gate_and_unit_tests_in_order() -> None:
    assert run_commands("check") == GATE


def test_the_web_job_runs_lint_types_tests_build_and_the_contract_check_in_order() -> None:
    assert run_commands("web") == WEB_GATE


def test_the_web_job_uses_node_22_and_pnpm_from_the_web_directory() -> None:
    job = read_workflow()["jobs"]["web"]
    actions = {step["uses"].split("@")[0]: step for step in job["steps"] if "uses" in step}

    assert job["defaults"]["run"]["working-directory"] == "web"
    assert actions["actions/setup-node"]["with"]["node-version"] == 22
    assert "pnpm/action-setup" in actions
    assert "astral-sh/setup-uv" in actions


def test_the_contract_check_runs_from_the_repository_root() -> None:
    steps = read_workflow()["jobs"]["web"]["steps"]
    root_steps = [step["run"] for step in steps if step.get("working-directory") == "."]

    assert root_steps == [
        "uv sync --locked --no-dev",
        EXPORT,
        f"git diff --exit-code -- {CONTRACT_FILES}",
    ]


def test_ci_never_uses_secrets_integration_tests_or_evals() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "secrets." not in text
    assert "integration" not in all_run_commands()
    assert "evals" not in all_run_commands()


def test_ci_runs_on_push_and_pull_request() -> None:
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    assert set(read_workflow()[True]) == {"push", "pull_request"}
