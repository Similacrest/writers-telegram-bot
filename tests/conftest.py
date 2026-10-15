import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from writers_bot.core.storage import Storage


class FakeClock:
    """Deterministic time for the sprint engine: ``sleep`` waits until the test calls ``advance``."""

    def __init__(self):
        self.now_value = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
        self._waiters: list[tuple[datetime, asyncio.Future]] = []

    def now(self) -> datetime:
        return self.now_value

    async def sleep(self, seconds: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now_value + timedelta(seconds=seconds), fut))
        await fut

    async def settle(self) -> None:
        # aiosqlite works in a thread, so give pending DB calls a moment of real time to finish.
        for _ in range(3):
            await asyncio.sleep(0.01)

    async def advance(self, seconds: float) -> None:
        """Move time forward in small steps so every wake-up along the way runs."""
        end = self.now_value + timedelta(seconds=seconds)
        while True:
            await self.settle()
            due = [w for w in self._waiters if w[0] <= end and not w[1].done()]
            if not due:
                break
            when = min(w[0] for w in due)
            self.now_value = max(self.now_value, when)
            for w in list(self._waiters):
                if w[0] <= self.now_value and not w[1].done():
                    w[1].set_result(None)
            self._waiters = [w for w in self._waiters if not w[1].done()]
        self.now_value = end
        await self.settle()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def storage(tmp_path):
    s = Storage(tmp_path / "test.sqlite3")
    await s.open()
    yield s
    await s.close()
