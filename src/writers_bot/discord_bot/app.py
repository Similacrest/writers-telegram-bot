"""Discord bot: the same prompts, word counter and sprints as Telegram, as slash commands and buttons.

Runs as its own process (``writers-bot-discord``) with ``DISCORD_TOKEN``. It needs no privileged
intents: reports come through ``/words`` or the report button's modal instead of plain replies.
"""

import contextlib
import logging
from datetime import datetime, timedelta, tzinfo

import discord
from discord import app_commands

from ..config import ConfigError, DiscordSettings
from ..core import sprint as sp
from ..core.models import ChatKey, Sprint, SprintStatus
from ..core.netguard import BlockedURLError, FetchError, SafeFetcher
from ..core.prompts import PromptsStore, PromptsUnavailableError
from ..core.render import report_text, sprint_text, text_stats_text, top_text, user_stats_text
from ..core.sources import UnsupportedLinkError, fetch_linked_text, find_url, source_locative
from ..core.storage import Storage
from ..core.wordcount import count_words, text_stats

logger = logging.getLogger(__name__)

PLATFORM = "discord"
LANG = "ua"
REPORT_HINT = "Натисніть «Звітувати слова» або надішліть /words 500 (чи посилання на текст)."
NO_PINGS = discord.AllowedMentions.none()
PING_USERS = discord.AllowedMentions(users=True, roles=False, everyone=False)


class MarkdownFormatter:
    def __init__(self, tz: tzinfo):
        self.tz = tz

    def escape(self, text: str) -> str:
        return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))

    def bold(self, text: str) -> str:
        return f"**{self.escape(text)}**"

    def code(self, text: str) -> str:
        return f"`{text.replace('`', 'ˋ')}`"

    def mention(self, user_id: str, name: str) -> str:
        return f"<@{int(user_id)}>"

    def time(self, when: datetime) -> str:
        return f"<t:{int(when.timestamp())}:t>"  # rendered in each reader's local time


def chat_key(channel_id: int) -> ChatKey:
    # Discord threads are channels with their own id, so every thread already gets its own sprint.
    return ChatKey(PLATFORM, str(channel_id), None)


def user_ref(user: discord.abc.User) -> sp.UserRef:
    return sp.UserRef(str(user.id), user.display_name)


class SprintButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sp:(?P<action>join|leave|report|repeat):(?P<a>\d+)(?::(?P<b>\d+))?",
):
    """Persistent button: works after restarts because its state lives in the custom id."""

    def __init__(
        self, action: str, a: int, b: int | None = None, *, label: str = "…", style=discord.ButtonStyle.secondary
    ):
        custom_id = f"sp:{action}:{a}" + (f":{b}" if b is not None else "")
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=custom_id))
        self.action, self.a, self.b = action, a, b

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["a"]), int(match["b"]) if match["b"] else None)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.writers.on_button(interaction, self.action, self.a, self.b)


def _view(*buttons: SprintButton) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for b in buttons:
        view.add_item(b)
    return view


def sprint_view(sprint: Sprint) -> discord.ui.View | None:
    if sprint.is_active:
        return _view(
            SprintButton("join", sprint.id, label="Долучитися", style=discord.ButtonStyle.success),
            SprintButton("leave", sprint.id, label="Вийти/скасувати", style=discord.ButtonStyle.danger),
        )
    if sprint.status == SprintStatus.CANCELLED:
        return _view(SprintButton("repeat", sprint.planned_duration, sprint.delay, label="Повторити"))
    return None


def report_view(sprint: Sprint) -> discord.ui.View:
    repeat = SprintButton("repeat", sprint.planned_duration, 0, label="🔁 Повторити зараз")
    if sprint.status == SprintStatus.REPORTING:
        return _view(
            SprintButton("report", sprint.id, label="📝 Звітувати слова", style=discord.ButtonStyle.primary), repeat
        )
    return _view(repeat)


class ReportModal(discord.ui.Modal, title="Звіт про слова"):
    value = discord.ui.TextInput(
        label="Скільки слів ви написали?", placeholder="500, =12500 або посилання на текст", max_length=500
    )

    def __init__(self, bot: "WritersDiscordBot", sprint_id: int):
        super().__init__()
        self.bot, self.sprint_id = bot, sprint_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_report(interaction, str(self.value), self.sprint_id)


