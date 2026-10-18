"""Which bots run in this process, with which modules and on which platforms.

From a TOML file (``BOT_CONFIG`` or ``--config``)::

    [common]
    db = "data/bot.sqlite3"

    [web]                      # optional: the web app (Mini App and website)
    url = "https://writers.example.org"
    listen = "0.0.0.0:8081"
    secret = "env:WEB_SECRET"

    [bots.main]
    modules = ["prompts", "wordcount", "sprints", "achievements", "dice"]
    sprints = { reports = true, works = true }
    telegram = { token = "env:TG_MAIN", super_admins = [123] }
    discord = { token = "env:DISCORD_MAIN" }

    [bots.greeter]
    modules = ["greeter"]
    telegram = { token = "env:TG_GREETER" }

``"env:NAME"`` takes a value from the environment. Without a file the environment alone describes one bot,
``main``, with the v2 modules (``EXTRAS`` adds the extended-branch ones, ``PREVIEW_FEATURES`` the
redesigned sprints), so existing deployments keep working.
"""

import os
import tomllib
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from .config import CommonSettings, ConfigError, DiscordSettings, TelegramSettings, _ids
from .modules import MODULES, ModuleSpec


@dataclass(frozen=True, slots=True)
class WebConfig:
    url: str  # public https base URL; Mini App methods only work on this domain
    host: str = "127.0.0.1"
    port: int = 8081
    secret: str = ""  # signs sessions and launch tokens
    discord_client_id: str | None = None
    discord_client_secret: str | None = None

    @property
    def domain(self) -> str:
        return self.url.split("://", 1)[-1].split("/", 1)[0]


@dataclass(frozen=True, slots=True)
class BotConfig:
    name: str
    modules: dict[str, dict]  # module name -> options
    telegram: TelegramSettings | None = None
    discord: DiscordSettings | None = None
    admin: bool = True  # chat list and broadcasts for super-admins (needs storage)
    chat_settings: bool = True  # /settings for chat admins (needs storage)

    def has(self, module: str) -> bool:
        return module in self.modules

    def options(self, module: str) -> dict:
        return self.modules.get(module, {})

    def specs(self) -> list[ModuleSpec]:
        return [MODULES[m] for m in self.modules]

    @property
    def needs(self) -> set[str]:
        result: set[str] = set()
        for spec in self.specs():
            result |= spec.needs(self.options(spec.name))
        return result

    @property
    def storage(self) -> bool:
        return "storage" in self.needs


@dataclass(frozen=True, slots=True)
class ProcessConfig:
    bots: dict[str, BotConfig]
    common: CommonSettings
    web: WebConfig | None = None
    source: str = "environment"

    @property
    def storage_bot(self) -> BotConfig | None:
        return next((b for b in self.bots.values() if b.storage), None)

    def only(self, name: str | None, platform: str | None = None) -> "ProcessConfig":
        """Keep one bot (``--bot``) and/or one platform (the legacy per-platform entry points)."""
        bots = dict(self.bots)
        if name is not None:
            if name not in bots:
                raise ConfigError(f"no bot named {name!r} (have: {', '.join(bots)})")
            bots = {name: bots[name]}
        if platform == "telegram":
            bots = {k: replace(b, discord=None) for k, b in bots.items() if b.telegram}
        elif platform == "discord":
            bots = {k: replace(b, telegram=None) for k, b in bots.items() if b.discord}
        if not bots:
            raise ConfigError(f"no bot with a {platform} target")
        return replace(self, bots=bots)


def _resolve(value):
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        resolved = os.environ.get(name, "").strip()
        if not resolved:
            raise ConfigError(f"environment variable {name} is not set")
        return resolved
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


def _check_modules(name: str, modules: dict[str, dict]) -> None:
    unknown = [m for m in modules if m not in MODULES]
    if unknown:
        raise ConfigError(f"bot {name}: unknown modules {', '.join(unknown)} (known: {', '.join(MODULES)})")
    for module, options in modules.items():
        spec = MODULES[module]
        bad = set(options) - set(spec.defaults)
        if bad:
            raise ConfigError(f"bot {name}: unknown {module} options {', '.join(sorted(bad))}")
    dice = modules.get("dice", {})
    if dice.get("randomness", "pseudo") not in ("pseudo", "random.org"):
        raise ConfigError(f'bot {name}: dice randomness must be "pseudo" or "random.org"')
    if dice.get("randomness") == "random.org" and not dice.get("random_org_key"):
        raise ConfigError(f'bot {name}: dice randomness = "random.org" needs random_org_key (a random.org API key)')


