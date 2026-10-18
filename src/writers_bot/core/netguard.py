"""HTTP client for fetching user-supplied links without exposing the internal network (SSRF guard).

Users can make the bot fetch any WriteFreely instance, so every outgoing connection is checked:

* only ``https`` on the default port;
* the host is resolved by us, every resolved address must be globally routable (no loopback, RFC 1918,
  link-local, CGNAT 100.64/10, multicast, ...), and the connection goes to the address we checked, so
  DNS rebinding between the check and the connect is not possible;
* redirects are followed manually and each hop is checked again;
* response size and total time are capped; proxies from the environment are ignored.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import httpcore
import httpx

USER_AGENT = "writers-bot/3 (+https://github.com/Similacrest/writers-telegram-bot)"
MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 15.0


class BlockedURLError(ValueError):
    """The URL points somewhere the bot must not connect to."""


class FetchError(RuntimeError):
    """The remote server could not be reached or returned something unusable."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status_code: int
    content_type: str
    content: bytes

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def is_public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def check_url(url: httpx.URL) -> None:
    if url.scheme != "https":
        raise BlockedURLError("only https links are supported")
    if url.port not in (None, 443):
        raise BlockedURLError("non-standard ports are not allowed")
    if not url.host:
        raise BlockedURLError("link has no host")
    if url.userinfo:
        raise BlockedURLError("links with credentials are not allowed")


async def resolve_public(host: str, port: int) -> str:
    """Resolve ``host`` and return one address, refusing if *any* address is not public."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"cannot resolve {host}") from e
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise FetchError(f"cannot resolve {host}")
    for addr in addresses:
        if not is_public_address(addr):
            raise BlockedURLError(f"{host} resolves to a non-public address")
    return addresses[0]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Network backend that only opens TCP connections to public addresses."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ASYNC109 - httpcore interface
        async with asyncio.timeout(timeout):
            addr = await resolve_public(host, port)
        # TLS SNI and certificate checks still use the original hostname (httpcore passes it to start_tls).
        return await self._inner.connect_tcp(
            addr, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, *args, **kwargs):  # pragma: no cover - never used
        raise BlockedURLError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _guarded_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    # httpx has no public hook for the network backend; swap the pool for one using the guarded backend.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=30.0,
        network_backend=GuardedBackend(),
    )
    return transport


class SafeFetcher:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            transport=transport or _guarded_transport(),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": USER_AGENT},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, max_bytes: int = DEFAULT_MAX_BYTES, accept: str = "*/*") -> FetchResult:
        try:
            async with asyncio.timeout(self._timeout * 2):
                return await self._get(httpx.URL(url), max_bytes=max_bytes, accept=accept)
        except TimeoutError as e:
            raise FetchError("timed out") from e
        except httpx.InvalidURL as e:
            raise BlockedURLError("invalid link") from e

    async def _get(self, url: httpx.URL, *, max_bytes: int, accept: str) -> FetchResult:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            try:
                async with self._client.stream("GET", url, headers={"Accept": accept}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("redirect without location")
                        url = url.join(location)
                        continue
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError("page is too large")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FetchError("page is too large")
                    return FetchResult(
                        url=str(url),
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        content=bytes(body),
                    )
            except httpx.HTTPError as e:
                raise FetchError(str(e) or type(e).__name__) from e
        raise FetchError("too many redirects")
