"""Golden Case declarations: base contracts, case.yaml, Mutations, Degradations and Splits."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from models import AmendmentKind, Topic, check_section_id, parse_section_id


class GoldenCaseError(ValueError):
    """A Golden Case declaration cannot produce a trustworthy case."""


class SplitLeakError(GoldenCaseError):
    """A case would put a base-contract family in a Split other than the family's (decision 50)."""


class SplitName(StrEnum):
    CALIBRATION = "calibration"
    VALIDATION = "validation"


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


REQUIRED_FIELDS: dict[MutationOp, frozenset[str]] = {
    MutationOp.MODIFY: frozenset({"text", "topic"}),
    MutationOp.ADD: frozenset({"title", "text", "topic"}),
    MutationOp.REMOVE: frozenset({"topic"}),
    MutationOp.RENUMBER: frozenset({"number"}),
    MutationOp.NOOP: frozenset(),
}
# Optional fields each op may declare besides its required ones; anything else is a typo or a
# truth the generator would silently ignore.
OPTIONAL_FIELDS: dict[MutationOp, frozenset[str]] = {
    MutationOp.MODIFY: frozenset({"title", "phrasing"}),
    MutationOp.ADD: frozenset({"after", "phrasing"}),
    MutationOp.REMOVE: frozenset({"phrasing"}),
    MutationOp.RENUMBER: frozenset({"phrasing"}),
    MutationOp.NOOP: frozenset({"phrasing"}),
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


class CaseSpec(BaseModel):
    """The case.yaml declaration: the source of truth every artifact is derived from."""

    model_config = Frozen

    base: str
    amendment_kind: Literal[AmendmentKind.RESTATED, AmendmentKind.DELTA]
    category: Literal["standard", "variant", "boundary"]
    split: SplitName
    template: TemplateName
    seed: int
    mutations: tuple[Mutation, ...]
    degradations: tuple[Degradation, ...] = ()
    key_facts: tuple[str, ...]
    reference_summary: str
    expected_error: str | None = None


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
