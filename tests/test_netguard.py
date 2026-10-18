"""SSRF guard tests. They never open connections to internal addresses: the guard must refuse before connecting."""

import httpx
import pytest

from writers_bot.core import netguard
from writers_bot.core.i18n import t
from writers_bot.core.netguard import (
    BlockedURLError,
    FetchError,
    GuardedBackend,
    SafeFetcher,
    check_url,
    is_public_address,
)


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.10", "100.64.0.1", "100.100.100.100", "169.254.169.254",
     "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1"],
)  # fmt: skip
def test_non_public_addresses(addr):
    assert not is_public_address(addr)


@pytest.mark.parametrize("addr", ["93.184.215.14", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(addr):
    assert is_public_address(addr)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "ftp://example.com/",
        "https://example.com:8443/",
        "https://user:pw@example.com/",
        "file:///etc/passwd",
    ],
)
def test_check_url_rejects(url):
    with pytest.raises(BlockedURLError):
        check_url(httpx.URL(url))


class RecordingBackend:
    def __init__(self):
        self.calls = []

    async def connect_tcp(self, host, port, **kwargs):
        self.calls.append((host, port))
        raise ConnectionRefusedError("test backend never connects")


async def test_guarded_backend_refuses_before_connecting(monkeypatch):
    inner = RecordingBackend()
    backend = GuardedBackend(inner)

    async def fake_getaddrinfo(host, port, **kwargs):
        return [(None, None, None, "", ("10.0.0.5", port)), (None, None, None, "", ("93.184.215.14", port))]

    monkeypatch.setattr(
        netguard.asyncio, "get_running_loop", lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})()
    )
    with pytest.raises(BlockedURLError):
        await backend.connect_tcp("rebind.example", 443)
    with pytest.raises(BlockedURLError):
        await backend.connect_tcp("127.0.0.1", 443)
    assert inner.calls == []


async def test_guarded_backend_connects_to_checked_address(monkeypatch):
    inner = RecordingBackend()
    backend = GuardedBackend(inner)

    async def fake_getaddrinfo(host, port, **kwargs):
        return [(None, None, None, "", ("93.184.215.14", port))]

    monkeypatch.setattr(
        netguard.asyncio, "get_running_loop", lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})()
    )
    with pytest.raises(ConnectionRefusedError):
        await backend.connect_tcp("example.com", 443)
    assert inner.calls == [("93.184.215.14", 443)]


async def test_transport_uses_guarded_backend():
    transport = netguard._guarded_transport()
    assert isinstance(transport._pool._network_backend, GuardedBackend)


def mock_fetcher(handler) -> SafeFetcher:
    return SafeFetcher(transport=httpx.MockTransport(handler))


async def test_redirect_to_http_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://internal.example/"})

    fetcher = mock_fetcher(handler)
    with pytest.raises(BlockedURLError):
        await fetcher.get("https://example.com/")


async def test_redirects_are_followed_and_rechecked():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, text="ok")

    result = await mock_fetcher(handler).get("https://example.com/a")
    assert result.text() == "ok"
    assert seen == ["https://example.com/a", "https://example.com/b"]


async def test_redirect_loop_stops():
    def handler(request):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match=t("links.redirects")):
        await mock_fetcher(handler).get("https://example.com/")


async def test_size_cap():
    def handler(request):
        return httpx.Response(200, content=b"x" * 2000)

    with pytest.raises(FetchError, match=t("links.too_large")):
        await mock_fetcher(handler).get("https://example.com/", max_bytes=1000)
