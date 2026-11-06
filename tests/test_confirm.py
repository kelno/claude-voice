"""The owner's confirmation: a session asks, the owner taps the card in a voice chat, the session checks.

Design and reasoning: docs/owner-confirmation.md.
"""

import contextlib
import json

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import confirm, procs, transcripts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-confirm-tests-x"
ANTHROPIC = "160.79.106.179"  # inside Anthropic's published outbound range
VOICE = {"x-anthropic-client": "ClaudeAI"}


# -- who may confirm -------------------------------------------------------------------


def test_only_a_voice_chat_through_anthropic_may_confirm():
    assert confirm.from_a_voice_chat(confirm.Caller("ClaudeAI", ANTHROPIC))
    assert confirm.from_a_voice_chat(confirm.Caller("ClaudeAI", "153.61.210.4"))
    # A text chat's approvals are answered by a classifier, not the owner's tap.
    assert not confirm.from_a_voice_chat(confirm.Caller("Cowork", ANTHROPIC))
    assert not confirm.from_a_voice_chat(confirm.Caller("ClaudeCode", ANTHROPIC))
    # Reaching the bridge directly, a caller could send any label.
    assert not confirm.from_a_voice_chat(confirm.Caller("ClaudeAI", "10.40.70.1"))
    assert not confirm.from_a_voice_chat(confirm.Caller("ClaudeAI", "127.0.0.1"))
    assert not confirm.from_a_voice_chat(confirm.Caller("ClaudeAI", "not an address"))
    assert not confirm.from_a_voice_chat(confirm.Caller(None, ANTHROPIC))


def test_the_summary_must_match_except_for_spaces():
    asked = "Add the rule 'push after every commit' to CLAUDE.md."
    assert confirm.same_words(asked, "  Add the rule 'push after every\ncommit'  to CLAUDE.md.\n")
    assert not confirm.same_words(asked, "add the rule 'push after every commit' to CLAUDE.md.")
    assert not confirm.same_words(asked, "Add the rule 'push after every commit' to CLAUDE.md")
    assert not confirm.same_words("Allow Bash(git push:*)", "Allow Bash(git push*)")


# -- the requests ----------------------------------------------------------------------


@pytest.fixture
def requests(tmp_path):
    now = [1_800_000_000.0]
    return confirm.Confirmations(Store(tmp_path / "b.db"), clock=lambda: now[0]), now


def test_a_request_is_confirmed_once_and_reported_once(requests):
    book, _ = requests
    asked = book.ask("sid-1", "Allow Bash(git push:*)")
    code = asked["code"]
    assert book.check(code, "sid-1")["status"] == "pending"
    assert book.confirm(code, "Allow Bash(git push:*)")["status"] == "confirmed"
    first = book.check(code, "sid-1")
    assert first["status"] == "confirmed" and first["summary"] == "Allow Bash(git push:*)" and first["confirmed_at"]
    # The app can be set to skip the card, which the bridge can't see; every confirmation says so.
    assert "always allow" in first["limit"]
    assert book.check(code, "sid-1")["status"] == "used"
    with pytest.raises(ValueError, match="already"):
        book.confirm(code, "Allow Bash(git push:*)")


def test_only_the_session_that_asked_can_check_its_request(requests):
    book, _ = requests
    code = book.ask("sid-1", "Allow Bash(git push:*)")["code"]
    book.confirm(code, "Allow Bash(git push:*)")
    assert book.check(code, "sid-2")["status"] == "unknown"
    assert book.check(code, "sid-1")["status"] == "confirmed"


def test_a_request_expires_after_fifteen_minutes(requests):
    book, now = requests
    code = book.ask("sid-1", "Allow Bash(git push:*)")["code"]
    now[0] += 15 * 60 + 1
    assert book.check(code, "sid-1")["status"] == "expired"
    with pytest.raises(ValueError, match="expired"):
        book.confirm(code, "Allow Bash(git push:*)")


def test_a_confirmation_with_other_words_is_refused(requests):
    book, _ = requests
    code = book.ask("sid-1", "Allow Bash(git push:*)")["code"]
    with pytest.raises(ValueError, match="exactly"):
        book.confirm(code, "Allow Bash(git push*)")
    assert book.check(code, "sid-1")["status"] == "pending"


def test_an_empty_or_long_summary_is_refused(requests):
    book, _ = requests
    with pytest.raises(ValueError):
        book.ask("sid-1", "   ")
    with pytest.raises(ValueError, match="300"):
        book.ask("sid-1", "x" * 301)


def test_requests_outlive_a_bridge_restart(tmp_path):
    code = confirm.Confirmations(Store(tmp_path / "b.db")).ask("sid-1", "Allow it")["code"]
    again = confirm.Confirmations(Store(tmp_path / "b.db"))
    assert again.confirm(code, "Allow it")["status"] == "confirmed"


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
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET)
    sent: list[tuple[str, str]] = []

    async def deliver(name, text):
        sent.append((name, text))
        return {"delivered": True}

    m = SessionManager(store, project_root=home, client_factory=FakeClaude())
    server = build_server(
        m, oauth=oauth, conversations=lambda **k: [], live_dir=live, deliver=deliver, projects_dir=projects
    )
    return server, home / "app", projects, sent


@contextlib.asynccontextmanager
async def reached(server):
    """The bridge over HTTP, as the Claude app and Claude Code reach it."""
    transport = httpx.ASGITransport(app=build_app(server), client=("127.0.0.1", 4321))
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8811") as c,
    ):
        yield c


async def call(app, name, args, headers=None, address=ANTHROPIC, tool_use_id=None):
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
        **(headers or {}),
    }
    r = await app.post("/mcp", json=body, headers=sent)
    text = r.text
    data = json.loads(text[text.index("{") :].splitlines()[0]) if "data:" in text else r.json()
    assert "result" in data, (r.status_code, text[:400])
    return data["result"]


def outcome(result):
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


async def test_a_session_asks_the_owner_confirms_in_voice_and_the_session_checks(bridge):
    server, cwd, projects, sent = bridge
    async with reached(server) as app:
        await asks_confirms_and_checks(app, cwd, projects, sent)


async def asks_confirms_and_checks(app, cwd, projects, sent):
    transcript(projects, cwd, "sid-7", "toolu_ask")
    asked = outcome(await call(app, "confirm_ask", {"summary": "Allow Bash(git push:*)"}, tool_use_id="toolu_ask"))
    code = asked["code"]
    assert asked["running"]["design"].endswith("/docs/owner-confirmation.md")
    assert "«Allow Bash(git push:*)»" in asked["tell_the_relay"]

    args = {"owner_approves": "Allow Bash(git push:*)", "code": code}
    from_text = await call(app, "confirm_with_owner", args, headers={"x-anthropic-client": "Cowork"})
    assert from_text.get("isError")
    done = outcome(await call(app, "confirm_with_owner", args, headers=VOICE))
    assert done["status"] == "confirmed"
    assert sent and sent[-1][0] == "desk" and code in sent[-1][1] and "confirm_check" in sent[-1][1]

    transcript(projects, cwd, "sid-7", "toolu_check")
    checked = outcome(await call(app, "confirm_check", {"code": code}, tool_use_id="toolu_check"))
    assert checked["status"] == "confirmed" and checked["summary"] == "Allow Bash(git push:*)"


async def test_a_caller_no_session_on_this_pc_made_cannot_ask(bridge, monkeypatch):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    server, *_ = bridge
    async with reached(server) as app:
        refused = await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_nowhere")
    assert refused.get("isError")
