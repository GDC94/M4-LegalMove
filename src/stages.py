"""Names of the Stages the pipeline traces; the one vocabulary shared by tracer and API."""

from enum import StrEnum

from models import DocumentRole


class StageName(StrEnum):
    """Every span name a Stage may open; a literal at a `tracer.stage()` call is a test failure."""

    PARSE_ORIGINAL_CONTRACT = "parse_original_contract"
    PARSE_AMENDMENT_CONTRACT = "parse_amendment_contract"
    CONTEXTUALIZATION_AGENT = "contextualization_agent"
    EXTRACTION_AGENT = "extraction_agent"
    OUTPUT_VALIDATION = "output_validation"


def parse_stage_for(role: DocumentRole) -> StageName:
    """The parse Stage of one Contract role."""
    if role is DocumentRole.ORIGINAL:
        return StageName.PARSE_ORIGINAL_CONTRACT
    return StageName.PARSE_AMENDMENT_CONTRACT
