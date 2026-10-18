import re
from zoneinfo import ZoneInfo

from writers_bot.core.extras import parse_roll, roll_html, smash
from writers_bot.core.fmt import HtmlFormatter

F = HtmlFormatter(ZoneInfo("Europe/Kyiv"))


def test_parse_roll():
    r = parse_roll("/2d6+3 Атака")
    assert (r.expression, r.caption, r.mode) == ("2d6+3", "Атака", None)
    assert parse_roll("/2d6 + 3 Атака").expression == "2d6 + 3"  # spaces inside the expression
    assert parse_roll("/d20@writers_bot").expression == "d20"
    assert parse_roll("/roll 4к6").expression == "4d6"  # the Cyrillic ka
    assert parse_roll("/r d20adv").mode == "adv"
    assert parse_roll("/2d6b2").expression == "2d6+2" and parse_roll("/2d6m1").expression == "2d6-1"  # v2 bonuses
    assert parse_roll("/4d6dl1").expression == "4d6pl1"  # Roll20's drop
    assert parse_roll("/r (1d6+2)*2").expression == "(1d6+2)*2"
    assert parse_roll("/d%").expression == "d%"  # percentile
    assert parse_roll("/r hello").expression == ""  # a dice command that isn't a roll: explained
    assert parse_roll("/prompt") is None
    assert parse_roll("/report") is None
    assert parse_roll("/words 2d6") is None


def test_roll_html_bounds_and_escaping():
    r = parse_roll("/3d6 <script>")
    for _ in range(50):
        out = roll_html(F, r)
        total = int(re.search(r"= <b>(-?\d+)</b>", out)[1])
        assert 3 <= total <= 18
    assert "&lt;script&gt;" in out and "<script>" not in out
    adv = roll_html(F, parse_roll("/d20adv"))
    assert "<s>" in adv
    kept = roll_html(F, parse_roll("/4d6kh3"))
    assert kept.count("<s>") == 1  # the dropped die
    assert "до 500" in roll_html(F, parse_roll("/1000d6"))
    assert "Не зрозумів" in roll_html(F, parse_roll("/r hello"))
    assert "&lt;i&gt;" in roll_html(F, parse_roll("/r 1d6 [<i>] x"))  # annotations are escaped


def test_smash():
    for _ in range(50):
        s = smash()
        assert 9 <= len(s) <= 21 and all(c in "qwertyuiop[]asdfghjkl;'zxcvbnm,./" for c in s)


def test_dice_sticker_files():
    """Every result of every die has its sticker, within Telegram's limits for video stickers."""
    from importlib.resources import files

    from writers_bot.modules.dice.telegram import SIDES

    folder = files("writers_bot.assets.dice")
    for sides in SIDES:
        for value in range(1, sides + 1):
            data = folder.joinpath(f"d{sides}_{value}.webm").read_bytes()
            assert data[:4] == b"\x1a\x45\xdf\xa3", (sides, value)  # WEBM (EBML)
            assert len(data) <= 256 * 1024, (sides, value, len(data))