class DiscordSprintPresenter:
    def __init__(self, client: discord.Client, fmt: MarkdownFormatter):
        self.client = client
        self.fmt = fmt

    async def _channel(self, sprint: Sprint):
        cid = int(sprint.key.chat_id)
        return self.client.get_channel(cid) or await self.client.fetch_channel(cid)

    async def _edit(self, sprint: Sprint, ref: str, content: str, view) -> None:
        mid = sprint.refs.get(ref)
        if not mid:
            return
        channel = await self._channel(sprint)
        try:
            await channel.get_partial_message(mid).edit(content=content, view=view, allowed_mentions=NO_PINGS)
        except discord.HTTPException as e:
            logger.warning("cannot edit sprint %s message %s: %s", sprint.id, mid, e)

    async def _pin(self, message, pin: bool) -> None:
        with contextlib.suppress(discord.HTTPException):
            await (message.pin() if pin else message.unpin())

    async def show(self, sprint: Sprint, event: sp.SprintEvent, origin=None) -> None:
        now = sp.utc_now()
        main = sprint_text(self.fmt, sprint, now)
        channel = await self._channel(sprint)
        E = sp.SprintEvent
        if event == E.CREATED:
            msg = await channel.send(main, view=sprint_view(sprint), allowed_mentions=NO_PINGS)
            sprint.refs["msg"] = msg.id
            await self._pin(msg, True)
        elif event == E.STARTED:
            old = sprint.refs.get("msg")
            msg = await channel.send(main, view=sprint_view(sprint), allowed_mentions=PING_USERS)
            sprint.refs["msg"] = msg.id
            if old:
                with contextlib.suppress(discord.HTTPException):
                    await channel.get_partial_message(old).delete()
            await self._pin(msg, True)
        elif event in (E.TICK, E.PARTICIPANTS_CHANGED):
            await self._edit(sprint, "msg", main, sprint_view(sprint))
        elif event == E.CANCELLED:
            await self._edit(sprint, "msg", main, sprint_view(sprint))
            if sprint.refs.get("msg"):
                await self._pin(channel.get_partial_message(sprint.refs["msg"]), False)
        elif event == E.FINISHED:
            await self._edit(sprint, "msg", main, None)
            if sprint.refs.get("msg"):
                await self._pin(channel.get_partial_message(sprint.refs["msg"]), False)
            msg = await channel.send(
                report_text(self.fmt, sprint, now, REPORT_HINT), view=report_view(sprint), allowed_mentions=PING_USERS
            )
            sprint.refs["finish"] = msg.id
        elif event in (E.REPORT_UPDATED, E.CLOSED):
            await self._edit(sprint, "finish", report_text(self.fmt, sprint, now, REPORT_HINT), report_view(sprint))


