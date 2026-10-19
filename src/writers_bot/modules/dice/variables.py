"""Roll variables in chats: ``/dicevar``, rolling ``/$name``, and moving variables from one chat to another.

A variable belongs to a person in a chat (the private chat with the bot is a chat too) and is a series of up to 8 rolls
(``core.dicevars``). ``/dicevar`` answers privately (an ephemeral message where Telegram allows, else a reply that goes away):

    /dicevar                          your variables here
    /dicevar $hit d20+5 Атака; 2d6+3 Шкода    keep (or change) one; 6x 4d6dl1 is six rolls
    /dicevar $hit                     show one
    /dicevar unset $hit | unset all   delete
    /dicevar import                   copy variables from another chat (the private one too), asking about every name both have
"""

import logging
import secrets
import time
from dataclasses import replace

from telegram import InlineKeyboardMarkup, Message

from ...core import dicevars, extras
from ...core.dicevars import VarError
from ...core.i18n import raw, t
from ...telegram_bot.app import command_args_text
from ...telegram_bot.base import Command, Sent, button
from .store import VarStore

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
FLOW_TTL = 10 * 60
MAX_SOURCES = 8


class Variables:
    def __init__(self, module):
        self.module = module
        self.bot = module.bot
        self.store = VarStore(self.bot.storage)
        self._flows: dict[str, dict] = {}  # token -> a /dicevar import in progress

    # --- wiring -----------------------------------------------------------------------------------------

    def commands(self) -> list[Command]:
        return [Command("dicevar", self.command, "commands.dicevar", uk="aliases.dicevar", usage="help.usage_dicevar")]

    def asks(self) -> dict:
        return {"dv": self.on_button}

    @staticmethod
    def scope(message: Message) -> tuple[str, str, str]:
        return PLATFORM, str(message.chat_id), str(message.from_user.id)

    async def answer(self, message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> Sent | None:
        """A private answer to the sender, and the command is deleted where the bot may."""
        return await self.bot.asks.respond(message, text, markup, delete_command=True)

    # --- /dicevar ---------------------------------------------------------------------------------------

    async def command(self, update, _context) -> None:
        message = update.effective_message
        f = self.bot.fmt
        args = command_args_text(message)
        word, _, rest = args.partition(" ")
        low = word.lower()
        try:
            if not args:
                await self.show_all(message)
            elif low in raw("dice.var_unset_words"):
                await self.unset(message, rest.strip())
            elif low in raw("dice.var_import_words"):
                await self.start_import(message)
            elif word.startswith("$"):
                if rest.strip():
                    await self.define(message, word, rest)
                else:
                    await self.show_one(message, word)
            else:
                await self.answer(message, f.t("dice.var_usage"))
        except VarError as e:
            await self.answer(message, f.t(e.key, **e.values))

    async def show_all(self, message: Message) -> None:
        f = self.bot.fmt
        mine = await self.store.all(*self.scope(message))
        if not mine:
            await self.answer(message, f.t("dice.var_none"))
            return
        lines = [f.t("dice.var_list_title")] + [
            f.t("dice.var_show", name=name, steps=dicevars.compact(steps)) for name, steps in mine.items()
        ]
        await self.answer(message, f.lines(lines))

    async def show_one(self, message: Message, word: str) -> None:
        name = dicevars.clean_name(word)
        steps = await self.store.get(*self.scope(message), name)
        if steps is None:
            await self.unknown(message, name)
            return
        await self.answer(message, self.bot.fmt.t("dice.var_show", name=name, steps=dicevars.compact(steps)))

    async def define(self, message: Message, word: str, text: str) -> None:
        name = dicevars.clean_name(word)
        steps = dicevars.parse_steps(text)
        new = await self.store.set(*self.scope(message), name, steps)
        key = "dice.var_saved" if new else "dice.var_updated"
        await self.answer(message, self.bot.fmt.t(key, name=name, steps=dicevars.compact(steps)))

    async def unset(self, message: Message, target: str) -> None:
        f = self.bot.fmt
        if not target:
            await self.answer(message, f.t("dice.var_unset_usage"))
        elif target.lower() in raw("dice.var_all_words"):
            count = await self.store.unset_all(*self.scope(message))
            await self.answer(message, f.t("dice.var_unset_all", count=count))
        else:
            name = dicevars.clean_name(target)
            if await self.store.unset(*self.scope(message), name):
                await self.answer(message, f.t("dice.var_unset", name=name))
            else:
                await self.unknown(message, name)

    async def unknown(self, message: Message, name: str) -> None:
        known = list(await self.store.all(*self.scope(message)))
        listed = " " + t("dice.var_known", names=", ".join(f"${n}" for n in known)) if known else ""
        await self.answer(message, self.bot.fmt.t("dice.var_unknown", name=name, known=listed))

    # --- keeping a roll as a variable (/d20+4 =$strcheck) -------------------------------------------------

    async def save_from_roll(self, message: Message, roll: extras.Roll) -> None:
        """``=$name`` on a roll: keep the roll (without its flags) as that variable, and say so."""
        f = self.bot.fmt
        step = roll.expression + (roll.mode or "")
        if roll.caption != t("dice.result"):
            step += " " + roll.caption
        try:
            name = dicevars.clean_name(roll.save_as)
            steps = dicevars.parse_steps(step)
            new = await self.store.set(*self.scope(message), name, steps)
        except VarError as e:
            await self.bot.asks.respond(message, f.t(e.key, **e.values))
            return
        key = "dice.var_saved" if new else "dice.var_updated"
        await self.bot.asks.respond(message, f.t(key, name=name, steps=dicevars.compact(steps)))

    # --- rolling a variable (/$hit) -----------------------------------------------------------------------

    async def roll_reference(self, message: Message, ref: dicevars.Reference) -> None:
        steps = await self.store.get(*self.scope(message), ref.name)
        if steps is None:
            await self.unknown(message, ref.name)
            return
        flags = {"-s": {"stickers": True}, "-q": {"stickers": False}, "-p": {"private": True}}
        rolls = []
        for step in steps:
            roll = extras.parse_roll("/" + step)
            if roll is None or not roll.expression:
                continue  # a step that was valid when kept and is not now (the notation changed): skip it
            if roll.caption == t("dice.result"):
                roll = replace(roll, caption=ref.name)  # "hit 17" rather than "Результат 17"
            for flag in ref.flags:
                roll = replace(roll, **flags[flag])
            rolls.append(roll)
        if rolls:
            await self.module.deliver(message, rolls)

    # --- moving variables between chats (/dicevar import) ---------------------------------------------------

    def _remember(self, state: dict) -> str:
        now = time.monotonic()
        for key in [k for k, v in self._flows.items() if now - v["since"] > FLOW_TTL]:
            del self._flows[key]
        token = secrets.token_hex(4)
        self._flows[token] = state | {"since": now}
        return token

    async def chat_title(self, chat_id: str, user_id: str) -> str:
        if chat_id == user_id:
            return t("dice.var_private_title")
        record = await self.bot.storage.get_chat(PLATFORM, chat_id)
        return record.title if record is not None and record.title else t("dice.var_chat_unknown", id=chat_id)

    async def start_import(self, message: Message) -> None:
        f = self.bot.fmt
        _, here, uid = self.scope(message)
        sources = [(chat, n) for chat, n in await self.store.chats_of(PLATFORM, uid) if chat != here]
        if not sources:
            await self.answer(message, f.t("dice.var_import_none"))
            return
        token = self._remember({"user": uid, "target": here, "sources": [c for c, _ in sources[:MAX_SOURCES]]})
        rows = [
            [
                button(
                    t("dice.var_import_source", title=await self.chat_title(chat, uid), count=n),
                    uid,
                    f"dv:src:{token}:{i}",
                )
            ]
            for i, (chat, n) in enumerate(sources[:MAX_SOURCES])
        ]
        rows.append([button(t("common.cancel"), uid, f"dv:stop:{token}")])
        await self.answer(message, f.t("dice.var_import_pick"), InlineKeyboardMarkup(rows))

    async def on_button(self, query, args: list[str]) -> None:
        """``dv:src:<token>:<i>`` (a source chat), ``dv:keep|repl|keepall|replall|stop:<token>``."""
        sent = Sent.from_query(query)
        action, token = (args + ["", ""])[:2]
        state = self._flows.get(token)
        if state is None or sent is None or str(query.from_user.id) != state["user"]:
            await self.bot.asks.toast(query, "common.stale")
            return
        await query.answer()
        if action == "stop":
            self._flows.pop(token, None)
            await self.bot.asks.delete(sent)
            return
        uid, target = state["user"], state["target"]
        if action == "src":
            try:
                source = state["sources"][int(args[2])]
            except (IndexError, ValueError):
                await self.bot.asks.toast(query, "common.stale")
                return
            theirs, mine = await self.store.all(PLATFORM, source, uid), await self.store.all(PLATFORM, target, uid)
            state.update(copied=0, kept=0, queue=[])
            for name, steps in theirs.items():
                if name not in mine:
                    await self._copy(state, name, steps)
                elif mine[name] != steps:
                    state["queue"].append((name, steps, mine[name]))
        elif state.get("queue"):
            await self._resolve(state, action)
        await self._next(sent, token, state)

    async def _copy(self, state: dict, name: str, steps: list[str]) -> None:
        try:
            await self.store.set(PLATFORM, state["target"], state["user"], name, steps)
            state["copied"] += 1
        except VarError:  # too many variables here: the rest stays where it is
            state["kept"] += 1

    async def _resolve(self, state: dict, action: str) -> None:
        """Answer the first conflict (or all of them) with their variable: keep the one here, or replace it."""
        replace_it = action in ("repl", "replall")
        batch = list(state["queue"] if action.endswith("all") else state["queue"][:1])
        del state["queue"][: len(batch)]
        for name, theirs, _mine in batch:
            if replace_it:
                await self._copy(state, name, theirs)
            else:
                state["kept"] += 1

    async def _next(self, sent: Sent, token: str, state: dict) -> None:
        f = self.bot.fmt
        uid = state["user"]
        if not state["queue"]:
            self._flows.pop(token, None)
            await self.bot.asks.edit(sent, f.t("dice.var_import_done", copied=state["copied"], kept=state["kept"]))
            return
        name, theirs, mine = state["queue"][0]
        text = f.t(
            "dice.var_conflict",
            name=name,
            here=dicevars.compact(mine),
            there=dicevars.compact(theirs),
            left=len(state["queue"]) - 1,
        )
        rows = [
            [
                button(t("dice.var_keep_here"), uid, f"dv:keep:{token}"),
                button(t("dice.var_replace"), uid, f"dv:repl:{token}"),
            ],
        ]
        if len(state["queue"]) > 1:
            rows.append(
                [
                    button(t("dice.var_keep_all"), uid, f"dv:keepall:{token}"),
                    button(t("dice.var_replace_all"), uid, f"dv:replall:{token}"),
                ]
            )
        rows.append([button(t("common.cancel"), uid, f"dv:stop:{token}")])
        await self.bot.asks.edit(sent, text, InlineKeyboardMarkup(rows))

    # --- your data -----------------------------------------------------------------------------------------

    async def export_rows(self, accounts) -> list[list[str]]:
        rows = []
        for account in accounts:
            for chat_id, name, steps in await self.store.everything(account.platform, account.user_id):
                rows.append([chat_id, f"${name}", dicevars.compact(steps)])
        return rows

    async def forget(self, platform: str, user_id: str) -> None:
        await self.store.forget(platform, user_id)
