"""The owner's confirmation: a session asks, the owner confirms with a passkey on the bridge's page, the session checks.

Design and reasoning: docs/owner-confirmation.md, ADR-CV-106.
"""

import asyncio
import contextlib
import json

import httpx
import pytest
from fakes import FakeClaude
from softkey import SoftKey

from claude_voice import api, confirm, passkeys, procs, transcripts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-confirm-tests-x"
PUBLIC = "http://127.0.0.1:8811"
ANTHROPIC = "160.79.106.179"  # where the Claude app's calls come from
PHONE = "10.40.70.5"  # the owner's phone, on the VPN


# -- the requests ----------------------------------------------------------------------


@pytest.fixture
def requests(tmp_path):
    now = [1_800_000_000.0]
    return confirm.Confirmations(Store(tmp_path / "b.db"), clock=lambda: now[0]), now


def test_a_request_is_confirmed_once_and_reported_once(requests):
    book, _ = requests
    asked = book.ask("sid-1", "desk", "Allow Bash(git push:*)")
    code = asked["code"]
    assert book.check(code, "sid-1")["status"] == "pending"
    book.decide(asked["link"], confirmed=True)
    first = book.check(code, "sid-1")
    assert first["status"] == "confirmed" and first["summary"] == "Allow Bash(git push:*)" and first["confirmed_at"]
    # The result says on its own how it was verified, for a reader who sees nothing else.
    proof = first["proof"]
    assert "passkey" in proof and "verified that signature itself" in proof
    assert code in proof and "one carrying-out" in proof and "could not change it" in proof
    assert "not a message from another session" in proof
    assert "permission prompt shown at the PC" in proof  # and what it does not answer
    assert book.check(code, "sid-1")["status"] == "used"


def test_a_request_is_answered_once_while_it_is_pending(requests):
    book, _ = requests
    asked = book.ask("sid-1", "desk", "Allow it")
    book.decide(asked["link"], confirmed=False)
    assert book.check(asked["code"], "sid-1")["status"] == "declined"
    with pytest.raises(ValueError, match="declined"):
        book.decide(asked["link"], confirmed=True)


def test_only_the_session_that_asked_can_check_its_request(requests):
    book, _ = requests
    asked = book.ask("sid-1", "desk", "Allow Bash(git push:*)")
    book.decide(asked["link"], confirmed=True)
    assert book.check(asked["code"], "sid-2")["status"] == "unknown"
    assert book.check(asked["code"], "sid-1")["status"] == "confirmed"


def test_a_request_expires_after_fifteen_minutes(requests):
    book, now = requests
    asked = book.ask("sid-1", "desk", "Allow Bash(git push:*)")
    now[0] += 15 * 60 + 1
    assert book.check(asked["code"], "sid-1")["status"] == "expired"
    with pytest.raises(ValueError, match="expired"):
        book.decide(asked["link"], confirmed=True)


def test_an_empty_or_long_summary_is_refused(requests):
    book, _ = requests
    with pytest.raises(ValueError):
        book.ask("sid-1", "desk", "   ")
    with pytest.raises(ValueError, match="300"):
        book.ask("sid-1", "desk", "x" * 301)


def test_requests_outlive_a_bridge_restart(tmp_path):
    asked = confirm.Confirmations(Store(tmp_path / "b.db")).ask("sid-1", "desk", "Allow it")
    again = confirm.Confirmations(Store(tmp_path / "b.db"))
    assert again.decide(asked["link"], confirmed=True)["code"] == asked["code"]


def test_the_confirm_page_answers_only_from_a_private_address():
    assert api.from_private_network(PHONE) and api.from_private_network("127.0.0.1")
    assert api.from_private_network("100.101.102.103")  # a tailnet, upstream's setup
    assert not api.from_private_network(ANTHROPIC)  # nor any model's web fetch through Anthropic
    assert not api.from_private_network("8.8.8.8") and not api.from_private_network("nonsense")


# -- which session asked ---------------------------------------------------------------


def transcript(projects, cwd, sid, tool_use_id):
    folder = projects / transcripts.project_key_for_directory(str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    line = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_use_id, "name": "x"}]}}
    (folder / f"{sid}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def test_a_request_belongs_to_the_session_whose_transcript_holds_the_call(tmp_path):
    projects = tmp_path / "projects"
    transcript(projects, tmp_path / "a", "sid-a", "toolu_A")
    transcript(projects, tmp_path / "b", "sid-b", "toolu_B")
    sessions = [{"sessionId": "sid-a", "cwd": str(tmp_path / "a")}, {"sessionId": "sid-b", "cwd": str(tmp_path / "b")}]
    assert transcripts.session_of_tool_use("toolu_B", sessions, projects) == "sid-b"
    assert transcripts.session_of_tool_use("toolu_C", sessions, projects) is None


# -- through the bridge, as the callers reach it -----------------------------------------


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(procs, "alive", lambda pid: True)
    home, live, projects = tmp_path / "home", tmp_path / "live", tmp_path / "projects"
    home.mkdir()
    live.mkdir()
    (home / "app").mkdir()
    entry = {"pid": 7, "sessionId": "sid-7", "cwd": str(home / "app"), "name": "desk", "status": "idle"}
    (live / "7.json").write_text(json.dumps(entry), encoding="utf-8")
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, PUBLIC, static_token=SECRET)
    sent: list[tuple[str, str]] = []

    async def deliver(name, text):
        sent.append((name, text))
        return {"delivered": True}

    m = SessionManager(store, project_root=home, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver,
        projects_dir=projects,
        claude_config=tmp_path / "claude.json",
    )
    return {"server": server, "cwd": home / "app", "projects": projects, "sent": sent, "store": store, "m": m}


