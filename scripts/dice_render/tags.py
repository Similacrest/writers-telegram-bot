"""The emoji and search keywords of a sticker: how people find it, and how the bot checks its packs.

Telegram suggests a pack's stickers when someone types an emoji that is on a sticker's list (1-20 emoji each), and
finds them in the sticker search by their keywords (up to 20, 64 characters in all). The bot can read back only the
first emoji of a sticker (``Sticker.emoji``), so that one is the die's marker, and the exact value is in the keywords.

    die         the marker of the die, first: 🪙 d2, 🔶 d3, 🔺 d4, 🎲 d6, 🔷 d8, 🔟 d10, 🕛 d12, 🧊 d20, 💯 d100
    colour      🔴 / 🔵 for the red / blue d12
    property    🌟 crit, 💀 fumble, 🟢 advantage, 🟣 disadvantage (the colours of their halos), ➖ crit-less
    end         ⬆️ the highest roll, ⬇️ the lowest
    value       its digits as keycaps (17: 1️⃣ 7️⃣; a repeated digit once)

Keywords: ``d20``, the value, the property in words (crit, fumble, adv, dis, nocrit), red or blue, and a Ukrainian "кубик".
"""

import clips

DIE = {2: "🪙", 3: "🔶", 4: "🔺", 6: "🎲", 8: "🔷", 10: "🔟", 12: "🕛", 20: "🧊", 100: "💯"}
COLOUR = {"r": "🔴", "b": "🔵"}
PROPERTY = {"crit": "🌟", "fumble": "💀", "adv": "🟢", "dis": "🟣", "critless": "➖"}
HIGHEST, LOWEST = "⬆️", "⬇️"
KEYCAP = {str(d): f"{d}️⃣" for d in range(10)}
WORDS = {"crit": "crit", "fumble": "fumble", "adv": "adv", "dis": "dis", "critless": "nocrit"}
COIN = {1: ("🔱", "arms"), 2: ("", "5")}  # the coat of arms; "5 копійок"
MAX_EMOJI, MAX_KEYWORDS, MAX_KEYWORD_CHARS = 20, 20, 64


def tags(name: str) -> tuple[list[str], list[str]]:
    """The emoji list and the keywords of a clip, e.g. ``d12_12``, ``3d6_1_4_4`` or ``2d20a_5_17``."""
    clip = clips.parse(name)
    if clip.kind != "single":
        return _group(clip)
    sides, (value,) = clip.sides, clip.values
    prop = "critless" if clip.critless else clip.effect
    emoji = [DIE[sides]]
    words = [f"d{sides}", str(value)]
    if sides == 12:
        colour = clip.pieces[0].colour
        emoji.append(COLOUR[colour])
        words.append("red" if colour == "r" else "blue")
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


def _group(clip: clips.Clip) -> tuple[list[str], list[str]]:
    """Two or three dice in one sticker: the die's marker first, the count, the property, the digits of the values."""
    count = len(clip.pieces)
    emoji = [DIE[clip.sides], KEYCAP[str(count)]]
    words = [f"{count}d{clip.sides}", *dict.fromkeys(str(v) for v in clip.values)]
    if clip.variant in ("a", "d"):
        emoji.append(PROPERTY["adv" if clip.variant == "a" else "dis"])
        words.append("adv" if clip.variant == "a" else "dis")
    if clip.kind == "duality":
        emoji += [COLOUR["b"], COLOUR["r"]]
        words.append("duality")
    if clip.effect:
        emoji.append(PROPERTY[clip.effect])
        words.append(WORDS[clip.effect])
    words.append("кубики")
    emoji += [KEYCAP[digit] for digit in dict.fromkeys("".join(map(str, clip.values)))]
    return list(dict.fromkeys(emoji)), words


def check(name: str, emoji: list[str], keywords: list[str]) -> str | None:
    if not 1 <= len(emoji) <= MAX_EMOJI:
        return f"{len(emoji)} emoji"
    if len(keywords) > MAX_KEYWORDS or sum(len(k) for k in keywords) > MAX_KEYWORD_CHARS:
        return f"keywords {keywords} too long"
    return None
