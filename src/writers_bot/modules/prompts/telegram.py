"""Prompts on Telegram: /prompt, /image (and /image_<category>), /promptstats.

``source = "sheet"`` reads the Google Sheet and Drive as in v2 (and keeps /reload). ``source = "db"`` keeps
prompts in the database: the chat's (or community's) set plus the shared set, filled once from the sheet;
chat admins add prompts (/addprompt, /addimage), members suggest them (/suggest, reviewed with
/suggestions), and super-admins review what chats submit to the shared set (/review).
"""

import asyncio
import contextlib
import difflib
import logging

from telegram import InlineKeyboardMarkup, LinkPreviewOptions, Message, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes, MessageHandler, filters

from ...core.i18n import t
from ...core.prompts import PromptsUnavailableError, upload_image
from ...telegram_bot.app import command_args_text
from ...telegram_bot.base import Command, Sent, TelegramModule, button
from .store import ACTIVE, REJECTED, SUBMITTED, SUGGESTED, Category, PromptDB

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
LANG = "ua"
IMAGE_CMD = filters.Regex(r"^/image(?:_[\w]+)?(?:@\w+)?(?:\s|$)")


class Module(TelegramModule):
    name = "prompts"

    def __init__(self, bot, options):
        super().__init__(bot, options)
        self.db = PromptDB(bot.storage) if options.get("source") == "db" and bot.storage is not None else None
        if self.db is not None:
            bot.services.shared.extra["prompts"] = self.db

    @property
    def store(self):
        return self.bot.services.sheet

    def config_text(self, key: str) -> str | None:
        return self.store.config_text(key) if self.store is not None else None

    @property
    def categories(self) -> bool:
        return self.db is not None or bool(self.options.get("categories"))

    def commands(self) -> list[Command]:
        commands = [
            Command(
                "prompt",
                self.prompt,
                "commands.prompt",
                uk="aliases.prompt",
                aliases=("prompt_ua",),
                usage="help.usage_category" if self.categories else None,
                values=self._text_categories,
            ),
            Command(
                "image",
                self.image,
                "commands.image",
                uk="aliases.image",
                usage="help.usage_category" if self.categories else None,
                values=self._image_categories,
            ),
            Command("promptstats", self.stats, "commands.promptstats", aliases=("stats",), menu=False),
        ]
        if self.db is None:
            commands.append(Command("reload", self.reload, "commands.reload", scope="chat_admin", menu=False))
        else:
            commands += [
                Command(
                    "addprompt",
                    self.add_prompt,
                    "commands.addprompt",
                    scope="chat_admin",
                    usage="help.usage_addprompt",
                ),
                Command(
                    "suggest",
                    self.suggest,
                    "commands.suggest",
                    uk="aliases.suggest",
                    usage="help.usage_addprompt",
                ),
                Command(
                    "suggestions",
                    self.suggestions,
                    "commands.suggestions",
                    scope="chat_admin",
                ),
                Command(
                    "addimage",
                    self.add_image,
                    "commands.addimage",
                    scope="chat_admin",
                    menu=False,
                    usage="help.usage_addprompt",
                ),
                Command("review", self.review, "commands.review", scope="admin"),
            ]
        return commands

    async def _categories(self, ctx, kind: str) -> dict:
        """This chat's categories for /help (the shared ones in the private chat)."""
        if not self.categories:
            return {}
        try:
            if self.db is not None:
                chat = ctx.chat_id if ctx.chat_id is not None else 1  # a private chat: the shared set
                _, sets = await self.sets(chat)
                labels = sorted({c.label for c in await self.db.categories(sets, kind)}, key=str.casefold)
            elif self.store is not None:
                labels = self.store.text_categories(LANG) if kind == "text" else list(self.store.image_categories())
            else:
                labels = []
        except Exception:  # /help shouldn't fail over a category list
            logger.exception("no categories for /help")
            labels = []
        return {"categories": ", ".join(labels[:15]) or t("help.no_categories")}

    async def _text_categories(self, ctx) -> dict:
        return await self._categories(ctx, "text")

    async def _image_categories(self, ctx) -> dict:
        return await self._categories(ctx, "image")

    def handlers(self):
        # /image_<category> isn't a fixed command name, so it takes a regex.
        handlers = [(MessageHandler(filters.COMMAND & IMAGE_CMD, self.image), 0)]
        if self.db is not None:  # a photo with "/addimage <category>" as its caption
            handlers.append((MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/addimage"), self.add_image), 0))
        return handlers

    def asks(self):
        return {"pr": self.on_review_button} if self.db is not None else {}

    async def post_init(self) -> None:
        if self.db is None or self.store is None:
            return
        if not self.store.data.texts and not self.store.data.images:
            return
        count = await self.db.import_sheet(self.store.data, LANG)
        if count:
            logger.info("imported %d prompts from the sheet into the shared set", count)

    # --- which sets a chat uses ------------------------------------------------------------------

    async def sets(self, chat_id: int) -> tuple[int | None, list[int]]:
        """(the chat's own set, every set it picks from)."""
        if chat_id > 0:
            return None, [await self.db.shared()]
        communities = self.bot.services.shared.extra.get("communities")
        community = await communities.of_chat(PLATFORM, str(chat_id)) if communities else None
        own = await self.db.chat_set(PLATFORM, str(chat_id), community.id if community else None)
        settings = self.bot.services.shared.extra.get("settings")
        use_shared = await settings.get_bool(PLATFORM, str(chat_id), "prompts.use_shared") if settings else True
        return own, [own, *([await self.db.shared()] if use_shared else [])]

    # --- /prompt, /image ---------------------------------------------------------------------------

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        f = self.bot.fmt
        query = " ".join(context.args or []) if self.categories else ""  # v2: one prompt from every category
        if self.db is not None:
            await self._prompt_db(message, query)
            return
        if self.store is None:
            await message.reply_text(t("prompts.unavailable"))
            return
        try:
            category = None
            if query:
                category = self.store.find_category(LANG, query)
                if category is None:
                    cats = ", ".join(self.store.text_categories(LANG)) or "—"
                    await message.reply_html(f.t("prompts.no_category", categories=cats))
                    return
            prompt = self.store.random_text(LANG, category)
        except PromptsUnavailableError:
            await message.reply_text(t("prompts.unavailable"))
            return
        await message.reply_html("\n".join(f"{f.bold(k)}: {f.escape(v)}" for k, v in prompt.items()))

    def _groups(self, categories: list[Category]) -> dict[str, list[Category]]:
        """Categories of several sets merged by their label."""
        groups: dict[str, list[Category]] = {}
        for c in categories:
            groups.setdefault(c.label.casefold(), []).append(c)
        return groups

    async def _prompt_db(self, message: Message, query: str) -> None:
        f = self.bot.fmt
        _, sets = await self.sets(message.chat_id)
        categories = await self.db.categories(sets, "text")
        if not categories:
            await message.reply_text(t("prompts.unavailable"))
            return
        if query:
            found = self.db.find(categories, query)
            if not found:
                labels = ", ".join(sorted({c.label for c in categories})) or "—"
                await message.reply_html(f.t("prompts.no_category", categories=labels))
                return
            groups = {"": found}
        else:
            groups = self._groups(categories)
        lines = []
        for cats in groups.values():
            prompt = await self.db.pick(cats)
            if prompt is not None:
                lines.append(f"{f.bold(prompt.category.label)}: {f.escape(prompt.text or '')}")
        await message.reply_html("\n".join(lines) or f.escape(t("prompts.unavailable")))

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        f = self.bot.fmt
        command = (message.text or message.caption or "").split()[0].split("@")[0].lower()
        category = command.removeprefix("/image").removeprefix("_") or None
        if category is None and context.args and self.categories:
            category = context.args[0]  # v2 only had /image_<category>
        if self.db is not None:
            await self._image_db(message, category)
            return
        if self.store is None:
            await message.reply_text(t("prompts.images_unavailable"))
            return
        try:
            cat, num, image = self.store.random_image(category)
        except PromptsUnavailableError:
            await message.reply_text(t("prompts.images_unavailable"))
            return
        link = image.get("webContentLink") or image.get("webViewLink") or ""
        await message.reply_html(
            f"{f.escape(cat)} #{f.link(link, num) if link else num}",
            link_preview_options=LinkPreviewOptions(is_disabled=False, url=link, prefer_large_media=True)
            if link
            else None,
        )

    async def _image_db(self, message: Message, query: str | None) -> None:
        f = self.bot.fmt
        _, sets = await self.sets(message.chat_id)
        categories = await self.db.categories(sets, "image")
        if query:
            categories = (
                self.db.find(categories, query) or categories
            )  # an unknown category falls back to all, as in v2
        prompt = await self.db.pick(categories)
        if prompt is None:
            await message.reply_text(t("prompts.images_unavailable"))
            return
        label = f.escape(prompt.category.label)
        if prompt.file_id:
            await message.reply_photo(prompt.file_id, caption=f"{label} #{prompt.id}")
            return
        link = prompt.url or ""
        await message.reply_html(
            f"{label} #{f.link(link, prompt.id) if link else prompt.id}",
            link_preview_options=LinkPreviewOptions(is_disabled=False, url=link, prefer_large_media=True)
            if link
            else None,
        )

    # --- adding and suggesting ---------------------------------------------------------------------

    @staticmethod
    def _split(text: str) -> tuple[str, str] | None:
        parts = text.split(maxsplit=1)
        return (parts[0], parts[1]) if len(parts) == 2 else None

    async def _target_set(self, message: Message) -> int | None:
        """Where an admin's /addprompt goes: the chat's set, or (super-admins, private chat) the shared one."""
        if message.chat.type == ChatType.PRIVATE:
            return await self.db.shared() if message.from_user.id in self.bot.settings.super_admins else None
        if not await self.bot.is_chat_admin(message.chat, message.from_user.id):
            return None
        own, _ = await self.sets(message.chat_id)
        return own

    async def add_prompt(self, update: Update, _context) -> None:
        message = update.effective_message
        target = await self._target_set(message)
        if target is None:
            await self.bot.asks.respond(message, t("prompts.admins_only"), html=False, delete_command=True, ttl=60)
            return
        parsed = self._split(command_args_text(message))
        if parsed is None:
            await self.bot.asks.respond(message, self.bot.fmt.t("prompts.add_usage"), delete_command=True)
            return
        try:
            prompt = await self.db.add(target, parsed[0], text=parsed[1], author=(PLATFORM, str(message.from_user.id)))
        except ValueError:
            await self.bot.asks.respond(message, self.bot.fmt.t("prompts.add_usage"), delete_command=True)
            return
        await self.bot.asks.respond(
            message, self.bot.fmt.t("prompts.added", category=prompt.category.label), delete_command=True
        )

    async def suggest(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(t("prompts.suggest_in_chat"))
            return
        parsed = self._split(command_args_text(message))
        if parsed is None:
            await self.bot.asks.respond(message, self.bot.fmt.t("prompts.suggest_usage"), delete_command=True)
            return
        own, _ = await self.sets(message.chat_id)
        try:
            await self.db.add(
                own, parsed[0], text=parsed[1], status=SUGGESTED, author=(PLATFORM, str(message.from_user.id))
            )
        except ValueError:
            await self.bot.asks.respond(message, self.bot.fmt.t("prompts.suggest_usage"), delete_command=True)
            return
        await self.bot.asks.respond(message, self.bot.fmt.t("prompts.suggested"), delete_command=True)

    async def add_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/addimage <category> as a photo's caption or in reply to a photo (chat admins)."""
        message = update.effective_message
        target = await self._target_set(message)
        if target is None:
            await self.bot.asks.respond(message, t("prompts.admins_only"), html=False, delete_command=True, ttl=60)
            return
        photo_message = message if message.photo else message.reply_to_message
        category = command_args_text(message).split(maxsplit=1)
        if photo_message is None or not photo_message.photo or not category:
            await self.bot.asks.respond(message, self.bot.fmt.t("prompts.image_usage"))
            return
        photo = photo_message.photo[-1]
        file_id, url = photo.file_id, None
        google = self.bot.settings.common.google
        if google is not None and google.drive_folder_id:
            try:
                tg_file = await context.bot.get_file(photo.file_id)
                data = bytes(await tg_file.download_as_bytearray())
                folder = f"chat {message.chat_id}" if message.chat_id < 0 else "shared"
                _drive_id, url = await asyncio.to_thread(
                    upload_image, google, folder, data, f"{photo.file_unique_id}.jpg", "image/jpeg"
                )
                file_id = None
            except Exception as e:  # no Drive write access: keep the Telegram photo
                logger.info("Drive upload failed, keeping the Telegram file: %s", e)
        prompt = await self.db.add(
            target, category[0], file_id=file_id, url=url, kind="image", author=(PLATFORM, str(message.from_user.id))
        )
        await self.bot.asks.respond(message, self.bot.fmt.t("prompts.image_added", category=prompt.category.label))

    # --- reviewing ---------------------------------------------------------------------------------

    async def settings_notes(self, chat_id: int) -> list[str]:
        """A line on the /settings card when members' suggestions are waiting."""
        if self.db is None or chat_id > 0:
            return []
        own, _ = await self.sets(chat_id)
        waiting = len(await self.db.by_status(own, SUGGESTED))
        return [self.bot.fmt.t("prompts.waiting", n=waiting)] if waiting else []

    async def _next(self, set_id: int, status: str, owner: int, scope: str) -> tuple[str, InlineKeyboardMarkup | None]:
        f = self.bot.fmt
        pending = await self.db.by_status(set_id, status)
        if not pending:
            return f.t("prompts.review_empty"), None
        p = pending[0]
        body = (
            f.escape(p.text)
            if p.text
            else f.link(p.url, t("prompts.image_link"))
            if p.url
            else f.escape(t("prompts.image_link"))
        )
        text = f.t("prompts.review_item", left=len(pending), category=p.category.label) + "\n\n" + body
        rows = [
            [
                button(t("prompts.accept"), owner, f"pr:{scope}:{p.id}:ok"),
                button(t("prompts.reject"), owner, f"pr:{scope}:{p.id}:no"),
            ]
        ]
        if scope != "shared":  # a chat's suggestion can go on to the shared set in the same tap
            rows.append([button(t("prompts.accept_share"), owner, f"pr:{scope}:{p.id}:share")])
        rows.append([button(t("common.close"), owner, f"pr:{scope}:0:close")])
        return text, InlineKeyboardMarkup(rows)

    async def _similar(self, prompt, set_ids: list[int]) -> Category | None:
        """An existing category the suggestion's (new) category is probably meant to be: the same name in
        another case, or a likely typo of it."""
        active = await self.db.by_status(prompt.category.set_id, ACTIVE, prompt.category.kind)
        if any(p.category.id == prompt.category.id for p in active):
            return None  # the category already has prompts: it's an established one
        others = [c for c in await self.db.categories(set_ids, prompt.category.kind) if c.id != prompt.category.id]
        by_label = {c.label.casefold(): c for c in others}
        mine = prompt.category.label.casefold()
        if mine in by_label:
            return by_label[mine]
        close = difflib.get_close_matches(mine, list(by_label), n=1, cutoff=0.75)
        return by_label[close[0]] if close else None

    async def _into(self, own: int, prompt, category: Category) -> None:
        """Move the prompt into ``category`` (its namesake in the chat's own set, if it is a shared one)."""
        target = category if category.set_id == own else await self.db.category(own, category.label, category.kind)
        await self.db.move(prompt.id, target.id)

    async def suggestions(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type == ChatType.PRIVATE or not await self.bot.is_chat_admin(
            message.chat, message.from_user.id
        ):
            await self.bot.asks.respond(message, t("prompts.admins_only"), html=False, delete_command=True, ttl=60)
            return
        own, _ = await self.sets(message.chat_id)
        text, markup = await self._next(own, SUGGESTED, message.from_user.id, str(message.chat_id))
        await self.bot.asks.respond(message, text, markup, delete_command=True, ttl=10 * 60)

    async def review(self, update: Update, _context) -> None:
        message = update.effective_message
        if message.chat.type != ChatType.PRIVATE or message.from_user.id not in self.bot.settings.super_admins:
            return
        text, markup = await self._next(await self.db.shared(), SUBMITTED, message.from_user.id, "shared")
        await message.reply_html(text, reply_markup=markup)

    async def on_review_button(self, query, args: list[str]) -> None:
        sent = Sent.from_query(query)
        if len(args) < 3:
            await self.bot.asks.toast(query, "common.stale")
            return
        scope, prompt_id, action = args[0], int(args[1]), args[2]
        if action == "close":
            await query.answer()
            if sent:
                await self.bot.asks.delete(sent)
            return
        if scope == "shared":
            if query.from_user.id not in self.bot.settings.super_admins:
                await self.bot.asks.toast(query, "prompts.admins_only")
                return
            set_id, status = await self.db.shared(), SUBMITTED
        else:
            if not await self.bot.is_chat_admin(int(scope), query.from_user.id):
                await self.bot.asks.toast(query, "prompts.admins_only", alert=True)
                return
            set_id, status = (await self.sets(int(scope)))[0], SUGGESTED
        prompt = await self.db.get(prompt_id)
        if prompt is None or prompt.category.set_id != set_id or prompt.status != status:
            await self.bot.asks.toast(query, "common.stale")
            text, markup = await self._next(set_id, status, query.from_user.id, scope)
            with contextlib.suppress(TelegramError):
                await self.bot.asks.edit(sent, text, markup)
            return
        share = action in ("share", "to1", "keep1")
        if scope != "shared" and action in ("ok", "share"):
            similar = await self._similar(prompt, (await self.sets(int(scope)))[1])
            if similar is not None and similar.label.casefold() == prompt.category.label.casefold():
                await self._into(set_id, prompt, similar)  # only the case differs: the existing spelling
            elif similar is not None:
                await query.answer()
                flag = "1" if share else "0"
                owner = query.from_user.id
                markup = InlineKeyboardMarkup(
                    [
                        [button(t("prompts.into", category=similar.label), owner, f"pr:{scope}:{prompt.id}:to{flag}:{similar.id}")],
                        [button(t("prompts.keep_new", category=prompt.category.label), owner, f"pr:{scope}:{prompt.id}:keep{flag}")],
                    ]
                )  # fmt: skip
                question = self.bot.fmt.t("prompts.similar", new=prompt.category.label, existing=similar.label)
                with contextlib.suppress(TelegramError):
                    await self.bot.asks.edit(sent, question, markup)
                return
        elif action.startswith("to") and len(args) > 3:
            target = next((c for c in await self.db.categories((await self.sets(int(scope)))[1], prompt.category.kind)
                           if c.id == int(args[3])), None)  # fmt: skip
            if target is not None:
                await self._into(set_id, prompt, target)
        accepted = action != "no"
        await self.db.set_status(prompt_id, ACTIVE if accepted else REJECTED)
        if accepted and share and scope != "shared":
            await self.db.submit(await self.db.get(prompt_id), (PLATFORM, str(query.from_user.id)))
        key = "prompts.rejected" if not accepted else "prompts.accepted_shared" if share else "prompts.accepted"
        await self.bot.asks.toast(query, key)
        text, markup = await self._next(set_id, status, query.from_user.id, scope)
        with contextlib.suppress(TelegramError):
            await self.bot.asks.edit(sent, text, markup)

    # --- the web app ----------------------------------------------------------------------------------

    def web_routes(self, app) -> None:
        if self.db is None:
            return
        from aiohttp import web

        from ...web.server import ApiError, ok

        def prompt_json(p):
            return {"id": p.id, "category": p.category.label, "category_id": p.category.id, "kind": p.category.kind,
                    "text": p.text, "url": p.url, "photo": bool(p.file_id), "status": p.status}  # fmt: skip

        async def own_set(request, user) -> tuple[int, int]:
            chat_id = int(request.match_info["chat"])
            await app.require_chat_admin(user, chat_id)
            own, _ = await self.sets(chat_id)
            return chat_id, own

        async def listing(request, user):
            chat_id, own = await own_set(request, user)
            settings = self.bot.services.shared.extra.get("settings")
            use_shared = await settings.get_bool(PLATFORM, str(chat_id), "prompts.use_shared") if settings else True
            return web.json_response(
                {
                    "prompts": [prompt_json(p) for p in await self.db.by_status(own, ACTIVE)],
                    "suggestions": [prompt_json(p) for p in await self.db.by_status(own, SUGGESTED)],
                    "categories": [
                        {"id": c.id, "name": c.name, "alias": c.alias, "kind": c.kind}
                        for c in await self.db.categories([own], "text", with_prompts=False)
                    ],
                    "use_shared": use_shared,
                }
            )

        async def add(request, user):
            _chat_id, own = await own_set(request, user)
            body = await app.body(request)
            try:
                await self.db.add(
                    own,
                    str(body.get("category", "")),
                    text=str(body.get("text", "")),
                    author=(user.platform, user.user_id),
                )
            except ValueError:
                raise ApiError(400, "prompts.add_usage") from None
            return await listing(request, user)

        async def act(request, user):
            _chat_id, own = await own_set(request, user)
            prompt = await self.db.get(int(request.match_info["prompt"]))
            if prompt is None or prompt.category.set_id != own:
                raise ApiError(404, "web.error_bad_request")
            action = request.match_info["action"]
            if action == "accept" and prompt.status == SUGGESTED:
                await self.db.set_status(prompt.id, ACTIVE)
            elif action == "reject" and prompt.status == SUGGESTED:
                await self.db.set_status(prompt.id, REJECTED)
            elif action == "submit" and prompt.status == ACTIVE:
                await self.db.submit(prompt, (user.platform, user.user_id))
            elif action == "delete":
                await self.db.delete(prompt.id)
            else:
                raise ApiError(400, "web.error_bad_request")
            return ok()

        async def rename(request, user):
            _chat_id, own = await own_set(request, user)
            body = await app.body(request)
            cats = {c.id: c for c in await self.db.categories([own], "text", with_prompts=False)}
            category_id = int(request.match_info["category"])
            if category_id not in cats:
                raise ApiError(404, "web.error_bad_request")
            await self.db.rename(category_id, body.get("name") or None, body.get("alias"))
            return await listing(request, user)

        app.add("GET", r"/api/chat/{chat:-?\d+}/prompts", listing)
        app.add("POST", r"/api/chat/{chat:-?\d+}/prompts", add)
        app.add("POST", r"/api/chat/{chat:-?\d+}/prompts/{prompt:\d+}/{action:accept|reject|submit|delete}", act)
        app.add("PATCH", r"/api/chat/{chat:-?\d+}/categories/{category:\d+}", rename)

    def web_tabs(self) -> list[str]:
        return ["prompts"] if self.db is not None else []

    # --- stats and reload --------------------------------------------------------------------------

    async def stats(self, update: Update, _context) -> None:
        f = self.bot.fmt
        if self.db is not None:
            _, sets = await self.sets(update.effective_message.chat_id)
            stats = await self.db.stats(sets)
        else:
            stats = self.store.stats() if self.store is not None else {}
        text = "\n".join(f"{f.bold(k)}: {v}" for k, v in stats.items()) or f.escape(t("prompts.not_loaded"))
        await update.effective_message.reply_html(text)

    async def reload(self, update: Update, _context) -> None:
        message = update.effective_message
        if not await self.bot.is_chat_admin(message.chat, message.from_user.id):
            await message.reply_text(t("prompts.reload_admins"))
            return
        if self.store is None:
            await message.reply_text(t("prompts.unavailable"))
            return
        try:
            await self.store.load()
        except Exception as e:
            logger.exception("reload failed")
            await message.reply_text(t("prompts.reload_failed", error=type(e).__name__))
            return
        await message.reply_text(t("prompts.reloaded"))
