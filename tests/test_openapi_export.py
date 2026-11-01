"""`scripts/export_openapi.py` writes the OpenAPI the web client is generated from (16c)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/export_openapi.py"
COMMITTED = ROOT / "web/openapi.json"


def load_export_script() -> ModuleType:
    """The script lives outside `src/`, so it is loaded by path rather than imported."""
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_export(output: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Runs from an empty directory with no secrets in the environment: no `.env` to read."""
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_export_writes_a_valid_openapi_document_without_a_dot_env(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"

    result = run_export(output, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["openapi"].startswith("3.")
    assert {"/analyses", "/analyses/{job_id}", "/analyses/{job_id}/events"} <= set(
        document["paths"]
    )
    assert {"AnalysisBody", "StageEvent", "JobFinishedEvent"} <= set(
        document["components"]["schemas"]
    )


def test_the_export_is_byte_stable_so_ci_can_diff_it(tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    run_export(first, cwd=tmp_path)
    run_export(second, cwd=tmp_path)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").endswith("}\n")


def test_the_committed_openapi_matches_the_app() -> None:
    """Fails here, before CI's diff, when the backend changed and the export was not re-run."""
    rendered: str = load_export_script().render_openapi()

    assert COMMITTED.read_text(encoding="utf-8") == rendered
