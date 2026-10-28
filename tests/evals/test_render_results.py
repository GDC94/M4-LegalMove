"""The README "Measured results" table, rendered from a report or the frozen baseline (T10)."""

import json
from pathlib import Path
from typing import Any

import pytest

from eval_doubles import build_aggregate, build_perfect_report
from render_results import main, render_results_table


def render_table_rows(report: dict[str, Any]) -> list[str]:
    return render_results_table(report).splitlines()


def test_the_header_names_the_split_the_values_were_measured_on() -> None:
    rows = render_table_rows(build_perfect_report())

    assert rows[:2] == ["| Metric | Target | Validation result | Met |", "|---|---|---|---|"]


def test_one_row_per_target_with_its_direction_value_and_outcome() -> None:
    rows = render_table_rows(build_perfect_report(change_recall=build_aggregate(0.85, 6)))

    assert "| `cer` (clean scans) | ≤ 0.02 | 0.010 (4 cases) | yes |" in rows
    assert "| `change_recall` | ≥ 0.9 | 0.850 (6 cases) | no |" in rows
    assert "| `judge_score` | ≥ 7 | 8.000 (4 cases) | yes |" in rows
    assert len(rows) == 2 + 10


def test_a_single_evaluable_case_is_singular() -> None:
    rows = render_table_rows(build_perfect_report(human_review=build_aggregate(1.0, 1)))

    assert "| `human_review` | ≥ 1 | 1.000 (1 case) | yes |" in rows


def test_a_metric_without_evaluable_cases_reads_not_applicable() -> None:
    rows = render_table_rows(build_perfect_report(change_type_accuracy=build_aggregate(None)))

    assert "| `change_type_accuracy` | ≥ 0.9 | N/A (0 cases) | N/A |" in rows


def test_the_cli_prints_the_table_of_a_report_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(build_perfect_report()), encoding="utf-8")

    exit_code = main([str(path)])

    assert exit_code == 0
    assert capsys.readouterr().out == render_results_table(build_perfect_report())
