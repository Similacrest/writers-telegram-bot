"""Running the bot unattended: log format, a health endpoint and error alerts for the admins."""

import asyncio
import contextlib
import json
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

ALERT_EVERY = 15 * 60  # the same error is reported at most once per this many seconds
ALERTS_PER_HOUR = 10


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(fmt: str = "text") -> None:
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# --- error alerts ------------------------------------------------------------------------------------


class AlertHandler(logging.Handler):
    """Forwards ERROR records to ``send`` (e.g. a private message to the super-admins), rate-limited.

    ``emit`` may be called from any thread; sending happens on the event loop given to ``start``.
    """

    def __init__(self, send: Callable[[str], Awaitable[None]], *, clock: Callable[[], float] = time.monotonic):
        super().__init__(level=logging.ERROR)
        self.send = send
        self.clock = clock
        self._last: dict[tuple, float] = {}
        self._sent: list[float] = []
        self._queue: asyncio.Queue[logging.LogRecord] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=100)
        self._task = asyncio.create_task(self._run(), name="error-alerts")
        logging.getLogger().addHandler(self)

    async def stop(self) -> None:
        logging.getLogger().removeHandler(self)
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__ or self._loop is None or self._loop.is_closed():
            return
        with contextlib.suppress(RuntimeError, asyncio.QueueFull):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, record)

    def should_send(self, record: logging.LogRecord) -> bool:
        now = self.clock()
        exc = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
        key = (record.name, str(record.msg), exc)
        if now - self._last.get(key, -1e9) < ALERT_EVERY:
            return False
        self._sent = [t for t in self._sent if now - t < 3600]
        if len(self._sent) >= ALERTS_PER_HOUR:
            return False
        self._last[key] = now
        self._sent.append(now)
        return True

    @staticmethod
    def render(record: logging.LogRecord) -> str:
        lines = [f"{record.levelname} {record.name}: {record.getMessage()}"[:500]]
        if record.exc_info and record.exc_info[1] is not None:
            tb = traceback.format_exception(*record.exc_info)
            lines.append("".join(tb)[-2500:])
        return "\n".join(lines)

    async def _run(self) -> None:
        while True:
            record = await self._queue.get()
            if not self.should_send(record):
                continue
            try:
                await self.send(self.render(record))
            except Exception as e:  # never let alerting take the bot down, and don't alert about it
                logger.warning("could not send an error alert: %s", e)


# --- health endpoint ---------------------------------------------------------------------------------


class HealthServer:
    """Minimal HTTP endpoint: any GET returns the JSON from ``check``; 200 if ``ok`` is true, else 503."""

    def __init__(self, host: str, port: int, check: Callable[[], Awaitable[dict]]):
        self.host, self.port, self.check = host, port, check
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        logger.info("health endpoint on http://%s:%s/health", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(5):
                await reader.readuntil(b"\r\n\r\n")
                try:
                    body = await self.check()
                except Exception as e:
                    body = {"ok": False, "error": type(e).__name__}
            payload = json.dumps(body).encode()
            status = "200 OK" if body.get("ok") else "503 Service Unavailable"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()
