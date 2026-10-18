"""Runs the bots of a config (see :mod:`.botconfig`) in one process.

    writers-bot [--config bots.toml] [--bot NAME]
    writers-bot-telegram      # the Telegram targets only (v3 entry point, environment config)
    writers-bot-discord       # the Discord targets only

Every bot can also run in its own container from the same image (``--bot greeter``); that's up to ops.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import time

from telegram import Update
from telegram.error import TelegramError

from . import __version__
from .botconfig import ProcessConfig, load
from .config import ConfigError
from .core.i18n import t
from .core.ops import AlertHandler, HealthServer, setup_logging
from .services import Services

logger = logging.getLogger(__name__)


class Runner:
    def __init__(self, config: ProcessConfig):
        self.config = config
        self.services = Services.build(config)
        self.telegram = []
        self.discord = []
        self.health: HealthServer | None = None
        self.alerts: AlertHandler | None = None
        self.started = time.monotonic()
        self._stop = asyncio.Event()

    def _build(self) -> None:
        from .telegram_bot.app import TelegramBot

        for bot in self.config.bots.values():
            services = self.services.for_bot(bot)
            if bot.telegram is not None:
                self.telegram.append(TelegramBot(bot, services))
            if bot.discord is not None:
                from .discord_bot.app import DiscordBot

                self.discord.append(DiscordBot(bot, services))

    async def health_status(self) -> dict:
        if self.services.storage is not None:
            await self.services.storage.ping()
        bots = {f"telegram:{b.config.name}": b.health() for b in self.telegram}
        bots |= {f"discord:{b.config.name}": b.health() for b in self.discord}
        return {
            "ok": all(b["ok"] for b in bots.values()),
            "version": __version__,
            "uptime_s": round(time.monotonic() - self.started),
            "bots": bots,
        }

    async def _send_alert(self, text: str) -> None:
        for tbot in self.telegram:
            for admin_id in tbot.settings.super_admins:
                with contextlib.suppress(TelegramError):
                    await tbot.app.bot.send_message(
                        admin_id, t("ops.alert", bot=tbot.config.name, text=text), parse_mode=None
                    )
            return  # one bot is enough

    async def run(self) -> None:
        self._build()
        await self.services.start()
        if self.services.web is None and self.config.web is not None and self.config.storage_bot is not None:
            from .web.server import WebApp

            self.services.web = WebApp(self.config.web, self.services)
            for tbot in self.telegram:
                if tbot.storage is not None:
                    tbot.services.web = self.services.web
        common = self.config.common
        try:
            for tbot in self.telegram:
                app = tbot.build()
                await app.initialize()
                await tbot.post_init(app)
                await app.start()
                s = tbot.settings
                if s.polling:
                    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                else:
                    await app.updater.start_webhook(
                        listen=s.webhook_listen,
                        port=s.webhook_port,
                        url_path=s.webhook_path,
                        webhook_url=f"https://{s.webhook_host}/{s.webhook_path}",
                        secret_token=s.webhook_secret,
                        allowed_updates=Update.ALL_TYPES,
                    )
                logger.info("telegram bot %s started (@%s)", tbot.config.name, tbot.me.username)
            for dbot in self.discord:
                await dbot.start()
            if self.services.web is not None:
                await self.services.web.start([b for b in self.telegram if b.storage is not None])
            if common.error_alerts and any(b.settings.super_admins for b in self.telegram):
                self.alerts = AlertHandler(self._send_alert)
                self.alerts.start()
            if common.health_port:
                self.health = HealthServer(common.health_host, common.health_port, self.health_status)
                try:
                    await self.health.start()
                except OSError as e:
                    logger.warning("health endpoint not started: %s", e)
                    self.health = None
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, self._stop.set)
            await self._stop.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self.alerts:
            await self.alerts.stop()
        if self.health:
            await self.health.stop()
        if self.services.web is not None:
            await self.services.web.stop()
        for dbot in self.discord:
            with contextlib.suppress(Exception):
                await dbot.stop()
        for tbot in self.telegram:
            app = tbot.app
            if app is None:
                continue
            with contextlib.suppress(Exception):
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await tbot.shutdown()
                await app.shutdown()
        await self.services.stop()


def _run(platform: str | None, argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="TOML config file (default: $BOT_CONFIG, else the environment)")
    parser.add_argument("--bot", help="run only this bot from the config")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_FORMAT", "text").strip().lower())
    try:
        config = load(platform, args.config).only(args.bot, platform)
    except ConfigError as e:
        raise SystemExit(f"config error: {e}") from None
    setup_logging(config.common.log_format)
    logger.info("starting %s from %s: %s", __version__, config.source, ", ".join(config.bots))
    asyncio.run(Runner(config).run())


def main() -> None:
    _run(None)


def main_telegram() -> None:
    _run("telegram")


def main_discord() -> None:
    _run("discord")


if __name__ == "__main__":
    main()
