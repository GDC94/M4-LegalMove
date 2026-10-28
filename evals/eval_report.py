"""eval_report.json: metrics per run, category, Amendment Kind and case, plus the baseline check."""

import json
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_case import CaseRun, GoldenCase
from metrics import METRICS, Aggregate, Ratio, aggregate_scores, metric_named, score_case

# Ratios are exact fractions of small integers, so only float noise is tolerated.
RATIO_TOLERANCE = 1e-9
# The Judge at temperature 0 is still not deterministic: one case moving one point on the 1-10
# scale shifts the mean by 1/N. A quarter point absorbs a single flip once a split holds four or
# more judged cases, and still flags it on smaller splits or when several cases drop together.
REGRESSION_TOLERANCE: Mapping[str, float] = {"judge_score": 0.25}

ScoredCase = tuple[GoldenCase, CaseRun, Mapping[str, Ratio | None]]


@dataclass(frozen=True)
class ReportHeader:
    split: str
    run_name: str
    dataset_name: str
    judge_prompt_version: str
    case_filter: str | None = None


def build_report(
    cases: Sequence[GoldenCase], runs: Mapping[str, CaseRun], header: ReportHeader
) -> dict[str, Any]:
    """Every case must have a run: a missing one would silently shrink each denominator."""
    scored = [(case, runs[case.case_id], score_case(runs[case.case_id], case)) for case in cases]
    return {
        "split": header.split,
        "run_name": header.run_name,
        "dataset_name": header.dataset_name,
        "judge_prompt_version": header.judge_prompt_version,
        "case_filter": header.case_filter,
        "experiment_url": None,
        "cases_total": len(scored),
        "global": describe_aggregates([scores for _, _, scores in scored]),
        "by_category": group_aggregates(scored, lambda case: case.category),
        "by_kind": group_aggregates(scored, lambda case: case.amendment_kind.value),
        "cases": [describe_case(case, run, scores) for case, run, scores in scored],
        "baseline": None,
    }


def describe_aggregates(scored: Sequence[Mapping[str, Ratio | None]]) -> dict[str, Any]:
    return {name: describe_aggregate(value) for name, value in aggregate_scores(scored).items()}


def describe_aggregate(aggregate: Aggregate) -> dict[str, Any]:
    return {"value": aggregate.value, "evaluable": aggregate.evaluable}


def group_aggregates(
    scored: Sequence[ScoredCase], key: Callable[[GoldenCase], str]
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Ratio | None]]] = {}
    for case, _, scores in scored:
        groups.setdefault(key(case), []).append(scores)
    return {name: describe_aggregates(group) for name, group in sorted(groups.items())}


def describe_case(
    case: GoldenCase, run: CaseRun, scores: Mapping[str, Ratio | None]
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "amendment_kind": case.amendment_kind.value,
        "exit_code": run.exit_code,
        "error": run.error,
        "trace_id": run.trace_id,
        "judge_reasoning": run.judge_reasoning,
        "metrics": {name: None if ratio is None else ratio.value for name, ratio in scores.items()},
    }


def read_baseline(path: Path) -> dict[str, Any] | None:
    """The baseline is optional (plan §6.7); without one there is nothing to compare."""
    if not path.is_file():
        return None
    baseline: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return baseline


def compare_with_baseline(report: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Deltas per global metric; a regression is a move in the worse direction beyond tolerance.

    Fewer evaluable cases is a regression too: a value that holds on a smaller denominator is
    not evidence that nothing broke.
    """
    if baseline.get("split") != report["split"]:
        reason = f"baseline split {baseline.get('split')} differs from {report['split']}"
        return {"compared": False, "reason": reason}
    current, before = report["global"], baseline.get("global", {})
    judge_changed = baseline.get("judge_prompt_version") != report.get("judge_prompt_version")
    excluded = {"judge_score"} if judge_changed else set()
    deltas = global_deltas(current, before, "value", excluded)
    evaluable_deltas = global_deltas(current, before, "evaluable", excluded)
    regressions = [
        *find_regressions(deltas),
        *(f"{name}.evaluable" for name, delta in evaluable_deltas.items() if delta < 0),
    ]
    return {
        "compared": True,
        "judge_prompt_version_changed": judge_changed,
        "deltas": deltas,
        "evaluable_deltas": evaluable_deltas,
        "regressions": regressions,
    }


def global_deltas(
    current: Mapping[str, Any], baseline: Mapping[str, Any], field: str, excluded: AbstractSet[str]
) -> dict[str, float]:
    """Only metrics both runs could evaluate; N/A on either side is no evidence either way.

    `excluded` drops metrics measured differently in each run, such as the Judge after a prompt
    change: its delta would compare two graders, not two pipelines.
    """
    deltas: dict[str, float] = {}
    for metric in METRICS:
        if metric.name in excluded:
            continue
        now = current.get(metric.name, {}).get(field)
        before = baseline.get(metric.name, {}).get(field)
        if now is not None and before is not None:
            deltas[metric.name] = now - before
    return deltas


def find_regressions(deltas: Mapping[str, float]) -> list[str]:
    regressions: list[str] = []
    for name, delta in deltas.items():
        worse = delta if metric_named(name).lower_is_better else -delta
        if worse > REGRESSION_TOLERANCE.get(name, RATIO_TOLERANCE):
            regressions.append(name)
    return regressions
