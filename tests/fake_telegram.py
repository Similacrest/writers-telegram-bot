"""An in-process fake of the Telegram Bot API, for workflow tests.

It plugs into python-telegram-bot as its HTTP request class, keeps the chats' messages (ordinary and
ephemeral), member roles and reactions, enforces the ephemeral-message rules (a callback query or
ephemeral command in the last 15 seconds, or the bot is a chat admin), and builds the updates a user's
actions would produce. ``Chat.visible(user)`` is what that user sees in the chat right now.
"""

import itertools
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from telegram import Update
from telegram.request import BaseRequest, RequestData

BOT_ID = 999
BOT_USERNAME = "writers_test_bot"
EPHEMERAL_WINDOW = 15


@dataclass
class FakeUser:
    id: int
    first_name: str
    username: str | None = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "is_bot": False, "first_name": self.first_name}
        if self.username:
            d["username"] = self.username
        return d


@dataclass
class Msg:
    chat_id: int
    message_id: int
    text: str
    sender: dict
    markup: dict | None = None
    reply_to: int | None = None
    thread_id: int | None = None
    deleted: bool = False
    edits: int = 0
    pinned: bool = False
    kind: str = "text"  # text, document, sticker, photo
    receiver: int | None = None  # ephemeral messages: who sees it
    ephemeral_id: int | None = None
    reactions: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def from_bot(self) -> bool:
        return self.sender.get("id") == BOT_ID

    def buttons(self) -> list[str]:
        if not self.markup or "inline_keyboard" not in self.markup:
            return []
        return [b["text"] for row in self.markup["inline_keyboard"] for b in row]

    def button(self, text: str) -> dict:
        for row in (self.markup or {}).get("inline_keyboard", []):
            for b in row:
                if b["text"] == text or b["text"].startswith(text):
                    return b
        raise KeyError(f"no button {text!r} on {self.text[:60]!r}: {self.buttons()}")


@dataclass
class Chat:
    id: int
    type: str = "supergroup"
    title: str = "Writers"
    is_forum: bool = False
    bot_status: str = "administrator"  # member | administrator
    bot_can_delete: bool = True
    roles: dict[int, str] = field(default_factory=dict)  # user id -> creator | administrator | member
    community: dict | None = None
    member_count: int = 10

    def to_dict(self) -> dict:
        d = {"id": self.id, "type": self.type}
        if self.type == "private":
            d["first_name"] = self.title
        else:
            d["title"] = self.title
        if self.is_forum:
            d["is_forum"] = True
        return d


