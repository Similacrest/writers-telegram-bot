import json
from types import SimpleNamespace

from telegram import Bot, BotCommand
from telegram.request import BaseRequest, RequestData

from writers_bot.telegram_bot.ephemeral import incoming_ephemeral_id, send_ephemeral, set_commands


class Recorder(BaseRequest):
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def read_timeout(self):
        return 5.0

    async def do_request(self, url, method, request_data: RequestData | None = None, **kwargs):
        params = {k: v for k, v in (request_data.json_parameters if request_data else {}).items()}
        self.calls.append((url.rsplit("/", 1)[1], params))
        if self.fail:
            return 400, json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: unknown"}).encode()
        result = (
            True
            if self.calls[-1][0] == "setMyCommands"
            else {"message_id": 1, "date": 0, "chat": {"id": -1, "type": "group"}}
        )
        return 200, json.dumps({"ok": True, "result": result}).encode()


async def test_send_ephemeral_payload():
    req = Recorder()
    bot = Bot("123:abc", request=req, get_updates_request=Recorder())
    assert await send_ephemeral(bot, -100, 5, "<b>hi</b>", reply_to_ephemeral=77, message_thread_id=3)
    method, params = req.calls[-1]
    assert method == "sendMessage"
    assert json.loads(params["ephemeral_message_parameters"]) == {"receiver_user_id": 5}
    assert json.loads(params["reply_parameters"]) == {"ephemeral_message_id": 77}
    assert params["parse_mode"] == "HTML" and params["message_thread_id"] == "3"


async def test_send_ephemeral_failure_returns_false():
    bot = Bot("123:abc", request=Recorder(fail=True), get_updates_request=Recorder())
    assert not await send_ephemeral(bot, -100, 5, "hi")


async def test_set_commands_marks_ephemeral():
    req = Recorder()
    bot = Bot("123:abc", request=req, get_updates_request=Recorder())
    cmds = [BotCommand("words", "w"), BotCommand("prompt", "p")]
    assert await set_commands(bot, cmds, {"type": "all_group_chats"}, {"words"})
    _, params = req.calls[-1]
    assert json.loads(params["commands"]) == [
        {"command": "words", "description": "w", "is_ephemeral": True},
        {"command": "prompt", "description": "p"},
    ]


def test_incoming_ephemeral_id():
    assert incoming_ephemeral_id(SimpleNamespace(api_kwargs={"ephemeral_message_id": 9})) == 9
    assert incoming_ephemeral_id(SimpleNamespace(api_kwargs={})) is None
    assert incoming_ephemeral_id(None) is None
