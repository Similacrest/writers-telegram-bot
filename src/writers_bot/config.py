"""Settings from environment variables. See README for the full list."""

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core.prompts import GoogleSettings

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def _ids(value: str | None) -> frozenset[int]:
    result = set()
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            raise ConfigError(f"not a numeric id: {part!r}") from None
    return frozenset(result)


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"} if value else default


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None


def _prompt_sheets(value: str | None) -> dict[str, str]:
    # "ua=База-ua,en=Base-en"
    result = {}
    for part in (value or "ua=База-ua").split(","):
        lang, _, sheet = part.partition("=")
        if lang.strip() and sheet.strip():
            result[lang.strip()] = sheet.strip()
    return result


def google_settings() -> GoogleSettings | None:
    spreadsheet = os.environ.get("SPREADSHEET_ID", "").strip()
    if not spreadsheet:
        return None
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    return GoogleSettings(
        spreadsheet_id=spreadsheet,
        config_sheet=os.environ.get("CONFIG_SHEET_NAME", "").strip() or None,
        drive_folder_id=os.environ.get("GOOGLE_DRIVE_BASE_FOLDER_ID", "").strip() or None,
        prompt_sheets=_prompt_sheets(os.environ.get("PROMPT_SHEETS")),
        prompt_range=os.environ.get("PROMPT_RANGE", "B1:ZZ").strip(),
        token_file=Path(os.environ.get("GOOGLE_TOKEN_FILE", "token.json")),
        service_account_file=Path(sa) if sa else None,
    )


@dataclass(frozen=True, slots=True)
class CommonSettings:
    timezone: ZoneInfo
    db_path: Path
    report_window: timedelta
    google: GoogleSettings | None
    log_format: str = "text"  # text | json
    health_host: str = "127.0.0.1"
    health_port: int = 0  # 0 = no health endpoint
    error_alerts: bool = True  # send ERROR log records to the super-admins
    # Phase 1-2 user features still being designed (see PREVIEW_FEATURES in the README)
    preview_features: bool = False

    @classmethod
    def from_env(cls) -> "CommonSettings":
        tz_name = os.environ.get("BOT_TIMEZONE", "Europe/Kyiv")
        return cls(
            timezone=ZoneInfo(tz_name),
            db_path=Path(os.environ.get("DB_PATH", "data/bot.sqlite3")),
            report_window=timedelta(minutes=_int("SPRINT_REPORT_WINDOW", 30)),
            google=google_settings(),
            log_format=os.environ.get("LOG_FORMAT", "text").strip().lower() or "text",
            health_host=os.environ.get("HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1",
            health_port=_int("HEALTH_PORT", 0),
            error_alerts=_flag("ERROR_ALERTS", default=True),
            preview_features=_flag("PREVIEW_FEATURES"),
        )


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    token: str
    super_admins: frozenset[int]
    allowed_chats: frozenset[int] | None  # None = every chat
    polling: bool
    webhook_host: str | None
    webhook_listen: str
    webhook_port: int
    webhook_path: str
    webhook_secret: str | None
    extras: bool  # the community extras from the v2 "extended" branch (see EXTRAS in the README)
    allow_private: bool  # private chats work even when ALLOWED_CHATS is set
    slogan_replies: bool
    ephemeral_replies: bool  # Bot API 10.2 ephemeral messages for personal replies in groups
    ephemeral_commands: bool  # mark personal commands as ephemeral in the command menu
    common: CommonSettings

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        token = os.environ.get("TELEGRAM_TOKEN", "").strip()
        if not token:
            raise ConfigError("TELEGRAM_TOKEN is not set")
        allowed = _ids(os.environ.get("ALLOWED_CHATS"))
        if not allowed:
            logger.warning("ALLOWED_CHATS is not set: the bot answers in every chat")
        polling = _flag("TEST_ENV") or _flag("TELEGRAM_POLLING")
        secret = os.environ.get("TELEGRAM_WEBHOOK_TOKEN", "").strip() or None
        # In Docker HOSTNAME is the container id, so WEBHOOK_HOST takes precedence.
        host = (os.environ.get("WEBHOOK_HOST") or os.environ.get("HOSTNAME") or "").strip() or None
        if not polling:
            if not secret:
                raise ConfigError(
                    "TELEGRAM_WEBHOOK_TOKEN must be set in webhook mode, otherwise anyone can send fake updates"
                )
            if not host:
                raise ConfigError("WEBHOOK_HOST must be set in webhook mode")
        extras = _flag("EXTRAS")
        common = CommonSettings.from_env()
        return cls(
            token=token,
            super_admins=_ids(os.environ.get("BOT_SUPERADMINS")),
            allowed_chats=allowed or None,
            polling=polling,
            webhook_host=host,
            webhook_listen=os.environ.get("WEBHOOK_LISTEN", "0.0.0.0"),  # noqa: S104 - container listens on all
            webhook_port=_int("PORT", 80),
            webhook_path=os.environ.get("WEBHOOK_PATH", "").strip("/"),
            webhook_secret=secret,
            extras=extras,
            allow_private=_flag("ALLOW_PRIVATE_CHATS", default=extras),
            slogan_replies=_flag("SLOGAN_REPLIES", default=extras),
            ephemeral_replies=_flag("EPHEMERAL_REPLIES", default=common.preview_features),
            ephemeral_commands=_flag("EPHEMERAL_COMMANDS"),
            common=common,
        )


@dataclass(frozen=True, slots=True)
class DiscordSettings:
    token: str
    guild_ids: frozenset[int]  # sync slash commands to these guilds instantly; empty = global sync
    allowed_guilds: frozenset[int] | None
    common: CommonSettings

    @classmethod
    def from_env(cls) -> "DiscordSettings":
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError("DISCORD_TOKEN is not set")
        allowed = _ids(os.environ.get("DISCORD_ALLOWED_GUILDS"))
        return cls(
            token=token,
            guild_ids=_ids(os.environ.get("DISCORD_GUILD_IDS")),
            allowed_guilds=allowed or None,
            common=CommonSettings.from_env(),
        )
