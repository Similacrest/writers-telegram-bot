"""The emoji and search keywords of a sticker: how people find it, and how the bot checks its packs.

Telegram suggests a pack's stickers when someone types an emoji that is on a sticker's list (1-20 emoji each), and
finds them in the sticker search by their keywords (up to 20, 64 characters in all). The bot can read back only the
first emoji of a sticker (``Sticker.emoji``), so that one is the die's marker, and the exact value is in the keywords.

    die         the marker of the die, first: 🪙 d2, 🔺 d4, 🎲 d6, 🔷 d8, 🔟 d10, 🕛 d12, 🧊 d20, 💯 d100
    colour      🔴 / 🔵 for the red / blue d12
    property    🌟 crit, 💀 fumble, 🟢 advantage, 🟣 disadvantage (the colours of their halos), ➖ crit-less
    end         ⬆️ the highest roll, ⬇️ the lowest
    value       its digits as keycaps (17: 1️⃣ 7️⃣; a repeated digit once)

Keywords: ``d20``, the value, the property in words (crit, fumble, adv, dis, nocrit), red or blue, and a Ukrainian "кубик".
"""

import render

DIE = {2: "🪙", 4: "🔺", 6: "🎲", 8: "🔷", 10: "🔟", 12: "🕛", 20: "🧊", 100: "💯"}
COLOUR = {"r": "🔴", "b": "🔵"}
PROPERTY = {"crit": "🌟", "fumble": "💀", "adv": "🟢", "dis": "🟣", "critless": "➖"}
HIGHEST, LOWEST = "⬆️", "⬇️"
KEYCAP = {str(d): f"{d}️⃣" for d in range(10)}
WORDS = {"crit": "crit", "fumble": "fumble", "adv": "adv", "dis": "dis", "critless": "nocrit"}
COIN = {1: ("🔱", "arms"), 2: ("", "5")}  # the coat of arms; "5 копійок"
MAX_EMOJI, MAX_KEYWORDS, MAX_KEYWORD_CHARS = 20, 20, 64


def tags(name: str) -> tuple[list[str], list[str]]:
    """The emoji list and the keywords of a clip, e.g. ``d12ba_12``."""
    _, sides, value, variant = render.job(name)
    effect = render.effect_of(sides, value, variant)
    critless = variant == "n" or (sides == 12 and variant in ("r", "b") and value in (1, 12))
    prop = "critless" if critless else effect
    emoji = [DIE[sides]]
    words = [f"d{sides}" if sides != 100 else "d100", str(value)]
    if sides == 12:
        emoji.append(COLOUR[variant[0]])
        words.append("red" if variant[0] == "r" else "blue")
    if prop:
        emoji.append(PROPERTY[prop])
        words.append(WORDS[prop])
    if sides == 2:
        extra, word = COIN[value]
        emoji += [extra] if extra else []
        words += ["coin", word, "монета"]
    else:
        if value == sides:
            emoji.append(HIGHEST)
            words.append("max")
        elif value == 1:
            emoji.append(LOWEST)
            words.append("min")
        words.append("кубик")
    emoji += [KEYCAP[digit] for digit in dict.fromkeys(str(value))]
    return list(dict.fromkeys(emoji)), words


def check(name: str, emoji: list[str], keywords: list[str]) -> str | None:
    if not 1 <= len(emoji) <= MAX_EMOJI:
        return f"{len(emoji)} emoji"
    if len(keywords) > MAX_KEYWORDS or sum(len(k) for k in keywords) > MAX_KEYWORD_CHARS:
        return f"keywords {keywords} too long"
    return None
