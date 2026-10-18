"""The web app: one small server, opened as a Telegram Mini App or as a website.

Only the bot with storage serves it (one domain, one bot token to check ``initData`` and Telegram Login
against). Modules add their API routes with a ``web_routes(web)`` hook; the page itself is one static
app (``static/``) that gets its texts from the locale file through ``/api/texts``.

Without a configured URL (or with the tunnel down) everything works in chat and the web buttons are hidden.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from importlib import resources
from typing import TYPE_CHECKING

from aiohttp import web
from telegram import InlineKeyboardButton, WebAppInfo

from ..core.i18n import t, texts
from .auth import AuthError, Signer, WebUser, user_from_telegram, verify_init_data, verify_login_widget

if TYPE_CHECKING:
    from ..botconfig import WebConfig
    from ..core.models import Sprint
    from ..services import Services
    from ..telegram_bot.app import TelegramBot

logger = logging.getLogger(__name__)

Handler = Callable[[web.Request, WebUser], Awaitable[web.StreamResponse]]
TAB_ORDER = ["sprint", "works", "progress", "settings", "chat", "prompts", "achievements"]
ADMIN_TABS = {"chat", "prompts", "achievements"}
STATIC = {"app.js": "application/javascript", "app.css": "text/css", "index.html": "text/html"}
CSP = (
    "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; frame-src https://oauth.telegram.org; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org"
)


class ApiError(web.HTTPException):
    def __init__(self, status: int, key: str, **values):
        self.status_code = status
        super().__init__(text=json.dumps({"error": t(key, **values), "code": key}), content_type="application/json")


def ok(data=None) -> web.Response:
    return web.json_response(data if data is not None else {"ok": True})


class WebApp:
    def __init__(self, config: "WebConfig", services: "Services"):
        self.config = config
        self.services = services
        self.signer = Signer(config.secret)
        self.tbots: dict[str, TelegramBot] = {}
        self.app = web.Application(middlewares=[self._headers], client_max_size=64 * 1024)
        self._runner: web.AppRunner | None = None
        self.admin_chats: Callable[[WebUser], Awaitable[list[dict]]] | None = None  # set by the runtime
        self._routes_added = False

    # --- setup -----------------------------------------------------------------------------------

    def attach(self, tbots: list["TelegramBot"]) -> None:
        """Register the bots whose users sign in here, and their modules' routes."""
        for tbot in tbots:
            self.tbots[tbot.config.name] = tbot
        if self._routes_added:
            return
        self._routes_added = True
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/static/{name}", self._static)
        r.add_get("/api/texts", self._texts)
        r.add_get("/api/config", self._config)
        r.add_post("/api/session", self._session)
        r.add_get("/api/me", self.authed(self._me))
        from .discord_oauth import routes as discord_routes

        discord_routes(self)
        for tbot in tbots:
            for module in tbot.modules:
                hook = getattr(module, "web_routes", None)
                if hook is not None:
                    hook(self)
            hook = getattr(tbot, "web_routes", None)
            if hook is not None:
                hook(self)

    async def start(self, tbots: list["TelegramBot"]) -> None:
        self.attach(tbots)
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        logger.info("web app on %s:%s for %s", self.config.host, self.config.port, self.config.url)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def add(self, method: str, path: str, handler: Handler) -> None:
        self.app.router.add_route(method, path, self.authed(handler))

    def authed(self, handler: Handler):
        async def wrapper(request: web.Request) -> web.StreamResponse:
            return await handler(request, self.user(request))

        return wrapper

    def user(self, request: web.Request) -> WebUser:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "web.error_login")
        try:
            return self.signer.check_session(header[7:])
        except AuthError:
            raise ApiError(401, "web.error_login") from None

    @web.middleware
    async def _headers(self, request: web.Request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as e:  # returning the exception itself is deprecated in aiohttp
            response = web.Response(status=e.status, body=e.body, headers=e.headers)
        except Exception:
            logger.exception("web request %s %s failed", request.method, request.path)
            response = web.json_response({"error": t("web.error_server")}, status=500)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # --- pages and auth --------------------------------------------------------------------------

    def _read_static(self, name: str) -> bytes:
        return resources.files("writers_bot.web").joinpath("static", name).read_bytes()

    async def _index(self, _request: web.Request) -> web.Response:
        return web.Response(body=self._read_static("index.html"), content_type="text/html", charset="utf-8")

    async def _static(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in STATIC:
            raise web.HTTPNotFound()
        return web.Response(body=self._read_static(name), content_type=STATIC[name], charset="utf-8")

    async def _texts(self, _request: web.Request) -> web.Response:
        data = {k[4:]: v for k, v in texts().items() if k.startswith("web.")}
        data |= {"plural_" + k[7:]: v for k, v in texts().items() if k.startswith("plural.")}
        return web.json_response(data)

    async def _config(self, _request: web.Request) -> web.Response:
        bots = [{"name": n, "username": b.me.username if b.me else None} for n, b in self.tbots.items()]
        return web.json_response({"bots": bots, "discord": bool(self.config.discord_client_id)})

    async def _session(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            raise ApiError(400, "web.error_bad_request") from None
        user = None
        start_param = None
        for name, tbot in self.tbots.items():
            try:
                if "init_data" in body:
                    data, fields = verify_init_data(str(body["init_data"]), tbot.settings.token)
                    user = user_from_telegram(data, name)
                    start_param = fields.get("start_param")
                elif "login" in body:
                    data = verify_login_widget(dict(body["login"]), tbot.settings.token)
                    user = user_from_telegram(data, name)
                break
            except (AuthError, KeyError, TypeError, ValueError):
                continue
        if user is None:
            raise ApiError(401, "web.error_login")
        return web.json_response({"token": self.signer.session(user), "name": user.name, "start": start_param})

    async def _me(self, _request: web.Request, user: WebUser) -> web.Response:
        tabs = []
        for tbot in self.tbots.values():
            for module in tbot.modules:
                tabs += getattr(module, "web_tabs", lambda: [])()
            tabs += tbot.web_tabs()
        if ADMIN_TABS & set(tabs) and not (self.admin_chats is not None and await self.admin_chats(user)):
            tabs = [tab for tab in tabs if tab not in ADMIN_TABS]  # only for people who administer a chat
        tabs = sorted(
            dict.fromkeys(tabs), key=lambda tab: TAB_ORDER.index(tab) if tab in TAB_ORDER else len(TAB_ORDER)
        )
        return web.json_response({"name": user.name, "platform": user.platform, "tabs": tabs})

    # --- helpers for modules -----------------------------------------------------------------------

    def bot_for(self, user: WebUser) -> "TelegramBot":
        tbot = self.tbots.get(user.bot) or next(iter(self.tbots.values()), None)
        if tbot is None:
            raise ApiError(503, "web.error_server")
        return tbot

    async def require_member(self, user: WebUser, chat_id: int) -> None:
        """The launch link doesn't prove which chat it came from, so check the member list."""
        if user.platform == "telegram" and chat_id == int(user.user_id):
            return  # their own private chat with the bot
        tbot = self.bot_for(user)
        try:
            member = await tbot.app.bot.get_chat_member(chat_id, int(user.user_id))
        except Exception:
            raise ApiError(403, "web.error_not_member") from None
        if member.status in ("left", "kicked"):
            raise ApiError(403, "web.error_not_member")

    async def require_chat_admin(self, user: WebUser, chat_id: int) -> None:
        """Admin rights are checked with getChatMember on every change."""
        if user.platform != "telegram" or not await self.bot_for(user).is_chat_admin(chat_id, int(user.user_id)):
            raise ApiError(403, "web.error_not_admin")

    def sprint_button(self, tbot: "TelegramBot", sprint: "Sprint") -> InlineKeyboardButton | None:
        """Group chats can't open ``web_app`` buttons, so they get a link to the Main Mini App with a
        short-lived launch token; private chats get a plain ``web_app`` button."""
        if tbot.me is None:
            return None
        chat_id = int(sprint.key.chat_id)
        token = self.signer.launch(chat_id, sprint.id)
        if chat_id > 0:
            return InlineKeyboardButton(t("sprint.button_panel"), web_app=WebAppInfo(f"{self.config.url}/?s={token}"))
        return InlineKeyboardButton(t("sprint.button_panel"), url=f"https://t.me/{tbot.me.username}?startapp={token}")

    @staticmethod
    async def body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            raise ApiError(400, "web.error_bad_request") from None
        if not isinstance(data, dict):
            raise ApiError(400, "web.error_bad_request")
        return data
