import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from harness import ALICE, BOB, GROUP, Harness

from writers_bot.botconfig import WebConfig
from writers_bot.web.auth import AuthError, Signer, WebUser, verify_init_data, verify_login_widget

TOKEN = "123:TEST"
WEB = WebConfig(url="https://writers.example.org", secret="s" * 32)


def init_data(user, token=TOKEN, auth_date=None, **extra) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps(user), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def login_fields(user_id, token=TOKEN, auth_date=None) -> dict:
    fields = {"id": str(user_id), "first_name": "Alice", "auth_date": str(auth_date or int(time.time()))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return fields


def test_init_data():
    user, fields = verify_init_data(init_data({"id": 1, "first_name": "Alice"}, start_param="abc"), TOKEN)
    assert user["id"] == 1 and fields["start_param"] == "abc"
    with pytest.raises(AuthError):
        verify_init_data(init_data({"id": 1}, token="other:token"), TOKEN)
    with pytest.raises(AuthError):
        verify_init_data(init_data({"id": 1}, auth_date=int(time.time()) - 2 * 86400), TOKEN)
    tampered = init_data({"id": 1}).replace("%22id%22%3A+1", "%22id%22%3A+2")
    with pytest.raises(AuthError):
        verify_init_data(tampered, TOKEN)


def test_login_widget():
    assert verify_login_widget(login_fields(5), TOKEN)["id"] == 5
    with pytest.raises(AuthError):
        verify_login_widget(login_fields(5, token="x:y"), TOKEN)


def test_session_and_launch_tokens():
    signer = Signer("secret" * 4)
    user = WebUser("telegram", "1", "Alice", "main")
    assert signer.check_session(signer.session(user)) == user
    with pytest.raises(AuthError):
        signer.check_session(signer.session(user, ttl=-1))
    with pytest.raises(AuthError):
        Signer("other" * 4).check_session(signer.session(user))
    token = signer.launch(-1001234567890, 42)
    assert len(token) <= 64 and token.replace("-", "").replace("_", "").isalnum()
    assert signer.check_launch(token) == (-1001234567890, 42)
    with pytest.raises(AuthError):
        signer.check_launch(token[:-2] + "AA")


@pytest.fixture
async def h(tmp_path, clock):
    harness = await Harness.create(tmp_path, clock, web=WEB)
    yield harness
    await harness.close()


@pytest.fixture
async def client(h):
    c = TestClient(TestServer(h.services.web.app))
    await c.start_server()
    yield c
    await c.close()


async def login(client, user) -> dict:
    r = await client.post("/api/session", json={"init_data": init_data(user.to_dict())})
    assert r.status == 200, await r.text()
    return {"Authorization": "Bearer " + (await r.json())["token"]}


async def test_static_and_texts(client):
    r = await client.get("/")
    assert r.status == 200 and "app.js" in await r.text()
    assert "Content-Security-Policy" in r.headers
    texts = await (await client.get("/api/texts")).json()
    assert texts["tab_sprint"] and texts["plural_words"][2] == "слів"
    assert (await client.get("/static/../server.py")).status == 404
    assert (await client.get("/api/me")).status == 401


async def test_the_csp_allows_no_eval_and_no_inline_script(client):
    """Telegram Login by redirect needs neither, nor any other origin: script-src is 'self' and nothing is framed."""
    csp = (await client.get("/")).headers["Content-Security-Policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-eval'" not in script_src and "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in csp
    assert "telegram.org" not in script_src and "frame-src" not in csp
    assert "data-onauth" not in await (await client.get("/static/app.js")).text()


async def test_login_by_redirect_parameters(client):
    """What /?id=..&hash=.. carries is what the widget calls with: a valid hash is a session, a bad or stale one is not."""
    fields = login_fields(5)
    r = await client.post("/api/session", json={"login": fields})
    assert r.status == 200
    token = (await r.json())["token"]
    me = await client.get("/api/me", headers={"Authorization": "Bearer " + token})
    assert me.status == 200 and (await me.json())["name"] == "Alice"
    bad = await client.post("/api/session", json={"login": {**fields, "id": "6"}})  # changed after the hash was made
    assert bad.status == 401
    stale = await client.post("/api/session", json={"login": login_fields(5, auth_date=int(time.time()) - 2 * 86400)})
    assert stale.status == 401
    assert (await client.post("/api/session", json={"login": {"id": "5"}})).status == 401  # no hash
    assert (await client.post("/api/session", json={"login": ["id"]})).status == 401  # not even an object


async def test_sprint_panel(h, client):
    await h.say(ALICE, "/sprint 20 0")
    sprint = h.sprint()
    panel = h.sprint_message().button("Панель")
    launch = panel["url"].split("startapp=")[1]
    bob = await login(client, BOB)
    r = await client.get(f"/api/sprint?launch={launch}", headers=bob)
    data = (await r.json())["sprint"]
    assert data["joined"] is False and data["status"] == "running"
    r = await client.post(f"/api/sprint/{sprint.id}/join", headers=bob, json={})
    assert (await r.json())["sprint"]["joined"] is True
    assert "Bob" in h.sprint_message().text

    r = await client.post("/api/works", headers=bob, json={"name": "Роман"})
    work = await r.json()
    r = await client.post(f"/api/sprint/{sprint.id}/toggle", headers=bob, json={"work_id": work["id"]})
    assert [w["in_sprint"] for w in (await r.json())["sprint"]["works"] if w["name"]] == [True]
    await h.advance(20 * 60)
    r = await client.post(
        f"/api/sprint/{sprint.id}/report", headers=bob, json={"entries": [{"work_id": work["id"], "text": "640"}]}
    )
    assert r.status == 200, await r.text()
    assert "640" in (await r.json())["message"]
    assert "640" in h.board().text


async def test_not_a_member(h, client):
    await h.say(ALICE, "/sprint 20 0")
    launch = h.sprint_message().button("Панель")["url"].split("startapp=")[1]
    h.fake.chat(GROUP).roles[BOB.id] = "left"
    bob = await login(client, BOB)
    r = await client.get(f"/api/sprint?launch={launch}", headers=bob)
    assert r.status == 403


async def test_works_and_progress(h, client):
    alice = await login(client, ALICE)
    r = await client.post("/api/works", headers=alice, json={"name": "Роман"})
    work = await r.json()
    r = await client.patch(f"/api/works/{work['id']}", headers=alice, json={"goal": {"amount": 1000, "period": "day"}})
    assert r.status == 409  # write to the bot privately first
    await h.dm(ALICE, "/start")
    r = await client.patch(
        f"/api/works/{work['id']}", headers=alice, json={"goal": {"amount": 1000, "period": "day"}, "hide_title": True}
    )
    assert (await r.json())["goal"] == {"amount": 1000, "period": "day"}
    r = await client.post("/api/progress", headers=alice, json={"text": "+1200"})
    assert r.status == 200, await r.text()
    assert any("Ціль виконано" in m.text for m in h.dms(ALICE))
    stats = await (await client.get("/api/stats", headers=alice)).json()
    assert stats["today"] == 1200 and stats["streak"] == 1 and stats["works"][0]["goal_done"] == 1200
    bob = await login(client, BOB)
    assert (await client.delete(f"/api/works/{work['id']}", headers=bob)).status == 404
    assert (await client.delete(f"/api/works/{work['id']}", headers=alice)).status == 200


async def test_panel_for_a_sprint_in_a_private_chat(h, client):
    await h.dm(ALICE, "/sprint 10 0")
    sprint = h.sprint()
    token = h.services.web.signer.launch(ALICE.id, sprint.id)
    alice = await login(client, ALICE)
    r = await client.get(f"/api/sprint?launch={token}", headers=alice)
    assert r.status == 200 and (await r.json())["sprint"]["joined"] is True
