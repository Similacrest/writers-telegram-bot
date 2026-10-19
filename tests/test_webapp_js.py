"""Runs the web app's JavaScript in QuickJS with a tiny DOM, fed real answers from the server, and checks
that every tab renders without errors and shows texts from the locale (not raw keys)."""

import base64
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from harness import ADMIN, ALICE, DAN, GROUP, Harness
from test_web import WEB, init_data

from writers_bot.core.i18n import texts

quickjs = pytest.importorskip("quickjs")

APP_JS = Path(__file__).parent.parent / "src" / "writers_bot" / "web" / "static" / "app.js"

SHIM = r"""
var Node = function () {};
function makeNode(tag) {
  var n = Object.create(Node.prototype);
  n.tag = tag; n.children = []; n.attrs = {}; n.listeners = {}; n.className = ""; n.textContent = "";
  n.hidden = false; n.dataset = {}; n.value = "";
  n.append = function () { for (var i = 0; i < arguments.length; i++) this.children.push(arguments[i]); };
  n.prepend = function () { for (var i = arguments.length - 1; i >= 0; i--) this.children.unshift(arguments[i]); };
  n.replaceChildren = function () { this.children = Array.prototype.slice.call(arguments); };
  n.setAttribute = function (k, v) {
    this.attrs[k] = v;
    if (k.indexOf("data-") === 0) this.dataset[k.slice(5).replace(/-(\w)/g, function (m, a) { return a.toUpperCase(); })] = v;
    if (k === "value") this.value = v;
  };
  n.addEventListener = function (e, f) { this.listeners[e] = f; };
  n.remove = function () {}; n.click = function () {};
  return n;
}
var elements = {};
var document = {
  createElement: makeNode,
  createTextNode: function (t) { var n = Object.create(Node.prototype); n.text = t; return n; },
  createElementNS: function (ns, t) { return makeNode(t); },
  getElementById: function (id) { return elements[id] || (elements[id] = makeNode(id)); },
  querySelectorAll: function () { return []; },
  documentElement: makeNode("html"), body: makeNode("body"), activeElement: null, title: "",
};
var window = {};
var location = { search: "", hash: "", pathname: "/", origin: "https://w.example" };
var HISTORY = [];
var history = { replaceState: function (a, b, url) { HISTORY.push(url); } };
var sessionStorage = { _: {}, getItem: function (k) { return k in this._ ? this._[k] : null; },
  setItem: function (k, v) { this._[k] = v; }, removeItem: function (k) { delete this._[k]; } };
var setTimeout = function () { return 0; }, clearTimeout = function () {};
var setInterval = function () { return 1; }, clearInterval = function () {};
var confirm = function () { return true; };
function URLSearchParams(s) {
  this.map = {};
  (s || "").replace(/^[?#]/, "").split("&").forEach(function (p) {
    if (!p) return; var kv = p.split("="); this.map[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || "");
  }, this);
  this.get = function (k) { return k in this.map ? this.map[k] : null; };
  this.has = function (k) { return k in this.map; };
  this["delete"] = function (k) { delete this.map[k]; };
  this.toString = function () {
    return Object.keys(this.map).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(this.map[k]); }, this).join("&");
  };
}
var ERRORS = [];
var RESPONSES = {};
var CALLS = [];
function fetch(path, opts) {
  var method = (opts && opts.method) || "GET";
  CALLS.push(method + " " + path);
  var key = method + " " + path.split("?")[0];
  var r = RESPONSES[key] || RESPONSES[method + " *"] || { status: 404, body: { error: "missing " + key } };
  return Promise.resolve({ ok: r.status < 400, status: r.status, json: function () { return Promise.resolve(r.body); },
    blob: function () { return Promise.resolve({}); } });
}
var URL = { createObjectURL: function () { return "blob:"; }, revokeObjectURL: function () {} };
"""


def walk_text(node, out):
    if isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            out.append(node["text"])
        for child in node.get("children", []):
            walk_text(child, out)
    return out


