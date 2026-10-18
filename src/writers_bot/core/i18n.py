"""Ukrainian plural forms used across the bot."""

MINUTES = ("хвилину", "хвилини", "хвилин")
WORDS = ("слово", "слова", "слів")
CHARACTERS = ("символ", "символи", "символів")
LETTERS = ("літера", "літери", "літер")
SPRINTS = ("спринт", "спринти", "спринтів")


def plural(num: int, forms: tuple[str, str, str]) -> str:
    """Pick the Ukrainian plural form for ``num``: 1 слово, 2 слова, 5 слів."""
    n = abs(num)
    if n % 10 == 1 and n % 100 != 11:
        form = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{num} {form}"
