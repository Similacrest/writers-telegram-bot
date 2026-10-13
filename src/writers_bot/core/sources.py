"""Fetch the text behind a link so it can be word-counted.

Supported:

* Telegraph (``telegra.ph`` / ``graph.org``) through its public JSON API;
* Ellipsus public Reader links (``ellipsus.com/read/<id>``): the reader API returns a Yjs document
  that is decoded locally with ``pycrdt``;
* any WriteFreely / Write.as instance: the post page is detected by its generator meta tag or nodeinfo,
  and the text is taken from the ``e-content`` element.
"""

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from .netguard import FetchError, SafeFetcher

URL_RE = re.compile(r"https?://[^\s<>\"'«»]+", re.IGNORECASE)

TELEGRAPH_HOSTS = {"telegra.ph", "graph.org", "www.telegra.ph"}
ELLIPSUS_HOSTS = {"ellipsus.com", "www.ellipsus.com"}
ELLIPSUS_API = "https://service.ellipsus.com/api/reader/link"
ELLIPSUS_ID_RE = re.compile(r"^/read/([A-Za-z0-9]{10,40})(?:/|$)")

BLOCK_TAGS = {
    "p", "div", "br", "hr", "li", "ul", "ol", "blockquote", "aside", "pre", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th", "section", "article", "header", "footer",
    "paragraph", "heading", "blockquote", "listitem", "bulletlist", "orderedlist", "codeblock", "hardbreak",
}  # fmt: skip


class UnsupportedLinkError(ValueError):
    """The link is not one of the supported writing platforms."""


@dataclass(frozen=True, slots=True)
class LinkedText:
    source: str  # human-readable platform name
    title: str
    text: str
    url: str


def find_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match[0].rstrip(".,;:!?)]}") if match else None


