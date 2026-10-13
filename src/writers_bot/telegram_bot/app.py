"""Telegram bot: commands, callbacks and application setup."""

import contextlib
import logging
import random
import re
from datetime import timedelta
from functools import wraps

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    Chat,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReactionTypeEmoji,
    Update,
)
from telegram.constants import ChatAction, ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)

from ..config import ConfigError, TelegramSettings
from ..core import extras
from ..core import sprint as sp
from ..core.models import ChatKey, Sprint, SprintStatus
from ..core.netguard import BlockedURLError, FetchError, SafeFetcher
from ..core.prompts import PromptsStore, PromptsUnavailableError
from ..core.render import text_stats_text, top_text, user_stats_text
from ..core.sources import UnsupportedLinkError, fetch_linked_text, find_url, source_locative
from ..core.storage import Storage
from ..core.wordcount import count_words, text_stats
from .admin import AdminTools
from .presenter import HtmlFormatter, TelegramSprintPresenter

logger = logging.getLogger(__name__)

PLATFORM = "telegram"
LANG = "ua"
LINK_WITH_SHORT_TEXT = 20  # a message that is mostly a link is counted by fetching the link

DEFAULT_HELP = """<b>Бот для письменників</b>

/prompt — випадковий текстовий промпт (/prompt &lt;категорія&gt; — з однієї категорії)
/image — випадковий промпт-зображення (/image &lt;категорія&gt;)
/wc — порахувати слова (у відповідь на повідомлення, текст після команди, або посилання на Телеграф, WriteFreely чи Ellipsus)
/sprint [хвилин] [затримка] — спринт, напр. /sprint 30 2
/sprinthelp — як працюють спринти та звіти про слова
/d20, /2d6+1, /r 4к6 — кинути кубики"""

SPRINT_HELP = f"""<b>Спринти</b>
/sprint [хвилин] [затримка] [посилання] запускає спринт: {sp.MIN_SPRINT}–{sp.MAX_SPRINT} хв., затримка {sp.MIN_SPRINT_DELAY}–{sp.MAX_SPRINT_DELAY} хв., типово {sp.DEFAULT_SPRINT} і {sp.DEFAULT_SPRINT_DELAY}.
Під спринтом є кнопки, щоб долучитися чи вийти. Останній учасник або адмін чату може його скасувати.
У форумах кожна тема має свій спринт.

<b>Звіти про слова</b>
Якщо дати посилання на текст до чи під час спринту (/words &lt;посилання&gt; або одразу в /sprint), бот запам'ятає, скільки там слів, і після спринту сам порахує, скільки додалося.

Інакше після спринту відповідайте на повідомлення з результатами або надішліть:
/words 500, якщо написали 500 слів;
/words &lt;посилання&gt;, щоб бот порахував слова в тексті;
/words =12500, загальну кількість слів у рукописі (якщо до спринту задали стартову, напр. /words =12000).

/mystats показує вашу статистику в цьому чаті, /top [днів] — хто найбільше написав за період (типово 30 днів)."""

COMMANDS = [
    BotCommand("prompt", "Випадковий текстовий промпт"),
    BotCommand("image", "Випадковий промпт-зображення"),
    BotCommand("wc", "Порахувати слова в тексті чи за посиланням"),
    BotCommand("sprint", "Почати спринт: /sprint 30 2"),
    BotCommand("words", "Звіт про слова за спринт"),
    BotCommand("mystats", "Моя статистика спринтів"),
    BotCommand("top", "Топ за словами"),
    BotCommand("sprinthelp", "Як працюють спринти"),
    BotCommand("help", "Допомога"),
]

ADMIN_COMMANDS = [
    *COMMANDS,
    BotCommand("chats", "Адмін: усі чати з ботом"),
    BotCommand("broadcast", "Адмін: розсилка (groups / private / all)"),
    BotCommand("bcexclude", "Адмін: прибрати чати з розсилок"),
    BotCommand("bcinclude", "Адмін: повернути чати до розсилок"),
    BotCommand("stats", "Адмін: кількість промптів"),
    BotCommand("reload", "Адмін: перезавантажити промпти"),
]

LEGACY_REPEAT_RE = re.compile(r"^repeat_last_sprint_(\d+)(?:_(\d+))?$")


def chat_key(message: Message) -> ChatKey:
    # message_thread_id is also set for reply threads in ordinary groups, so only topics count (issue #3).
    thread = message.message_thread_id if getattr(message, "is_topic_message", False) else None
    # InaccessibleMessage (buttons on messages older than ~48h) has .chat but no .chat_id.
    return ChatKey(PLATFORM, str(message.chat.id), str(thread) if thread else None)


