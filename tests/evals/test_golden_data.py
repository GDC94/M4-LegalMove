"""The committed golden set: base contracts, templates and six Standard cases (plan §6.4, §6.5)."""

import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from string import Template

import pytest
import yaml

from checks import KindHeuristic, detect_kind_heuristic
from generate_golden import GOLDEN_ROOT, TEST_PAIRS, derive_case_expected, derive_expected
from golden_case import (
    BaseContract,
    CaseSpec,
    SplitName,
    TemplateName,
    load_base_contract,
    load_case_spec,
)
from golden_text import (
    block_text,
    build_delta_instructions,
    render_delta_text,
    render_restated_text,
)
from models import AmendmentKind

BASES = sorted((GOLDEN_ROOT / "base").glob("*.md"))
CASE_DIRS = sorted(path.parent for path in (GOLDEN_ROOT / "cases").glob("*/case.yaml"))


def load(case_dir: Path) -> tuple[CaseSpec, BaseContract]:
    case = load_case_spec(case_dir / "case.yaml")
    return case, load_base_contract(GOLDEN_ROOT / "base" / f"{case.base}.md")


def standard_cases() -> list[tuple[CaseSpec, BaseContract]]:
    return [pair for pair in map(load, CASE_DIRS) if pair[0].category == "standard"]


def test_there_are_five_to_eight_base_contracts() -> None:
    assert 5 <= len(BASES) <= 8


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


def test_six_standard_cases_three_restated_three_delta() -> None:
    kinds = Counter(case.amendment_kind for case, _ in standard_cases())

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
    for case, base in map(load, CASE_DIRS):
        if case.amendment_kind == "restated":
            text = render_restated_text(base, case.mutations)
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
