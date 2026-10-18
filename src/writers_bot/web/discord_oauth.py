"""Signing in on the website with Discord (OAuth2 authorization code, ``identify`` scope only).

The ``state`` is signed and expires, so a callback can't be forged; after the exchange the server hands
out its own session token in the URL fragment (never sent to a server or logged), and the page keeps it.
"""

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
from aiohttp import web

from .auth import WebUser

if TYPE_CHECKING:
    from .server import WebApp

AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = "https://discord.com/api/oauth2/token"  # noqa: S105 - an endpoint, not a secret
ME = "https://discord.com/api/users/@me"
STATE_TTL = 10 * 60


def _state(secret: str, now: float | None = None) -> str:
    nonce = secrets.token_urlsafe(12)
    expires = str(int((now or time.time()) + STATE_TTL))
    mac = hmac.new(secret.encode(), f"{nonce}.{expires}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{nonce}.{expires}.{mac}"


def _check_state(secret: str, state: str, now: float | None = None) -> bool:
    try:
        nonce, expires, mac = state.split(".")
    except ValueError:
        return False
    good = hmac.new(secret.encode(), f"{nonce}.{expires}".encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(good, mac) and int(expires) >= (now or time.time())


def routes(app: "WebApp", *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    config = app.config
    if not config.discord_client_id or not config.discord_client_secret:
        return
    redirect_uri = f"{config.url}/auth/discord/callback"
    bot_name = next(iter(app.tbots), "")

    async def start(_request: web.Request) -> web.Response:
        query = urlencode(
            {
                "client_id": config.discord_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "identify",
                "state": _state(config.secret),
                "prompt": "none",
            }
        )
        raise web.HTTPFound(f"{AUTHORIZE}?{query}")

    async def callback(request: web.Request) -> web.Response:
        code, state = request.query.get("code", ""), request.query.get("state", "")
        if not code or not _check_state(config.secret, state):
            raise web.HTTPFound("/?login=failed")
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            token = await client.post(
                TOKEN,
                data={
                    "client_id": config.discord_client_id,
                    "client_secret": config.discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            if token.status_code != 200:
                raise web.HTTPFound("/?login=failed")
            me = await client.get(ME, headers={"Authorization": f"Bearer {token.json()['access_token']}"})
            if me.status_code != 200:
                raise web.HTTPFound("/?login=failed")
            data = me.json()
        user = WebUser(
            "discord", str(data["id"]), data.get("global_name") or data.get("username") or str(data["id"]), bot_name
        )
        raise web.HTTPFound(f"/#session={app.signer.session(user)}")

    app.app.router.add_get("/auth/discord", start)
    app.app.router.add_get("/auth/discord/callback", callback)
