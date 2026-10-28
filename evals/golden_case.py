"""Golden Case declarations: base contracts, case.yaml, Mutations, Degradations and Splits."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from errors import NeedsHumanReviewError, UnclassifiableAmendmentError
from models import AmendmentKind, Topic, check_section_id, parse_section_id


class GoldenCaseError(ValueError):
    """A Golden Case declaration cannot produce a trustworthy case."""


class SplitLeakError(GoldenCaseError):
    """A case would put a base-contract family in a Split other than the family's (decision 50)."""


class SplitName(StrEnum):
    CALIBRATION = "calibration"
    VALIDATION = "validation"


class ScanKind(StrEnum):
    """Plan §6.6 holds a scan with any declared Degradation to a looser CER target."""

    CLEAN = "clean"
    DEGRADED = "degraded"


class Numbering(StrEnum):
    """How a base contract names its Sections: Cláusula Quinta, Artículo V or 5."""

    CLAUSE = "clausula"
    ARTICLE = "articulo"
    NUMERIC = "numerico"


class TemplateName(StrEnum):
    ONE_COLUMN = "one_column"
    TWO_COLUMN = "two_column"
    TABLE = "table"
    ROMAN_ARTICLES = "roman_articles"


Frozen = ConfigDict(extra="forbid", frozen=True)


class BaseSection(BaseModel):
    model_config = Frozen

    number: int
    title: str
    body: str


class BaseContract(BaseModel):
    """A one-page base contract; its file stem is the family that owns a single Split."""

    model_config = Frozen

    family: str
    split: SplitName
    numbering: Numbering
    title: str
    preamble: str
    closing: str
    restated_heading: str
    restated_preamble: str
    delta_heading: str
    delta_preamble: str
    delta_closing: str
    sections: tuple[BaseSection, ...]
    reviewed_by: str | None = None
    reviewed_at: str | None = None


FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
SECTION_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.M)


def parse_base_contract(markdown: str, family: str) -> BaseContract:
    match = FRONT_MATTER.match(markdown)
    if match is None:
        raise GoldenCaseError(f"base contract {family} has no YAML front matter")
    front_matter, body = match.groups()
    return BaseContract.model_validate(
        {**yaml.safe_load(front_matter), "family": family, "sections": parse_sections(body)}
    )


def parse_sections(body: str) -> list[BaseSection]:
    pieces = SECTION_HEADING.split(body)[1:]  # [number, title, text, number, title, text, ...]
    return [
        BaseSection(number=int(number), title=title.strip(), body=" ".join(text.split()))
        for number, title, text in zip(pieces[::3], pieces[1::3], pieces[2::3], strict=True)
    ]


def load_base_contract(path: Path) -> BaseContract:
    return parse_base_contract(path.read_text(encoding="utf-8"), path.stem)


class MutationOp(StrEnum):
    MODIFY = "modify"
    ADD = "add"
    REMOVE = "remove"
    RENUMBER = "renumber"
    NOOP = "noop"
    # A delta instruction aimed at a Section the original lacks (plan §2.6); never a Change.
    DANGLING = "dangling"


REQUIRED_FIELDS: dict[MutationOp, frozenset[str]] = {
    MutationOp.MODIFY: frozenset({"text", "topic"}),
    MutationOp.ADD: frozenset({"title", "text", "topic"}),
    MutationOp.REMOVE: frozenset({"topic"}),
    MutationOp.RENUMBER: frozenset({"number"}),
    MutationOp.NOOP: frozenset(),
    MutationOp.DANGLING: frozenset({"title", "text"}),
}
# Optional fields each op may declare besides its required ones; anything else is a typo or a
# truth the generator would silently ignore.
OPTIONAL_FIELDS: dict[MutationOp, frozenset[str]] = {
    MutationOp.MODIFY: frozenset({"title", "phrasing"}),
    MutationOp.ADD: frozenset({"after", "phrasing"}),
    MutationOp.REMOVE: frozenset({"phrasing"}),
    MutationOp.RENUMBER: frozenset({"phrasing"}),
    MutationOp.NOOP: frozenset({"phrasing"}),
    MutationOp.DANGLING: frozenset({"phrasing"}),
}
DECLARABLE_FIELDS = frozenset({"title", "text", "topic", "number", "after", "phrasing"})


class Mutation(BaseModel):
    """One declared edit. `section` is an original Section ID, or `new:<n>` for `add`."""

    model_config = Frozen

    op: MutationOp
    section: str
    title: str | None = None
    text: str | None = None
    topic: Topic | None = None
    number: int | None = None
    after: str | None = None
    phrasing: str | None = None

    @model_validator(mode="after")
    def check_declared_fields(self) -> Self:
        check_section_id(self.section)
        required = REQUIRED_FIELDS[self.op]
        declared = {name for name in DECLARABLE_FIELDS if getattr(self, name) is not None}
        if missing := sorted(required - declared):
            raise ValueError(f"{self.op} needs {', '.join(missing)}")
        if extra := sorted(declared - required - OPTIONAL_FIELDS[self.op]):
            raise ValueError(f"{self.op} does not take {', '.join(extra)}")
        if parse_section_id(self.section).is_new != (self.op is MutationOp.ADD):
            raise ValueError(f"only add targets a new: section, got {self.op} {self.section}")
        return self


