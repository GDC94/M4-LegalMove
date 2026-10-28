"""The committed golden set: base contracts, templates and its 39 cases (plan §6.4, §6.5)."""

import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from string import Template

import pytest
import yaml

from checks import KindHeuristic, detect_kind_heuristic
from generate_golden import (
    GOLDEN_ROOT,
    PAIR_FILES,
    TEST_PAIRS,
    build_amendment_document,
    derive_case_expected,
    derive_expected,
    is_reviewed,
)
from golden_case import (
    BaseContract,
    CaseSpec,
    DegradationOp,
    MutationOp,
    SplitName,
    TemplateName,
    load_base_contract,
    load_case_spec,
)
from golden_text import (
    PHRASINGS,
    block_text,
    build_delta_instructions,
    document_text,
    render_delta_text,
)
from metrics import FACT_STOPWORDS, drop_fact_stopwords, normalize_fact_text
from models import AmendmentKind

BASES = sorted((GOLDEN_ROOT / "base").glob("*.md"))
CASE_DIRS = sorted(path.parent for path in (GOLDEN_ROOT / "cases").glob("*/case.yaml"))


def load(case_dir: Path) -> tuple[CaseSpec, BaseContract]:
    case = load_case_spec(case_dir / "case.yaml")
    return case, load_base_contract(GOLDEN_ROOT / "base" / f"{case.base}.md")


def standard_cases() -> list[tuple[CaseSpec, BaseContract]]:
    return [pair for pair in map(load, CASE_DIRS) if pair[0].category == "standard"]


def split_cases(split: SplitName) -> list[tuple[CaseSpec, BaseContract]]:
    return [pair for pair in map(load, CASE_DIRS) if pair[1].split is split]


def test_there_are_eight_base_contracts() -> None:
    # Plan §6.4 caps the base contracts at 8; the T12 holdout adds the last three families.
    assert len(BASES) == 8


@pytest.mark.parametrize("path", BASES, ids=lambda path: path.stem)
def test_each_base_contract_declares_its_review_fields(path: Path) -> None:
    front_matter = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])

    assert {"reviewed_by", "reviewed_at"} <= set(front_matter)
    assert len(load_base_contract(path).sections) >= 5


