"""The single safe serialisation for every dynamic prompt block, and the Context Map render."""

import json
import re

from models import ContextMap, SectionMapping

SAFE_TAG = re.compile(r"[a-z][a-z_]*")


def serialize_block(tag: str, content: str) -> str:
    """Wrap untrusted content in its tag so the content can never close the block itself.

    Documents and model outputs are untrusted data (plan §4.3): every `<` that starts a closing
    tag of this block, in any case or spacing, becomes `&lt;`. Everything else stays verbatim so
    quotes copied from the block can still be grounded.
    """
    if not SAFE_TAG.fullmatch(tag):
        raise ValueError("tag must be lower-case letters and underscores")
    closing = re.compile(rf"<(?=\s*/\s*{tag}\b)", re.IGNORECASE)
    return f"<{tag}>\n{closing.sub('&lt;', content)}\n</{tag}>"


def render_context_map(context_map: ContextMap) -> str:
    """Plain-text `<context_map>` for the ExtractionAgent: kind line plus one line per Section."""
    lines = [f"amendment_kind: {context_map.amendment_kind.value}"]
    lines.extend(render_section_line(section) for section in context_map.sections)
    return serialize_block("context_map", "\n".join(lines))


def render_section_line(section: SectionMapping) -> str:
    title = f" {collapse_whitespace(section.title)}" if section.title else ""
    return (
        f"[{section.section_id}]{title}"
        f" | original: {render_ref(section.original_ref)}"
        f" | amendment: {render_ref(section.amendment_ref)}"
        f" | purpose: {collapse_whitespace(section.purpose)}"
    )


def render_ref(ref: str | None) -> str:
    """JSON quoting keeps a ref on one line and unambiguous, even with quotes inside."""
    return "none" if ref is None else json.dumps(ref, ensure_ascii=False)


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
