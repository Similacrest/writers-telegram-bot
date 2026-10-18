"""Discord target of a bot: slash commands and buttons for the modules that work there (prompts,
wordcount, sprints, smash). The full Discord pass (works, Ukrainian command names, achievements, linked
channels) is phase 6; until then this follows the shared core and the redesigned sprint flow.

It needs no privileged intents: reports come through the Report button's modal (a field per work) or
``/progress``, never by reading messages.
"""

import asyncio
import contextlib
import io
import logging
import time
from datetime import timedelta

import discord
from discord import app_commands

from ..botconfig import BotConfig
from ..core import sprint as sp
from ..core.board import board_text, confirmation, minutes_top_text, sprint_message, sprint_when
from ..core.days import bot_day
from ..core.export import export_person
from ..core.fmt import MarkdownFormatter
from ..core.i18n import t
from ..core.links import LinkError
from ..core.models import ChatKey, Outcome, Sprint, SprintStatus, Unit
from ..core.prompts import PromptsUnavailableError
from ..core.render import sprint_text, text_stats_text
from ..core.reporting import ChatOptions, Reporting, Status
from ..core.reportparse import parse
from ..core.sources import find_url, source_locative
from ..core.stats import history, top_minutes
from ..core.wordcount import count_words, text_stats
from ..services import BotServices

logger = logging.getLogger(__name__)

PLATFORM = "discord"
LANG = "ua"
NO_PINGS = discord.AllowedMentions.none()
PING_USERS = discord.AllowedMentions(users=True, roles=False, everyone=False)


def chat_key(channel_id: int) -> ChatKey:
    # Discord threads are channels with their own id, so every thread already gets its own sprint.
    return ChatKey(PLATFORM, str(channel_id), None)


def user_ref(user: discord.abc.User) -> sp.UserRef:
    return sp.UserRef(str(user.id), user.display_name)


class SprintButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sp:(?P<action>join|leave|report|repeat|done|fail):(?P<a>\d+)(?::(?P<b>\d+))?",
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


class ReportModal(discord.ui.Modal):
    """A field per work in the sprint (a Discord modal holds up to five, as many as a person's works)."""

    def __init__(self, bot: "DiscordBot", sprint: Sprint, labels: list[tuple[int | None, str]]):
        super().__init__(title=t("discord.report_title"))
        self.bot, self.sprint = bot, sprint
        self.fields: list[tuple[int | None, discord.ui.TextInput]] = []
        for work_id, label in labels[:5]:
            field = discord.ui.TextInput(
                label=label[:45], placeholder=t("discord.report_placeholder"), required=False, max_length=200
            )
            self.add_item(field)
            self.fields.append((work_id, field))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.submit_report(interaction, self.sprint, [(w, str(f.value)) for w, f in self.fields])