def test_there_are_four_html_templates() -> None:
    templates = sorted(path.stem for path in (GOLDEN_ROOT / "templates").glob("*.html"))

    assert templates == sorted(TemplateName)
    for name in templates:
        html = (GOLDEN_ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
        filled = Template(html).substitute(title="Título", blocks="<p>Texto</p>")
        assert "Título" in filled
        assert "<p>Texto</p>" in filled
        assert filled.lstrip().startswith("<!doctype html>")


def test_calibration_has_six_standard_cases_three_restated_three_delta() -> None:
    kinds = Counter(case.amendment_kind for case, _ in cases_in("standard"))

    assert kinds == {AmendmentKind.RESTATED: 3, AmendmentKind.DELTA: 3}


def test_standard_cases_are_clean_scans() -> None:
    # Plan §6.5: Standard means clear changes on a clean scan; Degradations start at Boundary.
    assert all(case.degradations == () for case, _ in standard_cases())


def test_standard_cases_change_one_to_three_sections() -> None:
    for case, base in standard_cases():
        assert 1 <= len(derive_expected(case, base, "")["changes"]) <= 3


def test_every_family_stays_in_one_split_and_both_splits_are_used() -> None:
    splits: defaultdict[str, set[str]] = defaultdict(set)
    for case, base in map(load, CASE_DIRS):
        splits[base.family].add(derive_expected(case, base, "")["split"])

    assert all(len(split) == 1 for split in splits.values()), dict(splits)
    assert set().union(*splits.values()) == set(SplitName)


# Decisions 59 and 92: blind validation run 1 missed two targets, so the families it showed
# are no longer blind; they never return to `validation`.
SEEN_IN_VALIDATION_RUN_1 = {"consultoria_profesional", "licencia_software", "locacion_oficina"}


def test_families_seen_by_validation_run_1_moved_to_calibration() -> None:
    seen = [load_base_contract(path) for path in BASES if path.stem in SEEN_IN_VALIDATION_RUN_1]

    assert {base.family for base in seen} == SEEN_IN_VALIDATION_RUN_1
    assert {base.split for base in seen} == {SplitName.CALIBRATION}


def delta_cases() -> list[tuple[CaseSpec, BaseContract]]:
    return [pair for pair in map(load, CASE_DIRS) if pair[0].amendment_kind == "delta"]


def test_every_delta_case_has_an_instruction_the_heuristic_does_not_know() -> None:
    for case, base in delta_cases():
        instructions = build_delta_instructions(base, case.mutations, case.seed)

        assert any(
            detect_kind_heuristic(block_text(block)) is KindHeuristic.INCONCLUSIVE
            for block in instructions
        ), case.base


def test_some_delta_amendment_escapes_the_heuristic_entirely() -> None:
    verdicts = [
        detect_kind_heuristic(render_delta_text(base, case.mutations, case.seed))
        for case, base in delta_cases()
    ]

    assert KindHeuristic.INCONCLUSIVE in verdicts


def test_restated_amendments_carry_no_heuristic_marker() -> None:
    # The whole page, injection included: a marker there would grade the heuristic, not the case.
    for case, base in map(load, CASE_DIRS):
        if case.amendment_kind == "restated":
            text = document_text(build_amendment_document(case, base))
            assert detect_kind_heuristic(text) is KindHeuristic.INCONCLUSIVE, case.base


def test_pairs_a_and_b_come_from_a_restated_and_a_delta_standard_case() -> None:
    kinds = {
        pair: load(GOLDEN_ROOT / "cases" / case_id)[0].amendment_kind
        for pair, case_id in TEST_PAIRS.items()
    }

    assert kinds == {"pair_a": AmendmentKind.RESTATED, "pair_b": AmendmentKind.DELTA}


def test_pair_b_keeps_a_heuristic_marker_so_kind_agreement_stays_measurable() -> None:
    case, base = load(GOLDEN_ROOT / "cases" / TEST_PAIRS["pair_b"])

    assert detect_kind_heuristic(render_delta_text(base, case.mutations, case.seed)) == "delta"


@pytest.mark.parametrize(
    ("pair", "sections", "topics"),
    [
        ("pair_a", ["3", "new:8"], ["price_payment", "confidentiality"]),
        ("pair_b", ["2", "5"], ["term_duration", "penalties"]),
    ],
)
def test_pair_cases_keep_the_truth_the_integration_tests_assert(
    pair: str, sections: list[str], topics: list[str]
) -> None:
    case, base = load(GOLDEN_ROOT / "cases" / TEST_PAIRS[pair])

    output = derive_expected(case, base, "")["output"]

    assert output["sections_changed"] == sections
    assert output["topics_touched"] == topics


@pytest.mark.parametrize(
    ("case_id", "section"),
    [
        ("boundary_restated_licencia_renumber", "new:5"),
        ("boundary_restated_servicios_renumber", "new:3"),
    ],
)
def test_added_training_and_onboarding_sections_are_deliverables(
    case_id: str, section: str
) -> None:
    # Decision 96: a Section a party must provide (training, a migration) is deliverables_sla.
    expected = json.loads((GOLDEN_ROOT / "cases" / case_id / "expected.json").read_text("utf-8"))

    assert expected["changes"] == [
        {"section_id": section, "change_type": "added", "topic": "deliverables_sla"}
    ]
    assert expected["output"]["topics_touched"] == ["deliverables_sla"]


def without_review(expected: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in expected.items() if not key.startswith("reviewed_")}


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=lambda path: path.name)
def test_a_written_expected_json_still_matches_its_declaration(case_dir: Path) -> None:
    written_path = case_dir / "expected.json"
    if not written_path.is_file():
        pytest.skip(f"{case_dir.name} has not been generated yet")
    written = json.loads(written_path.read_text(encoding="utf-8"))
    declared = derive_case_expected(case_dir, GOLDEN_ROOT)

    assert written["declaration_sha256"] == declared["declaration_sha256"]
    assert without_review(written) == without_review(declared)


def test_derive_case_expected_hashes_the_case_yaml_bytes() -> None:
    case_dir = CASE_DIRS[0]

    declared = derive_case_expected(case_dir, GOLDEN_ROOT)

    assert (
        declared["declaration_sha256"] == sha256((case_dir / "case.yaml").read_bytes()).hexdigest()
    )


# --- T9: full coverage (plan §2.6, §6.5) ---


