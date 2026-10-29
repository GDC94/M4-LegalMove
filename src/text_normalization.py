"""Grounding normalization shared by the Checks and Section Comparison."""

import unicodedata

# NFKC already folds full-width forms; these are the variants it leaves alone: double quotes
# (curly, low-9, guillemets), single quotes (curly, low-9, prime, accents) and dashes (hyphen,
# non-breaking hyphen, figure, en, em, horizontal bar, minus sign).
QUOTES_AND_DASHES = str.maketrans(
    {
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u00ab\u00bb", '"'),
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032`\u00b4", "'"),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)


def normalize_for_grounding(text: str) -> str:
    """Grounding normalization: NFKC, casefold, unified quotes and dashes, collapsed whitespace."""
    folded = unicodedata.normalize("NFKC", text).casefold().translate(QUOTES_AND_DASHES)
    return " ".join(folded.split())
