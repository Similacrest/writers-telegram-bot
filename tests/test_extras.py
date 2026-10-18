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


def test_parse_roll_sticker_flags():
    """-s asks for stickers, -q silences them; a flag is not part of the roll or the caption, wherever it stands."""
    assert parse_roll("/d20").stickers is None
    r = parse_roll("/3d6 -s Атака")
    assert (r.expression, r.caption, r.stickers) == ("3d6", "Атака", True)
    assert parse_roll("/d20 -q").stickers is False and parse_roll("/d20 -q").expression == "d20"
    assert parse_roll("/d20 Атака -Q").stickers is False
    assert parse_roll("/r -s 2d6+3").expression == "2d6+3"
    assert parse_roll("/d20adv -s").mode == "adv"
    assert parse_roll("/3d6 -s -q").stickers is False and parse_roll("/3d6 -q -s").stickers is False  # silence wins
    assert parse_roll("/d20 -5").stickers is None  # a minus sign with a number is arithmetic


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


def test_keep_one_of_two_is_advantage_and_disadvantage():
    """2d20kh1 is d20adv, 2d20kl1 is d20dis (also with a flat bonus and the Cyrillic ka); other keeps are left alone."""
    for text, expression, mode in (
        ("/2d20kh1", "d20", "adv"), ("/2d20kl1+3", "d20+3", "dis"), ("!2к20KH1-2 Атака", "d20-2", "adv"),
        ("/r 2d12kh1", "d12", "adv"), ("/d20adv", "d20", "adv"), ("/d20dis", "d20", "dis"),
    ):  # fmt: skip
        r = parse_roll(text)
        assert (r.expression, r.mode) == (expression, mode), text
    assert parse_roll("!2к20KH1-2 Атака").caption == "Атака"
    assert parse_roll("/4d6kh3").mode is None and parse_roll("/4d6kh3").expression == "4d6kh3"
    assert parse_roll("/3d20kh1").mode is None  # three dice: the library's own keep
    assert parse_roll("/2d20kh1+1d4").mode is None  # more than a bonus: the library's own keep


def test_a_bang_starts_a_dice_command_as_a_slash_does():
    assert parse_roll("!к12").expression == "d12" and parse_roll("!d20+5 Атака").caption == "Атака"
    assert parse_roll("!r 4d6kh3").expression == "4d6kh3" and parse_roll("!промпт") is None


def test_advantage_and_disadvantage_put_the_die_that_counts_first():
    import random

    from writers_bot.core.extras import throw

    for seed in range(40):
        adv = throw(F, parse_roll("/2d20kh1"), random.Random(seed)).dice
        dis = throw(F, parse_roll("/2d20kl1"), random.Random(seed)).dice
        assert adv[0][1] >= adv[1][1] and dis[0][1] <= dis[1][1], seed
        assert adv == throw(F, parse_roll("/d20adv"), random.Random(seed)).dice  # one behaviour, two spellings
