"""What the checks compare: a reviewed Golden Case and the Case Run the pipeline produced for it."""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from golden_case import GoldenCaseError, SplitName, load_case_spec
from models import AmendmentKind, ChangeType, ContractChangeOutput, Topic

Frozen = ConfigDict(extra="forbid", frozen=True)


class ChangeRecord(BaseModel):
    """The checkable part of one Change: which Section, how it changed and its Topic."""

    model_config = Frozen

    section_id: str
    change_type: ChangeType
    topic: Topic


class GoldenCase(BaseModel):
    """A reviewed Golden Case: its declared truth plus the files the pipeline reads."""

    model_config = Frozen

    case_id: str
    split: SplitName
    category: str
    amendment_kind: AmendmentKind
    expected_error: str | None
    output: ContractChangeOutput | None
    changes: tuple[ChangeRecord, ...]
    key_facts: tuple[str, ...]
    reference_summary: str
    source_original: str
    source_amendment: str
    original_image: Path
    amendment_image: Path

    @property
    def expects_output(self) -> bool:
        """False when the expected result is a Human Review (exit 10)."""
        return self.expected_error is None and self.output is not None


class CaseRun(BaseModel):
    """Everything one pipeline run left for the checks; the fields a failed run lacks are None."""

    model_config = Frozen

    case_id: str
    exit_code: int
    error: str | None = None
    issue_codes: tuple[str, ...] = ()
    trace_id: str | None = None
    output: ContractChangeOutput | None = None
    amendment_kind: AmendmentKind | None = None
    changes: tuple[ChangeRecord, ...] = ()
    parsed_original: str | None = None
    parsed_amendment: str | None = None
    judge_score: float | None = None
    judge_reasoning: str | None = None


def read_expected(case_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    return payload


def is_reviewed(expected: dict[str, Any]) -> bool:
    """Only a reviewed expected.json is truth; a draft would grade the pipeline on a guess."""
    reviewed_at = expected.get("reviewed_at")
    return reviewed_at is not None and bool(str(reviewed_at).strip())


def ensure_declaration_unchanged(case_dir: Path, expected: dict[str, Any]) -> None:
    declared = hashlib.sha256((case_dir / "case.yaml").read_bytes()).hexdigest()
    if declared != expected.get("declaration_sha256"):
        raise GoldenCaseError(
            f"{case_dir.name}: case.yaml no longer matches its reviewed declaration_sha256"
        )


def load_golden_case(case_dir: Path) -> GoldenCase:
    expected = read_expected(case_dir)
    ensure_declaration_unchanged(case_dir, expected)
    spec = load_case_spec(case_dir / "case.yaml")
    return GoldenCase.model_validate(
        {
            "case_id": case_dir.name,
            "split": expected["split"],
            "category": expected["category"],
            "amendment_kind": expected["amendment_kind"],
            "expected_error": expected["expected_error"],
            "output": expected["output"],
            "changes": expected["changes"],
            "key_facts": expected["key_facts"],
            "reference_summary": spec.reference_summary,
            "source_original": (case_dir / "source_original.txt").read_text(encoding="utf-8"),
            "source_amendment": (case_dir / "source_amendment.txt").read_text(encoding="utf-8"),
            "original_image": case_dir / "original.png",
            "amendment_image": case_dir / "amendment.png",
        }
    )


def load_split(golden_root: Path, split: SplitName) -> list[GoldenCase]:
    """The Split's reviewed cases in case_id order; the Split is read before anything else."""
    case_dirs = sorted(path.parent for path in (golden_root / "cases").glob("*/expected.json"))
    selected = [
        case_dir
        for case_dir in case_dirs
        if (expected := read_expected(case_dir))["split"] == split.value and is_reviewed(expected)
    ]
    return [load_golden_case(case_dir) for case_dir in selected]
