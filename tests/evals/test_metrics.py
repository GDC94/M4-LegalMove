"""Eval checks: pure (run, case) -> score functions; N/A is None and never counts as zero."""

from pathlib import Path

import pytest

from eval_doubles import case_run, failed_run, golden_case, review_case
from metrics import (
    METRICS,
    Aggregation,
    Ratio,
    aggregate_metric,
    metric_named,
    score_amendment_kind,
    score_case,
    score_cer,
    score_change_precision,
    score_change_recall,
    score_change_type,
    score_human_review,
    score_judge,
    score_key_facts,
    score_topic_jaccard,
)
from test_layering import INFRASTRUCTURE, runtime_imports

NO_OUTPUT = {"output": None, "changes": [], "judge_score": None}
EMPTY_OUTPUT = {
    "sections_changed": [],
    "topics_touched": [],
    "summary_of_the_change": "No se identificaron cambios.",
}


def value(ratio: Ratio | None) -> float | None:
    return None if ratio is None else ratio.value


# --- Ratio and aggregation -------------------------------------------------------------------


def test_a_ratio_without_denominator_cannot_exist() -> None:
    with pytest.raises(ValueError, match="denominator"):
        Ratio(0, 0)


def test_micro_aggregation_pools_numerators_and_denominators() -> None:
    aggregate = aggregate_metric(Aggregation.MICRO, [Ratio(1, 1), Ratio(1, 3), None])

    assert aggregate.value == pytest.approx(2 / 4)
    assert aggregate.evaluable == 2


def test_mean_aggregation_averages_the_case_values() -> None:
    aggregate = aggregate_metric(Aggregation.MEAN, [Ratio(1, 1), Ratio(1, 3), None])

    assert aggregate.value == pytest.approx((1 + 1 / 3) / 2)
    assert aggregate.evaluable == 2


@pytest.mark.parametrize("aggregation", list(Aggregation))
def test_not_applicable_scores_are_omitted_never_counted_as_zero(
    aggregation: Aggregation,
) -> None:
    aggregate = aggregate_metric(aggregation, [Ratio(1, 1), None, None])

    assert aggregate.value == 1.0
    assert aggregate.evaluable == 1


@pytest.mark.parametrize("aggregation", list(Aggregation))
def test_an_aggregate_with_no_evaluable_case_is_not_applicable(aggregation: Aggregation) -> None:
    aggregate = aggregate_metric(aggregation, [None, None])

    assert aggregate.value is None
    assert aggregate.evaluable == 0


def test_every_metric_of_plan_6_6_is_registered_once() -> None:
    names = [metric.name for metric in METRICS]

    assert names == [
        "cer",
        "amendment_kind_accuracy",
        "change_precision",
        "change_recall",
        "change_type_accuracy",
        "topic_jaccard",
        "key_facts_coverage",
        "judge_score",
        "human_review",
    ]
    assert metric_named("cer").lower_is_better
    assert not metric_named("change_recall").lower_is_better
    assert metric_named("change_precision").aggregation is Aggregation.MICRO
    assert metric_named("judge_score").aggregation is Aggregation.MEAN


def test_score_case_returns_every_metric_with_none_for_not_applicable() -> None:
    scores = score_case(failed_run(), review_case())

    assert set(scores) == {metric.name for metric in METRICS}
    assert scores["human_review"] == Ratio(1, 1)
    assert scores["change_recall"] is None


# --- CER ------------------------------------------------------------------------------------


def test_cer_is_zero_when_the_parsed_text_matches_the_source() -> None:
    assert value(score_cer(case_run(), golden_case())) == 0.0


def test_cer_counts_character_edits_over_both_documents() -> None:
    case = golden_case(source_original="abcd", source_amendment="efgh")
    run = case_run(parsed_original="abXd", parsed_amendment="efgh")

    assert score_cer(run, case) == Ratio(1, 8)


def test_cer_ignores_layout_whitespace_only() -> None:
    case = golden_case(source_original="Cláusula  Primera\nPrecio", source_amendment="Adenda")
    run = case_run(parsed_original="Cláusula Primera Precio", parsed_amendment="Adenda\n")

    assert value(score_cer(run, case)) == 0.0


