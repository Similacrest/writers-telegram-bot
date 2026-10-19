"""Roll variables (/dicevar, /$name, =$name), private rolls (-p) and moving variables between chats."""

import pytest
from harness import ALICE, BOB, Harness

from writers_bot.core import dicevars
from writers_bot.core.dicevars import VarError, compact, parse_steps, reference
from writers_bot.core.extras import parse_roll

VARS = {"dice": {"variables": True}}


@pytest.fixture
async def make(tmp_path, clock):
    made = []

    async def build(modules, **kwargs):
        h = await Harness.create(tmp_path / str(len(made)), clock, modules, **kwargs)
        made.append(h)
        return h

    yield build
    for h in made:
        await h.close()


# --- the notation --------------------------------------------------------------------------------------


def test_steps_are_rolls_separated_by_semicolons_with_repeats():
    assert parse_steps("d20+5 Атака; 2d6+3 Шкода") == ["d20+5 Атака", "2d6+3 Шкода"]
    assert parse_steps("6x 4d6dl1") == ["4d6dl1"] * 6
    assert parse_steps("2× d20+5; 3х d6; /d8") == ["d20+5"] * 2 + ["d6"] * 3 + [
        "d8"
    ]  # × and Cyrillic х; a slash is allowed
    assert compact(["4d6dl1"] * 6 + ["d20+5 Атака"]) == "6x 4d6dl1; d20+5 Атака"
    assert compact(parse_steps("d20; d6")) == "d20; d6"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("", "dice.var_empty"),
        (" ; ;", "dice.var_empty"),
        ("d20; " * 8 + "d6", "dice.var_too_many_steps"),
        ("9x d6", "dice.var_too_many_steps"),
        ("0x d6", "dice.var_bad_repeat"),
        ("дракон", "dice.var_bad_step"),
        ("d20 =$other", "dice.var_bad_step"),  # no variable in a variable
        ("d20 -p", "dice.var_bad_step"),  # -p belongs to rolling it
        ("$other", "dice.var_bad_step"),
        ("d20 " + "x" * 300, "dice.var_step_too_long"),
    ],
)
def test_a_bad_series_says_why(text, key):
    with pytest.raises(VarError) as e:
        parse_steps(text)
    assert e.value.key == key


def test_names_and_references():
    assert dicevars.clean_name("$Сила_2") == "сила_2" and dicevars.clean_name("hit") == "hit"
    for bad in ("$", "$2fast", "$a b", "$" + "x" * 40):
        with pytest.raises(VarError):
            dicevars.clean_name(bad)
    assert reference("/$hit") == dicevars.Reference("hit", ())
    assert reference("!$Hit -p -s") == dicevars.Reference("hit", ("-p", "-s"))
    assert reference("/r $hit") == dicevars.Reference("hit", ()) and reference("/roll@bot $hit -q").flags == ("-q",)
    assert reference("/$hit Атака") is None  # a variable takes no caption
    assert reference("/d20 $hit") is None and reference("/d20") is None


def test_flags_and_the_save_token_are_read_by_parse_roll():
    r = parse_roll("/d20+4 Сила =$сила -p")
    assert (r.expression, r.caption, r.private, r.save_as) == ("d20+4", "Сила", True, "сила")
    assert not parse_roll("/d20").private and parse_roll("/d20").save_as is None


# --- /dicevar ------------------------------------------------------------------------------------------


