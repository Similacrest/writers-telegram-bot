import pytest

from writers_bot.core.i18n import WORDS, plural
from writers_bot.core.wordcount import count_words, text_stats


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("", 0),
        ("Привіт, світе!", 2),
        ("п'ять пʼять п’ять", 3),  # three apostrophe variants, one word each
        ("будь-який чорно-білий", 2),
        ("Він сказав — і пішов.", 4),  # a lone dash is not a word
        ("«Так» … – ― ...", 1),
        ("rock'n'roll 2026 рік", 3),
        ("emoji 🙂 here", 2),
        ("snake_case", 2),
        ("слово\nслово\tслово", 3),
    ],
)
def test_count_words(text, words):
    assert count_words(text) == words


def test_text_stats_letters_exclude_digits_and_punctuation():
    stats = text_stats("Аб, 12 вг!")
    assert (stats.words, stats.characters, stats.letters) == (3, 10, 4)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "1 слово"),
        (2, "2 слова"),
        (5, "5 слів"),
        (11, "11 слів"),
        (12, "12 слів"),
        (21, "21 слово"),
        (22, "22 слова"),
        (111, "111 слів"),
        (0, "0 слів"),
    ],
)
def test_plural(n, expected):
    assert plural(n, WORDS) == expected