class DegradationOp(StrEnum):
    ROTATE = "rotate"
    NOISE = "noise"
    BLUR = "blur"
    JPEG = "jpeg"
    DOWNSCALE = "downscale"
    STAMP = "stamp"


class Degradation(BaseModel):
    model_config = Frozen

    op: DegradationOp
    amount: float
    text: str | None = None


def human_review_error_names() -> frozenset[str]:
    """Concrete Human Review errors; the base class itself is never raised."""
    pending, names = list(NeedsHumanReviewError.__subclasses__()), set[str]()
    while pending:
        error = pending.pop()
        names.add(error.__name__)
        pending.extend(error.__subclasses__())
    return frozenset(names)


# The base class name as expected_error means "any Human Review error" (plan §2.6).
ANY_HUMAN_REVIEW = NeedsHumanReviewError.__name__

# Every ValidationIssue code src can emit; a test rebuilds this set from src so it cannot drift.
KNOWN_ISSUE_CODES = frozenset(
    {
        "change_type_mismatch",
        "empty_context_map",
        "empty_section_mapping",
        "inconsistent_extraction",
        "instruction_mismatch",
        "missing_structural_change",
        "missing_text_change",
        "number_parenthetical_dropped",
        "quote_rule_violation",
        "removed_quote_mismatch",
        "schema_violation",
        "section_order",
        "section_origin_mismatch",
        "unchanged_text_reported",
        "ungrounded_quote",
        "ungrounded_reference",
        "unparseable_output",
        "unresolved_reference",
        "unresolved_section_reference",
        "untargeted_delta_change",
    }
)


UNCLASSIFIABLE_ERROR = UnclassifiableAmendmentError.__name__


class UnrelatedDocument(BaseModel):
    """What an Unknown case shows instead of an Amendment: a document that amends nothing."""

    model_config = Frozen

    title: str
    paragraphs: tuple[str, ...]


class CaseSpec(BaseModel):
    """The case.yaml declaration: the source of truth every artifact is derived from."""

    model_config = Frozen

    base: str
    amendment_kind: Literal[AmendmentKind.RESTATED, AmendmentKind.DELTA, AmendmentKind.UNKNOWN]
    category: Literal["standard", "variant", "boundary"]
    split: SplitName
    template: TemplateName
    seed: int
    mutations: tuple[Mutation, ...]
    degradations: tuple[Degradation, ...] = ()
    key_facts: tuple[str, ...]
    reference_summary: str
    expected_error: str | None = None
    # Untrusted text placed in the Amendment to test the prompt-injection defense (plan §4.3).
    injection: str | None = None
    unrelated_document: UnrelatedDocument | None = None

    @field_validator("expected_error")
    @classmethod
    def check_expected_error(cls, value: str | None) -> str | None:
        """A Human Review error name, any of them, or an issue code; a typo is unwinnable."""
        accepted = human_review_error_names() | KNOWN_ISSUE_CODES | {ANY_HUMAN_REVIEW}
        if value is not None and value not in accepted:
            raise ValueError(
                "expected_error must name NeedsHumanReviewError, a subclass or a known issue code"
            )
        return value

    @model_validator(mode="after")
    def check_kind_declarations(self) -> Self:
        if self.amendment_kind is AmendmentKind.UNKNOWN:
            check_unknown_case(self)
        elif self.unrelated_document is not None:
            raise ValueError("only an unknown case declares an unrelated_document")
        if any(mutation.op is MutationOp.DANGLING for mutation in self.mutations):
            check_dangling_case(self)
        return self


def check_unknown_case(case: CaseSpec) -> None:
    """Decision 60: an Unknown Amendment goes straight to Human Review and changes nothing."""
    if case.unrelated_document is None:
        raise ValueError("an unknown case needs the unrelated_document it shows")
    if case.expected_error != UNCLASSIFIABLE_ERROR:
        raise ValueError(f"an unknown case expects {UNCLASSIFIABLE_ERROR}")
    if case.mutations or case.injection is not None:
        raise ValueError("an unknown case declares no mutations and no injection")


def check_dangling_case(case: CaseSpec) -> None:
    """Plan §2.6: a reference to a missing Section ends in a Human Review, not an output."""
    if case.amendment_kind is not AmendmentKind.DELTA:
        raise ValueError("only a delta amendment can reference a missing section")
    if case.expected_error is None:
        raise ValueError("a dangling reference needs the expected_error it ends in")


def load_case_spec(path: Path) -> CaseSpec:
    return CaseSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def assign_split(case: CaseSpec, base: BaseContract) -> SplitName:
    """The family decides the Split; a case can only restate it, never move it."""
    if case.base != base.family:
        raise SplitLeakError(f"case declares base {case.base} but was given {base.family}")
    if case.split is not base.split:
        raise SplitLeakError(
            f"family {base.family} belongs to {base.split}, the case declares {case.split}"
        )
    return base.split
