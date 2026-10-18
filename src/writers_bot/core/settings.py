"""Chat settings, changed by chat admins (``/settings`` and the web app's Chat tab).

A chat's value wins; otherwise its community's; otherwise the default. Settings belong to modules and a bot
only offers those of its modules (and of the options it has on: no works switch in a bot without works).
"""

from dataclasses import dataclass
from typing import Any

from .communities import Communities
from .models import ChatKey, Unit
from .reporting import ChatOptions
from .sprint import MAX_SPRINT, MIN_SPRINT
from .storage import Storage

LEGACY_UNIT = "sprint_unit"  # v3.2 /sprintmode


@dataclass(frozen=True, slots=True)
class SettingDef:
    key: str  # "<module>.<name>"; the locale has settings.<module>_<name> (label) and …_help
    kind: str  # bool | int | enum | text
    default: Any
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    needs_option: str | None = None  # a module option that must be on (e.g. sprints works)
    max_length: int = 2000

    @property
    def module(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def locale(self) -> str:
        return "chatsettings." + self.key.replace(".", "_")

    def parse(self, value: Any) -> Any:
        """A stored or submitted value as its type; raises ValueError."""
        if value is None:
            return self.default
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("1", "true", "on", "yes"):
                return True
            if text in ("0", "false", "off", "no"):
                return False
            raise ValueError(self.key)
        if self.kind == "int":
            number = int(value)
            if (self.minimum is not None and number < self.minimum) or (
                self.maximum is not None and number > self.maximum
            ):
                raise ValueError(self.key)
            return number
        if self.kind == "enum":
            if str(value) not in self.choices:
                raise ValueError(self.key)
            return str(value)
        text = str(value)
        if len(text) > self.max_length:
            raise ValueError(self.key)
        return text

    def dump(self, value: Any) -> str:
        if self.kind == "bool":
            return "1" if value else "0"
        return str(value)


SETTINGS: list[SettingDef] = [
    SettingDef("sprints.default_length", "int", 30, minimum=MIN_SPRINT, maximum=MAX_SPRINT),
    SettingDef("sprints.works", "bool", True, needs_option="works"),
    SettingDef("sprints.percent", "bool", True, needs_option="percent"),
    SettingDef("sprints.default_unit", "enum", "words", choices=("words", "percent"), needs_option="percent"),
    SettingDef("sprints.hidden_ok", "bool", True, needs_option="reports"),
    SettingDef("sprints.work_names", "bool", True, needs_option="works"),
    SettingDef("sprints.top", "bool", True, needs_option="reports"),
    SettingDef("sprints.delete_reports", "bool", True, needs_option="reports"),
    SettingDef("greeter.text", "text", ""),
    SettingDef("replies.defaults", "bool", True),
    SettingDef("prompts.use_shared", "bool", True, needs_option="db"),
    SettingDef("achievements.enabled", "bool", True),
]
BY_KEY = {s.key: s for s in SETTINGS}


class SettingsStore:
    def __init__(self, storage: Storage, communities: Communities, modules: dict[str, dict]):
        self.storage = storage
        self.communities = communities
        self.modules = modules  # the storage bot's modules and their options

    def defs(self) -> list[SettingDef]:
        out = []
        for s in SETTINGS:
            if s.module not in self.modules:
                continue
            options = self.modules[s.module]
            if s.needs_option == "db":
                if options.get("source") != "db":
                    continue
            elif s.needs_option and not options.get(s.needs_option):
                continue
            out.append(s)
        return out

    def known(self, key: str) -> SettingDef:
        definition = next((s for s in self.defs() if s.key == key), None)
        if definition is None:
            raise KeyError(key)
        return definition

    async def resolve(self, platform: str, chat_id: str) -> dict[str, tuple[Any, str]]:
        """Every setting of this bot for the chat: (value, where it comes from: chat|community|default)."""
        chat = await self.storage.chat_settings(platform, chat_id)
        community = await self.communities.of_chat(platform, chat_id)
        shared = await self.communities.settings(community.id) if community else {}
        if LEGACY_UNIT in chat and "sprints.default_unit" not in chat:
            chat["sprints.default_unit"] = chat[LEGACY_UNIT]
        out: dict[str, tuple[Any, str]] = {}
        for s in self.defs():
            for source, values in (("chat", chat), ("community", shared)):
                if s.key in values:
                    try:
                        out[s.key] = (s.parse(values[s.key]), source)
                        break
                    except ValueError:
                        continue
            else:
                out[s.key] = (s.default, "default")
        return out

    async def get(self, platform: str, chat_id: str, key: str) -> Any:
        values = await self.resolve(platform, chat_id)
        if key in values:
            return values[key][0]
        definition = BY_KEY.get(key)
        return definition.default if definition else None

    async def get_bool(self, platform: str, chat_id: str, key: str) -> bool:
        return bool(await self.get(platform, chat_id, key))

    async def get_chat(self, platform: str, chat_id: str, key: str) -> Any:
        return await self.get(platform, chat_id, key)

    async def set(self, platform: str, chat_id: str, key: str, value: Any) -> Any:
        """Set (or with None reset to the inherited value) a chat's setting; returns the new value."""
        definition = self.known(key)
        if value is None:
            await self.storage.set_chat_setting(platform, chat_id, key, None)
            if key == "sprints.default_unit":
                await self.storage.set_chat_setting(platform, chat_id, LEGACY_UNIT, None)
        else:
            parsed = definition.parse(value)
            await self.storage.set_chat_setting(platform, chat_id, key, definition.dump(parsed))
        return await self.get(platform, chat_id, key)

    async def set_community(self, community_id: int, key: str, value: Any) -> None:
        definition = self.known(key)
        await self.communities.set_setting(
            community_id, key, None if value is None else definition.dump(definition.parse(value))
        )

    # --- what modules ask ----------------------------------------------------------------------------

    async def sprint_options(self, key: ChatKey, *, works: bool, percent: bool) -> ChatOptions:
        values = await self.resolve(key.platform, key.chat_id)

        def value(name, default):
            return values.get(name, (default, ""))[0]

        works_on = works and value("sprints.works", True)
        percent_on = percent and value("sprints.percent", True)
        unit = Unit(value("sprints.default_unit", "words")) if percent_on else Unit.WORDS
        return ChatOptions(
            works=works_on,
            percent=percent_on,
            default_unit=unit,
            hidden_ok=value("sprints.hidden_ok", True),
            work_names=value("sprints.work_names", True),
        )

    async def community_chats(self, platform: str, chat_id: str) -> list[str]:
        return await self.communities.chats_with(platform, chat_id)
