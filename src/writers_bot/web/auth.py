"""Who is using the web app.

* **Telegram Mini App**: the client sends ``initData``; it is valid when its hash is the HMAC of the
  other fields with ``HMAC_SHA256("WebAppData", bot_token)`` as the key, and fresh.
* **Telegram Login** (the website): the widget's fields, hashed with ``SHA256(bot_token)`` as the key.
* **Discord OAuth** (the website): see :mod:`.discord_oauth`.

The server then hands out its own session token, signed with the web secret. Launch tokens (the
``startapp`` parameter of the Mini App link in a group) name a chat and a sprint and expire quickly; the
server still checks that the user is in that chat.
"""

import base64
import hashlib
import hmac
import json
import struct
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

INIT_DATA_MAX_AGE = 24 * 3600
SESSION_TTL = 12 * 3600
LAUNCH_TTL = 24 * 3600  # a sprint's Mini App button keeps working through its late reports


class AuthError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WebUser:
    platform: str
    user_id: str
    name: str
    bot: str  # the bot (config name) whose token signed the login


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _name(user: dict) -> str:
    return (
        " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
        or user.get("username")
        or str(user.get("id"))
    )


def verify_init_data(
    init_data: str, bot_token: str, *, now: float | None = None, max_age: int = INIT_DATA_MAX_AGE
) -> tuple[dict, dict]:
    """Returns (the user, all fields). Raises :class:`AuthError`."""
    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received = fields.pop("hash", None)
    if not received:
        raise AuthError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("bad hash")
    auth_date = int(fields.get("auth_date", "0") or 0)
    if (now or time.time()) - auth_date > max_age:
        raise AuthError("expired")
    try:
        user = json.loads(fields.get("user", "{}"))
    except ValueError:
        raise AuthError("bad user") from None
    if not isinstance(user, dict) or "id" not in user:
        raise AuthError("no user")
    return user, fields


def verify_login_widget(
    data: dict, bot_token: str, *, now: float | None = None, max_age: int = INIT_DATA_MAX_AGE
) -> dict:
    fields = {k: str(v) for k, v in data.items() if v is not None}
    received = fields.pop("hash", None)
    if not received:
        raise AuthError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("bad hash")
    if (now or time.time()) - int(fields.get("auth_date", "0") or 0) > max_age:
        raise AuthError("expired")
    return {"id": int(fields["id"]), **{k: fields[k] for k in ("first_name", "last_name", "username") if k in fields}}


def user_from_telegram(user: dict, bot: str) -> WebUser:
    return WebUser("telegram", str(int(user["id"])), _name(user), bot)


class Signer:
    def __init__(self, secret: str):
        self._key = hashlib.sha256(b"writers-bot web " + secret.encode()).digest()

    def _mac(self, payload: bytes, size: int = 32) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()[:size]

    # --- sessions --------------------------------------------------------------------------------

    def session(self, user: WebUser, *, now: float | None = None, ttl: int = SESSION_TTL) -> str:
        payload = json.dumps(
            {
                "p": user.platform,
                "u": user.user_id,
                "n": user.name,
                "b": user.bot,
                "e": int((now or time.time()) + ttl),
            },
            separators=(",", ":"),
        ).encode()
        return f"{_b64(payload)}.{_b64(self._mac(payload))}"

    def check_session(self, token: str, *, now: float | None = None) -> WebUser:
        try:
            body, mac = token.split(".", 1)
            payload = _unb64(body)
            if not hmac.compare_digest(self._mac(payload), _unb64(mac)):
                raise AuthError("bad session")
            data = json.loads(payload)
        except (ValueError, TypeError) as e:
            raise AuthError("bad session") from e
        if data.get("e", 0) < (now or time.time()):
            raise AuthError("session expired")
        return WebUser(data["p"], data["u"], data["n"], data.get("b", ""))

    # --- launch tokens (Mini App start_param: up to 64 characters of [A-Za-z0-9_-]) ------------------

    def launch(self, chat_id: int, sprint_id: int, *, now: float | None = None, ttl: int = LAUNCH_TTL) -> str:
        payload = struct.pack(">qII", chat_id, sprint_id, int((now or time.time()) + ttl))
        return _b64(payload + self._mac(payload, 12))

    def check_launch(self, token: str, *, now: float | None = None) -> tuple[int, int]:
        try:
            raw = _unb64(token)
        except ValueError as e:
            raise AuthError("bad launch token") from e
        payload, mac = raw[:16], raw[16:]
        if len(payload) != 16 or not hmac.compare_digest(self._mac(payload, 12), mac):
            raise AuthError("bad launch token")
        chat_id, sprint_id, expires = struct.unpack(">qII", payload)
        if expires < (now or time.time()):
            raise AuthError("launch token expired")
        return chat_id, sprint_id
