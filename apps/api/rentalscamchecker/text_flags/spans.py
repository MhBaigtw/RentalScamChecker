"""Check that a quote the model returned really appears in the listing text (ED-024).

Matching tolerates differences in letter case and whitespace (the model may join lines or change
spacing) and in curly vs straight quotes, but nothing else: a paraphrase is not evidence. The
returned span is cut from the ORIGINAL text, so what users see is always the listing's own words.
"""

import re

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _normalize(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space, casefold, straighten quotes. Also returns, for each
    character of the normalized text, its index in the original text."""
    chars: list[str] = []
    index: list[int] = []
    previous_space = True  # drops leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace():
            if not previous_space:
                chars.append(" ")
                index.append(i)
            previous_space = True
            continue
        folded = ch.translate(_QUOTE_CHARS).casefold()
        for c in folded:  # casefold can expand one character into several
            chars.append(c)
            index.append(i)
        previous_space = False
    while chars and chars[-1] == " ":
        chars.pop()
        index.pop()
    return "".join(chars), index


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) of the quote in `text`, or None if it isn't there."""
    needle, _ = _normalize(quote)
    if len(needle) < 3:
        return None  # too short to be meaningful evidence
    haystack, index = _normalize(text)
    position = haystack.find(needle)
    if position < 0:
        return None
    start = index[position]
    end = index[position + len(needle) - 1] + 1
    return start, end


def snippet(text: str, start: int, end: int, limit: int = 300) -> str:
    """The original text of the span, whitespace tidied, at most `limit` characters."""
    piece = re.sub(r"\s+", " ", text[start:end]).strip()
    return piece if len(piece) <= limit else piece[: limit - 1] + "…"
