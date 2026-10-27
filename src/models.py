"""Pydantic models and enums shared by the parser, agents, checks and CLI."""

import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

ILLEGIBLE_MARK = "[ILLEGIBLE]"

VisionDetail = Literal["low", "high", "auto"]

NEW_SECTION_PREFIX = "new:"
POSITIONAL_MARK = "§"
# Positive integers joined by dots (5, 5.2) or a positional § ID (§7), optionally new:-prefixed.
SECTION_ID_GRAMMAR = re.compile(r"(?:new:)?(?:[1-9]\d*(?:\.[1-9]\d*)*|§[1-9]\d*)")


class DocumentRole(StrEnum):
    """Which of the two input documents a Parsed Contract comes from."""

    ORIGINAL = "original"
    AMENDMENT = "amendment"


class ParsedContract(BaseModel):
    """Verbatim text read from one document image, with its hash and Illegible Mark count."""

    model_config = ConfigDict(frozen=True)

    role: DocumentRole
    source_path: str
    image_sha256: str
    text: str

    @field_validator("text")
    @classmethod
    def check_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @computed_field  # type: ignore[prop-decorator]  # pydantic's documented computed_field idiom
    @property
    def illegible_count(self) -> int:
        """Counted, never reported by the model, so it cannot be hallucinated."""
        return self.text.count(ILLEGIBLE_MARK)


class AmendmentKind(StrEnum):
    """How an Amendment expresses its changes (plan §2.2)."""

    RESTATED = "restated"
    DELTA = "delta"
    UNKNOWN = "unknown"


class SectionIdParts(NamedTuple):
    """A canonical Section ID split into the facts the order checks compare."""

    is_new: bool
    is_positional: bool
    numbers: tuple[int, ...]


def parse_section_id(section_id: str) -> SectionIdParts:
    """Split an ID that already matches the canonical grammar."""
    is_new = section_id.startswith(NEW_SECTION_PREFIX)
    body = section_id.removeprefix(NEW_SECTION_PREFIX)
    is_positional = body.startswith(POSITIONAL_MARK)
    numbers = tuple(int(part) for part in body.removeprefix(POSITIONAL_MARK).split("."))
    return SectionIdParts(is_new=is_new, is_positional=is_positional, numbers=numbers)


def find_order_breaks(section_ids: Sequence[str]) -> Iterator[int]:
    """Indexes that break the canonical order: original Sections first, then new: Sections.

    Numbered and positional IDs are compared only among themselves: their relative position
    cannot be derived from the IDs alone.
    """
    seen_new = False
    highest: dict[tuple[bool, bool], tuple[int, ...]] = {}
    for index, section_id in enumerate(section_ids):
        parts = parse_section_id(section_id)
        shape = (parts.is_new, parts.is_positional)
        previous = highest.get(shape)
        if (seen_new and not parts.is_new) or (previous is not None and parts.numbers < previous):
            yield index
        seen_new = seen_new or parts.is_new
        highest[shape] = max(previous or parts.numbers, parts.numbers)


def check_section_id(value: str) -> str:
    # The value is not echoed: a malformed ID may carry document text into stderr.
    if not SECTION_ID_GRAMMAR.fullmatch(value):
        raise ValueError("section_id does not follow the canonical grammar")
    return value