def cases_in(category: str) -> list[tuple[CaseSpec, BaseContract]]:
    """Calibration cases of one category: the split that carries the full §6.5 table."""
    return [pair for pair in split_cases(SplitName.CALIBRATION) if pair[0].category == category]


def test_calibration_has_25_cases_6_standard_10_variant_9_boundary() -> None:
    # Decision 98: the §6.5 table plus one restated Variant without changes on a stamped scan.
    categories = Counter(case.category for case, _ in split_cases(SplitName.CALIBRATION))

    assert categories == {"standard": 6, "variant": 10, "boundary": 9}


@pytest.mark.parametrize(
    ("category", "kinds"),
    [
        ("variant", {AmendmentKind.RESTATED: 5, AmendmentKind.DELTA: 5}),
        # Plan §6.5 counts 4 restated and 5 delta; the unknown case takes one delta slot,
        # since a document that is not an Amendment has neither kind.
        ("boundary", {AmendmentKind.RESTATED: 4, AmendmentKind.DELTA: 4, AmendmentKind.UNKNOWN: 1}),
    ],
)
def test_each_category_mixes_restated_and_delta(
    category: str, kinds: dict[AmendmentKind, int]
) -> None:
    assert Counter(case.amendment_kind for case, _ in cases_in(category)) == kinds


def test_the_golden_set_has_25_calibration_and_14_validation_cases() -> None:
    # Decision 93: run 1's cases stay in calibration and T12 adds a 14-case holdout; decision 98
    # adds a restated case without changes to calibration.
    splits = Counter(base.split for _, base in map(load, CASE_DIRS))

    assert splits == {SplitName.CALIBRATION: 25, SplitName.VALIDATION: 14}


def test_calibration_has_an_unchanged_amendment_of_each_kind_and_one_is_degraded() -> None:
    # Decision 98: the missing_text_change guard must stay quiet when only the scan differs, so
    # calibration holds a restated amendment without changes on a stamped, noisy scan.
    unchanged = [
        case
        for case, base in split_cases(SplitName.CALIBRATION)
        if not derive_expected(case, base, "")["changes"] and case.expected_error is None
    ]

    assert {case.amendment_kind for case in unchanged} == {
        AmendmentKind.RESTATED,
        AmendmentKind.DELTA,
    }
    restated = [case for case in unchanged if case.amendment_kind is AmendmentKind.RESTATED]
    assert any(
        {DegradationOp.STAMP, DegradationOp.NOISE} <= {d.op for d in case.degradations}
        for case in restated
    )


def is_internal_edit(case: CaseSpec, base: BaseContract) -> bool:
    """A modify that adds or drops a paragraph or sentence and keeps the rest of the Section."""
    bodies = {str(section.number): section.body for section in base.sections}
    return any(
        mutation.op is MutationOp.MODIFY
        and mutation.text is not None
        and mutation.section in bodies
        and (bodies[mutation.section] in mutation.text or mutation.text in bodies[mutation.section])
        for mutation in case.mutations
    )


def is_identical_rewrite(case: CaseSpec) -> bool:
    """A delta noop worded as a modification: the clause is rewritten with the same text."""
    return case.amendment_kind == "delta" and any(
        mutation.op is MutationOp.NOOP
        and PHRASINGS[MutationOp.NOOP].get(mutation.phrasing or "")
        in PHRASINGS[MutationOp.MODIFY].values()
        for mutation in case.mutations
    )


SCAN_DEGRADATIONS = {DegradationOp.BLUR, DegradationOp.NOISE, DegradationOp.DOWNSCALE}


def adversarial_categories(case: CaseSpec, base: BaseContract) -> set[str]:
    ops = {mutation.op for mutation in case.mutations}
    detected = {
        "injection": case.injection is not None,
        "dangling_reference": MutationOp.DANGLING in ops,
        "identical_rewrite": is_identical_rewrite(case),
        "internal_paragraph": is_internal_edit(case, base),
        "renumbering": MutationOp.RENUMBER in ops,
        "degradation": bool(SCAN_DEGRADATIONS & {d.op for d in case.degradations}),
        "unknown": case.amendment_kind == "unknown",
    }
    return {name for name, present in detected.items() if present}


ADVERSARIAL = {
    "injection",
    "dangling_reference",
    "identical_rewrite",
    "internal_paragraph",
    "renumbering",
    "degradation",
    "unknown",
}