@contextlib.asynccontextmanager
async def reached(server):
    """The bridge over HTTP, as the Claude app, Claude Code and the owner's phone reach it."""
    transport = httpx.ASGITransport(app=build_app(server), client=("127.0.0.1", 4321))
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url=PUBLIC) as c,
    ):
        yield c


async def call(app, name, args, address=ANTHROPIC, tool_use_id=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    # The sessionless protocol, as the Claude app and Claude Code call today.
    body["params"]["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
    }
    if tool_use_id:
        body["params"]["_meta"]["claudecode/toolUseId"] = tool_use_id
    sent = {
        "Authorization": f"Bearer {SECRET}",
        "Accept": "application/json, text/event-stream",
        "x-forwarded-for": address,
        "mcp-protocol-version": "2026-07-28",
        "mcp-method": "tools/call",
        "mcp-name": name,
    }
    r = await app.post("/mcp", json=body, headers=sent)
    text = r.text
    data = json.loads(text[text.index("{") :].splitlines()[0]) if "data:" in text else r.json()
    assert "result" in data, (r.status_code, text[:400])
    return data["result"]


def outcome(result):
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def path_of(link):
    return link.removeprefix(PUBLIC)


async def owner_registers(app, store):
    """At the PC the owner makes a one-time link; on the phone, they add a passkey with it."""
    token = passkeys.for_url(store, PUBLIC).new_registration()
    key = SoftKey("127.0.0.1", PUBLIC)
    page = f"/confirm/passkey/{token}"
    phone = {"x-forwarded-for": PHONE}
    assert (await app.get(page, headers=phone)).status_code == 200
    options = (await app.post(f"{page}/options", headers=phone)).json()
    body = {"name": "phone", "credential": key.register(options)}
    added = await app.post(f"{page}/register", json=body, headers=phone)
    assert added.status_code == 200, added.text
    return key


async def owner_confirms(app, link, key, address=PHONE):
    page = path_of(link)
    options = (await app.post(f"{page}/challenge", headers={"x-forwarded-for": PHONE})).json()
    return await app.post(f"{page}/confirm", json=key.assert_(options), headers={"x-forwarded-for": address})


async def test_a_session_asks_the_owner_confirms_with_a_passkey_and_the_session_checks(bridge):
    async with reached(bridge["server"]) as app:
        key = await owner_registers(app, bridge["store"])
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        ask = {"summary": "Allow Bash(git push:*)"}
        asked = outcome(await call(app, "confirm_ask", ask, tool_use_id="toolu_ask"))
        code = asked["code"]
        assert asked["running"]["design"].endswith("/docs/owner-confirmation.md")
        assert 'at the PC, ask them to type their OK in the session "desk"' in asked["tell_the_relay"]
        # The session hands the relay the link itself, so no relay has to turn a code into one.
        assert asked["link"] in asked["tell_the_relay"] and "confirm_with_owner" not in asked["tell_the_relay"]
        assert '"Allow Bash(git push:*)"' in asked["tell_the_relay"]  # so the relay can say what it asks

        handed = outcome(await call(app, "confirm_with_owner", {"code": code}))
        assert handed["link"] == asked["link"]
        assert handed["summary"] == "Allow Bash(git push:*)" and handed["from"] == "desk"
        page = await app.get(path_of(handed["link"]), headers={"x-forwarded-for": PHONE})
        assert page.status_code == 200 and "Allow Bash(git push:*)" in page.text
        assert "Why you can trust this page" in page.text and "only this request, once" in page.text
        # What the owner acts on comes first; the explanation sits below, apart.
        text = page.text
        assert text.index("id='yes'") < text.index("class='aside'") < text.index("Why you can trust")
        assert "window.close()" in text and "close this tab" in text

        # A passkey that was never registered confirms nothing.
        assert (await owner_confirms(app, handed["link"], SoftKey("127.0.0.1", PUBLIC))).status_code == 400
        done = await owner_confirms(app, handed["link"], key)
        assert done.status_code == 200, done.text
        sent = bridge["sent"]
        for _ in range(50):  # the bridge tells the session in the background
            if sent:
                break
            await asyncio.sleep(0.01)
        assert sent and sent[-1][0] == "desk" and code in sent[-1][1] and "confirm_check" in sent[-1][1]

        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_check")
        checked = outcome(await call(app, "confirm_check", {"code": code}, tool_use_id="toolu_check"))
    assert checked["status"] == "confirmed" and checked["summary"] == "Allow Bash(git push:*)"


async def test_the_page_refuses_whoever_reaches_it_from_the_internet(bridge):
    async with reached(bridge["server"]) as app:
        key = await owner_registers(app, bridge["store"])
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        code = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_ask"))["code"]
        link = outcome(await call(app, "confirm_with_owner", {"code": code}))["link"]
        assert (await app.get(path_of(link), headers={"x-forwarded-for": ANTHROPIC})).status_code == 403
        assert (await owner_confirms(app, link, key, address=ANTHROPIC)).status_code == 403


async def test_a_folder_opens_only_once_the_owner_confirms_it(bridge, tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    async with reached(bridge["server"]) as app:
        key = await owner_registers(app, bridge["store"])
        asked = outcome(await call(app, "request_access", {"folder": str(folder)}))
        assert asked["status"] == "waiting_for_owner"
        assert bridge["m"].access.state(folder) == "not_open"
        done = await owner_confirms(app, asked["link"], key)
        assert done.status_code == 200, done.text
    assert bridge["m"].access.state(folder) == "open"


async def test_a_caller_no_session_on_this_pc_made_cannot_ask(bridge, monkeypatch):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        refused = await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_nowhere")
    assert refused.get("isError")