class DiscordSprintPresenter:
    def __init__(self, client: discord.Client, fmt: MarkdownFormatter, reports: bool, now):
        self.client = client
        self.fmt = fmt
        self.reports = reports
        self.now = now

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

    def main_view(self, sprint: Sprint) -> discord.ui.View | None:
        if sprint.is_active:
            return _view(
                SprintButton("join", sprint.id, label=t("sprint.button_join"), style=discord.ButtonStyle.success),
                SprintButton("leave", sprint.id, label=t("sprint.button_leave"), style=discord.ButtonStyle.danger),
            )
        if sprint.status == SprintStatus.CANCELLED:
            return _view(
                SprintButton(
                    "repeat", sprint.planned_duration, sprint.delay, label=t("sprint.button_repeat_cancelled")
                )
            )
        return None

    def board_view(self, sprint: Sprint) -> discord.ui.View:
        repeat = SprintButton("repeat", sprint.planned_duration, 0, label=t("sprint.button_repeat"))
        if sprint.status == SprintStatus.REPORTING and self.reports:
            return _view(
                SprintButton("report", sprint.id, label=t("sprint.button_report"), style=discord.ButtonStyle.primary),
                SprintButton("done", sprint.id, label=t("sprint.button_done")),
                SprintButton("fail", sprint.id, label=t("sprint.button_failed")),
                repeat,
            )
        return _view(repeat)

    def main(self, sprint: Sprint) -> str:
        if self.reports:
            return sprint_message(self.fmt, sprint, self.now(), hint="sprint.hint_running_discord")
        return sprint_text(self.fmt, sprint, self.now())

    def board(self, sprint: Sprint) -> str:
        return board_text(self.fmt, sprint, self.now(), mention=True, hint="board.hint_discord")

    async def show(self, sprint: Sprint, event: sp.SprintEvent, origin=None) -> None:
        channel = await self._channel(sprint)
        E = sp.SprintEvent
        if event == E.CREATED:
            msg = await channel.send(self.main(sprint), view=self.main_view(sprint), allowed_mentions=NO_PINGS)
            sprint.refs["msg"] = msg.id
            await self._pin(msg, True)
        elif event == E.STARTED:
            old = sprint.refs.get("msg")
            msg = await channel.send(self.main(sprint), view=self.main_view(sprint), allowed_mentions=PING_USERS)
            sprint.refs["msg"] = msg.id
            if old:
                with contextlib.suppress(discord.HTTPException):
                    await channel.get_partial_message(old).delete()
            await self._pin(msg, True)
        elif event in (E.TICK, E.PARTICIPANTS_CHANGED, E.CANCELLED):
            await self._edit(sprint, "msg", self.main(sprint), self.main_view(sprint))
            if event == E.CANCELLED and sprint.refs.get("msg"):
                await self._pin(channel.get_partial_message(sprint.refs["msg"]), False)
        elif event == E.FINISHED:
            await self._edit(sprint, "msg", self.main(sprint), None)
            if sprint.refs.get("msg"):
                await self._pin(channel.get_partial_message(sprint.refs["msg"]), False)
            if not self.reports:
                if not sprint.stopped_early:
                    msg = await channel.send(
                        self.main(sprint), view=self.board_view(sprint), allowed_mentions=NO_PINGS
                    )
                    sprint.refs["finish"] = msg.id
                return
            msg = await channel.send(self.board(sprint), view=self.board_view(sprint), allowed_mentions=PING_USERS)
            sprint.refs["finish"] = msg.id
        elif event in (E.REPORT_UPDATED, E.CLOSED):
            await self._edit(sprint, "finish", self.board(sprint), self.board_view(sprint))