# The LLM sees these models through a strict json_schema: no Field constraints, only validators
# (decision 25). extra="forbid" emits the additionalProperties: false that strict mode requires.
class SectionMapping(BaseModel):
    """One row of the Context Map: how one Section appears in each document. Never a Change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    title: str | None
    original_ref: str | None
    amendment_ref: str | None
    purpose: str

    @field_validator("section_id")
    @classmethod
    def check_section_id_grammar(cls, value: str) -> str:
        return check_section_id(value)


class ContextMap(BaseModel):
    """The ContextualizationAgent's result: Amendment Kind plus one Section Mapping per Section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning: str
    amendment_kind: AmendmentKind
    sections: list[SectionMapping]

    @model_validator(mode="after")
    def check_unique_section_ids(self) -> Self:
        counts = Counter(section.section_id for section in self.sections)
        duplicates = sorted(section_id for section_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate section_id values: {', '.join(duplicates)}")
        return self


class ChangeType(StrEnum):
    """Whether a Change is an Addition, a Removal or a Modification of a whole Section."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class Topic(StrEnum):
    """Closed taxonomy of legal and commercial Topics; `other` is allowed and monitored."""

    PARTIES = "parties"
    OBJECT_SCOPE = "object_scope"
    PRICE_PAYMENT = "price_payment"
    TERM_DURATION = "term_duration"
    TERMINATION = "termination"
    OBLIGATIONS = "obligations"
    DELIVERABLES_SLA = "deliverables_sla"
    PENALTIES = "penalties"
    LIABILITY_INDEMNITY = "liability_indemnity"
    WARRANTIES = "warranties"
    CONFIDENTIALITY = "confidentiality"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    DATA_PROTECTION = "data_protection"
    INSURANCE = "insurance"
    ASSIGNMENT = "assignment"
    GOVERNING_LAW_DISPUTES = "governing_law_disputes"
    NOTICES = "notices"
    OTHER = "other"


# The single source of the taxonomy: the ExtractionAgent prompt is built from it (plan §4.1).
TOPIC_DESCRIPTIONS: dict[Topic, str] = {
    Topic.PARTIES: "parties, their identification and representation",
    Topic.OBJECT_SCOPE: "object and scope of the contract",
    Topic.PRICE_PAYMENT: "price, payment terms, currency and adjustments",
    Topic.TERM_DURATION: "term, duration, validity and renewal",
    Topic.TERMINATION: "termination, rescission and notice periods to end the contract",
    Topic.OBLIGATIONS: "general obligations of the parties",
    Topic.DELIVERABLES_SLA: "deliverables and service levels",
    Topic.PENALTIES: "fines, penalties, service credits and interest",
    Topic.LIABILITY_INDEMNITY: "liability, indemnity and their limits",
    Topic.WARRANTIES: "warranties",
    Topic.CONFIDENTIALITY: "confidentiality",
    Topic.INTELLECTUAL_PROPERTY: "intellectual property",
    Topic.DATA_PROTECTION: "personal data protection",
    Topic.INSURANCE: "insurance",
    Topic.ASSIGNMENT: "assignment and subcontracting",
    Topic.GOVERNING_LAW_DISPUTES: "governing law, jurisdiction and dispute resolution",
    Topic.NOTICES: "notices and addresses for service",
    Topic.OTHER: "any other subject",
}

NO_CHANGES = "No se identificaron cambios."

# True: the Quote is required; False: it must be None; None: it depends on the Amendment Kind,
# so checks.py decides it with the Context Map (decision 16).
QUOTE_RULES: dict[ChangeType, dict[str, bool | None]] = {
    ChangeType.ADDED: {"original_quote": False, "amendment_quote": True},
    ChangeType.MODIFIED: {"original_quote": True, "amendment_quote": True},
    ChangeType.REMOVED: {"original_quote": True, "amendment_quote": None},
}


def quote_rule_error(message: str) -> PydanticCustomError:
    """Its own error type, so the agent can report Quote rules apart from schema failures."""
    return PydanticCustomError("quote_rule_violation", "{detail}", {"detail": message})


class Change(BaseModel):
    """One Change to a whole Section, with the Quotes that prove it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    change_type: ChangeType
    topic: Topic
    description: str
    original_quote: str | None
    amendment_quote: str | None

    @field_validator("section_id")
    @classmethod
    def check_section_id_grammar(cls, value: str) -> str:
        return check_section_id(value)

    @field_validator("description")
    @classmethod
    def check_description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @model_validator(mode="after")
    def check_quotes_for_change_type(self) -> Self:
        """Only the Kind-independent Quote rules; Quotes are never echoed."""
        for field, required in QUOTE_RULES[self.change_type].items():
            quote: str | None = getattr(self, field)
            present = quote is not None and bool(quote.strip())
            if required is True and not present:
                raise quote_rule_error(f"every {self.change_type} change needs an {field}")
            if required is False and quote is not None:
                raise quote_rule_error(f"every {self.change_type} change must not have an {field}")
        return self


SECTIONS_CHANGED_DESCRIPTION = (
    "Canonical Section IDs that changed: original Sections in original order, then new: "
    "Sections in amendment order."
)
TOPICS_TOUCHED_DESCRIPTION = "Topic values of the changes, in order of first appearance."
SUMMARY_DESCRIPTION = (
    f"Detailed description of the changes, in Spanish; exactly '{NO_CHANGES}' when nothing changed."
)


def check_list_entries(values: list[str]) -> list[str]:
    """Shared by both output models; entries are never echoed, they may be model-invented."""
    if any(not value.strip() for value in values):
        raise ValueError("the list must not contain blank strings")
    if len(set(values)) != len(values):
        raise ValueError("the list must not contain duplicates")
    return values


def check_lists_and_summary(sections: list[str], topics: list[str], summary: str) -> None:
    """Shared by both output models: empty lists go together with the fixed Summary."""
    if bool(sections) != bool(topics):
        raise ValueError("sections_changed and topics_touched must be empty together")
    if not sections and summary != NO_CHANGES:
        raise ValueError(f"the summary must be exactly {NO_CHANGES!r} when nothing changed")
    if sections and summary.strip() == NO_CHANGES:
        raise ValueError(f"{NO_CHANGES!r} is reserved for an empty result")
    if not summary.strip():
        raise ValueError("summary_of_the_change must not be blank")


class ContractChangeOutput(BaseModel):
    """The public contract of the assignment: the three fields Compliance consumes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections_changed: list[str] = Field(description=SECTIONS_CHANGED_DESCRIPTION)
    topics_touched: list[str] = Field(description=TOPICS_TOUCHED_DESCRIPTION)
    summary_of_the_change: str = Field(description=SUMMARY_DESCRIPTION)

    @field_validator("sections_changed", "topics_touched")
    @classmethod
    def check_entries(cls, values: list[str]) -> list[str]:
        return check_list_entries(values)

    @model_validator(mode="after")
    def check_summary(self) -> Self:
        check_lists_and_summary(
            self.sections_changed, self.topics_touched, self.summary_of_the_change
        )
        return self


# Not a subclass of ContractChangeOutput: Pydantic would list the parent's fields first and
# reasoning would come last (decision 15). An architecture test keeps the three fields in sync.
class ExtractionResult(BaseModel):
    """The ExtractionAgent's result: reasoning, the Changes, and the three fields they back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning: str
    changes: list[Change]
    sections_changed: list[str] = Field(description=SECTIONS_CHANGED_DESCRIPTION)
    topics_touched: list[str] = Field(description=TOPICS_TOUCHED_DESCRIPTION)
    summary_of_the_change: str = Field(description=SUMMARY_DESCRIPTION)

    @field_validator("sections_changed")
    @classmethod
    def check_sections(cls, values: list[str]) -> list[str]:
        return [check_section_id(value) for value in check_list_entries(values)]

    @field_validator("topics_touched")
    @classmethod
    def check_topics(cls, values: list[str]) -> list[str]:
        if any(value not in TOPIC_VALUES for value in check_list_entries(values)):
            raise ValueError("topics_touched holds a value that is not a topic value")
        return values

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        """Validated, never corrected: an inconsistency means the model got confused (#6)."""
        check_lists_and_summary(
            self.sections_changed, self.topics_touched, self.summary_of_the_change
        )
        problems = [*find_consistency_problems(self), *find_list_order_problems(self)]
        if problems:
            # A dedicated error type lets the agent tell Consistency from schema failures.
            raise PydanticCustomError(
                "inconsistent_extraction", "{detail}", {"detail": problems[0]}
            )
        return self


TOPIC_VALUES = frozenset(topic.value for topic in Topic)


def unique_in_order(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def find_consistency_problems(result: ExtractionResult) -> Iterator[str]:
    """Names the differences; IDs and topics already passed the grammar and the taxonomy."""
    pairs = (
        ("sections_changed", result.sections_changed, [c.section_id for c in result.changes]),
        ("topics_touched", result.topics_touched, [c.topic.value for c in result.changes]),
    )
    for field, listed, backed in pairs:
        missing = [value for value in unique_in_order(backed) if value not in listed]
        unbacked = [value for value in listed if value not in backed]
        parts = []
        if missing:
            parts.append(f"missing from {field}: {', '.join(missing)}")
        if unbacked:
            parts.append(f"{field} entries not backed by a change: {', '.join(unbacked)}")
        if parts:
            yield f"{field} does not match the changes ({'; '.join(parts)})"


def find_list_order_problems(result: ExtractionResult) -> Iterator[str]:
    first_appearance = unique_in_order([change.topic.value for change in result.changes])
    if result.topics_touched != first_appearance:
        yield "topics_touched must follow the order of first appearance in changes"
    for index in find_order_breaks(result.sections_changed):
        section_id = result.sections_changed[index]
        yield f"sections_changed breaks the canonical order at {section_id}"


def render_summary(changes: Sequence[Change]) -> str:
    """Deterministic public Summary: one sentence per validated Change, in order (#56)."""
    if not changes:
        return NO_CHANGES
    return " ".join(render_change_sentence(change) for change in changes)


TERMINAL_PUNCTUATION = (".", "!", "?", "…")
# Quotes, guillemets and brackets that may close after the sentence's own punctuation.
CLOSING_MARKS = "\"'\u00bb\u201d\u2019)]"


def render_change_sentence(change: Change) -> str:
    description = " ".join(change.description.split())
    if not description.rstrip(CLOSING_MARKS).endswith(TERMINAL_PUNCTUATION):
        description += "."
    return f"{change.section_id} — {change.change_type.value}: {description}"


# One semantic retry per agent at most (plan §4.4).
type SemanticRetries = Literal[0, 1]


@dataclass(frozen=True)
class AgentOutcome[T]:
    """An agent's accepted result and the semantic retries it spent to get it (0 or 1)."""

    result: T
    retries: SemanticRetries