class WritersDiscordBot:
    def __init__(self, settings: DiscordSettings):
        self.settings = settings
        common = settings.common
        intents = discord.Intents.none()
        intents.guilds = True
        self.client = discord.Client(intents=intents, allowed_mentions=NO_PINGS)
        self.client.writers = self
        self.tree = app_commands.CommandTree(self.client)
        self.fmt = MarkdownFormatter(common.timezone)
        self.storage = Storage(common.db_path)
        self.prompts = PromptsStore(common.google)
        self.fetcher: SafeFetcher | None = None
        self.sprints = sp.SprintManager(
            self.storage,
            DiscordSprintPresenter(self.client, self.fmt),
            platform=PLATFORM,
            report_window=common.report_window,
        )
        self.client.setup_hook = self.setup_hook
        self._register_commands()

    async def setup_hook(self) -> None:
        self.fetcher = SafeFetcher()
        await self.storage.open()
        self.client.add_dynamic_items(SprintButton)
        await self.sprints.restore()
        if self.prompts.enabled:
            try:
                await self.prompts.load()
            except Exception:
                logger.exception("could not load prompts")
        for guild_id in self.settings.guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        if not self.settings.guild_ids:
            await self.tree.sync()

    async def close(self) -> None:
        await self.sprints.shutdown()
        if self.fetcher:
            await self.fetcher.aclose()
        await self.storage.close()

    # --- helpers ---------------------------------------------------------------------------------

    def allowed(self, interaction: discord.Interaction) -> bool:
        allowed = self.settings.allowed_guilds
        return allowed is None or (interaction.guild_id is not None and interaction.guild_id in allowed)

    @staticmethod
    def is_admin(interaction: discord.Interaction) -> bool:
        perms = interaction.permissions
        return bool(perms.manage_messages or perms.administrator)

    async def reply(self, interaction: discord.Interaction, text: str, *, ephemeral: bool = False) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS)

    async def count_link(self, url: str) -> int:
        return count_words((await fetch_linked_text(self.fetcher, url)).text)

    @staticmethod
    async def defer(interaction: discord.Interaction) -> None:
        """Discord fails an interaction not answered in 3 s; sprint actions do DB and API work first."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

    async def start_sprint(self, interaction: discord.Interaction, duration: int, delay: int) -> None:
        await self.defer(interaction)
        try:
            await self.sprints.start(chat_key(interaction.channel_id), user_ref(interaction.user), duration, delay)
        except sp.SprintAlreadyRunningError:
            await self.reply(interaction, "Спринт вже запущено!", ephemeral=True)
        except sp.InvalidSprintParamsError as e:
            await self.reply(interaction, str(e), ephemeral=True)
        else:
            await self.reply(interaction, "Спринт створено ✍️", ephemeral=True)

    async def on_button(self, interaction: discord.Interaction, action: str, a: int, b: int | None) -> None:
        if not self.allowed(interaction):
            await self.reply(interaction, "Бот недоступний на цьому сервері.", ephemeral=True)
            return
        key, user = chat_key(interaction.channel_id), user_ref(interaction.user)
        if action == "join":
            await self.defer(interaction)
            result, _ = await self.sprints.join(key, user, a)
            text = {
                sp.JoinResult.JOINED: "Додано до спринту!",
                sp.JoinResult.ALREADY: "Ви вже у спринті!",
                sp.JoinResult.CLOSED: "Цей спринт уже завершився.",
            }[result]
            await self.reply(interaction, text, ephemeral=True)
        elif action == "leave":

            async def admin() -> bool:
                return self.is_admin(interaction)

            await self.defer(interaction)
            result, _ = await self.sprints.leave_or_cancel(key, user, admin, a)
            text = {
                sp.LeaveResult.LEFT: "Вас вилучено зі спринту",
                sp.LeaveResult.CANCELLED: "Спринт скасовано",
                sp.LeaveResult.STOPPED: "Спринт зупинено. Можна звітувати про слова.",
                sp.LeaveResult.NOT_ALLOWED: "Ви не маєте права скасувати спринт",
                sp.LeaveResult.NOT_PARTICIPANT: "Ви не у спринті",
                sp.LeaveResult.CLOSED: "Цей спринт уже завершився.",
            }[result]
            await self.reply(interaction, text, ephemeral=True)
        elif action == "report":
            sprint = self.sprints.get(a)
            if sprint is None or sprint.status != SprintStatus.REPORTING:
                await self.reply(interaction, "Звіти для цього спринту вже закрито.", ephemeral=True)
                return
            await interaction.response.send_modal(ReportModal(self, a))
        elif action == "repeat":
            await self.start_sprint(interaction, a, b or 0)

    async def handle_report(self, interaction: discord.Interaction, text: str, sprint_id: int | None) -> None:
        report = sp.parse_report(text, find_url)
        if report is None:
            await self.reply(interaction, "Надішліть число слів (напр. 500) або посилання на текст.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outcome = await self.sprints.report(
                chat_key(interaction.channel_id), user_ref(interaction.user), report, self.count_link, sprint_id
            )
        except (UnsupportedLinkError, BlockedURLError, FetchError) as e:
            await self.reply(interaction, f"Не вдалося прочитати посилання: {e}", ephemeral=True)
            return
        S = sp.ReportStatus
        texts = {
            S.NO_SPRINT: "Немає спринту, до якого можна звітувати.",
            S.NEED_BASELINE: "Спершу задайте стартову кількість або надішліть просто число написаних слів.",
            S.TOO_EARLY: "Спринт ще триває — звітуйте після завершення. Стартову кількість можна задати як =12000.",
            S.TOO_MANY: "Це забагато слів для одного спринту 🙂",
        }
        if outcome.status in texts:
            text = texts[outcome.status]
        elif outcome.status == S.BASELINE:
            text = f"Стартова кількість: {outcome.participant.start_words} сл."
        else:
            text = f"Записано: {outcome.participant.words} сл." + (
                f" (у тексті {outcome.counted})" if outcome.counted is not None else ""
            )
        await self.reply(interaction, text, ephemeral=True)

    # --- commands --------------------------------------------------------------------------------

    def _register_commands(self) -> None:
        tree, bot = self.tree, self

        async def check(interaction: discord.Interaction) -> bool:
            if not bot.allowed(interaction):
                await bot.reply(interaction, "Бот недоступний на цьому сервері.", ephemeral=True)
                return False
            return True

        tree.interaction_check = check

        async def category_autocomplete(interaction, current: str):
            cats = bot.prompts.text_categories(LANG)
            return [app_commands.Choice(name=c, value=c) for c in cats if current.lower() in c.lower()][:25]

        async def image_autocomplete(interaction, current: str):
            cats = bot.prompts.image_categories()
            return [app_commands.Choice(name=c, value=c) for c in cats if current.lower() in c.lower()][:25]

        @tree.command(name="prompt", description="Випадковий текстовий промпт")
        @app_commands.describe(category="Категорія (необов'язково)")
        @app_commands.autocomplete(category=category_autocomplete)
        async def prompt(interaction: discord.Interaction, category: str | None = None):
            try:
                cat = None
                if category:
                    cat = bot.prompts.find_category(LANG, category)
                    if cat is None:
                        cats = ", ".join(bot.prompts.text_categories(LANG)) or "—"
                        await bot.reply(interaction, f"Немає такої категорії. Доступні: {cats}", ephemeral=True)
                        return
                result = bot.prompts.random_text(LANG, cat)
            except PromptsUnavailableError:
                await bot.reply(interaction, "Промпти зараз недоступні.", ephemeral=True)
                return
            await bot.reply(
                interaction, "\n".join(f"{bot.fmt.bold(k)}: {bot.fmt.escape(v)}" for k, v in result.items())
            )

        @tree.command(name="image", description="Випадковий промпт-зображення")
        @app_commands.autocomplete(category=image_autocomplete)
        async def image(interaction: discord.Interaction, category: str | None = None):
            try:
                cat, num, img = bot.prompts.random_image(category)
            except PromptsUnavailableError:
                await bot.reply(interaction, "Зображення зараз недоступні.", ephemeral=True)
                return
            link = img.get("webContentLink") or img.get("webViewLink") or ""
            await bot.reply(interaction, f"{bot.fmt.escape(cat)} #{num}\n{link}")

        @tree.command(name="wc", description="Порахувати слова в тексті чи за посиланням")
        @app_commands.describe(text="Текст або посилання (Телеграф, WriteFreely, Ellipsus)")
        async def wc(interaction: discord.Interaction, text: str):
            url = find_url(text)
            if url and count_words(text.replace(url, "")) < 20:
                await interaction.response.defer(thinking=True)
                try:
                    linked = await fetch_linked_text(bot.fetcher, url)
                except (UnsupportedLinkError, BlockedURLError, FetchError) as e:
                    await bot.reply(interaction, f"Не вдалося прочитати посилання: {e}")
                    return
                await bot.reply(
                    interaction, text_stats_text(bot.fmt, text_stats(linked.text), source_locative(linked.source))
                )
                return
            await bot.reply(interaction, text_stats_text(bot.fmt, text_stats(text)))

        @tree.command(name="sprint", description="Почати спринт")
        @app_commands.describe(duration="Тривалість, хвилин", delay="Затримка до початку, хвилин")
        async def sprint_cmd(
            interaction: discord.Interaction,
            duration: app_commands.Range[int, sp.MIN_SPRINT, sp.MAX_SPRINT] = sp.DEFAULT_SPRINT,
            delay: app_commands.Range[int, sp.MIN_SPRINT_DELAY, sp.MAX_SPRINT_DELAY] = sp.DEFAULT_SPRINT_DELAY,
        ):
            await bot.start_sprint(interaction, duration, delay)

        @tree.command(name="words", description="Звіт про слова: 500, =12500 або посилання")
        @app_commands.describe(
            value="Число написаних слів, =загальна кількість або посилання; порожньо — перерахувати"
        )
        async def words(interaction: discord.Interaction, value: str = ""):
            await bot.handle_report(interaction, value, None)

        @tree.command(name="mystats", description="Моя статистика спринтів")
        async def mystats(interaction: discord.Interaction):
            stats = await bot.storage.user_stats(PLATFORM, str(interaction.user.id))
            await bot.reply(
                interaction, user_stats_text(bot.fmt, interaction.user.display_name, stats), ephemeral=True
            )

        @tree.command(name="top", description="Топ за словами в цьому каналі")
        async def top(interaction: discord.Interaction, days: app_commands.Range[int, 1, 366] = 30):
            entries = await bot.storage.top(PLATFORM, str(interaction.channel_id), sp.utc_now() - timedelta(days=days))
            await bot.reply(interaction, top_text(bot.fmt, entries, days))

        @tree.command(name="reload", description="Перезавантажити промпти (адміни)")
        async def reload(interaction: discord.Interaction):
            if not bot.is_admin(interaction):
                await bot.reply(interaction, "Перезавантажувати можуть лише адміни.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await bot.prompts.load()
            except Exception as e:
                logger.exception("reload failed")
                await bot.reply(interaction, f"Не вдалося перезавантажити: {type(e).__name__}", ephemeral=True)
                return
            await bot.reply(interaction, "Перезавантажено!", ephemeral=True)

    def run(self) -> None:
        original_close = self.client.close

        async def close():
            await self.close()
            await original_close()

        self.client.close = close
        self.client.run(self.settings.token, log_handler=None)


def main() -> None:
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    try:
        settings = DiscordSettings.from_env()
    except ConfigError as e:
        raise SystemExit(f"config error: {e}") from None
    WritersDiscordBot(settings).run()


if __name__ == "__main__":
    main()