class DiscordBot:
    def __init__(self, config: BotConfig, services: BotServices):
        self.config = config
        self.settings = config.discord
        self.services = services
        self.storage = services.storage
        self.people = services.people
        intents = discord.Intents.none()
        intents.guilds = True
        self.client = discord.Client(intents=intents, allowed_mentions=NO_PINGS)
        self.client.writers = self
        self.tree = app_commands.CommandTree(self.client)
        self.fmt = MarkdownFormatter(self.settings.common.timezone)
        options = config.options("sprints")
        self.reports = config.has("sprints") and bool(options.get("reports"))
        self.works_on = self.reports and bool(options.get("works"))
        self.percent_on = self.reports and bool(options.get("percent"))
        self.engine = None
        self.rep = None
        if config.has("sprints") and self.storage is not None:
            self.engine = sp.SprintManager(
                self.storage,
                DiscordSprintPresenter(self.client, self.fmt, self.reports, sp.utc_now),
                platform=PLATFORM,
                report_window=sp.LATE_REPORTS if self.reports else self.settings.common.report_window,
                reports=self.reports,
            )
            self.rep = Reporting(
                self.storage,
                self.people,
                services.works,
                self.engine,
                services.links if self.works_on else None,
                works_enabled=self.works_on,
            )
            if self.reports:
                self.engine.after_finish = self.rep.after_finish
        self.client.setup_hook = self.setup_hook
        self.started = time.monotonic()
        self._register_commands()

    async def setup_hook(self) -> None:
        self.client.add_dynamic_items(SprintButton)
        if self.engine is not None:
            await self.engine.restore()
        for guild_id in self.settings.guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        if not self.settings.guild_ids:
            await self.tree.sync()

    async def start(self) -> None:
        await self.client.login(self.settings.token)
        self._task = asyncio.create_task(self.client.connect(), name=f"discord-{self.config.name}")

    async def stop(self) -> None:
        if self.engine is not None:
            await self.engine.shutdown()
        await self.client.close()

    def health(self) -> dict:
        return {"ok": self.client.is_ready() and not self.client.is_closed(), "last_update_s": None}

    # --- helpers ---------------------------------------------------------------------------------

    def allowed(self, interaction: discord.Interaction) -> bool:
        allowed = self.settings.allowed_guilds
        return allowed is None or (interaction.guild_id is not None and interaction.guild_id in allowed)

    @staticmethod
    def is_admin(interaction: discord.Interaction) -> bool:
        perms = interaction.permissions
        return bool(perms.manage_messages or perms.administrator)

    async def reply(self, interaction: discord.Interaction, text: str, *, ephemeral: bool = False, **kwargs) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS, **kwargs)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS, **kwargs)

    @staticmethod
    async def defer(interaction: discord.Interaction) -> None:
        """Discord fails an interaction not answered in 3 s; sprint actions do DB and API work first."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

    async def chat_options(self, key: ChatKey) -> ChatOptions:
        settings = self.services.shared.extra.get("settings")
        if settings is not None:
            return await settings.sprint_options(key, works=self.works_on, percent=self.percent_on)
        unit = await self.storage.sprint_unit(PLATFORM, key.chat_id) if self.percent_on else Unit.WORDS
        return ChatOptions(works=self.works_on, percent=self.percent_on, default_unit=unit)

    # --- sprints ---------------------------------------------------------------------------------

    async def start_sprint(self, interaction: discord.Interaction, duration: int, delay: int) -> None:
        await self.defer(interaction)
        key, user = chat_key(interaction.channel_id), user_ref(interaction.user)
        kwargs = {}
        if self.reports:
            kwargs = {"prepare": self.rep.preparer(user), "refs": {"opts": (await self.chat_options(key)).to_refs()}}
        try:
            await self.engine.start(key, user, duration, delay, **kwargs)
        except sp.SprintAlreadyRunningError:
            await self.reply(interaction, t("sprint.already_running"), ephemeral=True)
            return
        except sp.InvalidSprintParamsError as e:
            await self.reply(interaction, str(e), ephemeral=True)
            return
        await self.reply(interaction, t("discord.sprint_created"), ephemeral=True)

    async def on_button(self, interaction: discord.Interaction, action: str, a: int, b: int | None) -> None:
        if not self.allowed(interaction) or self.engine is None:
            await self.reply(interaction, t("discord.not_here"), ephemeral=True)
            return
        key, user = chat_key(interaction.channel_id), user_ref(interaction.user)
        if action == "join":
            await self.defer(interaction)
            prepare = self.rep.preparer(user) if self.reports else None
            result, _ = await self.engine.join(key, user, a, prepare=prepare)
            keys = {
                sp.JoinResult.JOINED: "sprint.joined",
                sp.JoinResult.ALREADY: "sprint.already_in",
                sp.JoinResult.CLOSED: "sprint.over",
            }
            await self.reply(interaction, t(keys[result]), ephemeral=True)
        elif action == "leave":

            async def admin() -> bool:
                return self.is_admin(interaction)

            await self.defer(interaction)
            result, _ = await self.engine.leave_or_cancel(key, user, admin, a)
            keys = {
                sp.LeaveResult.LEFT: "sprint.left",
                sp.LeaveResult.CANCELLED: "sprint.cancelled",
                sp.LeaveResult.STOPPED: "sprint.stopped_reports" if self.reports else "sprint.stopped",
                sp.LeaveResult.NOT_ALLOWED: "sprint.not_allowed",
                sp.LeaveResult.NOT_PARTICIPANT: "sprint.not_in",
                sp.LeaveResult.CLOSED: "sprint.over",
            }
            await self.reply(interaction, t(keys[result]), ephemeral=True)
        elif action == "report":
            sprint = self.engine.get(a)
            if sprint is None or sprint.status != SprintStatus.REPORTING:
                await self.reply(interaction, t("sprint.reports_closed"), ephemeral=True)
                return
            p = sprint.participants.get(str(interaction.user.id))
            if p is None:
                await self.reply(interaction, t("sprint.not_in"), ephemeral=True)
                return
            labels = [(w.work_id, w.label or t("discord.report_label")) for w in p.works.values()] or [
                (None, t("discord.report_label"))
            ]
            await interaction.response.send_modal(ReportModal(self, sprint, labels))
        elif action in ("done", "fail"):
            sprint = self.engine.get(a)
            if sprint is None:
                await self.reply(interaction, t("sprint.reports_closed"), ephemeral=True)
                return
            outcome = Outcome.DONE if action == "done" else Outcome.FAILED
            result = await self.rep.set_outcome(sprint, user, outcome)
            key = "report.done_toast" if outcome == Outcome.DONE else "report.failed_toast"
            await self.reply(interaction, t(key) if result.ok else t("sprint.not_in"), ephemeral=True)
        elif action == "repeat":
            await self.start_sprint(interaction, a, b or 0)

    async def submit_report(
        self, interaction: discord.Interaction, sprint: Sprint, values: list[tuple[int | None, str]]
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = user_ref(interaction.user)
        texts = []
        for work_id, value in values:
            value = value.strip()
            if not value:
                continue
            parsed = parse(value, find_url, today=bot_day(sp.utc_now()), allow_day=False)
            if parsed is None:
                texts.append(t("report.nothing"))
                continue
            choice = {i: work_id for i in range(len(parsed.items))} if work_id is not None else None
            result = await self.rep.report(sprint, user, parsed, choice)
            if result.status == Status.AMBIGUOUS:
                texts.append(t("discord.ambiguous"))
            elif result.ok:
                texts.append(confirmation(self.fmt, result.changes, result.outcome))
            else:
                texts.append(t("report.nothing") if result.status == Status.NOTHING else t("discord.report_error"))
        await self.reply(interaction, "\n".join(texts) or t("report.nothing"), ephemeral=True)

    # --- commands --------------------------------------------------------------------------------

    def _register_commands(self) -> None:
        tree, bot = self.tree, self

        async def check(interaction: discord.Interaction) -> bool:
            if not bot.allowed(interaction):
                await bot.reply(interaction, t("discord.not_here"), ephemeral=True)
                return False
            return True

        tree.interaction_check = check
        sheet = self.services.sheet

        if self.config.has("prompts"):

            async def category_autocomplete(interaction, current: str):
                cats = sheet.text_categories(LANG) if sheet else []
                return [app_commands.Choice(name=c, value=c) for c in cats if current.lower() in c.lower()][:25]

            async def image_autocomplete(interaction, current: str):
                cats = sheet.image_categories() if sheet else []
                return [app_commands.Choice(name=c, value=c) for c in cats if current.lower() in c.lower()][:25]

            @tree.command(name="prompt", description=t("commands.prompt"))
            @app_commands.autocomplete(category=category_autocomplete)
            async def prompt(interaction: discord.Interaction, category: str | None = None):
                try:
                    if sheet is None:
                        raise PromptsUnavailableError
                    cat = None
                    if category:
                        cat = sheet.find_category(LANG, category)
                        if cat is None:
                            cats = ", ".join(sheet.text_categories(LANG)) or "—"
                            await bot.reply(interaction, t("prompts.no_category", categories=cats), ephemeral=True)
                            return
                    result = sheet.random_text(LANG, cat)
                except PromptsUnavailableError:
                    await bot.reply(interaction, t("prompts.unavailable"), ephemeral=True)
                    return
                await bot.reply(
                    interaction, "\n".join(f"{bot.fmt.bold(k)}: {bot.fmt.escape(v)}" for k, v in result.items())
                )

            @tree.command(name="image", description=t("commands.image"))
            @app_commands.autocomplete(category=image_autocomplete)
            async def image(interaction: discord.Interaction, category: str | None = None):
                try:
                    if sheet is None:
                        raise PromptsUnavailableError
                    cat, num, img = sheet.random_image(category)
                except PromptsUnavailableError:
                    await bot.reply(interaction, t("prompts.images_unavailable"), ephemeral=True)
                    return
                link = img.get("webContentLink") or img.get("webViewLink") or ""
                await bot.reply(interaction, f"{bot.fmt.escape(cat)} #{num}\n{link}")

        if self.config.has("wordcount"):

            @tree.command(name="count", description=t("commands.count"))
            async def count(interaction: discord.Interaction, text: str):
                url = find_url(text)
                links = bot.services.links
                if url and links is not None and count_words(text.replace(url, "")) < 20:
                    await interaction.response.defer(thinking=True)
                    try:
                        linked = await links.fetch(url)
                    except LinkError as e:
                        await bot.reply(interaction, t("common.link_error", error=e))
                        return
                    await bot.reply(
                        interaction, text_stats_text(bot.fmt, text_stats(linked.text), source_locative(linked.source))
                    )
                    return
                await bot.reply(interaction, text_stats_text(bot.fmt, text_stats(text)))

        if self.config.has("smash"):
            from ..core import extras

            @tree.command(name="smash", description=t("commands.smash"))
            async def smash(interaction: discord.Interaction):
                await bot.reply(interaction, bot.fmt.code(extras.smash()))

        if self.engine is None:
            return

        @tree.command(name="sprint", description=t("commands.sprint"))
        async def sprint_cmd(
            interaction: discord.Interaction,
            duration: app_commands.Range[int, sp.MIN_SPRINT, sp.MAX_SPRINT] = sp.DEFAULT_SPRINT,
            delay: app_commands.Range[int, sp.MIN_SPRINT_DELAY, sp.MAX_SPRINT_DELAY] = sp.DEFAULT_SPRINT_DELAY,
        ):
            await bot.start_sprint(interaction, duration, delay)

        if not self.reports:
            return

        @tree.command(name="progress", description=t("commands.progress"))
        async def progress(interaction: discord.Interaction, value: str = ""):
            await bot.defer(interaction)
            user = user_ref(interaction.user)
            if not value.strip():
                person = await bot.people.person_id(PLATFORM, user.user_id, user.name)
                h = await history(bot.storage, person)
                today = bot_day(sp.utc_now())
                text = bot.fmt.t(
                    "progress.summary",
                    today=bot.fmt.plural(h.words(today, today), "words"),
                    week=bot.fmt.plural(h.words(today - timedelta(days=today.weekday()), today), "words"),
                    month=bot.fmt.plural(h.words(today.replace(day=1), today), "words"),
                )
                await bot.reply(interaction, text, ephemeral=True)
                return
            parsed = parse(value, find_url, today=bot_day(sp.utc_now()), allow_day=True)
            if parsed is None:
                await bot.reply(interaction, t("progress.usage"), ephemeral=True)
                return
            sprint = None if parsed.day else bot.engine.current_for(user.user_id, chat_key(interaction.channel_id))
            if sprint is not None:
                result = await bot.rep.report(sprint, user, parsed)
            else:
                result = await bot.rep.progress(
                    user, parsed, chat_id=str(interaction.channel_id), percent=bot.percent_on
                )
            if result.ok:
                corrected = (
                    sprint_when(bot.fmt, result.corrected, bot.settings.common.timezone, sp.utc_now())
                    if result.corrected
                    else None
                )
                text = confirmation(bot.fmt, result.changes, result.outcome, corrected=corrected)
                await bot.reply(interaction, text, ephemeral=True)
            elif result.status == Status.AMBIGUOUS:
                await bot.reply(interaction, t("discord.ambiguous"), ephemeral=True)
            else:
                await bot.reply(interaction, t("discord.report_error"), ephemeral=True)

        @tree.command(name="top", description=t("commands.top"))
        async def top(interaction: discord.Interaction, days: app_commands.Range[int, 1, 366] = 30):
            entries = await top_minutes(
                bot.storage, PLATFORM, [str(interaction.channel_id)], sp.utc_now() - timedelta(days=days)
            )
            await bot.reply(interaction, minutes_top_text(bot.fmt, entries, days))

        @tree.command(name="export", description=t("commands.export"))
        async def export_cmd(interaction: discord.Interaction):
            person = await bot.people.find(PLATFORM, str(interaction.user.id))
            if person is None:
                await bot.reply(interaction, t("data.empty"), ephemeral=True)
                return
            data, count = await export_person(bot.storage, bot.people, bot.services.works, person)
            if not count:
                await bot.reply(interaction, t("data.empty"), ephemeral=True)
                return
            await interaction.response.send_message(
                t("data.caption", count=count),
                file=discord.File(io.BytesIO(data), "my-writing-data.zip"),
                ephemeral=True,
            )

        @tree.command(name="forgetme", description=t("commands.forgetme"))
        async def forgetme_cmd(interaction: discord.Interaction, confirm: str = ""):
            if confirm.strip().casefold() not in {w.casefold() for w in t("discord.confirm_words").split()}:
                await bot.reply(interaction, t("discord.forget_confirm"), ephemeral=True)
                return
            removed = await bot.people.forget_account(PLATFORM, str(interaction.user.id))
            await bot.reply(interaction, t("data.forgotten", count=removed), ephemeral=True)
