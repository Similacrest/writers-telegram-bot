from datetime import date

import pytest

from writers_bot.core.reportparse import Op, parse
from writers_bot.core.sources import find_url

TODAY = date(2026, 10, 18)


def p(text, **kw):
    return parse(text, find_url, today=TODAY, **kw)


def ops(parsed):
    return [(i.op, i.value, i.percent, i.hint) for i in parsed.items]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500", [(Op.SET, 500, False, None)]),
        ("+300", [(Op.ADD, 300, False, None)]),
        ("+ 300", [(Op.ADD, 300, False, None)]),
        ("=5000", [(Op.TOTAL, 5000, False, None)]),
        ("= 12 500", [(Op.TOTAL, 12500, False, None)]),
        ("12 500", [(Op.SET, 12500, False, None)]),
        ("1,500", [(Op.SET, 1500, False, None)]),
        ("50%", [(Op.SET, 50, True, None)]),
        ("50 %", [(Op.SET, 50, True, None)]),
        ("500 слів", [(Op.SET, 500, False, None)]),
        ("520 50%", [(Op.SET, 520, False, None), (Op.SET, 50, True, None)]),
        ("+300 роман +200 есе", [(Op.ADD, 300, False, "роман"), (Op.ADD, 200, False, "есе")]),
        ("Роман 300", [(Op.SET, 300, False, "Роман")]),
        ("300 Нова книга", [(Op.SET, 300, False, "Нова книга")]),
        ("✓", [(Op.DONE, None, False, None)]),
        ("готово", [(Op.DONE, None, False, None)]),
        ("не вийшло", [(Op.FAILED, None, False, None)]),
        ("❌", [(Op.FAILED, None, False, None)]),
    ],
)
def test_items(text, expected):
    assert ops(p(text)) == expected


def test_link_is_a_total():
    parsed = p("https://telegra.ph/Test-10-18")
    assert parsed.items[0].op == Op.TOTAL and parsed.items[0].url == "https://telegra.ph/Test-10-18"


def test_link_with_work_name():
    parsed = p("роман https://telegra.ph/Test-10-18")
    assert parsed.items[0].hint == "роман" and parsed.items[0].url


@pytest.mark.parametrize(("text", "day"), [("+400 вчора", date(2026, 10, 17)), ("+400 позавчора", date(2026, 10, 16))])
def test_days(text, day):
    assert p(text).day == day


def test_date_and_today():
    assert p("+400 16.10").day == date(2026, 10, 16)
    assert p("+400 сьогодні").day is None


@pytest.mark.parametrize("text", ["", "привіт", "дякую всім!", "1234567890"])
def test_not_a_report(text):
    assert p(text) is None


def test_chat_text_is_names():
    # "500 нарешті!" parses (a name hint), and the caller rejects it unless "нарешті!" is a work
    assert p("500 нарешті!").items[0].hint == "нарешті!"
