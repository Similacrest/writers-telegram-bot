import base64
import json

import httpx
import pytest
from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from writers_bot.core.netguard import FetchError, SafeFetcher
from writers_bot.core.sources import (
    UnsupportedLinkError,
    fetch_linked_text,
    find_url,
    telegraph_nodes_to_text,
    ydoc_update_to_text,
)
from writers_bot.core.wordcount import count_words

WRITEFREELY_HTML = """<!DOCTYPE html><html><head>
<meta name="generator" content="WriteFreely">
<meta property="og:title" content="Мій допис">
</head><body><header>Navigation words here</header>
<article id="post-body" class="norm h-entry"><h2 id="title" class="p-name">Мій допис</h2>
<div class="e-content"><p>Перший <b>абз</b>ац.</p><p>Чолов&amp;#39;яга &amp; друг.</p><script>var x = "not words";</script><img src="a.png"></div>
</article><footer>Footer words</footer></body></html>"""


def make_ellipsus_update(paragraphs: list[str]) -> bytes:
    doc = Doc()
    doc["draft-content"] = fragment = XmlFragment()
    for text in paragraphs:
        para = fragment.children.append(XmlElement("paragraph", {"indent": "0"}))
        para.children.append(XmlText(text))
    return doc.get_update()


def test_find_url():
    assert find_url("see https://telegra.ph/Abc-01-02.") == "https://telegra.ph/Abc-01-02"
    assert find_url("no links") is None


def test_telegraph_inline_tags_do_not_split_words():
    nodes = [
        {"tag": "p", "children": [{"tag": "b", "children": ["Hel"]}, "lo world"]},
        {"tag": "p", "children": ["Next"]},
    ]
    assert telegraph_nodes_to_text(nodes) == "Hello world\nNext"


def test_ydoc_decoding_keeps_paragraph_boundaries():
    update = make_ellipsus_update(["Перше речення.", "Друге"])
    assert ydoc_update_to_text(update) == "Перше речення.\nДруге"


def fetcher_for(routes: dict[str, httpx.Response]) -> SafeFetcher:
    def handler(request: httpx.Request):
        key = f"{request.url.host}{request.url.path}"
        if key in routes:
            return routes[key]
        return httpx.Response(404, text="not found")

    return SafeFetcher(transport=httpx.MockTransport(handler))


async def test_fetch_telegraph():
    page = {"ok": True, "result": {"title": "Назва", "content": [{"tag": "p", "children": ["три слова тут"]}]}}
    fetcher = fetcher_for({"api.telegra.ph/getPage/Nazva-10-13": httpx.Response(200, json=page)})
    linked = await fetch_linked_text(fetcher, "https://telegra.ph/Nazva-10-13")
    assert linked.source == "Телеграф"
    assert count_words(linked.text) == 4


async def test_fetch_ellipsus():
    update = make_ellipsus_update(["Один два три.", "Чотири"])
    fetcher = fetcher_for(
        {
            "service.ellipsus.com/api/reader/link/6Wix2TBqszeWlODJo5mkx4/resolve": httpx.Response(
                200, json={"name": "Назва"}
            ),
            "service.ellipsus.com/api/reader/link/6Wix2TBqszeWlODJo5mkx4/content": httpx.Response(
                200, json={"content": base64.b64encode(update).decode()}
            ),
        }
    )
    linked = await fetch_linked_text(fetcher, "https://ellipsus.com/read/6Wix2TBqszeWlODJo5mkx4/")
    assert linked.title == "Назва"
    assert count_words(linked.text) == 5


async def test_ellipsus_private_link():
    fetcher = fetcher_for({})
    with pytest.raises(Exception, match="не публічний"):
        await fetch_linked_text(fetcher, "https://ellipsus.com/read/6Wix2TBqszeWlODJo5mkx4")


async def test_ellipsus_editor_links_are_rejected():
    with pytest.raises(UnsupportedLinkError):
        await fetch_linked_text(fetcher_for({}), "https://ellipsus.com/edit/abc")


async def test_fetch_writefreely_html():
    fetcher = fetcher_for({"paper.wf/sim/post": httpx.Response(200, html=WRITEFREELY_HTML)})
    linked = await fetch_linked_text(fetcher, "https://paper.wf/sim/post.md")
    assert linked.source == "WriteFreely"
    # title (2) + "Перший абзац" (2) + "Чолов'яга друг" (2); script, header and footer are ignored
    assert count_words(linked.text) == 6, linked.text


async def test_writefreely_detected_by_nodeinfo():
    html = WRITEFREELY_HTML.replace('<meta name="generator" content="WriteFreely">', "")
    fetcher = fetcher_for(
        {
            "blog.example/post": httpx.Response(200, html=html),
            "blog.example/.well-known/nodeinfo": httpx.Response(
                200, json={"links": [{"href": "https://blog.example/api/nodeinfo"}]}
            ),
            "blog.example/api/nodeinfo": httpx.Response(
                200, content=json.dumps({"software": {"name": "writefreely"}})
            ),
        }
    )
    linked = await fetch_linked_text(fetcher, "https://blog.example/post")
    assert count_words(linked.text) == 6


async def test_other_sites_are_unsupported():
    fetcher = fetcher_for({"example.com/": httpx.Response(200, html="<html><body>hi</body></html>")})
    with pytest.raises(UnsupportedLinkError):
        await fetch_linked_text(fetcher, "https://example.com/")


async def test_fetch_google_doc():
    doc = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"
    fetcher = fetcher_for(
        {
            f"docs.google.com/document/d/{doc}/export": httpx.Response(
                200, content="﻿Назва\r\nОдин два три".encode(), headers={"content-type": "text/plain; charset=utf-8"}
            )
        }
    )
    linked = await fetch_linked_text(fetcher, f"https://docs.google.com/document/d/{doc}/edit?usp=sharing")
    assert linked.source == "Google Docs" and linked.title == "Назва"
    assert count_words(linked.text) == 4


async def test_google_doc_not_shared():
    doc = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"
    fetcher = fetcher_for(
        {
            f"docs.google.com/document/d/{doc}/export": httpx.Response(
                302, headers={"location": "https://accounts.google.com/ServiceLogin"}
            ),
            "accounts.google.com/ServiceLogin": httpx.Response(200, html="<html>sign in</html>"),
        }
    )
    with pytest.raises(FetchError, match="не відкритий"):
        await fetch_linked_text(fetcher, f"https://docs.google.com/document/u/0/d/{doc}/edit")


async def test_only_telegraph_without_all_sources():
    page = {"ok": True, "result": {"title": "Назва", "content": ["слово"]}}
    fetcher = fetcher_for({"api.telegra.ph/getPage/Nazva-10-13": httpx.Response(200, json=page)})
    linked = await fetch_linked_text(fetcher, "https://telegra.ph/Nazva-10-13", all_sources=False)
    assert linked.source == "Телеграф"
    for url in (
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit",
        "https://ellipsus.com/read/6Wix2TBqszeWlODJo5mkx4",
        "https://paper.wf/sim/post",
    ):
        with pytest.raises(UnsupportedLinkError):
            await fetch_linked_text(fetcher_for({}), url, all_sources=False)
