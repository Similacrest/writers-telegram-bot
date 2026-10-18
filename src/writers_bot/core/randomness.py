"""True randomness for the dice: random bytes from random.org (atmospheric noise) instead of Python's generator.

The bot keeps a pool of bytes fetched with random.org's JSON-RPC API (``generateBlobs``, needs a free API key) and the
dice draw from it. The pool is refilled in the background when it runs low. A roll never waits for the network: if the
pool is empty (the service is down, the daily quota is spent) the roll takes bytes from the operating system's
generator (``os.urandom``), which is cryptographically secure but not "true", and the log says so once.
"""

import asyncio
import contextlib
import logging
import os
import random
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

API = "https://api.random.org/json-rpc/4/invoke"
POOL_BITS = 8192  # one request: 1 KiB (the free quota is 250 000 bits a day, 1000 requests)
LOW = 256  # refill when fewer bytes than this are left

Fetch = Callable[[int], Awaitable[bytes]]


async def random_org(key: str, nbytes: int, *, client: httpx.AsyncClient | None = None) -> bytes:
    """``nbytes`` random bytes from random.org. Raises on any failure."""
    body = {
        "jsonrpc": "2.0",
        "method": "generateBlobs",
        "params": {"apiKey": key, "n": 1, "size": nbytes * 8, "format": "hex"},
        "id": 1,
    }
    async with contextlib.AsyncExitStack() as stack:
        http = client or await stack.enter_async_context(httpx.AsyncClient(timeout=10, trust_env=False))
        response = await http.post(API, json=body)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"random.org: {data['error'].get('message', data['error'])}")
    return bytes.fromhex(data["result"]["random"]["data"][0])


class TrueRandom(random.Random):
    """A ``random.Random`` whose bits are bytes of a pool of true random bytes (``randrange`` and the rest of the
    class draw through ``getrandbits``, without bias); when the pool is empty, the operating system's bytes."""

    def __init__(self, fetch: Fetch):
        super().__init__()
        self._fetch = fetch
        self._pool = bytearray()
        self._task: asyncio.Task | None = None
        self._warned = False
        self.true_draws = 0  # bytes taken from the pool, and (below) from the operating system
        self.fallback_draws = 0

    # --- the pool ----------------------------------------------------------------------------------

    async def refill(self) -> None:
        try:
            self._pool += await self._fetch(POOL_BITS // 8)
            self._warned = False
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as e:
            logger.warning("no true random bytes (%s): rolls use the system's generator meanwhile", e)

    def _topup(self) -> None:
        """Start a refill in the background when the pool is low (rolls are not waiting for it)."""
        if len(self._pool) < LOW and (self._task is None or self._task.done()):
            try:
                self._task = asyncio.get_running_loop().create_task(self.refill(), name="true-random")
            except RuntimeError:  # no loop (a script): the next draw falls back
                pass

    async def start(self) -> None:
        await self.refill()

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _take(self, nbytes: int) -> bytes:
        self._topup()
        if len(self._pool) >= nbytes:
            out = bytes(self._pool[:nbytes])
            del self._pool[:nbytes]
            self.true_draws += nbytes
            return out
        if not self._warned:
            self._warned = True
            logger.warning("the pool of true random bytes is empty: rolls use the system's generator")
        self.fallback_draws += nbytes
        return os.urandom(nbytes)

    # --- random.Random -----------------------------------------------------------------------------

    def getrandbits(self, k: int) -> int:
        if k < 0:
            raise ValueError("number of bits must be non-negative")
        if k == 0:
            return 0
        nbytes = (k + 7) // 8
        return int.from_bytes(self._take(nbytes), "big") >> (nbytes * 8 - k)

    def random(self) -> float:
        return self.getrandbits(53) * (2**-53)

    def seed(self, *_args, **_kwargs) -> None:  # a true source has no seed
        return None

    def getstate(self):
        raise NotImplementedError("true random numbers have no state")

    setstate = getstate
