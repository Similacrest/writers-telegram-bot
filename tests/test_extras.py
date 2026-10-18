import re

from writers_bot.core.extras import parse_roll, roll_html, slogan_reply, smash


def test_parse_roll():
    r = parse_roll("/2d6+3 Атака")
    assert (r.count, r.sides, r.bonus, r.caption, r.mode) == (2, 6, 3, "Атака", None)
    assert parse_roll("/d20@writers_bot").sides == 20
    assert parse_roll("/roll 4к6").count == 4
    assert parse_roll("/r d20adv").mode == "adv"
    assert parse_roll("/d6mi-2").minimum == -2
    assert parse_roll("/1000d6").count == 1  # out of range falls back
    assert parse_roll("/prompt") is None
    assert parse_roll("/words 2d6") is None


def test_roll_html_bounds_and_escaping():
    r = parse_roll("/3d6 <script>")
    for _ in range(50):
        out = roll_html(r)
        total = int(re.search(r"</code> <b>(-?\d+)</b>", out)[1])
        assert 3 <= total <= 18
    assert "&lt;script&gt;" in out and "<script>" not in out
    adv = roll_html(parse_roll("/d20adv"))
    assert "<s>" in adv


def test_smash_and_slogans():
    for _ in range(50):
        s = smash()
        assert 9 <= len(s) <= 21 and all(c in "qwertyuiop[]asdfghjkl;'zxcvbnm,./" for c in s)
    assert slogan_reply("Слава Україні!") == "<i>Героям Слава!</i>"
    assert slogan_reply("привіт") is None