def covered(pairs: list[tuple[CaseSpec, BaseContract]]) -> set[str]:
    return set().union(*(adversarial_categories(case, base) for case, base in pairs))


def test_variant_and_boundary_cases_cover_every_adversarial_category() -> None:
    assert covered(cases_in("variant") + cases_in("boundary")) == ADVERSARIAL


def test_calibration_covers_every_adversarial_category_so_tuning_sees_each_one() -> None:
    calibration = [pair for pair in map(load, CASE_DIRS) if pair[1].split is SplitName.CALIBRATION]

    assert covered(calibration) == ADVERSARIAL


def scan_kind(case: CaseSpec) -> str:
    return "degraded" if case.degradations else "clean"


def composition(pairs: list[tuple[CaseSpec, BaseContract]]) -> Counter[tuple[object, ...]]:
    """Each case as category, kind, scan and adversarial categories; the family is left out."""
    return Counter(
        (
            case.category,
            case.amendment_kind,
            scan_kind(case),
            tuple(sorted(adversarial_categories(case, base))),
        )
        for case, base in pairs
    )


VALIDATION_RUN_1 = Path(__file__).parents[2] / "outputs" / "validation_run1.json"


def validation_run_1_cases() -> list[tuple[CaseSpec, BaseContract]]:
    report = json.loads(VALIDATION_RUN_1.read_text(encoding="utf-8"))
    return [load(GOLDEN_ROOT / "cases" / case["case_id"]) for case in report["cases"]]


def test_validation_run_1_cases_stay_in_calibration() -> None:
    cases = validation_run_1_cases()

    assert len(cases) == 14
    assert {base.split for _, base in cases} == {SplitName.CALIBRATION}


def test_the_holdout_mirrors_the_composition_of_validation_run_1() -> None:
    # Decision 93: the new holdout is compared case by case against run 1, so it repeats the
    # category, kind, scan and adversarial categories of each run-1 case.
    holdout = split_cases(SplitName.VALIDATION)

    assert composition(holdout) == composition(validation_run_1_cases())


def test_the_holdout_covers_every_adversarial_category_of_validation_run_1() -> None:
    run_1 = covered(validation_run_1_cases())

    assert run_1
    assert covered(split_cases(SplitName.VALIDATION)) == run_1


def test_the_holdout_uses_three_families_never_seen_before() -> None:
    families = {base.family for _, base in split_cases(SplitName.VALIDATION)}
    calibration = {base.family for _, base in split_cases(SplitName.CALIBRATION)}

    assert len(families) == 3
    assert not families & (calibration | SEEN_IN_VALIDATION_RUN_1)


@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=lambda path: path.name)
def test_every_case_is_generated(case_dir: Path) -> None:
    assert all((case_dir / name).is_file() for name in (*PAIR_FILES, "expected.json"))


def test_every_case_is_reviewed() -> None:
    pending = [case_dir.name for case_dir in CASE_DIRS if not is_reviewed(case_dir)]

    assert pending == []


def test_dangling_cases_accept_any_human_review() -> None:
    # Plan §2.6: "error o revisión humana"; the pipeline may stop at the map or the extraction.
    dangling = [
        case
        for case, _ in map(load, CASE_DIRS)
        if any(mutation.op is MutationOp.DANGLING for mutation in case.mutations)
    ]

    assert dangling
    assert {case.expected_error for case in dangling} == {"NeedsHumanReviewError"}


def is_atomic_fact(fact: str) -> bool:
    words = normalize_fact_text(fact).split()
    edges = {words[0], words[-1]} if words else set()
    return bool(drop_fact_stopwords(" ".join(words))) and not edges & FACT_STOPWORDS


def test_calibration_key_facts_keep_content_once_stopwords_are_dropped() -> None:
    # T13: the matcher drops FACT_STOPWORDS on both sides, so a fact must keep content words
    # and must not lean on a leading or trailing article or preposition.
    facts = [
        (case_dir.name, fact)
        for case_dir in CASE_DIRS
        for case, base in [load(case_dir)]
        if base.split is SplitName.CALIBRATION
        for fact in case.key_facts
    ]

    assert facts
    assert [(name, fact) for name, fact in facts if not is_atomic_fact(fact)] == []