def user_ref(user) -> sp.UserRef:
    return sp.UserRef(str(user.id), user.full_name or user.username or str(user.id))


def command_args_text(message: Message) -> str:
    """Text after the leading /command (works for captions too)."""
    text = message.text or message.caption or ""
    entities = message.entities if message.text else message.caption_entities
    for entity in entities or ():
        if entity.type == MessageEntity.BOT_COMMAND and entity.offset == 0:
            # Entity offsets are in UTF-16 code units.
            encoded = text.encode("utf-16-le")
            return encoded[2 * entity.length :].decode("utf-16-le").strip()
    return text.strip()


def allowed_chat(show_error: bool = False):
    def decorator(method):
        @wraps(method)
        async def wrapper(self: "WritersTelegramBot", update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat, user = update.effective_chat, update.effective_user
            if chat is not None and not self.is_allowed(chat, user):
                if show_error and update.effective_message:
                    await update.effective_message.reply_text(
                        "Цей бот доступний лише для деяких чатів. Зверніться до автора боту."
                    )
                elif update.callback_query:
                    await update.callback_query.answer()
                return None
            return await method(self, update, context)

        return wrapper

    return decorator


class WritersTelegramBot:
    def __init__(self, settings: TelegramSettings):
        self.settings = settings
        common = settings.common
        self.fmt = HtmlFormatter(common.timezone)
        self.storage = Storage(common.db_path)
        self.prompts = PromptsStore(common.google)
        self.fetcher: SafeFetcher | None = None
        self.presenter = TelegramSprintPresenter(self.fmt)
        self.sprints = sp.SprintManager(
            self.storage,
            self.presenter,
            platform=PLATFORM,
            report_window=common.report_window,
            count_link=self.count_link,
        )
        self.me = None
        self.admin = AdminTools(self.storage, settings.super_admins)

    # --- lifecycle -------------------------------------------------------------------------------

    async def post_init(self, app: Application) -> None:
        self.presenter.bot = app.bot
        self.me = await app.bot.get_me()
        self.fetcher = SafeFetcher()
        await self.storage.open()
        restored = await self.sprints.restore()
        if restored:
            logger.info("restored %d open sprints", restored)
        try:
            await app.bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
            await app.bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllGroupChats())
        except TelegramError as e:
            logger.warning("set_my_commands failed: %s", e)
        for admin_id in self.settings.super_admins:
            try:
                await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id))
            except TelegramError as e:  # the admin has not started a chat with the bot yet
                logger.info("no admin command menu for %s: %s", admin_id, e)
        if self.prompts.enabled:
            try:
                await self.prompts.load()
            except Exception:
                logger.exception("could not load prompts; /prompt and /image are unavailable until /reload")

    async def post_shutdown(self, _app: Application) -> None:
        await self.admin.shutdown()
        await self.sprints.shutdown()
        if self.fetcher:
            await self.fetcher.aclose()
        await self.storage.close()

    # --- permissions -----------------------------------------------------------------------------

    def is_allowed(self, chat: Chat, user) -> bool:
        allowed = self.settings.allowed_chats
        if allowed is None or chat.id in allowed:
            return True
        if chat.type != ChatType.PRIVATE:
            return False
        return self.settings.allow_private or (user is not None and user.id in self.settings.super_admins)

    async def is_chat_admin(self, chat: Chat, user_id: int) -> bool:
        if user_id in self.settings.super_admins:
            return True
        if chat.type == ChatType.PRIVATE:
            return False
        try:
            member = await chat.get_member(user_id)
        except TelegramError:
            return False
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)

    # --- help & prompts --------------------------------------------------------------------------

    async def _reply_html_safe(self, message: Message, text: str, **kwargs) -> None:
        try:
            await message.reply_html(text, **kwargs)
        except BadRequest as e:
            if "parse" not in str(e).lower():
                raise
            await message.reply_text(re.sub(r"<[^>]+>", "", text), parse_mode=None, **kwargs)

    @allowed_chat(show_error=True)
    async def start(self, update: Update, _context) -> None:
        text = self.prompts.config_text("help_message") or DEFAULT_HELP
        await self._reply_html_safe(update.effective_message, "Я переродився, Райтенчіле!\n\n" + text)

    async def help(self, update: Update, _context) -> None:
        text = self.prompts.config_text("help_message") or DEFAULT_HELP
        await self._reply_html_safe(update.effective_message, text)

    async def sprint_help(self, update: Update, _context) -> None:
        await update.effective_message.reply_html(SPRINT_HELP)

    @allowed_chat()
    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        query = " ".join(context.args or [])
        try:
            category = None
            if query:
                category = self.prompts.find_category(LANG, query)
                if category is None:
                    cats = ", ".join(self.prompts.text_categories(LANG)) or "—"
                    await message.reply_html(f"Немає такої категорії. Доступні: {self.fmt.escape(cats)}")
                    return
            prompt = self.prompts.random_text(LANG, category)
        except PromptsUnavailableError:
            await message.reply_text("Промпти зараз недоступні.")
            return
        await message.reply_html("\n".join(f"{self.fmt.bold(k)}: {self.fmt.escape(v)}" for k, v in prompt.items()))

    @allowed_chat()
    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        command = (message.text or message.caption or "").split()[0].split("@")[0].lower()
        category = command.removeprefix("/image").removeprefix("_") or (context.args[0] if context.args else None)
        try:
            cat, num, image = self.prompts.random_image(category)
        except PromptsUnavailableError:
            await message.reply_text("Зображення зараз недоступні.")
            return
        link = image.get("webContentLink") or image.get("webViewLink") or ""
        await message.reply_html(
            f'{self.fmt.escape(cat)} #<a href="{self.fmt.escape(link)}">{num}</a>',
            link_preview_options=LinkPreviewOptions(url=link, prefer_large_media=True) if link else None,
        )

    @allowed_chat()
    async def stats(self, update: Update, _context) -> None:
        stats = self.prompts.stats()
        text = "\n".join(f"{self.fmt.bold(k)}: {v}" for k, v in stats.items()) or "Промпти не завантажено."
        await update.effective_message.reply_html(text)

    @allowed_chat(show_error=True)
    async def reload(self, update: Update, _context) -> None:
        message = update.effective_message
        if not await self.is_chat_admin(message.chat, message.from_user.id):
            await message.reply_text("Перезавантажувати можуть лише адміни.")
            return
        try:
            await self.prompts.load()
        except Exception as e:
            logger.exception("reload failed")
            await message.reply_text(f"Не вдалося перезавантажити: {type(e).__name__}")
            return
        await message.reply_text("Перезавантажено!")

    async def debuginfo(self, update: Update, _context) -> None:
        message = update.effective_message
        chat, user = message.chat, message.from_user
        lines = [f"Чат id: {chat.id} ({'авторизований' if self.is_allowed(chat, user) else 'не авторизований'})"]
        if message.is_topic_message:
            lines.append(f"Тема id: {message.message_thread_id}")
        lines.append(f"Ваш юзер id: {user.id}{await self._roles(chat, user.id)}")
        replied = message.reply_to_message
        if replied and replied.from_user:
            other = replied.from_user
            marks = " (цей бот)" if other.id == self.me.id else " (інший бот)" if other.is_bot else ""
            lines.append(f"Відповідаєте юзеру з id: {other.id}{marks}{await self._roles(chat, other.id)}")
        await message.reply_text("\n".join(lines), parse_mode=None)

    async def _roles(self, chat: Chat, user_id: int) -> str:
        roles = ""
        if user_id in self.settings.super_admins:
            roles += " (адмін боту)"
        if (
            chat.type != ChatType.PRIVATE
            and await self.is_chat_admin(chat, user_id)
            and user_id not in self.settings.super_admins
        ):
            roles += " (адмін чату)"
        return roles

    # --- extras from the v2 "extended" branch ------------------------------------------------------

    @allowed_chat()
    async def imafan(self, update: Update, _context) -> None:
        texts = list(self.prompts.config_texts("imafan_message").values())
        if not texts:
            return
        message = update.effective_message
        name = self.fmt.bold(message.from_user.first_name)
        await self._reply_html_safe(message, random.choice(texts).replace("{name}", name))

    @allowed_chat()
    async def smash(self, update: Update, _context) -> None:
        await update.effective_message.reply_html(self.fmt.code(extras.smash()))

    @allowed_chat()
    async def other_command(self, update: Update, _context) -> None:
        """Commands no other handler took: dice rolls like /d20, /2d6+1, /r 4к6."""
        message = update.effective_message
        roll = extras.parse_roll(message.text or message.caption or "")
        if roll is not None:
            await message.reply_html(extras.roll_html(roll))

    @allowed_chat()
    async def on_text(self, update: Update, _context) -> None:
        if not self.settings.slogan_replies:
            return
        message = update.effective_message
        if reply := extras.slogan_reply(message.text or message.caption or ""):
            await message.reply_html(reply)

    @allowed_chat()
    async def welcome(self, update: Update, _context) -> None:
        """Greets new members in chats that have their own ``welcome_message`` row in the config sheet."""
        message = update.effective_message
        text = self.prompts.config_texts("welcome_message").get(str(message.chat_id))
        people = [u for u in message.new_chat_members or () if not u.is_bot]
        if not text or not people:
            return
        names = ", ".join(f"<b>{u.mention_html()}</b>" for u in people)
        await self._reply_html_safe(message, text.replace("{name}", names))

    # --- word count ------------------------------------------------------------------------------

    async def count_link(self, url: str) -> int:
        linked = await fetch_linked_text(self.fetcher, url)
        return count_words(linked.text)

    @allowed_chat()
    async def wordcount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        own = command_args_text(message)
        replied = message.reply_to_message
        text = own or ((replied.text or replied.caption or "") if replied else "")
        if not text:
            await message.reply_text("Надішліть /wc у відповідь на повідомлення, або /wc <текст чи посилання>.")
            return
        url = find_url(text)
        if url and count_words(text.replace(url, "")) < LINK_WITH_SHORT_TEXT:
            await context.bot.send_chat_action(
                message.chat_id,
                ChatAction.TYPING,
                message_thread_id=message.message_thread_id if message.is_topic_message else None,
            )
            try:
                linked = await fetch_linked_text(self.fetcher, url)
            except (UnsupportedLinkError, BlockedURLError, FetchError) as e:
                await message.reply_text(f"Не вдалося прочитати посилання: {e}", parse_mode=None)
                return
            await message.reply_html(
                text_stats_text(self.fmt, text_stats(linked.text), source_locative(linked.source))
            )
            return
        await message.reply_html(text_stats_text(self.fmt, text_stats(text)))

    # --- sprints ---------------------------------------------------------------------------------

    @staticmethod
    def _int_arg(args: list[str], index: int, default: int) -> int:
        try:
            return int(args[index])
        except (IndexError, ValueError):
            return default

    async def _start_sprint(self, message: Message, user, duration: int, delay: int) -> str | None:
        """Start a sprint; returns an error text or None."""
        try:
            await self.sprints.start(chat_key(message), user_ref(user), duration, delay, origin=message)
        except sp.SprintAlreadyRunningError:
            return "Спринт вже запущено."
        except sp.InvalidSprintParamsError as e:
            return str(e)
        return None

    @allowed_chat()
    async def sprint(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sprint [minutes] [delay] [link]: a link is counted now and again when the sprint ends."""
        message = update.effective_message
        url = find_url(command_args_text(message))
        args = [a for a in context.args or [] if a != url]
        duration = self._int_arg(args, 0, sp.DEFAULT_SPRINT)
        delay = self._int_arg(args, 1, sp.DEFAULT_SPRINT_DELAY)
        if error := await self._start_sprint(message, message.from_user, duration, delay):
            await message.reply_text(error)
            return
        if url:
            await self._handle_report(message, url, None, explicit=True)

    def _sprint_from_callback(self, data: str) -> int | None:
        try:
            return int(data.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None

    @allowed_chat()
    async def on_join(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint_id = self._sprint_from_callback(query.data) if query.data.startswith("sp:") else None
        result, _ = await self.sprints.join(chat_key(query.message), user_ref(query.from_user), sprint_id)
        await query.answer(
            {
                sp.JoinResult.JOINED: "Додано до спринту!",
                sp.JoinResult.ALREADY: "Ви вже у спринті!",
                sp.JoinResult.CLOSED: "Цей спринт уже завершився.",
            }[result]
        )

    @allowed_chat()
    async def on_leave(self, update: Update, _context) -> None:
        query = update.callback_query
        message = query.message
        sprint_id = self._sprint_from_callback(query.data) if query.data.startswith("sp:") else None
        result, _ = await self.sprints.leave_or_cancel(
            chat_key(message),
            user_ref(query.from_user),
            is_admin=lambda: self.is_chat_admin(message.chat, query.from_user.id),
            sprint_id=sprint_id,
        )
        await query.answer(
            {
                sp.LeaveResult.LEFT: "Вас вилучено зі спринту",
                sp.LeaveResult.CANCELLED: "Спринт скасовано",
                sp.LeaveResult.STOPPED: "Спринт зупинено. Можна звітувати про слова.",
                sp.LeaveResult.NOT_ALLOWED: "Ви не маєте права скасувати спринт",
                sp.LeaveResult.NOT_PARTICIPANT: "Ви не у спринті",
                sp.LeaveResult.CLOSED: "Цей спринт уже завершився.",
            }[result]
        )

    @allowed_chat()
    async def on_repeat(self, update: Update, _context) -> None:
        query = update.callback_query
        if query.data.startswith("sp:repeat:"):
            parts = query.data.split(":")
            duration, delay = int(parts[2]), int(parts[3])
        else:
            m = LEGACY_REPEAT_RE.match(query.data)
            duration = int(m[1]) if m else sp.DEFAULT_SPRINT
            delay = int(m[2]) if m and m[2] else sp.DEFAULT_SPRINT_DELAY
        error = await self._start_sprint(query.message, query.from_user, duration, delay)
        await query.answer(error or "")

    @allowed_chat()
    async def on_report_button(self, update: Update, _context) -> None:
        query = update.callback_query
        sprint = self.sprints.get(self._sprint_from_callback(query.data) or -1)
        if sprint is None or sprint.status != SprintStatus.REPORTING:
            await query.answer("Звіти для цього спринту вже закрито.")
            return
        await query.answer()
        user = query.from_user
        prompt_id = await self.presenter.ask_for_report(sprint, user.id, user.full_name)
        await self.sprints.update_refs(
            sprint, lambda refs: refs.setdefault("prompts", {}).update({str(prompt_id): str(user.id)})
        )

    def _sprint_for_reply(self, message: Message) -> Sprint | None:
        replied = message.reply_to_message
        if replied is None or replied.from_user is None or replied.from_user.id != self.me.id:
            return None
        rid = replied.message_id
        return self.sprints.find(
            lambda s: (
                s.key.chat_id == str(message.chat_id)
                and (s.refs.get("finish") == rid or s.refs.get("msg") == rid or str(rid) in s.refs.get("prompts", {}))
            )
        )

    @allowed_chat()
    async def words(self, update: Update, _context) -> None:
        message = update.effective_message
        target = self._sprint_for_reply(message)
        await self._handle_report(message, command_args_text(message), target.id if target else None, explicit=True)

    @allowed_chat()
    async def on_reply(self, update: Update, _context) -> None:
        """Plain replies to sprint messages are word reports."""
        message = update.effective_message
        target = self._sprint_for_reply(message)
        if target is None:
            return
        rid = message.reply_to_message.message_id
        explicit = str(rid) in target.refs.get("prompts", {})
        await self._handle_report(message, message.text or "", target.id, explicit=explicit)
        if explicit:
            with contextlib.suppress(TelegramError):
                await message.reply_to_message.delete()
            await self.sprints.update_refs(target, lambda refs: refs.get("prompts", {}).pop(str(rid), None))

    async def _handle_report(self, message: Message, text: str, sprint_id: int | None, *, explicit: bool) -> None:
        report = sp.parse_report(text, find_url)
        if report is None:
            if explicit:
                await message.reply_text("Надішліть число слів (напр. 500) або посилання на текст.")
            return
        if report.kind == sp.ReportKind.RECOUNT and not explicit:
            return
        try:
            outcome = await self.sprints.report(
                chat_key(message), user_ref(message.from_user), report, self.count_link, sprint_id
            )
        except (UnsupportedLinkError, BlockedURLError, FetchError) as e:
            await message.reply_text(f"Не вдалося прочитати посилання: {e}", parse_mode=None)
            return
        await self._report_feedback(message, outcome)

    async def _report_feedback(self, message: Message, outcome: sp.ReportOutcome) -> None:
        status = outcome.status
        texts = {
            sp.ReportStatus.NO_SPRINT: "Немає спринту, до якого можна звітувати. Звіти приймаються після спринту.",
            sp.ReportStatus.NEED_BASELINE: "Спершу задайте стартову кількість (до чи під час спринту) або надішліть просто число написаних слів.",
            sp.ReportStatus.TOO_EARLY: "Спринт ще триває — звітуйте після завершення. Стартову кількість можна задати як /words =12000.",
            sp.ReportStatus.TOO_MANY: "Це забагато слів для одного спринту.",
        }
        if status in texts:
            await message.reply_text(texts[status])
            return
        if outcome.counted is not None:
            prefix = "Стартова кількість" if status == sp.ReportStatus.BASELINE else "У тексті"
            extra = f", записано {outcome.participant.words}" if status == sp.ReportStatus.REPORTED else ""
            await message.reply_text(f"{prefix}: {outcome.counted} сл.{extra}")
            return
        try:
            await message.set_reaction(ReactionTypeEmoji("👍"))
        except TelegramError:
            await message.reply_text("Записано!")

    @allowed_chat()
    async def mystats(self, update: Update, _context) -> None:
        message = update.effective_message
        chat_id = None if message.chat.type == ChatType.PRIVATE else str(message.chat_id)
        stats = await self.storage.user_stats(PLATFORM, str(message.from_user.id), chat_id)
        await message.reply_html(user_stats_text(self.fmt, message.from_user.full_name, stats))

    @allowed_chat()
    async def top(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        days = min(366, max(1, self._int_arg(context.args or [], 0, 30)))
        entries = await self.storage.top(PLATFORM, str(message.chat_id), sp.utc_now() - timedelta(days=days))
        await message.reply_html(top_text(self.fmt, entries, days))

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("error while handling %r", update, exc_info=context.error)

    # --- wiring ----------------------------------------------------------------------------------

    def register(self, app: Application) -> None:
        image_cmd = filters.Regex(r"^/image(?:_[\w]+)?(?:@\w+)?(?:\s|$)")
        handlers = [
            CommandHandler("start", self.start),
            CommandHandler("help", self.help),
            CommandHandler("sprinthelp", self.sprint_help),
            CommandHandler(["prompt", "prompt_ua"], self.prompt),
            MessageHandler(filters.COMMAND & image_cmd, self.image),
            CommandHandler("wc", self.wordcount),
            CommandHandler("stats", self.stats),
            CommandHandler("reload", self.reload),
            CommandHandler("debuginfo", self.debuginfo),
            CommandHandler("sprint", self.sprint),
            CommandHandler("words", self.words),
            CommandHandler("mystats", self.mystats),
            CommandHandler("top", self.top),
            CommandHandler("imafan", self.imafan),
            CommandHandler("smash", self.smash),
            CallbackQueryHandler(self.on_join, pattern=r"^(sp:join:\d+|join_sprint)$"),
            CallbackQueryHandler(self.on_leave, pattern=r"^(sp:leave:\d+|leave_or_cancel_sprint)$"),
            CallbackQueryHandler(self.on_report_button, pattern=r"^sp:report:\d+$"),
            CallbackQueryHandler(self.on_repeat, pattern=r"^(sp:repeat:\d+:\d+|repeat_last_sprint_\d+(_\d+)?)$"),
            MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, self.on_reply),
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome),
        ]
        for handler in handlers:
            app.add_handler(handler)
        # Slogan replies in their own group, so a reply that is also a word report gets both.
        app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, self.on_text), group=2)
        # Chat registry: runs before everything else and never stops other handlers.
        app.add_handler(TypeHandler(Update, self.admin.track), group=-1)
        app.add_handler(ChatMemberHandler(self.admin.on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER), group=1)
        app.add_handler(CommandHandler("chats", self.admin.chats))
        app.add_handler(CommandHandler("broadcast", self.admin.broadcast))
        app.add_handler(CommandHandler("bcexclude", self.admin.exclude))
        app.add_handler(CommandHandler("bcinclude", self.admin.include))
        app.add_handler(CallbackQueryHandler(self.admin.on_broadcast_button, pattern=r"^bc:(go|no):[0-9a-f]+$"))
        # Last in group 0: only commands that no handler above took (dice rolls).
        app.add_handler(MessageHandler(filters.COMMAND, self.other_command))
        app.add_error_handler(self.on_error)


def build_application(settings: TelegramSettings) -> Application:
    bot = WritersTelegramBot(settings)
    app = (
        Application.builder()
        .token(settings.token)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .defaults(
            Defaults(
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                tzinfo=settings.common.timezone,
            )
        )
        .post_init(bot.post_init)
        .post_shutdown(bot.post_shutdown)
        .build()
    )
    bot.register(app)
    app.bot_data["writers_bot"] = bot
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = TelegramSettings.from_env()
    except ConfigError as e:
        raise SystemExit(f"config error: {e}") from None
    app = build_application(settings)
    if settings.polling:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        path = settings.webhook_path
        app.run_webhook(
            listen=settings.webhook_listen,
            port=settings.webhook_port,
            url_path=path,
            webhook_url=f"https://{settings.webhook_host}/{path}",
            secret_token=settings.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
        )


if __name__ == "__main__":
    main()