def _validate(config: ProcessConfig) -> ProcessConfig:
    with_storage = [b.name for b in config.bots.values() if b.storage]
    if len(with_storage) > 1:
        raise ConfigError(
            f"only one bot may have storage, but {', '.join(with_storage)} all have modules that need it"
        )
    for bot in config.bots.values():
        if bot.telegram is None and bot.discord is None:
            raise ConfigError(f"bot {bot.name} has neither a telegram nor a discord target")
        for spec in bot.specs():
            if "discord" not in spec.platforms and bot.discord is not None and bot.telegram is None:
                raise ConfigError(f"bot {bot.name}: module {spec.name} doesn't work on Discord")
    return config


# --- from the environment (no config file) ----------------------------------------------------------


def _dice_packs() -> list[str]:
    """DICE_PACKS: the two dice sticker packs (names or t.me/addstickers links), comma-separated."""
    return [p.strip() for p in os.environ.get("DICE_PACKS", "").split(",") if p.strip()]


def _dice_randomness() -> dict:
    """DICE_RANDOMNESS=random.org with RANDOM_ORG_KEY: true random dice (see the dice module's options)."""
    if os.environ.get("DICE_RANDOMNESS", "").strip() == "random.org":
        return {"randomness": "random.org", "random_org_key": os.environ.get("RANDOM_ORG_KEY", "").strip()}
    return {}


def env_modules(extras: bool, preview: bool) -> dict[str, dict]:
    modules: dict[str, dict] = {
        "prompts": {"categories": preview},
        "wordcount": {},
        "sprints": {"reports": preview, "works": preview, "percent": preview},
    }
    if extras:
        modules |= {
            "dice": {"stickers": preview, "commands": preview, "packs": _dice_packs()} | _dice_randomness(),
            "smash": {},
            "greeter": {"from_sheet": True},
            "replies": {"legacy": True},
        }
    return {k: MODULES[k].defaults | v for k, v in modules.items()}


def web_from_env() -> WebConfig | None:
    url = os.environ.get("WEB_APP_URL", "").strip().rstrip("/")
    if not url:
        return None
    if not url.startswith("https://"):
        raise ConfigError("WEB_APP_URL must be an https URL")
    secret = os.environ.get("WEB_APP_SECRET", "").strip()
    if len(secret) < 16:
        raise ConfigError("WEB_APP_SECRET must be set (16+ characters) when WEB_APP_URL is set")
    listen = os.environ.get("WEB_APP_LISTEN", "127.0.0.1:8081")
    host, _, port = listen.rpartition(":")
    return WebConfig(
        url=url,
        host=host or "127.0.0.1",
        port=int(port or 8081),
        secret=secret,
        discord_client_id=os.environ.get("DISCORD_CLIENT_ID", "").strip() or None,
        discord_client_secret=os.environ.get("DISCORD_CLIENT_SECRET", "").strip() or None,
    )


def from_env(platform: str) -> ProcessConfig:
    common = CommonSettings.from_env()
    telegram = TelegramSettings.from_env() if platform == "telegram" else None
    discord = DiscordSettings.from_env() if platform == "discord" else None
    extras = telegram.extras if telegram else False
    preview = common.preview_features
    bot = BotConfig(
        "main", env_modules(extras, preview), telegram=telegram, discord=discord, admin=preview, chat_settings=preview
    )
    return _validate(ProcessConfig({"main": bot}, common, web_from_env() if preview else None))


# --- from a file ------------------------------------------------------------------------------------


