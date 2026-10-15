import asyncio
import json
import logging
import sys

from writers_bot.core.ops import AlertHandler, HealthServer, JsonFormatter


def record(msg, exc=None):
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except Exception:
            exc_info = sys.exc_info()
    return logging.LogRecord("writers_bot.x", logging.ERROR, __file__, 1, msg, (), exc_info)


def test_alert_rate_limits():
    now = [0.0]
    h = AlertHandler(send=None, clock=lambda: now[0])
    assert h.should_send(record("boom"))
    assert not h.should_send(record("boom"))  # same error within 15 minutes
    assert h.should_send(record("other"))
    now[0] += 16 * 60
    assert h.should_send(record("boom"))
    for i in range(20):
        h.should_send(record(f"spam {i}"))
    assert len(h._sent) == 10  # at most 10 an hour
    assert "ValueError: bad" in AlertHandler.render(record("x", ValueError("bad")))


async def test_alerts_are_sent_from_log_records():
    sent = []

    async def send(text):
        sent.append(text)

    h = AlertHandler(send)
    h.start()
    try:
        logging.getLogger("writers_bot.test").error("sprint runner failed", exc_info=RuntimeError("x"))
        logging.getLogger("writers_bot.test").warning("not an error")
        await asyncio.sleep(0.05)
    finally:
        await h.stop()
    assert len(sent) == 1 and "sprint runner failed" in sent[0]


def test_json_formatter():
    line = JsonFormatter().format(record("hello", ValueError("v")))
    data = json.loads(line)
    assert data["message"] == "hello" and data["level"] == "ERROR" and "ValueError" in data["exc"]


async def test_health_server():
    state = {"ok": True}

    async def check():
        return dict(state)

    server = HealthServer("127.0.0.1", 0, check)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def get():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        data = await reader.read()
        writer.close()
        return data

    try:
        assert (await get()).startswith(b"HTTP/1.1 200")
        state["ok"] = False
        assert (await get()).startswith(b"HTTP/1.1 503")
    finally:
        await server.stop()


def test_discord_command_tree_builds(monkeypatch, tmp_path):
    from writers_bot.config import DiscordSettings
    from writers_bot.discord_bot.app import WritersDiscordBot

    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "d.sqlite3"))
    monkeypatch.delenv("PREVIEW_FEATURES", raising=False)
    plain = {c.name for c in WritersDiscordBot(DiscordSettings.from_env()).tree.get_commands()}
    assert {"sprint", "join", "words", "mystats", "sprintmode"} <= plain
    assert not plain & {"tz", "goal", "export", "forgetme"}

    monkeypatch.setenv("PREVIEW_FEATURES", "1")
    bot = WritersDiscordBot(DiscordSettings.from_env())
    names = {c.name for c in bot.tree.get_commands()}
    assert {"sprint", "join", "words", "mystats", "tz", "goal", "export", "forgetme", "sprintmode"} <= names
    payload = {c.name: c.to_dict(bot.tree) for c in bot.tree.get_commands()}
    goal_opts = {o["name"]: o for o in payload["goal"]["options"]}
    assert goal_opts["words"]["max_value"] == 1_000_000 and not goal_opts["words"].get("required")