class Page:
    def __init__(self, responses: dict):
        self.ctx = quickjs.Context()
        self.ctx.eval(SHIM)
        self.ctx.eval(f"RESPONSES = {json.dumps(responses)};")
        source = APP_JS.read_text(encoding="utf-8")
        # toast() reports errors: record them
        self.ctx.eval(
            source.replace("start().catch", "void 0; (function(){})").replace(
                "function toast(text, error) {", "function toast(text, error) { if (error) ERRORS.push(String(text));"
            )
        )

    def run(self, script: str):
        self.ctx.eval(
            f"(async function(){{ try {{ {script} }} catch (e) {{ ERRORS.push(String(e) + ' ' + e.stack); }} }})()"
        )
        for _ in range(2000):
            if not self.ctx.execute_pending_job():
                break

    def errors(self) -> list[str]:
        return json.loads(self.ctx.eval("JSON.stringify(ERRORS)"))

    def view_text(self) -> str:
        data = json.loads(self.ctx.eval("JSON.stringify(document.getElementById('view'))"))
        return " ".join(walk_text(data, []))


LOGIN_USER = {"id": 7, "first_name": "Alice", "auth_date": 1700000000, "hash": "abc"}


def tg_auth_result(user: dict, padded: bool = False) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(user, ensure_ascii=False).encode()).decode()
    return raw if padded else raw.rstrip("=")


ATOB = r"""
var atob = function (s) {
  var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", out = "", bits = 0, n = 0;
  s.replace(/=+$/, "").split("").forEach(function (c) {
    bits = (bits << 6) | chars.indexOf(c); n += 6;
    if (n >= 8) { n -= 8; out += String.fromCharCode((bits >> n) & 255); }
  });
  return out;
};
"""


def login_page(me_status: int = 200) -> Page:
    page = Page(
        {
            "GET /api/texts": {"status": 200, "body": {"title": "t", "login_intro": "Увійдіть", "login_telegram": "TG"}},
            "GET /api/config": {
                "status": 200,
                "body": {"bots": [{"name": "main", "username": "b", "id": 123}], "discord": False},
            },
            "POST /api/session": {"status": 200, "body": {"token": "T0KEN", "name": "Alice"}},
            "GET /api/me": {"status": me_status, "body": {"name": "Alice", "tabs": []}},
        }
    )
    page.ctx.eval(ATOB)
    page.ctx.eval(
        "var SENT = null; var _fetch = fetch; fetch = function (p, o) { if (o && o.body) SENT = JSON.parse(o.body); return _fetch(p, o); };"
    )
    return page


def test_telegram_login_is_a_top_level_redirect_not_the_widget():
    """The widget's iframe loses the popup's login where cookies are partitioned (Firefox), so the page links to
    oauth.telegram.org and takes the user from the #tgAuthResult fragment it comes back with."""
    source = APP_JS.read_text(encoding="utf-8")
    assert "telegram-widget" not in source and "eval(" not in source
    page = login_page()
    page.run("await start();")
    link = json.loads(page.ctx.eval("JSON.stringify(document.getElementById('view'))"))
    hrefs = []

    def find(node):
        if isinstance(node, dict):
            if node.get("attrs", {}).get("href"):
                hrefs.append(node["attrs"]["href"])
            for child in node.get("children", []):
                find(child)

    find(link)
    assert len(hrefs) == 1 and hrefs[0].startswith("https://oauth.telegram.org/auth?bot_id=123&")
    assert "return_to=" in hrefs[0] and "origin=" in hrefs[0]


@pytest.mark.parametrize("padded", [False, True])
def test_the_fragment_is_the_login(padded):
    page = login_page()
    page.run(f"location.hash = {json.dumps('#tgAuthResult=' + tg_auth_result(LOGIN_USER, padded))}; await start();")
    assert json.loads(page.ctx.eval("JSON.stringify(SENT)")) == {"login": LOGIN_USER}
    assert page.ctx.eval("HISTORY[0]") == "/"  # the fragment is gone from the address bar
    assert page.ctx.eval("sessionStorage.getItem('token')") == "T0KEN"


def test_a_broken_fragment_shows_the_login_again():
    page = login_page(me_status=401)
    page.run("location.hash = '#tgAuthResult=!!not-base64!!'; await start();")
    assert page.ctx.eval("SENT") is None and page.errors() == []
    assert page.ctx.eval("HISTORY[0]") == "/"


@pytest.fixture
async def server(tmp_path, clock):
    h = await Harness.create(
        tmp_path,
        clock,
        {"replies": {}, "prompts": {"source": "db"}, "sprints": {"reports": True, "works": True, "percent": True}},
        web=WEB,
    )
    client = TestClient(TestServer(h.services.web.app))
    await client.start_server()
    yield h, client
    await client.close()
    await h.close()


