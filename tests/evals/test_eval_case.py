"""Loading a Split's reviewed Golden Cases from data/golden for the eval runner."""

import ast
import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from eval_case import load_golden_case, load_split
from golden_case import KNOWN_ISSUE_CODES, CaseSpec, SplitName
from models import AmendmentKind, ChangeType

GOLDEN = Path(__file__).resolve().parents[2] / "data" / "golden"
SRC = Path(__file__).resolve().parents[2] / "src"


def test_calibration_holds_the_ten_reviewed_cases() -> None:
    cases = load_split(GOLDEN, SplitName.CALIBRATION)

    assert [case.case_id for case in cases] == [
        "boundary_delta_servicios_identical",
        "boundary_delta_suministro_dangling",
        "boundary_delta_suministro_injection",
        "boundary_restated_servicios_renumber",
        "boundary_unknown_servicios",
        "standard_delta_servicios",
        "standard_delta_suministro",
        "standard_restated_servicios",
        "variant_delta_servicios",
        "variant_restated_suministro",
    ]
    assert {case.split for case in cases} == {SplitName.CALIBRATION}


def test_a_loaded_case_carries_its_truth_sources_and_images() -> None:
    case = load_golden_case(GOLDEN / "cases" / "standard_delta_consultoria")

    assert case.amendment_kind is AmendmentKind.DELTA
    assert case.category == "standard"
    assert case.expected_error is None
    assert case.output is not None
    assert case.output.sections_changed == ["2", "7", "new:8"]
    assert case.changes[2].change_type is ChangeType.ADDED
    assert case.key_facts == ("$ 2.900.000", "cuarenta y cinco (45) días", "notificaciones")
    assert case.reference_summary.startswith("Los honorarios mensuales")
    assert "$ 2.400.000" in case.source_original
    assert "$ 2.900.000" in case.source_amendment
    assert case.original_image.name == "original.png" and case.original_image.is_file()
    assert case.amendment_image.name == "amendment.png" and case.amendment_image.is_file()


def copy_case(tmp_path: Path, case_id: str) -> Path:
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN / "cases" / case_id, golden / "cases" / case_id)
    return golden


def test_unreviewed_cases_are_left_out_of_the_split(tmp_path: Path) -> None:
    golden = copy_case(tmp_path, "standard_delta_servicios")
    expected = golden / "cases" / "standard_delta_servicios" / "expected.json"
    payload = json.loads(expected.read_text(encoding="utf-8"))
    expected.write_text(json.dumps({**payload, "reviewed_at": None}), encoding="utf-8")

    assert load_split(golden, SplitName.CALIBRATION) == []


def test_cases_of_the_other_split_are_left_out(tmp_path: Path) -> None:
    golden = copy_case(tmp_path, "standard_delta_consultoria")

    assert load_split(golden, SplitName.CALIBRATION) == []
    assert len(load_split(golden, SplitName.VALIDATION)) == 1


def test_an_edited_declaration_after_review_is_refused(tmp_path: Path) -> None:
    golden = copy_case(tmp_path, "standard_delta_servicios")
    declaration = golden / "cases" / "standard_delta_servicios" / "case.yaml"
    declaration.write_text(declaration.read_text(encoding="utf-8") + "\n# edited\n", "utf-8")

    with pytest.raises(ValueError, match="declaration_sha256"):
        load_split(golden, SplitName.CALIBRATION)


def declaration(**overrides: object) -> dict[str, object]:
    path = GOLDEN / "cases" / "standard_delta_servicios" / "case.yaml"
    loaded: dict[str, object] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {**loaded, **overrides}


@pytest.mark.parametrize(
    "expected_error",
    ["UnclassifiableAmendmentError", "InvalidExtractionError", "unresolved_section_reference"],
)
def test_expected_error_accepts_human_review_errors_and_issue_codes(expected_error: str) -> None:
    spec = CaseSpec.model_validate(declaration(expected_error=expected_error))

    assert spec.expected_error == expected_error


@pytest.mark.parametrize(
    "expected_error", ["IllegibleDocumentError", "UpstreamError", "unresolved_reference"]
)
def test_expected_error_rejects_anything_the_pipeline_cannot_report(expected_error: str) -> None:
    with pytest.raises(ValidationError, match="expected_error"):
        CaseSpec.model_validate(declaration(expected_error=expected_error))


def issue_code_literals() -> set[str]:
    """First literal argument of every call that builds a ValidationIssue code in src."""
    builders = {"build_feedback", "ungrounded_feedback", "PydanticCustomError"}
    codes: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            first = node.args[0]
            if name in builders and isinstance(first, ast.Constant):
                codes.add(str(first.value))
    return codes


def test_known_issue_codes_are_exactly_the_codes_src_can_emit() -> None:
    assert issue_code_literals() == KNOWN_ISSUE_CODES
