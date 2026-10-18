"""The runtime's part of the web app: launch links, the chats you administer and their settings, and your
data (export and deletion). Modules add their own routes (sprints, replies, prompts)."""

from typing import TYPE_CHECKING

from aiohttp import web

from ..core.export import export_person
from ..core.i18n import t
from ..web.auth import AuthError, WebUser
from ..web.server import ApiError, WebApp, ok

if TYPE_CHECKING:
    from .app import TelegramBot

PLATFORM = "telegram"


def routes(tbot: "TelegramBot", app: WebApp) -> None:
    settings = tbot.services.shared.extra.get("settings")

    async def launch(request: web.Request, user: WebUser) -> web.Response:
        try:
            chat_id, target = app.signer.check_launch(request.query.get("token", ""))
        except AuthError:
            raise ApiError(400, "web.error_link") from None
        await app.require_member(user, chat_id)
        return web.json_response(
            {"kind": "sprint" if target else "chat", "chat_id": chat_id, "sprint_id": target or None}
        )

    async def admin_chats(user: WebUser) -> list[dict]:
        if user.platform != "telegram":
            return []
        return await tbot.admin_chats(int(user.user_id))

    async def chats(_request: web.Request, user: WebUser) -> web.Response:
        return web.json_response({"chats": await admin_chats(user)})

    app.admin_chats = admin_chats

    def chat_id_of(request: web.Request) -> int:
        try:
            return int(request.match_info["chat"])
        except ValueError:
            raise ApiError(400, "web.error_bad_request") from None

    async def chat_settings(request: web.Request, user: WebUser) -> web.Response:
        chat_id = chat_id_of(request)
        await app.require_chat_admin(user, chat_id)
        if settings is None:
            return web.json_response({"settings": []})
        values = await settings.resolve(PLATFORM, str(chat_id))
        record = await tbot.storage.get_chat(PLATFORM, str(chat_id))
        community = await settings.communities.of_chat(PLATFORM, str(chat_id))
        out = []
        for d in settings.defs():
            value, source = values[d.key]
            out.append(
                {
                    "key": d.key,
                    "kind": d.kind,
                    "value": value,
                    "source": source,
                    "label": t(d.locale),
                    "help": t(d.locale + "_help"),
                    "choices": [{"value": c, "label": t(f"{d.locale}_{c}")} for c in d.choices],
                    "min": d.minimum,
                    "max": d.maximum,
                }
            )
        return web.json_response(
            {
                "title": record.title if record else str(chat_id),
                "community": community.name if community else None,
                "settings": out,
                "modules": list(tbot.config.modules),
            }
        )

    async def update_settings(request: web.Request, user: WebUser) -> web.Response:
        chat_id = chat_id_of(request)
        await app.require_chat_admin(user, chat_id)  # checked on every change
        body = await app.body(request)
        try:
            for key, value in body.items():
                await settings.set(PLATFORM, str(chat_id), key, value)
        except (KeyError, ValueError):
            raise ApiError(400, "web.error_bad_request") from None
        return await chat_settings(request, user)

    async def export(_request: web.Request, user: WebUser) -> web.StreamResponse:
        person = await tbot.people.find(user.platform, user.user_id)
        if person is None:
            raise ApiError(404, "data.empty")
        extra = [h for m in tbot.modules if (h := getattr(m, "export_files", None))]
        data, count = await export_person(tbot.storage, tbot.people, tbot.services.works, person, extra)
        if not count:
            raise ApiError(404, "data.empty")
        return web.Response(
            body=data,
            content_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="my-writing-data.zip"'},
        )

    async def send_export(_request: web.Request, user: WebUser) -> web.Response:
        """In Telegram the file goes to your private chat with the bot (a Mini App can't save a download)."""
        if user.platform != "telegram" or tbot.data is None:
            raise ApiError(400, "web.error_bad_request")

        class _User:
            id = int(user.user_id)

        result = await tbot.data.send_export(_User())
        key = {"sent": "data.sent", "empty": "data.empty", "dm": "data.open_dm"}[result]
        if result != "sent":
            raise ApiError(409, key)
        return ok({"message": t(key)})

    async def forget(_request: web.Request, user: WebUser) -> web.Response:
        for module in tbot.modules:
            hook = getattr(module, "forget", None)
            if hook is not None:
                await hook(user.platform, user.user_id)
        await tbot.people.forget_account(user.platform, user.user_id)
        return ok()

    app.add("GET", "/api/launch", launch)
    app.add("GET", "/api/chats", chats)
    app.add("GET", r"/api/chat/{chat:-?\d+}/settings", chat_settings)
    app.add("PATCH", r"/api/chat/{chat:-?\d+}/settings", update_settings)
    app.add("GET", "/api/export", export)
    app.add("POST", "/api/export/send", send_export)
    app.add("DELETE", "/api/me", forget)
