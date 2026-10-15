"""Word counting that behaves sensibly for Ukrainian (and other Unicode) prose."""

import re
from dataclasses import dataclass

# A word is a run of letters/digits, optionally joined by apostrophes or hyphens:
# "п'ять", "пʼять", "будь-який", "rock'n'roll" are one word each. Dashes, «», "…" alone are not words.
_JOINERS = "'’ʼ`\\-‐‑"
WORD_RE = re.compile(rf"[^\W_]+(?:[{_JOINERS}][^\W_]+)*")


@dataclass(frozen=True, slots=True)
class TextStats:
    words: int
    characters: int
    letters: int


def count_words(text: str) -> int:
    return sum(1 for _ in WORD_RE.finditer(text))


def text_stats(text: str) -> TextStats:
    return TextStats(
        words=count_words(text),
        characters=len(text),
        letters=sum(1 for ch in text if ch.isalpha()),
    )