def _join_blocks(parts: list[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


# --- Telegraph ---------------------------------------------------------------------------------------


def telegraph_nodes_to_text(nodes) -> str:
    parts: list[str] = []

    def walk(node):
        if isinstance(node, str):
            parts.append(node)
            return
        if not isinstance(node, dict):
            return
        block = node.get("tag") in BLOCK_TAGS
        if block:
            parts.append("\n")
        for child in node.get("children") or []:
            walk(child)
        if block:
            parts.append("\n")

    for n in nodes or []:
        walk(n)
    return _join_blocks(parts)


async def fetch_telegraph(fetcher: SafeFetcher, url: str) -> LinkedText:
    path = urlsplit(url).path.strip("/")
    if not path or "/" in path:
        raise UnsupportedLinkError("not a Telegraph page")
    api_url = f"https://api.telegra.ph/getPage/{quote(path)}?return_content=true"
    data = _json(await fetcher.get(api_url, accept="application/json"))
    if not data.get("ok"):
        raise FetchError(data.get("error") or "Telegraph error")
    page = data["result"]
    title = page.get("title") or ""
    body = telegraph_nodes_to_text(page.get("content"))
    return LinkedText("Телеграф", title, f"{title}\n{body}" if title else body, url)


# --- Ellipsus ----------------------------------------------------------------------------------------


def ydoc_update_to_text(update: bytes, root: str = "draft-content") -> str:
    from pycrdt import Doc, XmlElement, XmlFragment, XmlText

    doc = Doc()
    doc.apply_update(update)
    fragment = doc.get(root, type=XmlFragment)
    parts: list[str] = []

    def walk(node):
        if isinstance(node, XmlText):
            parts.append(_xml_text(node))
        elif isinstance(node, XmlElement):
            block = node.tag.lower() in BLOCK_TAGS
            if block:
                parts.append("\n")
            for child in node.children:
                walk(child)
            if block:
                parts.append("\n")

    for child in fragment.children:
        walk(child)
    return _join_blocks(parts)


def _xml_text(node) -> str:
    # XmlText formats itself as XML (with formatting marks as tags); take the plain-text deltas instead.
    return "".join(chunk for chunk, _attrs in node.diff() if isinstance(chunk, str))


async def fetch_ellipsus(fetcher: SafeFetcher, url: str) -> LinkedText:
    match = ELLIPSUS_ID_RE.match(urlsplit(url).path)
    if not match:
        raise UnsupportedLinkError("only public Ellipsus Reader links (ellipsus.com/read/…) are supported")
    link_id = match[1]
    meta_resp, content_resp = await asyncio.gather(
        fetcher.get(f"{ELLIPSUS_API}/{link_id}/resolve", accept="application/json"),
        fetcher.get(f"{ELLIPSUS_API}/{link_id}/content", accept="application/json", max_bytes=20 * 1024 * 1024),
    )
    if content_resp.status_code in (401, 403, 404):
        raise FetchError("документ не публічний або посилання не існує")
    meta = _json(meta_resp) if meta_resp.status_code == 200 else {}
    content = _json(content_resp).get("content")
    if not content:
        raise FetchError("Ellipsus returned no content")
    try:
        update = base64.b64decode(content, validate=True)
    except ValueError as e:
        raise FetchError("unexpected Ellipsus content format") from e
    try:
        body = await asyncio.to_thread(ydoc_update_to_text, update)
    except Exception as e:  # pycrdt raises plain exceptions on malformed updates
        raise FetchError("could not decode Ellipsus document") from e
    title = str(meta.get("name") or "")
    return LinkedText("Ellipsus", title, f"{title}\n{body}" if title else body, url)


# --- WriteFreely -------------------------------------------------------------------------------------


class _WriteFreelyParser(HTMLParser):
    """Collects text inside the element with class ``e-content`` and notes the page generator."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.generator = ""
        self.title = ""
        self.parts: list[str] = []
        self._depth = 0  # nesting depth inside e-content, 0 = outside
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").lower() == "generator":
            self.generator = a.get("content") or ""
        if tag == "meta" and a.get("property") == "og:title" and not self.title:
            self.title = a.get("content") or ""
        if self._depth:
            if tag in ("script", "style"):
                self._skip += 1
            if tag not in ("br", "hr", "img", "meta", "link", "input", "wbr"):
                self._depth += 1
            if tag in BLOCK_TAGS:
                self.parts.append("\n")
        elif "e-content" in (a.get("class") or "").split():
            self._depth = 1

    def handle_endtag(self, tag):
        if not self._depth:
            return
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        self._depth -= 1

    def handle_data(self, data):
        if self._depth and not self._skip:
            self.parts.append(data)


def _is_writefreely_generator(generator: str) -> bool:
    return generator.strip().lower() in {"writefreely", "write.as"}


async def _nodeinfo_says_writefreely(fetcher: SafeFetcher, origin: str) -> bool:
    try:
        well_known = _json(await fetcher.get(f"{origin}/.well-known/nodeinfo", max_bytes=64 * 1024))
        for link in well_known.get("links", []):
            href = str(link.get("href", ""))
            if not href.startswith(origin + "/"):
                continue  # only trust nodeinfo on the same origin
            info = _json(await fetcher.get(href, max_bytes=256 * 1024))
            return str(info.get("software", {}).get("name", "")).lower() in {"writefreely", "writeas"}
    except (FetchError, ValueError):
        return False
    return False


async def fetch_writefreely(fetcher: SafeFetcher, url: str) -> LinkedText:
    resp = await fetcher.get(url, accept="text/html")
    if resp.status_code != 200 or "html" not in resp.content_type:
        raise UnsupportedLinkError("сторінка недоступна або це не допис")
    parser = _WriteFreelyParser()
    parser.feed(resp.text())
    parser.close()
    if not _is_writefreely_generator(parser.generator):
        parts = urlsplit(resp.url)
        if not await _nodeinfo_says_writefreely(fetcher, f"{parts.scheme}://{parts.netloc}"):
            raise UnsupportedLinkError("непідтримуваний сайт")
    if not parser.parts:
        raise UnsupportedLinkError("на сторінці немає тексту допису")
    body = _join_blocks(parser.parts)
    title = parser.title.strip()
    if title and not body.startswith(title):
        body = f"{title}\n{body}"
    return LinkedText("WriteFreely", title, body, resp.url)


# --- dispatch ----------------------------------------------------------------------------------------


def _json(resp) -> dict:
    try:
        data = json.loads(resp.content)
    except ValueError as e:
        raise FetchError("unexpected response") from e
    if not isinstance(data, dict):
        raise FetchError("unexpected response")
    return data


async def fetch_linked_text(fetcher: SafeFetcher, url: str) -> LinkedText:
    host = (urlsplit(url).hostname or "").lower()
    if url.lower().startswith("http://"):
        url = "https://" + url[7:]
    if host in TELEGRAPH_HOSTS:
        return await fetch_telegraph(fetcher, url)
    if host in ELLIPSUS_HOSTS:
        return await fetch_ellipsus(fetcher, url)
    return await fetch_writefreely(fetcher, url)