def test_cer_is_not_applicable_without_parsed_text() -> None:
    assert score_cer(failed_run(), review_case()) is None


def test_cer_is_not_applicable_without_reference_characters() -> None:
    case = golden_case(source_original="", source_amendment=" \n ")

    assert score_cer(case_run(), case) is None


def test_cer_skips_a_page_without_reference_text() -> None:
    case = golden_case(source_original="abcd", source_amendment="")
    run = case_run(parsed_original="abXd", parsed_amendment="texto inventado")

    assert score_cer(run, case) == Ratio(1, 4)


# --- Amendment Kind -------------------------------------------------------------------------


def test_amendment_kind_hit_and_miss() -> None:
    assert score_amendment_kind(case_run(), golden_case()) == Ratio(1, 1)
    assert score_amendment_kind(case_run(amendment_kind="restated"), golden_case()) == Ratio(0, 1)


def test_unknown_is_a_kind_hit_where_the_case_expects_an_unclassifiable_amendment() -> None:
    assert score_amendment_kind(failed_run(), review_case()) == Ratio(1, 1)


def test_any_known_kind_is_a_miss_where_the_case_expects_an_unclassifiable_amendment() -> None:
    run = case_run(case_id="boundary_unknown_demo", amendment_kind="delta")

    assert score_amendment_kind(run, review_case()) == Ratio(0, 1)


def test_unknown_is_a_kind_miss_on_a_case_with_a_declared_kind() -> None:
    assert score_amendment_kind(failed_run(), golden_case()) == Ratio(0, 1)


def test_another_human_review_keeps_the_declared_kind_as_the_truth() -> None:
    case = review_case(expected_error="InvalidExtractionError")

    assert score_amendment_kind(failed_run(), case) == Ratio(0, 1)


def test_amendment_kind_is_not_applicable_when_no_kind_was_produced() -> None:
    run = failed_run(exit_code=6, error="IllegibleDocumentError", amendment_kind=None)

    assert score_amendment_kind(run, golden_case()) is None


# --- Changes: micro precision and recall ----------------------------------------------------


def test_precision_and_recall_count_section_hits() -> None:
    run = case_run(
        output={
            "sections_changed": ["2", "5"],
            "topics_touched": ["price_payment"],
            "summary_of_the_change": "Cambios.",
        }
    )

    assert score_change_precision(run, golden_case()) == Ratio(1, 2)
    assert score_change_recall(run, golden_case()) == Ratio(1, 2)


def test_a_failed_run_on_a_case_with_changes_has_zero_recall_and_no_precision() -> None:
    run = case_run(exit_code=5, error="UpstreamError", **NO_OUTPUT)

    assert score_change_recall(run, golden_case()) == Ratio(0, 2)
    assert score_change_precision(run, golden_case()) is None


def test_precision_and_recall_are_not_applicable_on_human_review_cases() -> None:
    run = case_run(case_id="boundary_unknown_demo")

    assert score_change_precision(run, review_case()) is None
    assert score_change_recall(run, review_case()) is None


def test_a_no_change_case_answered_empty_has_no_precision_or_recall_denominator() -> None:
    case = golden_case(output=EMPTY_OUTPUT, changes=[], key_facts=[])
    run = case_run(output=EMPTY_OUTPUT, changes=[])

    assert score_change_precision(run, case) is None
    assert score_change_recall(run, case) is None


# --- change_type ----------------------------------------------------------------------------


def test_change_type_is_scored_only_on_matched_sections() -> None:
    run = case_run(
        changes=[
            {"section_id": "2", "change_type": "removed", "topic": "price_payment"},
            {"section_id": "new:8", "change_type": "added", "topic": "notices"},
            {"section_id": "9", "change_type": "added", "topic": "other"},
        ]
    )

    assert score_change_type(run, golden_case()) == Ratio(1, 2)


def test_change_type_is_not_applicable_without_matched_sections() -> None:
    run = case_run(changes=[{"section_id": "9", "change_type": "added", "topic": "other"}])

    assert score_change_type(run, golden_case()) is None


