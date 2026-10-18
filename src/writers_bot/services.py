"""Shared services, started only when some bot in this process needs them.

At most one bot has storage (see :mod:`.botconfig`); what builds on it (people, works, settings,
communities, the web app) exists only in that bot. Link counting is shared by every bot that needs it.
"""

import logging
from dataclasses import dataclass, field

from .botconfig import BotConfig, ProcessConfig
from .core.communities import Communities
from .core.links import LinkCounter
from .core.people import People
from .core.prompts import PromptsStore
from .core.settings import SettingsStore
from .core.storage import Storage
from .core.works import Works
from .modules import schemas

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Services:
    config: ProcessConfig
    storage: Storage | None = None
    people: People | None = None
    works: Works | None = None
    links: LinkCounter | None = None
    web: object | None = None  # writers_bot.web.server.WebApp
    sheet: PromptsStore | None = None  # the Google Sheet: prompts, help text, v2 config texts
    extra: dict = field(default_factory=dict)  # services added by later phases (settings, communities)

    @classmethod
    def build(cls, config: ProcessConfig) -> "Services":
        services = cls(config)
        storage_bot = config.storage_bot
        if storage_bot is not None:
            services.storage = Storage(config.common.db_path, schemas(storage_bot.modules))
            services.people = People(services.storage)
            services.works = Works(services.storage)
            communities = Communities(services.storage)
            services.extra["communities"] = communities
            services.extra["settings"] = SettingsStore(services.storage, communities, storage_bot.modules)
        if any("links" in b.needs for b in config.bots.values()):
            services.links = LinkCounter()
        if config.common.google is not None and any(_uses_sheet(b) for b in config.bots.values()):
            services.sheet = PromptsStore(config.common.google)
        return services

    def for_bot(self, bot: BotConfig) -> "BotServices":
        has_storage = (
            self.storage is not None
            and self.config.storage_bot is not None
            and self.config.storage_bot.name == bot.name
        )
        return BotServices(
            storage=self.storage if has_storage else None,
            people=self.people if has_storage else None,
            works=self.works if has_storage else None,
            links=self.links if "links" in bot.needs else None,
            web=self.web if has_storage else None,
            sheet=self.sheet,
            shared=self,
        )

    async def start(self) -> None:
        if self.storage is not None:
            await self.storage.open()
        if self.links is not None:
            await self.links.start()
        if self.sheet is not None:
            try:
                await self.sheet.load()
            except Exception:
                logger.exception("could not load the Google Sheet; prompts are unavailable until /reload")

    async def stop(self) -> None:
        if self.links is not None:
            await self.links.close()
        if self.storage is not None:
            await self.storage.close()


@dataclass(slots=True)
class BotServices:
    """What one bot gets."""

    storage: Storage | None
    people: People | None
    works: Works | None
    links: LinkCounter | None
    web: object | None
    sheet: PromptsStore | None
    shared: Services


def _uses_sheet(bot: BotConfig) -> bool:
    return (
        (bot.has("prompts") and bot.options("prompts").get("source") == "sheet")
        or bot.options("greeter").get("from_sheet", False)
        or bot.options("replies").get("legacy", False)
    )