class FakeTelegram(BaseRequest):
    def __init__(self, clock=None):
        self.clock = clock
        self.chats: dict[int, Chat] = {}
        self.messages: list[Msg] = []
        self.calls: list[tuple[str, dict]] = []
        self.callbacks: dict[str, tuple[float, int, int]] = {}  # query id -> (time, user, chat)
        self.answers: dict[str, dict] = {}
        self.incoming_ephemeral: dict[tuple[int, int], float] = {}  # (chat, ephemeral id) -> time
        self.documents: list[dict] = []
        self.sticker_sets: dict[str, list[dict]] = {}
        self._ids = itertools.count(100)
        self._eph = itertools.count(1)
        self._qid = itertools.count(1)
        self._update_id = itertools.count(1)
        self.blocked_dm: set[int] = set()  # users who never started the bot
        self.app = None

    # --- BaseRequest ---------------------------------------------------------------------------------

    @property
    def read_timeout(self):
        return 1.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(self, url, method, request_data: RequestData | None = None, **_kwargs):
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data is not None else {}
        for key, value in list(params.items()):
            if (
                isinstance(value, str)
                and value[:1] in "[{"
                and key
                in (
                    "reply_markup",
                    "reply_parameters",
                    "ephemeral_message_parameters",
                    "link_preview_options",
                    "commands",
                    "scope",
                    "reaction",
                )
            ):
                params[key] = json.loads(value)
        self.calls.append((name, params))
        handler = getattr(self, f"m_{name}", None)
        if handler is None:
            return 200, json.dumps({"ok": True, "result": True}).encode()
        try:
            result = handler(params)
        except ApiError as e:
            return 400, json.dumps({"ok": False, "error_code": 400, "description": str(e)}).encode()
        return 200, json.dumps({"ok": True, "result": result}).encode()

    # --- helpers -------------------------------------------------------------------------------------

    def now(self) -> float:
        return self.clock.now().timestamp() if self.clock else time.time()

    def chat(self, chat_id) -> Chat:
        chat_id = int(chat_id)
        if chat_id not in self.chats:
            self.chats[chat_id] = Chat(chat_id, "private" if chat_id > 0 else "supergroup", f"chat{chat_id}")
        return self.chats[chat_id]

    def _bot_user(self) -> dict:
        return {"id": BOT_ID, "is_bot": True, "first_name": "Writers", "username": BOT_USERNAME}

    def _date(self) -> int:
        return int(self.now())

    def _message_dict(self, m: Msg) -> dict:
        d = {
            "message_id": m.message_id,
            "date": self._date(),
            "chat": self.chat(m.chat_id).to_dict(),
            "from": m.sender,
        }
        if m.kind == "text":
            d["text"] = m.text
        if m.markup:
            d["reply_markup"] = m.markup
        if m.thread_id:
            d["message_thread_id"] = m.thread_id
            d["is_topic_message"] = True
        if m.ephemeral_id:
            d["ephemeral_message_id"] = m.ephemeral_id
            d["receiver_user"] = {"id": m.receiver, "is_bot": False, "first_name": "x"}
        if m.reply_to:
            original = self.find(m.chat_id, m.reply_to)
            if original is not None:
                d["reply_to_message"] = self._message_dict(Msg(**{**original.__dict__, "reply_to": None}))
        return d

    def find(self, chat_id, message_id) -> Msg | None:
        return next(
            (
                m
                for m in self.messages
                if m.chat_id == int(chat_id) and m.message_id == int(message_id) and not m.ephemeral_id
            ),
            None,
        )

    def find_ephemeral(self, chat_id, user_id, eph_id) -> Msg | None:
        return next(
            (
                m
                for m in self.messages
                if m.chat_id == int(chat_id) and m.receiver == int(user_id) and m.ephemeral_id == int(eph_id)
            ),
            None,
        )

    def _markup(self, params) -> dict | None:
        markup = params.get("reply_markup")
        if isinstance(markup, str):
            markup = json.loads(markup)
        return markup

    # --- Bot API methods -----------------------------------------------------------------------------

    def m_getMe(self, _p):
        return {
            **self._bot_user(),
            "can_join_groups": True,
            "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        }

    def m_sendMessage(self, p):
        chat_id = int(p["chat_id"])
        eph_params = p.get("ephemeral_message_parameters")
        if isinstance(eph_params, str):
            eph_params = json.loads(eph_params)
        reply = p.get("reply_parameters")
        if isinstance(reply, str):
            reply = json.loads(reply)
        if eph_params:
            receiver = int(eph_params["receiver_user_id"])
            self._check_ephemeral(
                chat_id, receiver, eph_params.get("callback_query_id"), (reply or {}).get("ephemeral_message_id")
            )
            m = Msg(
                chat_id,
                0,
                p["text"],
                self._bot_user(),
                self._markup(p),
                thread_id=p.get("message_thread_id"),
                receiver=receiver,
                ephemeral_id=next(self._eph),
            )
            self.messages.append(m)
            return self._message_dict(m)
        if chat_id > 0 and chat_id in self.blocked_dm:
            raise ApiError("Forbidden: bot can't initiate conversation with a user")
        m = Msg(
            chat_id,
            next(self._ids),
            p["text"],
            self._bot_user(),
            self._markup(p),
            reply_to=(reply or {}).get("message_id"),
            thread_id=p.get("message_thread_id"),
        )
        self.messages.append(m)
        return self._message_dict(m)

    def _check_ephemeral(self, chat_id, receiver, callback_query_id, reply_eph):
        now = self.now()
        if callback_query_id:
            when, user, _chat = self.callbacks.get(callback_query_id, (0, 0, 0))
            if user == receiver and now - when <= EPHEMERAL_WINDOW:
                return
            raise ApiError("Bad Request: callback query is too old or not from this user")
        if reply_eph:
            when = self.incoming_ephemeral.get((chat_id, reply_eph), 0)
            if now - when <= EPHEMERAL_WINDOW:
                return
            raise ApiError("Bad Request: ephemeral message to reply not found")
        if self.chat(chat_id).bot_status == "administrator":
            return
        raise ApiError("Bad Request: not enough rights to send ephemeral messages")

    def m_editMessageText(self, p):
        m = self.find(p["chat_id"], p["message_id"])
        if m is None or m.deleted:
            raise ApiError("Bad Request: message to edit not found")
        if m.text == p["text"] and m.markup == self._markup(p):
            raise ApiError("Bad Request: message is not modified")
        m.text = p["text"]
        m.markup = self._markup(p)
        m.edits += 1
        return self._message_dict(m)

    def m_editMessageReplyMarkup(self, p):
        m = self.find(p["chat_id"], p["message_id"])
        if m is None:
            raise ApiError("Bad Request: message to edit not found")
        m.markup = self._markup(p)
        return self._message_dict(m)

    def m_editEphemeralMessageText(self, p):
        m = self.find_ephemeral(p["chat_id"], p["receiver_user_id"], p["ephemeral_message_id"])
        if m is None or m.deleted:
            raise ApiError("Bad Request: message to edit not found")
        m.text = p["text"]
        m.markup = p.get("reply_markup")
        m.edits += 1
        return True

    def m_deleteEphemeralMessage(self, p):
        m = self.find_ephemeral(p["chat_id"], p["receiver_user_id"], p["ephemeral_message_id"])
        if m is not None:
            m.deleted = True
        return True

    def m_deleteMessage(self, p):
        m = self.find(p["chat_id"], p["message_id"])
        if m is None or m.deleted:
            raise ApiError("Bad Request: message to delete not found")
        chat = self.chat(p["chat_id"])
        if (
            not m.from_bot
            and chat.type != "private"
            and not (chat.bot_status == "administrator" and chat.bot_can_delete)
        ):
            raise ApiError("Bad Request: message can't be deleted")
        m.deleted = True
        return True

    def m_pinChatMessage(self, p):
        chat = self.chat(p["chat_id"])
        if chat.bot_status != "administrator":
            raise ApiError("Bad Request: not enough rights to manage pinned messages in the chat")
        m = self.find(p["chat_id"], p["message_id"])
        if m:
            m.pinned = True
        return True

    def m_unpinChatMessage(self, p):
        m = self.find(p["chat_id"], p["message_id"])
        if m:
            m.pinned = False
        return True

    def m_setMessageReaction(self, p):
        m = self.find(p["chat_id"], p["message_id"])
        if m is None:
            raise ApiError("Bad Request: message not found")
        reaction = p.get("reaction")
        if isinstance(reaction, str):
            reaction = json.loads(reaction)
        m.reactions = [r.get("emoji") for r in reaction or []]
        return True

    def m_answerCallbackQuery(self, p):
        self.answers[p["callback_query_id"]] = p
        return True

    def m_getChatMember(self, p):
        chat = self.chat(p["chat_id"])
        user_id = int(p["user_id"])
        if user_id == BOT_ID:
            status = chat.bot_status
            d = {"status": status, "user": self._bot_user()}
            if status == "administrator":
                d |= {
                    "can_be_edited": False, "is_anonymous": False, "can_manage_chat": True,
                    "can_delete_messages": chat.bot_can_delete, "can_manage_video_chats": False,
                    "can_restrict_members": False, "can_promote_members": False, "can_change_info": False,
                    "can_invite_users": True, "can_post_stories": False, "can_edit_stories": False,
                    "can_delete_stories": False, "can_pin_messages": True,
                }  # fmt: skip
            return d
        status = chat.roles.get(user_id, "member")
        d = {"status": status, "user": {"id": user_id, "is_bot": False, "first_name": "u"}}
        if status == "administrator":
            d |= {
                "can_be_edited": False, "is_anonymous": False, "can_manage_chat": True, "can_delete_messages": True,
                "can_manage_video_chats": False, "can_restrict_members": False, "can_promote_members": False,
                "can_change_info": False, "can_invite_users": True, "can_post_stories": False,
                "can_edit_stories": False, "can_delete_stories": False,
            }  # fmt: skip
        if status == "creator":
            d["is_anonymous"] = False
        return d

    def m_getChat(self, p):
        chat = self.chat(p["chat_id"])
        d = chat.to_dict() | {
            "accent_color_id": 0,
            "max_reaction_count": 11,
            "accepted_gift_types": {
                "unlimited_gifts": False,
                "limited_gifts": False,
                "unique_gifts": False,
                "premium_subscription": False,
                "gifts_from_channels": False,
            },
        }
        if chat.community:
            d["community"] = chat.community
        return d

    def m_getChatMemberCount(self, p):
        return self.chat(p["chat_id"]).member_count

    def m_sendDocument(self, p):
        chat_id = int(p["chat_id"])
        if chat_id in self.blocked_dm:
            raise ApiError("Forbidden: bot can't initiate conversation with a user")
        m = Msg(chat_id, next(self._ids), p.get("caption") or "", self._bot_user(), kind="document")
        self.messages.append(m)
        self.documents.append(p)
        return self._message_dict(m) | {"document": {"file_id": "f", "file_unique_id": "u"}}

    def m_copyMessage(self, p):
        chat_id = int(p["chat_id"])
        if chat_id in self.blocked_dm:
            raise ApiError("Forbidden: bot was blocked by the user")
        source = self.find(p["from_chat_id"], p["message_id"])
        m = Msg(chat_id, next(self._ids), source.text if source else "", self._bot_user(), kind="copy")
        self.messages.append(m)
        return {"message_id": m.message_id}

    def m_sendSticker(self, p):
        chat_id = int(p["chat_id"])
        m = Msg(
            chat_id,
            next(self._ids),
            "",
            self._bot_user(),
            kind="sticker",
            reply_to=(p.get("reply_parameters") or {}).get("message_id")
            if isinstance(p.get("reply_parameters"), dict)
            else None,
            thread_id=p.get("message_thread_id"),
        )
        m.extra["sticker"] = p.get("sticker")
        self.messages.append(m)
        d = self._message_dict(m)
        d["sticker"] = {
            "file_id": str(p.get("sticker")),
            "file_unique_id": "x",
            "type": "regular",
            "width": 512,
            "height": 512,
            "is_animated": False,
            "is_video": False,
        }
        return d

    def m_getStickerSet(self, p):
        name = p["name"]
        if name not in self.sticker_sets:
            raise ApiError("Bad Request: STICKERSET_INVALID")
        return {"name": name, "title": name, "sticker_type": "regular", "stickers": self.sticker_sets[name]}

    def m_sendChatAction(self, _p):
        return True

    # --- what users do -------------------------------------------------------------------------------

    def _user_message(
        self, chat: Chat, user: FakeUser, text: str, reply_to: int | None, thread_id=None, *, ephemeral=False, **extra
    ) -> Msg:
        m = Msg(chat.id, next(self._ids), text, user.to_dict(), reply_to=reply_to, thread_id=thread_id, extra=extra)
        if ephemeral:
            m.extra["incoming_ephemeral_id"] = next(self._eph)
            self.incoming_ephemeral[(chat.id, m.extra["incoming_ephemeral_id"])] = self.now()
        self.messages.append(m)
        return m

    def message_update(self, m: Msg, *, ephemeral_command: bool = False) -> dict:
        d = self._message_dict(m)
        text = m.text
        if text.startswith("/"):
            cmd = text.split()[0]
            if cmd[1:2].isascii() and cmd[1:2].isalpha():
                d["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd.encode("utf-16-le")) // 2}]
        if m.extra.get("incoming_ephemeral_id"):
            d["ephemeral_message_id"] = m.extra["incoming_ephemeral_id"]
        for key in (
            "new_chat_members",
            "sticker",
            "photo",
            "caption",
            "community_chat_added",
            "community_chat_removed",
        ):
            if key in m.extra:
                d[key] = m.extra[key]
                if key in ("sticker", "photo"):
                    d.pop("text", None)
        return {"update_id": next(self._update_id), "message": d}

    def callback_update(self, chat_id: int, user: FakeUser, msg: Msg, data: str) -> dict:
        qid = str(next(self._qid))
        self.callbacks[qid] = (self.now(), user.id, chat_id)
        return {
            "update_id": next(self._update_id),
            "callback_query": {
                "id": qid,
                "from": user.to_dict(),
                "chat_instance": "ci",
                "data": data,
                "message": self._message_dict(msg),
            },
        }

    # --- reading the chat ----------------------------------------------------------------------------

    def visible(self, chat_id: int, user_id: int | None = None) -> list[Msg]:
        """Messages in the chat right now, as ``user_id`` sees them (their ephemeral messages included)."""
        return [
            m
            for m in self.messages
            if m.chat_id == chat_id and not m.deleted and (m.receiver is None or m.receiver == user_id)
        ]

    def public(self, chat_id: int) -> list[Msg]:
        return [m for m in self.messages if m.chat_id == chat_id and not m.deleted and m.receiver is None]


class ApiError(Exception):
    pass


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


async def process(app, data: dict) -> None:
    await app.process_update(Update.de_json(data, app.bot))