async def collect(client, user, paths: list[tuple[str, str]], extra=None) -> dict:
    r = await client.post("/api/session", json={"init_data": init_data(user.to_dict())})
    token = (await r.json())["token"]
    headers = {"Authorization": "Bearer " + token}
    out = {
        "GET /api/texts": {"status": 200, "body": await (await client.get("/api/texts")).json()},
        "GET /api/config": {"status": 200, "body": await (await client.get("/api/config")).json()},
    }
    for method, path in paths:
        r = await client.request(method, path, headers=headers)
        out[f"{method} {path.split('?')[0]}"] = {"status": r.status, "body": await r.json()}
    return out | (extra or {}), token


async def test_every_tab_renders(server):
    h, client = server
    await h.say(ALICE, "/sprint 20 0")
    await h.dm(ALICE, "/start")
    await h.say(ALICE, "/progress +400")
    responses, token = await collect(
        client,
        ALICE,
        [("GET", "/api/me"), ("GET", "/api/sprint"), ("GET", "/api/works"), ("GET", "/api/stats")],
    )
    page = Page(responses)
    page.run(f"sessionStorage.setItem('token', {json.dumps(token)}); await start();")
    assert page.errors() == []
    for tab in ("sprint", "works", "progress", "settings"):
        page.run(f"await TABS[{json.dumps(tab)}](document.getElementById('view'));")
        assert page.errors() == [], tab
        text = page.view_text()
        assert text, tab
        unknown = [
            w
            for w in text.split()
            if "_" in w and w.islower() and w in {k[4:] for k in texts() if k.startswith("web.")}
        ]
        assert not unknown, (tab, unknown)  # a raw key means a missing text


async def test_members_dont_get_admin_tabs(server):
    _h, client = server
    responses, _ = await collect(client, ALICE, [("GET", "/api/me")])
    assert responses["GET /api/me"]["body"]["tabs"] == ["sprint", "works", "progress", "settings"]


async def test_admin_tabs_render(server):
    h, client = server
    h.fake.chats[GROUP].roles[DAN.id] = "administrator"
    await h.say(DAN, "hi")
    await h.say(DAN, "/addprompt fantasy дракон")
    await h.say(ALICE, "/suggest fantasy гном")
    responses, token = await collect(
        client,
        DAN,
        [
            ("GET", "/api/me"),
            ("GET", "/api/sprint"),
            ("GET", "/api/chats"),
            ("GET", f"/api/chat/{GROUP}/settings"),
            ("GET", f"/api/chat/{GROUP}/replies"),
            ("GET", f"/api/chat/{GROUP}/prompts"),
        ],
    )
    page = Page(responses)
    page.run(f"sessionStorage.setItem('token', {json.dumps(token)}); await start();")
    for tab in ("chat", "prompts"):
        page.run(f"await TABS[{json.dumps(tab)}](document.getElementById('view'));")
        assert page.errors() == [], tab
    text = page.view_text()
    assert "дракон" in text and "гном" in text


async def test_errors_show_the_server_text(server):
    _h, client = server
    responses, token = await collect(client, ALICE, [("GET", "/api/me"), ("GET", "/api/sprint")])
    responses["GET /api/stats"] = {"status": 500, "body": {"error": "Щось пішло не так"}}
    page = Page(responses)
    page.run(
        f"sessionStorage.setItem('token', {json.dumps(token)}); await start(); await TABS.progress(document.getElementById('view'));"
    )
    assert page.errors() == ["Щось пішло не так"]


async def test_communities_tab_for_super_admins(server):
    h, client = server
    await h.say(DAN, "hi")  # the group is known
    responses, token = await collect(
        client, ADMIN, [("GET", "/api/me"), ("GET", "/api/sprint"), ("GET", "/api/communities")]
    )
    assert "communities" in responses["GET /api/me"]["body"]["tabs"]
    page = Page(responses)
    page.run(f"sessionStorage.setItem('token', {json.dumps(token)}); await start();")
    page.run("await TABS.communities(document.getElementById('view'));")
    assert page.errors() == []
    assert "Нова спільнота" in page.view_text()
    members, _ = await collect(client, ALICE, [("GET", "/api/me"), ("GET", "/api/communities")])
    assert "communities" not in members["GET /api/me"]["body"]["tabs"]
    assert members["GET /api/communities"]["status"] == 403
