"""Your data: export and deletion, from /profile → Settings → My data (/export and /forgetme stay as
fallbacks, off the group menu)."""

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatType, KeyboardButtonStyle
from telegram.error import Forbidden

from ..core.export import export_person
from ..core.i18n import t
from .base import Command, Sent, button

if TYPE_CHECKING:
    from .app import TelegramBot

logger = logging.getLogger(__name__)

PLATFORM = "telegram"


class YourData:
    def __init__(self, tbot: "TelegramBot"):
        self.tbot = tbot

    def commands(self) -> list[Command]:
        return [
            Command(
                "export",
                self.export_command,
                "commands.export",
                uk="aliases.export",
                scope="private",
            ),
            Command("forgetme", self.forget_command, "commands.forgetme", uk="aliases.forgetme", scope="private"),
        ]

    def asks(self) -> dict:
        return {"dt": self.on_button, "fm": self.on_forget}

    def _menu(self, user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [button(t("data.export"), user_id, "dt:export")],
                [button(t("data.forget"), user_id, "dt:forget", style=KeyboardButtonStyle.DANGER)],
                [button(t("common.back"), user_id, "pf:settings")],
            ]
        )

    async def on_button(self, query, args: list[str]) -> None:
        action = args[0] if args else ""
        sent = Sent.from_query(query)
        user = query.from_user
        if action == "menu":
            await query.answer()
            await self.tbot.asks.edit(sent, self.tbot.fmt.t("data.menu"), self._menu(user.id))
        elif action == "export":
            ok = await self.send_export(user)
            await self.tbot.asks.toast(
                query,
                "data.sent" if ok == "sent" else "data.empty" if ok == "empty" else "data.open_dm",
                alert=ok == "dm",
            )
        elif action == "forget":
            await query.answer()
            await self.tbot.asks.edit(sent, self.tbot.fmt.t("data.forget_confirm"), self._confirm(user.id))
        else:
            await self.tbot.asks.toast(query, "common.stale")

    def _confirm(self, user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    button(t("data.forget_yes"), user_id, "fm:go", style=KeyboardButtonStyle.DANGER),
                    button(t("common.cancel"), user_id, "fm:no"),
                ]
            ]
        )

    async def send_export(self, user) -> str:
        """ "sent", "empty" or "dm" (the user has no private chat with the bot)."""
        people = self.tbot.people
        person = await people.find(PLATFORM, str(user.id))
        if person is None:
            return "empty"
        extra = [h for m in self.tbot.modules if (h := getattr(m, "export_files", None))]
        data, count = await export_person(self.tbot.storage, people, self.tbot.services.works, person, extra)
        if not count:
            return "empty"
        try:
            await self.tbot.app.bot.send_document(
                user.id,
                InputFile(data, filename="my-writing-data.zip"),
                caption=t("data.caption", count=count),
                parse_mode=None,
            )
        except Forbidden:
            return "dm"
        return "sent"

    async def export_command(self, update: Update, _context) -> None:
        message = update.effective_message
        result = await self.send_export(message.from_user)
        if result == "sent" and message.chat.type == ChatType.PRIVATE:
            return
        key = {"sent": "data.sent", "empty": "data.empty", "dm": "data.open_dm"}[result]
        await self.tbot.asks.respond(message, t(key), html=False, delete_command=True)

    async def forget_command(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type != ChatType.PRIVATE:
            await self.tbot.asks.respond(message, t("data.forget_private"), html=False, delete_command=True)
            return
        await message.reply_text(
            self.tbot.fmt.t("data.forget_confirm"), parse_mode="HTML", reply_markup=self._confirm(message.from_user.id)
        )

    async def on_forget(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        await query.answer()
        if args and args[0] == "go":
            for module in self.tbot.modules:  # before the person goes: modules may look it up
                hook = getattr(module, "forget", None)
                if hook is not None:
                    await hook(PLATFORM, str(query.from_user.id))
            removed = await self.tbot.people.forget_account(PLATFORM, str(query.from_user.id))
            logger.info("user data deleted on request (%d sprint entries)", removed)
            await self.tbot.asks.edit(sent, self.tbot.fmt.t("data.forgotten", count=removed))
        else:
            await self.tbot.asks.edit(sent, self.tbot.fmt.t("data.kept"))