def _telegram_target(name: str, data: dict, common: CommonSettings) -> TelegramSettings:
    data = _resolve(data)
    token = str(data.get("token", "")).strip()
    if not token:
        raise ConfigError(f"bot {name}: telegram.token is required")
    polling = bool(data.get("polling", True))
    secret = data.get("webhook_secret")
    host = data.get("webhook_host")
    if not polling and (not secret or not host):
        raise ConfigError(f"bot {name}: webhook mode needs webhook_secret and webhook_host")
    allowed = frozenset(int(x) for x in data.get("allowed_chats", [])) or None
    return TelegramSettings(
        token=token,
        super_admins=frozenset(int(x) for x in data.get("super_admins", []))
        or _ids(os.environ.get("BOT_SUPERADMINS")),
        allowed_chats=allowed,
        polling=polling,
        webhook_host=host,
        webhook_listen=data.get("webhook_listen", "0.0.0.0"),  # noqa: S104 - container listens on all
        webhook_port=int(data.get("webhook_port", 80)),
        webhook_path=str(data.get("webhook_path", "")).strip("/"),
        webhook_secret=secret,
        extras=False,
        allow_private=bool(data.get("allow_private", True)),
        slogan_replies=False,
        ephemeral_replies=bool(data.get("ephemeral_replies", True)),
        ephemeral_commands=bool(data.get("ephemeral_commands", False)),
        common=common,
    )


def _discord_target(name: str, data: dict, common: CommonSettings) -> DiscordSettings:
    data = _resolve(data)
    token = str(data.get("token", "")).strip()
    if not token:
        raise ConfigError(f"bot {name}: discord.token is required")
    return DiscordSettings(
        token=token,
        guild_ids=frozenset(int(x) for x in data.get("guild_ids", [])),
        allowed_guilds=frozenset(int(x) for x in data.get("allowed_guilds", [])) or None,
        common=common,
    )


def from_file(path: str | Path) -> ProcessConfig:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    env_common = CommonSettings.from_env()
    c = _resolve(data.get("common", {}))
    common = replace(
        env_common,
        db_path=Path(c.get("db", env_common.db_path)),
        report_window=timedelta(minutes=int(c["report_window"])) if "report_window" in c else env_common.report_window,
        log_format=c.get("log_format", env_common.log_format),
        health_host=c.get("health_host", env_common.health_host),
        health_port=int(c.get("health_port", env_common.health_port)),
        error_alerts=bool(c.get("error_alerts", env_common.error_alerts)),
        preview_features=True,  # the file chooses the modules; the env switch doesn't apply
    )
    web = None
    if "web" in data:
        w = _resolve(data["web"])
        host, _, port = str(w.get("listen", "127.0.0.1:8081")).rpartition(":")
        if not str(w.get("url", "")).startswith("https://"):
            raise ConfigError("web.url must be an https URL")
        if len(str(w.get("secret", ""))) < 16:
            raise ConfigError("web.secret must be 16+ characters")
        web = WebConfig(
            url=w["url"].rstrip("/"),
            host=host or "127.0.0.1",
            port=int(port or 8081),
            secret=w["secret"],
            discord_client_id=w.get("discord_client_id"),
            discord_client_secret=w.get("discord_client_secret"),
        )
    bots: dict[str, BotConfig] = {}
    for name, b in (data.get("bots") or {}).items():
        names = b.get("modules", [])
        if not isinstance(names, list):
            raise ConfigError(f"bot {name}: modules must be a list")
        modules = {m: dict(b.get(m, {})) for m in names}
        _check_modules(name, modules)
        modules = {m: MODULES[m].defaults | _resolve(opts) for m, opts in modules.items()}
        bots[name] = BotConfig(
            name=name,
            modules=modules,
            telegram=_telegram_target(name, b["telegram"], common) if "telegram" in b else None,
            discord=_discord_target(name, b["discord"], common) if "discord" in b else None,
            admin=bool(b.get("admin", True)),
            chat_settings=bool(b.get("chat_settings", True)),
        )
    if not bots:
        raise ConfigError(f"{path}: no [bots.*] sections")
    return _validate(ProcessConfig(bots, common, web, source=str(path)))


def load(platform: str | None = None, path: str | None = None) -> ProcessConfig:
    path = path or os.environ.get("BOT_CONFIG", "").strip() or None
    if path:
        return from_file(path)
    return from_env(platform or "telegram")
