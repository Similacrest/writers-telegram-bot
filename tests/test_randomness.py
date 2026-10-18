"""True random dice: the pool of random.org bytes, the fallback, the API call and the config."""

import json
import random
from zoneinfo import ZoneInfo

import httpx
import pytest

from writers_bot.botconfig import ConfigError, _check_modules
from writers_bot.core import extras, randomness
from writers_bot.core.fmt import HtmlFormatter

F = HtmlFormatter(ZoneInfo("Europe/Kyiv"))


def source(data: bytes) -> randomness.TrueRandom:
    async def fetch(_n):
        return data

    rng = randomness.TrueRandom(fetch)
    rng._pool += data
    return rng


def test_a_roll_takes_its_dice_from_the_pool():
    rng = source(bytes([0x80, 0x00, 0x98]))  # 5 bits of each: 16, 0, 19 -> the d20 shows 17, 1, 20
    rolled = [extras.throw(F, extras.parse_roll("/d20"), rng).dice[0][1] for _ in range(3)]
    assert rolled == [17, 1, 20] and rng.true_draws == 3 and rng.fallback_draws == 0


def test_the_module_random_is_back_after_a_roll():
    import d20.expression

    before = d20.expression.random
    extras.throw(F, extras.parse_roll("/d20"), source(b"\x00"))
    assert d20.expression.random is before
    with pytest.raises(ZeroDivisionError):
        extras.throw(F, extras.parse_roll("/d20"), type("Boom", (random.Random,), {"randrange": lambda *_: 1 / 0})())
    assert d20.expression.random is before


def test_an_empty_pool_falls_back_to_the_system_generator(caplog):
    rng = source(b"")
    values = {extras.throw(F, extras.parse_roll("/d6"), rng).dice[0][1] for _ in range(60)}
    assert values <= set(range(1, 7)) and len(values) > 1
    assert (
        rng.true_draws == 0 and rng.fallback_draws > 0 and caplog.text.count("pool of true random bytes is empty") == 1
    )


async def test_the_pool_is_refilled_in_the_background():
    calls = []

    async def fetch(n):
        calls.append(n)
        return b"\x80" * n

    rng = randomness.TrueRandom(fetch)
    await rng.start()
    assert len(rng._pool) == randomness.POOL_BITS // 8 and calls == [randomness.POOL_BITS // 8]
    rng._pool = bytearray(b"\x80" * 10)  # nearly empty: the next draw asks for more, without waiting
    rng.getrandbits(5)
    await rng._task
    assert len(calls) == 2 and len(rng._pool) > 1000
    await rng.stop()


async def test_a_failing_service_leaves_the_pool_as_it_was(caplog):
    async def fetch(_n):
        raise httpx.ConnectError("down")

    rng = randomness.TrueRandom(fetch)
    await rng.refill()
    assert not rng._pool and "no true random bytes" in caplog.text


async def test_random_org_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"random": {"data": ["00ff80"]}}, "id": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await randomness.random_org("KEY", 3, client=client) == b"\x00\xff\x80"
    assert seen["method"] == "generateBlobs" and seen["params"] == {
        "apiKey": "KEY",
        "n": 1,
        "size": 24,
        "format": "hex",
    }

    def failing(_request):
        return httpx.Response(200, json={"error": {"code": 402, "message": "allowance exceeded"}, "id": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        with pytest.raises(RuntimeError, match="allowance exceeded"):
            await randomness.random_org("KEY", 3, client=client)


def test_the_config_needs_a_key_for_random_org():
    _check_modules("main", {"dice": {"randomness": "pseudo"}})
    _check_modules("main", {"dice": {"randomness": "random.org", "random_org_key": "k"}})
    with pytest.raises(ConfigError, match="random_org_key"):
        _check_modules("main", {"dice": {"randomness": "random.org"}})
    with pytest.raises(ConfigError, match="pseudo"):
        _check_modules("main", {"dice": {"randomness": "quantum"}})
