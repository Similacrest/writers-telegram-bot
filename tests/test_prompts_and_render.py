from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from writers_bot.core.models import ChatKey, Participant, Sprint, SprintStatus
from writers_bot.core.prompts import PromptsStore, parse_config_rows, parse_prompt_columns
from writers_bot.core.render import report_text, sprint_text
from writers_bot.telegram_bot.app import chat_key, command_args_text
from writers_bot.telegram_bot.presenter import HtmlFormatter

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_config_rows():
    rows = [["help_message", "", "comment", "line 1", "line 2"], ["single", "", "c", "value"], ["broken", ""], []]
    config = parse_config_rows(rows)
    assert config["help_message"][""] == ["line 1", "line 2"]
    assert config["single"][""] == ["value"]
    assert "broken" not in config


def test_prompt_columns_are_dynamic_categories():
    columns = [["Персонаж", "лицар", "", "відьма"], ["Місце", "ліс"], ["", "ignored"], ["Порожня"]]
    assert parse_prompt_columns(columns) == {"Персонаж": ["лицар", "відьма"], "Місце": ["ліс"]}


def test_prompt_category_lookup():
    store = PromptsStore(None)
    store.data.texts = {"ua": {"Персонаж": ["a"], "Місце": ["b"], "Місія": ["c"]}}
    assert store.find_category("ua", "персонаж") == "Персонаж"
    assert store.find_category("ua", "пер") == "Персонаж"
    assert store.find_category("ua", "мі") is None  # ambiguous
    assert store.random_text("ua", "Місце") == {"Місце": "b"}


def test_random_image_does_not_mutate_store():
    store = PromptsStore(None)
    store.data.images = {"all": [{"id": "1"}], "character": [{"id": "1"}]}
    cat, num, image = store.random_image("unknown")
    assert cat == "all" and num == 0
    image["cat"] = "x"
    assert store.data.images["all"][0] == {"id": "1"}


def make_sprint(status=SprintStatus.RUNNING, name="<b>Ann</b>"):
    sprint = Sprint(
        id=1, key=ChatKey("telegram", "1"), status=status, duration=30, planned_duration=30, delay=2,
        created_by="1", created_at=NOW, start_at=NOW, end_at=NOW + timedelta(minutes=30),
        report_until=NOW + timedelta(minutes=60),
    )  # fmt: skip
    sprint.participants["1"] = Participant("1", name, NOW)
    return sprint


def test_sprint_text_escapes_names_and_uses_local_time():
    fmt = HtmlFormatter(ZoneInfo("Europe/Kyiv"))
    text = sprint_text(fmt, make_sprint(), NOW + timedelta(minutes=15))
    assert "&lt;b&gt;Ann&lt;/b&gt;" in text and "<b>Ann</b>" not in text
    assert f'<tg-time unix="{int(NOW.timestamp())}" format="t">15:00</tg-time>' in text
    assert "50%" in text


def test_report_text_leaderboard():
    fmt = HtmlFormatter(ZoneInfo("Europe/Kyiv"))
    sprint = make_sprint(SprintStatus.REPORTING, name="Ann")
    sprint.participants["2"] = Participant("2", "Bob", NOW, words=900, reported_at=NOW)
    sprint.participants["1"].words, sprint.participants["1"].reported_at = 300, NOW
    text = report_text(fmt, sprint, NOW)
    assert text.index("Bob") < text.index("Ann")
    assert "30.0 сл/хв" in text and "1200 слів" in text


def test_command_args_text_handles_utf16_offsets():
    entity = SimpleNamespace(type="bot_command", offset=0, length=len("/wc@bot"))
    message = SimpleNamespace(text="/wc@bot 🙂 два слова", caption=None, entities=[entity], caption_entities=[])
    assert command_args_text(message) == "🙂 два слова"


def test_chat_key_only_uses_topic_threads():
    topic = SimpleNamespace(chat_id=-100, message_thread_id=5, is_topic_message=True)
    reply_thread = SimpleNamespace(chat_id=-100, message_thread_id=5, is_topic_message=False)
    assert chat_key(topic).thread_id == "5"
    assert chat_key(reply_thread).thread_id is None
