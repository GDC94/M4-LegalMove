"""Change details: each Change with its Section title, for the API body (plan decision 113)."""

from pydantic import BaseModel, ConfigDict

from models import ChangeType, ContextMap, ExtractionResult, Topic


class ChangeDetail(BaseModel):
    """One Change as the web client shows it: the Quotes are the contract's literal text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    section_title: str | None
    change_type: ChangeType
    topic: Topic
    description: str
    original_quote: str | None
    amendment_quote: str | None


def build_change_details(
    extraction: ExtractionResult, context_map: ContextMap
) -> list[ChangeDetail]:
    """One entry per Change, in `extraction.changes` order (the order of the summary sentences)."""
    titles = {section.section_id: section.title for section in context_map.sections}
    return [
        ChangeDetail(
            section_id=change.section_id,
            section_title=titles.get(change.section_id),
            change_type=change.change_type,
            topic=change.topic,
            description=change.description,
            original_quote=change.original_quote,
            amendment_quote=change.amendment_quote,
        )
        for change in extraction.changes
    ]
