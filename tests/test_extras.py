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


def test_adv_is_a_mode_and_kh1_is_the_librarys_own_keep():
    """adv / dis roll the whole expression twice; 2d20kh1 is the d20 library's keep, so it is no mode (the dice as rolled)."""
    for text, expression, mode in (
        ("/d20adv", "d20", "adv"), ("/d20dis", "d20", "dis"), ("/d20+3adv", "d20+3", "adv"),
        ("/2d20kh1", "2d20kh1", None), ("/2d20kl1+3", "2d20kl1+3", None), ("!2к20kh1-2 Атака", "2d20kh1-2", None),
        ("/4d6kh3", "4d6kh3", None),
    ):  # fmt: skip
        r = parse_roll(text)
        assert (r.expression, r.mode) == (expression, mode), text
    assert parse_roll("!2к20kh1-2 Атака").caption == "Атака"


def test_a_bang_starts_a_dice_command_as_a_slash_does():
    assert parse_roll("!к12").expression == "d12" and parse_roll("!d20+5 Атака").caption == "Атака"
    assert parse_roll("!r 4d6kh3").expression == "4d6kh3" and parse_roll("!промпт") is None


def test_adv_shows_the_two_rolls_sorted_with_the_dropped_one_struck_out_and_kh1_the_dice_as_rolled():
    import random
    import re

    from writers_bot.core.extras import throw

    for seed in range(40):
        adv = throw(F, parse_roll("/d20adv"), random.Random(seed))
        dis = throw(F, parse_roll("/d20dis"), random.Random(seed))
        totals = lambda html: [int(re.findall(r"= <b>(\d+)</b>", line)[0]) for line in html.split("\n")[2:]]  # noqa: E731
        low, high = totals(adv.html), totals(dis.html)
        assert low == sorted(low) and high == sorted(high), seed  # the lower roll first, whichever counts
        assert adv.html.split("\n")[1] == f"<b>{max(low)}</b>" and dis.html.split("\n")[1] == f"<b>{min(high)}</b>"
        assert adv.html.count("<s>") == 1 and dis.html.count("<s>") == 1
        if low[0] != low[1]:
            assert (
                "<s>" in adv.html.split("\n")[2] and "<s>" in dis.html.split("\n")[3]
            )  # advantage drops the lower roll
        assert (
            adv.dice[0][1] >= adv.dice[1][1] and dis.dice[0][1] <= dis.dice[1][1]
        )  # stickers: the die that counts first
    keep = throw(F, parse_roll("/2d20kh1"), random.Random(3))
    assert [v for _, v in keep.dice] == [
        8,
        19,
    ] and "(<s>8</s>, 19)" in keep.html  # as rolled, the dropped one struck out