# --- Topics ---------------------------------------------------------------------------------


def test_topic_jaccard_is_intersection_over_union() -> None:
    run = case_run(
        output={
            "sections_changed": ["2"],
            "topics_touched": ["price_payment", "term"],
            "summary_of_the_change": "Cambios.",
        }
    )

    assert score_topic_jaccard(run, golden_case()) == Ratio(1, 3)


def test_topic_jaccard_of_two_empty_sets_is_one() -> None:
    case = golden_case(output=EMPTY_OUTPUT, changes=[], key_facts=[])

    assert score_topic_jaccard(case_run(output=EMPTY_OUTPUT), case) == Ratio(1, 1)


def test_topic_jaccard_of_a_failed_run_is_zero_on_a_case_with_topics() -> None:
    assert score_topic_jaccard(case_run(**NO_OUTPUT), golden_case()) == Ratio(0, 2)


def test_topic_jaccard_is_not_applicable_on_human_review_cases() -> None:
    assert score_topic_jaccard(failed_run(), review_case()) is None


# --- Key Facts ------------------------------------------------------------------------------


def test_key_facts_coverage_counts_facts_found_in_the_summary() -> None:
    case = golden_case(key_facts=["$ 2.900.000", "NOTIFICACIONES", "cuarenta y cinco (45) días"])

    assert score_key_facts(case_run(), case) == Ratio(2, 3)


def test_key_facts_match_after_unicode_case_and_space_normalization() -> None:
    case = golden_case(key_facts=["Cuarenta  y cinco\n(45) DÍAS"])
    run = case_run(
        output={
            "sections_changed": ["7"],
            "topics_touched": ["termination"],
            "summary_of_the_change": "El preaviso pasa a cuarenta y cinco (45) días.",
        }
    )

    assert score_key_facts(run, case) == Ratio(1, 1)


def test_key_facts_are_not_applicable_without_declared_facts() -> None:
    assert score_key_facts(case_run(), golden_case(key_facts=[])) is None


def test_key_facts_of_a_failed_run_are_all_missed() -> None:
    assert score_key_facts(case_run(**NO_OUTPUT), golden_case()) == Ratio(0, 2)


# --- Judge ----------------------------------------------------------------------------------


def test_judge_score_is_the_verdict_on_its_1_to_10_scale() -> None:
    assert score_judge(case_run(judge_score=7), golden_case()) == Ratio(7, 1)


def test_judge_score_is_not_applicable_without_a_verdict() -> None:
    assert score_judge(case_run(judge_score=None), golden_case()) is None


# --- Human Review ---------------------------------------------------------------------------


def test_human_review_needs_exit_10_and_the_expected_error() -> None:
    assert score_human_review(failed_run(), review_case()) == Ratio(1, 1)


def test_human_review_accepts_an_issue_code_as_the_expected_error() -> None:
    case = review_case(expected_error="unresolved_section_reference")
    run = failed_run(error="InvalidExtractionError", issue_codes=["unresolved_section_reference"])

    assert score_human_review(run, case) == Ratio(1, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"error": "InvalidExtractionError"},
        {"exit_code": 6, "error": "UnclassifiableAmendmentError"},
    ],
    ids=["other error", "other exit code"],
)
def test_human_review_misses(overrides: dict[str, object]) -> None:
    assert score_human_review(failed_run(**overrides), review_case()) == Ratio(0, 1)


def test_an_answer_where_a_human_review_was_expected_is_a_miss() -> None:
    assert score_human_review(case_run(case_id="boundary_unknown_demo"), review_case()) == Ratio(
        0, 1
    )


def test_human_review_is_not_applicable_when_an_answer_is_expected() -> None:
    assert score_human_review(case_run(), golden_case()) is None


# --- Purity ---------------------------------------------------------------------------------


def test_metrics_module_touches_no_adapter_or_infrastructure() -> None:
    path = Path(__file__).resolve().parents[2] / "evals" / "metrics.py"
    adapters = {"observability", "main", "image_parser", "agents", "judge", "langfuse_publisher"}

    assert runtime_imports(path).isdisjoint(INFRASTRUCTURE | adapters)
