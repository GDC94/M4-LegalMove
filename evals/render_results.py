"""The README "Measured results" table, rendered from an eval report or the frozen baseline.

Run from the repository root, with src on the path like run_evals.py:
    PYTHONPATH=src uv run python evals/render_results.py outputs/baseline.json
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from thresholds import ThresholdCheck, check_thresholds

MET_LABELS = {True: "yes", False: "no", None: "N/A"}


def render_target(check: ThresholdCheck) -> str:
    direction = "≤" if check.threshold.lower_is_better else "≥"
    return f"{direction} {check.threshold.target:g}"


def render_value(check: ThresholdCheck) -> str:
    measured = "N/A" if check.value is None else f"{check.value:.3f}"
    noun = "case" if check.evaluable == 1 else "cases"
    return f"{measured} ({check.evaluable} {noun})"


def render_label(check: ThresholdCheck) -> str:
    return f"`{check.threshold.metric}`{check.threshold.scan_qualifier}"


def render_results_table(report: Mapping[str, Any]) -> str:
    """One row per §6.6 target; the header names the Split the values were measured on."""
    split = str(report.get("split", "measured")).capitalize()
    rows = [f"| Metric | Target | {split} result | Met |", "|---|---|---|---|"]
    rows.extend(
        f"| {render_label(check)} | {render_target(check)} | {render_value(check)} "
        f"| {MET_LABELS[check.met]} |"
        for check in check_thresholds(report)
    )
    return "\n".join(rows) + "\n"


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="render_results", description=__doc__)
    parser.add_argument("report", type=Path, help="an eval report or outputs/baseline.json")
    path: Path = parser.parse_args(argv).report
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    sys.stdout.write(render_results_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
