"""Change details: one entry per Change, in extraction order, with the Section title resolved."""

from change_details import ChangeDetail, build_change_details
from models import (
    AmendmentKind,
    Change,
    ChangeType,
    ContextMap,
    ExtractionResult,
    InstructionKind,
    SectionMapping,
    Topic,
)


def mapping(section_id: str, title: str | None) -> SectionMapping:
    return SectionMapping(
        section_id=section_id,
        title=title,
        instruction=InstructionKind.NONE,
        original_ref=None,
        amendment_ref=None,
        purpose="p",
    )


def change(section_id: str, quote: str = "q") -> Change:
    return Change(
        section_id=section_id,
        change_type=ChangeType.MODIFIED,
        topic=Topic.PRICE_PAYMENT,
        description=f"description {section_id}",
        original_quote=f"old {quote}",
        amendment_quote=f"new {quote}",
    )


def extraction_of(*changes: Change) -> ExtractionResult:
    return ExtractionResult(
        reasoning="r",
        changes=list(changes),
        sections_changed=sorted({c.section_id for c in changes}),
        topics_touched=sorted({c.topic.value for c in changes}),
        summary_of_the_change="s",
    )


def context_of(*sections: SectionMapping) -> ContextMap:
    return ContextMap(reasoning="r", amendment_kind=AmendmentKind.DELTA, sections=list(sections))


def test_one_detail_per_change_in_extraction_order() -> None:
    extraction = extraction_of(change("2"), change("1"))

    details = build_change_details(extraction, context_of(mapping("1", "A"), mapping("2", "B")))

    assert [detail.section_id for detail in details] == ["2", "1"]


def test_two_changes_of_the_same_section_give_two_entries() -> None:
    extraction = extraction_of(change("1", "first"), change("1", "second"))

    details = build_change_details(extraction, context_of(mapping("1", "Price")))

    assert [detail.original_quote for detail in details] == ["old first", "old second"]


def test_the_section_title_comes_from_the_context_map() -> None:
    [detail] = build_change_details(extraction_of(change("1")), context_of(mapping("1", "Price")))

    assert detail.section_title == "Price"


def test_the_title_is_none_without_a_title_or_without_a_mapping() -> None:
    extraction = extraction_of(change("1"), change("2"))

    untitled, unmapped = build_change_details(extraction, context_of(mapping("1", None)))

    assert untitled.section_title is None
    assert unmapped.section_title is None


def test_the_fields_and_the_quotes_pass_through_unchanged() -> None:
    odd = Change(
        section_id="1",
        change_type=ChangeType.REMOVED,
        topic=Topic.TERMINATION,
        description="d",
        original_quote="  $ 1.500  ",
        amendment_quote=None,
    )

    [detail] = build_change_details(extraction_of(odd), context_of(mapping("1", "T")))

    assert detail == ChangeDetail(
        section_id="1",
        section_title="T",
        change_type=ChangeType.REMOVED,
        topic=Topic.TERMINATION,
        description="d",
        original_quote="  $ 1.500  ",
        amendment_quote=None,
    )