async def test_keep_show_list_change_and_unset(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $атака d20+5 Атака; 2d6+3 Шкода")
    assert "Збережено $атака: d20+5 Атака; 2d6+3 Шкода" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $атака")
    assert "$атака: d20+5 Атака; 2d6+3 Шкода" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $ст 6x 4d6dl1")
    await h.say(ALICE, "/dicevar")
    listing = h.last_private(ALICE).text
    assert "$атака" in listing and "$ст: 6x 4d6dl1" in listing
    await h.say(ALICE, "/dicevar $атака d20+7 Атака")
    assert "Змінено $атака" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar unset $атака")
    assert "Видалено $атака" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $атака")
    assert "немає змінної $атака" in h.last_private(ALICE).text and "Є: $ст" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar unset all")
    assert "Видалено змінних: 1" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar")
    assert "немає змінних" in h.last_private(ALICE).text


async def test_the_ukrainian_command_and_the_errors(make):
    h = await make(VARS)
    await h.say(ALICE, "!змінна $а d20")
    assert "Збережено $а" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $а дракон")
    assert "Не зрозумів кидок" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $2 d20")
    assert "Назва змінної" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar щось")
    assert "/dicevar $атака" in h.last_private(ALICE).text  # the usage


async def test_a_person_has_so_many_variables_in_a_chat(make):
    h = await make(VARS)
    for i in range(dicevars.MAX_PER_SCOPE):
        await h.say(ALICE, f"/dicevar $v{i} d20")
    await h.say(ALICE, "/dicevar $one_more d20")
    assert f"уже {dicevars.MAX_PER_SCOPE} змінних" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $v0 d12")  # changing one is fine
    assert "Змінено $v0" in h.last_private(ALICE).text


# --- rolling -------------------------------------------------------------------------------------------


async def test_rolling_a_series_is_one_message_with_a_line_per_roll(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $атака d20+5 Атака; 2d6+3 Шкода; d4")
    mark = h.mark()
    await h.say(ALICE, "/$атака")
    sent = h.public_since(mark)
    assert [m.kind for m in sent] == ["text"]  # no stickers for a series unless asked for
    text = sent[0].text
    assert "Атака" in text and "Шкода" in text and "атака" in text  # a step without a caption is named by the variable
    await h.say(ALICE, "/r $атака")
    await h.say(ALICE, "!$атака")
    assert h.last_public().text.count("= <b>") == 3  # the three rolls


async def test_six_rolls_for_a_new_character(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $ст 6x 4d6dl1")
    await h.say(ALICE, "/$ст")
    assert (
        h.last_public().text.count("4d6pl1") == 6 and h.last_public().text.count("<s>") >= 6
    )  # each drops its lowest


async def test_a_variable_is_the_senders_own_in_that_chat(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $сила d20+4")
    await h.say(BOB, "/$сила")
    assert "немає змінної $сила" in h.last_private(BOB).text  # not shared with other people
    await h.say(ALICE, "/$сила", chat=ALICE.id)
    assert "немає змінної $сила" in h.dms(ALICE)[-1].text  # nor with another chat (the private one)
    await h.say(ALICE, "/$сила")
    assert "= <b>" in h.last_public().text


async def test_the_save_shorthand_keeps_and_rolls(make):
    h = await make(VARS)
    mark = h.mark()
    await h.say(ALICE, "/d20+4 Сила =$сила")
    assert "Збережено $сила: d20+4 Сила" in h.last_private(ALICE).text
    assert any("Сила" in m.text for m in h.public_since(mark))  # and it rolled
    await h.say(ALICE, "/$сила")
    assert "Сила" in h.last_public().text
    await h.say(ALICE, "/d20 дракон =$ой")
    await h.say(ALICE, "/dicevar")
    assert "$ой" in h.last_private(ALICE).text  # "дракон" is a caption here
    await h.say(ALICE, "/дракон =$погано")
    assert "$погано" not in h.last_private(ALICE).text


async def test_variables_are_off_unless_asked_for(make):
    h = await make({"dice": {}})
    await h.say(ALICE, "/$сила")
    assert "вимкнені" in h.last_public().text
    await h.say(ALICE, "/dicevar")  # not even a command
    assert not [m for m in h.fake.messages if "dicevar" in m.text.lower() and m.sender.get("is_bot")]


# --- private rolls ---------------------------------------------------------------------------------------


async def test_a_private_roll_is_for_the_roller_alone_and_the_request_goes(make):
    h = await make({"dice": {"stickers": True}})
    mark = h.mark()
    request = await h.say(ALICE, "/d20+5 -p Потайний")
    assert not [m for m in h.public_since(mark) if m.sender.get("is_bot")]  # nothing public from the bot
    assert request.deleted  # the bot may delete: the request is gone
    assert "Потайний" in h.last_private(ALICE).text
    assert not h.fake.uploads  # a private roll has no stickers


async def test_a_private_variable_roll_and_a_private_chat_is_a_plain_reply(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $тайна d20; d6")
    mark = h.mark()
    request = await h.say(ALICE, "/$тайна -p")
    assert request.deleted and "тайна" in h.last_private(ALICE).text.lower()
    assert not [m for m in h.public_since(mark) if m.sender.get("is_bot")]
    await h.dm(ALICE, "/d20 -p")
    assert "= <b>" in h.dms(ALICE)[-1].text  # in the private chat it is just the answer


# --- moving variables between chats ----------------------------------------------------------------------


async def test_import_from_the_private_chat_with_conflicts(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $a d20+1; d6", chat=ALICE.id)  # in the private chat with the bot
    await h.say(ALICE, "/dicevar $b 2d6", chat=ALICE.id)
    await h.say(ALICE, "/dicevar $c d8", chat=ALICE.id)
    await h.say(ALICE, "/dicevar $a d20+9")  # in the group: $a differs, $b and $c are new there
    await h.say(ALICE, "/dicevar $c d8")  # the same as in the private chat: no question
    await h.say(ALICE, "/dicevar import")
    card = h.last_private(ALICE)
    source = next(b for b in card.buttons() if b.startswith("Особисті з ботом"))
    assert source == "Особисті з ботом (3)"
    await h.press(ALICE, card, source)
    assert "$a тут уже є" in card.text and "Тут: d20+9" in card.text and "Там: d20+1; d6" in card.text
    await h.press(ALICE, card, "Замінити")
    assert "перенесено 2" in card.text and "залишено тутешніх 0" in card.text  # $b new, $a replaced; $c was the same
    await h.say(ALICE, "/dicevar")
    assert "$a: d20+1; d6" in h.last_private(ALICE).text and "$b: 2d6" in h.last_private(ALICE).text


async def test_import_keep_here_and_for_all(make):
    h = await make(VARS)
    for name in "abc":
        await h.say(ALICE, f"/dicevar ${name} d20", chat=ALICE.id)
        await h.say(ALICE, f"/dicevar ${name} d6")
    await h.say(ALICE, "/dicevar import")
    card = h.last_private(ALICE)
    await h.press(ALICE, card, "Особисті з ботом (3)")
    await h.press(ALICE, card, "Залишити тут")  # $a stays; two left
    assert "Ще таких: 1" in card.text
    await h.press(ALICE, card, "Усі: замінити")
    await h.say(ALICE, "/dicevar")
    listing = h.last_private(ALICE).text
    assert "$a: d6" in listing and "$b: d20" in listing and "$c: d20" in listing


async def test_import_with_nothing_to_move_and_nobody_elses_buttons(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar import")
    assert "немає змінних, які можна перенести" in h.last_private(ALICE).text
    await h.say(ALICE, "/dicevar $a d20", chat=ALICE.id)
    await h.say(ALICE, "/dicevar import")
    card = h.last_private(ALICE)
    other = await h.press(BOB, card, "Особисті з ботом (1)")  # a button for ALICE only
    assert other and "Особисті" in " ".join(card.buttons())


# --- your data -------------------------------------------------------------------------------------------


async def test_variables_are_exported_and_forgotten_with_the_person(make):
    h = await make(VARS)
    await h.say(ALICE, "/dicevar $атака d20+5 Атака; 2d6+3 Шкода")
    module = h.tbot.module("dice")
    person = await h.tbot.people.person_id("telegram", str(ALICE.id), "Alice")
    files = await module.export_files(person)
    text = files["dice-variables.csv"].decode("utf-8-sig")
    assert "$атака" in text and "d20+5 Атака; 2d6+3 Шкода" in text
    assert await module.export_files(await h.tbot.people.person_id("telegram", str(BOB.id), "Bob")) == {}
    await module.forget("telegram", str(ALICE.id))
    assert await module.export_files(person) == {}
    await h.say(ALICE, "/dicevar")
    assert "немає змінних" in h.last_private(ALICE).text
