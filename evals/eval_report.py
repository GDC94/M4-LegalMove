"""eval_report.json: metrics per run, category, Amendment Kind, scan and case, plus the baseline."""

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
# Decision 90: CER has a target per scan, so a rise on one scan kind must not hide in the global
# mean. Each scan group's CER is compared under its own key, such as cer[degraded].
SCAN_COMPARED_METRICS = ("cer",)

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
        "by_scan": group_aggregates(scored, lambda case: case.scan),
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
        "predicted": describe_prediction(run),
        "metrics": {name: None if ratio is None else ratio.value for name, ratio in scores.items()},
    }


def describe_prediction(run: CaseRun) -> dict[str, Any] | None:
    """What the pipeline answered, so a miss is diagnosed from the report alone (decision 97)."""
    if run.output is None:
        return None
    return {
        "sections_changed": run.output.sections_changed,
        "topics_touched": run.output.topics_touched,
        "summary": run.output.summary_of_the_change,
        "changes": [change.model_dump(mode="json") for change in run.changes],
    }


def read_baseline(path: Path) -> dict[str, Any] | None:
    """The baseline is optional (plan §6.7); without one there is nothing to compare."""
    if not path.is_file():
        return None
    baseline: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return baseline


def compare_with_baseline(report: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Deltas per global metric and per scan CER; a regression is a worse move beyond tolerance.

    Fewer evaluable cases is a regression too: a value that holds on a smaller denominator is
    not evidence that nothing broke.
    """
    if baseline.get("split") != report["split"]:
        reason = f"baseline split {baseline.get('split')} differs from {report['split']}"
        return {"compared": False, "reason": reason}
    judge_changed = baseline.get("judge_prompt_version") != report.get("judge_prompt_version")
    excluded = {"judge_score"} if judge_changed else set()
    current = collect_compared_aggregates(report, excluded)
    before = collect_compared_aggregates(baseline, excluded)
    deltas = compute_deltas(current, before, "value")
    evaluable_deltas = compute_deltas(current, before, "evaluable")
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


def collect_compared_aggregates(
    report: Mapping[str, Any], excluded: AbstractSet[str]
) -> dict[str, Mapping[str, Any]]:
    """Global aggregates by metric name, then each scan group's CER keyed as cer[<scan>].

    `excluded` drops metrics measured differently in each run, such as the Judge after a prompt
    change: its delta would compare two graders, not two pipelines.
    """
    global_group = report.get("global", {})
    aggregates = {
        metric.name: global_group.get(metric.name, {})
        for metric in METRICS
        if metric.name not in excluded
    }
    for scan, group in sorted(report.get("by_scan", {}).items()):
        for name in SCAN_COMPARED_METRICS:
            aggregates[f"{name}[{scan}]"] = group.get(name, {})
    return aggregates


def compute_deltas(
    current: Mapping[str, Mapping[str, Any]], baseline: Mapping[str, Mapping[str, Any]], field: str
) -> dict[str, float]:
    """Only aggregates both runs could evaluate; N/A on either side is no evidence either way."""
    deltas: dict[str, float] = {}
    for key, aggregate in current.items():
        now = aggregate.get(field)
        before = baseline.get(key, {}).get(field)
        if now is not None and before is not None:
            deltas[key] = now - before
    return deltas


def strip_scan(key: str) -> str:
    """The metric name behind a compared key: cer[degraded] is measured as cer."""
    return key.partition("[")[0]


def find_regressions(deltas: Mapping[str, float]) -> list[str]:
    regressions: list[str] = []
    for key, delta in deltas.items():
        name = strip_scan(key)
        worse = delta if metric_named(name).lower_is_better else -delta
        if worse > REGRESSION_TOLERANCE.get(name, RATIO_TOLERANCE):
            regressions.append(key)
    return regressions
