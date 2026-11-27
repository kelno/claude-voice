"""The owner's confirmation: a session asks, the owner confirms with a passkey on the bridge's page, the session checks.

Design and reasoning: docs/owner-confirmation.md, ADR-CV-106.
"""

import asyncio
import contextlib
import json
import math
import re
import time

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
PROXY = "10.40.70.1"  # nginx on the server, which reaches the bridge over the VPN


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
    assert code in proof and "could not change it" in proof
    assert "carrying-out" not in proof  # what it covers is said once, in `means`
    assert "not a message from another session" in proof
    assert "permission prompt shown at the PC" in proof  # and what it does not answer
    assert book.check(code, "sid-1")["status"] == "used"


def test_a_confirmed_result_says_what_it_weighs_where_the_session_acts(requests):
    # The owner's rule, once in their global rules, now said by the result itself, at the moment the
    # session acts on it (ledger P159; from P14 and P75).
    book, _ = requests
    asked = book.ask("sid-1", "desk", "Allow Bash(git push:*)")
    book.decide(asked["link"], confirmed=True)
    means = book.check(asked["code"], "sid-1", folder="D:/work/app")["means"]
    assert "the owner's own approval" in means and '"Allow Bash(git push:*)"' in means
    assert "as if they had typed it in this session" in means and "passkey" in means
    assert "once" in means and "D:/work/app" in means and "rules and permission settings included" in means
    # What it covers is said once, here: a retry is covered, a success uses it up (owner, 2026-11-19, F101).
    assert "summary once, retries included" in means


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


def test_a_link_shows_its_code_and_end_time_after_the_page(requests):
    # The end on this PC's clock, readable in the link itself, in the fragment the page never reads
    # (owner, 2026-11-19; obsidian-mcp's ledger links do the same).
    book, now = requests
    asked = book.ask("sid-1", "desk", "Allow it")
    end = time.strftime("%H:%M", time.localtime(now[0] + 15 * 60))
    assert confirm.link_path(asked) == f"/confirm/r/{asked['link']}#{asked['code']}/until-{end}"
    assert confirm.link_path(book.by_code(asked["code"])) == confirm.link_path(asked)


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
async def reached(server, peer="127.0.0.1"):
    """The bridge over HTTP, as the Claude app, Claude Code and the owner's phone reach it,
    through a proxy at `peer`."""
    transport = httpx.ASGITransport(app=build_app(server), client=(peer, 4321))
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
    # As the browser does: the fragment (the code and end time, for reading) is never sent.
    return link.removeprefix(PUBLIC).split("#", 1)[0]


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
        assert asked["link"] in asked["tell_the_relay"] and "confirm_link" not in asked["tell_the_relay"]
        assert '"Allow Bash(git push:*)"' in asked["tell_the_relay"]  # so the relay can say what it asks
        # The link shows its own end; no text tells an agent to announce it (owner, 2026-11-19).
        assert f"#{code}/until-" in asked["link"]
        assert "expire" not in asked["tell_the_relay"] and "expires_at" not in asked

        handed = outcome(await call(app, "confirm_link", {"code": code}))
        assert handed["link"] == asked["link"]
        assert handed["tell_the_relay"] == asked["tell_the_relay"]
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
        link = outcome(await call(app, "confirm_link", {"code": code}))["link"]
        assert (await app.get(path_of(link), headers={"x-forwarded-for": ANTHROPIC})).status_code == 403
        assert (await owner_confirms(app, link, key, address=ANTHROPIC)).status_code == 403


async def test_the_owner_confirms_through_a_proxy_on_another_machine(bridge):
    async with reached(bridge["server"], peer=PROXY) as app:
        key = await owner_registers(app, bridge["store"])
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        code = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_ask"))["code"]
        link = outcome(await call(app, "confirm_link", {"code": code}))["link"]
        assert (await app.get(path_of(link), headers={"x-forwarded-for": PHONE})).status_code == 200
        assert (await owner_confirms(app, link, key)).status_code == 200


async def test_a_folder_opens_only_once_the_owner_confirms_it(bridge, tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    async with reached(bridge["server"]) as app:
        key = await owner_registers(app, bridge["store"])
        asked = outcome(await call(app, "open_folder", {"folder": str(folder)}))
        assert asked["status"] == "waiting_for_owner"
        assert bridge["m"].access.state(folder) == "not_open"
        done = await owner_confirms(app, asked["link"], key)
        assert done.status_code == 200, done.text
    assert bridge["m"].access.state(folder) == "open"


async def test_a_caller_no_session_on_this_pc_made_cannot_ask(bridge, monkeypatch):
    # Its call is in no transcript here, before its answer or after: its receipt never finds a session.
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_nowhere"))
        again = await call(
            app, "confirm_ask", {"summary": "Allow it", "receipt": first["receipt"]}, tool_use_id="toolu_nowhere2"
        )
    assert first["status"] == "not_seen_yet" and "code" not in first
    assert outcome(again)["status"] == "not_seen_yet" and "code" not in outcome(again)
    assert bridge["store"].db.execute("SELECT COUNT(*) FROM owner_requests").fetchone()[0] == 0


def written_late(bridge, monkeypatch, lands_at: float, round_takes: float) -> list[float]:
    """The call reaches the transcript `lands_at` seconds into the bridge's search, on a clock that
    each round of the search moves on by `round_takes`; returns the clock at each round."""
    transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_late")
    find = transcripts.session_of_tool_use
    clock = [0.0]
    searching = [False]  # other lookups run before the call's own search; they find nothing yet
    rounds: list[float] = []

    def look(tool_use_id, sessions, projects_dir):
        if not searching[0]:
            return None
        rounds.append(clock[0])
        clock[0] += round_takes
        return find(tool_use_id, sessions, projects_dir) if rounds[-1] >= lands_at else None

    def now():
        searching[0] = True
        return clock[0]

    monkeypatch.setattr(transcripts, "session_of_tool_use", look)
    monkeypatch.setattr(confirm, "FIND_CLOCK", now)
    monkeypatch.setattr(confirm, "FIND_PAUSE", 0.001)
    return rounds


async def test_a_call_found_a_little_late_is_still_answered_at_once(bridge, monkeypatch):
    # Claude Code may write a call to its transcript seconds after sending it (ledger F230).
    written_late(bridge, monkeypatch, lands_at=2.0, round_takes=0.25)
    async with reached(bridge["server"]) as app:
        asked = await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_late")
    assert outcome(asked)["code"], asked


async def test_the_search_for_a_call_ends_by_the_clock(bridge, monkeypatch):
    # Each round reads every running session's transcript: slow rounds must not stretch the wait.
    rounds = written_late(bridge, monkeypatch, lands_at=math.inf, round_takes=5.0)
    async with reached(bridge["server"]) as app:
        answered = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_late"))
    assert answered["status"] == "not_seen_yet"
    assert rounds[-1] < confirm.FIND_SECONDS <= rounds[-1] + 5.0  # no round begun past the end


async def test_a_call_found_late_is_logged_with_how_late(bridge, monkeypatch, caplog):
    # So a late write shows how late it was, and whether the search is long enough (review, F230).
    written_late(bridge, monkeypatch, lands_at=2.0, round_takes=0.25)
    with caplog.at_level("INFO", logger="claude_voice"):
        async with reached(bridge["server"]) as app:
            await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_late")
    assert any(re.search(r"toolu_late in its transcript after 2\.\d s", r.getMessage()) for r in caplog.records)


# -- a call Claude Code writes down only with its answer (ADR-CV-109) ----------------------


def lands_with_its_answer(bridge, tool_use_id):
    """As Claude Code does when it holds a call back: its line reaches the transcript with its result."""
    transcript(bridge["projects"], bridge["cwd"], "sid-7", tool_use_id)


async def test_a_call_not_yet_in_its_transcript_gets_a_receipt_and_its_retry_is_answered(bridge, monkeypatch):
    # On 2026-11-27 a session's calls reached its transcript only with their results, over 21 s after
    # being sent, so no wait could find them in time (ledger F230).
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        asked = outcome(await call(app, "confirm_ask", retried, tool_use_id="toolu_held_again"))
    assert first["status"] == "not_seen_yet" and "code" not in first
    assert f'receipt "{first["receipt"]}"' in first["next_step"] and "confirm_ask again" in first["next_step"]
    assert asked["code"] and '"desk"' in asked["tell_the_relay"]  # the session whose transcript holds the call
    assert [tuple(r) for r in bridge["store"].db.execute("SELECT session_id FROM owner_requests")] == [("sid-7",)]


async def test_a_receipt_whose_call_is_not_yet_written_gives_the_same_receipt(bridge, monkeypatch):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        again = outcome(await call(app, "confirm_ask", retried, tool_use_id="toolu_held_again"))
    assert again["status"] == "not_seen_yet" and again["receipt"] == first["receipt"]
    # Still unseen after its answer: likely a caller the bridge can't see, told to stop (review, F230).
    assert "subagent" not in first["next_step"]
    assert "subagent" in again["next_step"] and "stop calling confirm_ask" in again["next_step"]


async def test_a_receipt_works_once(bridge, monkeypatch, caplog):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        assert outcome(await call(app, "confirm_ask", retried, tool_use_id="toolu_2"))["code"]
        with caplog.at_level("WARNING", logger="claude_voice"):
            reused = await call(app, "confirm_ask", retried, tool_use_id="toolu_3")
    assert reused.get("isError") and "already used that receipt" in reused["content"][0]["text"]
    # Logged, since a second call with one receipt may be a session that read it in the transcript.
    assert any(
        "toolu_3 of confirm_ask brought a receipt another call already used" in r.getMessage() for r in caplog.records
    )


async def test_two_retries_at_once_with_one_receipt_act_once(bridge, monkeypatch):
    # Both arrive while the first call is still in no transcript, and it lands while they wait (review, F230).
    monkeypatch.setattr(confirm, "FIND_SECONDS", 2.0)
    monkeypatch.setattr(confirm, "FIND_PAUSE", 0.01)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        # The first call lands once both retries look for it, or half a second after the first one does.
        find, asking, began = transcripts.session_of_tool_use, set(), []

        def look(tool_use_id, sessions, projects_dir):
            if tool_use_id == "toolu_held":
                asking.add(asyncio.current_task())
                began.append(time.monotonic())
                if len(asking) == 2 or time.monotonic() - began[0] > 0.5:
                    lands_with_its_answer(bridge, "toolu_held")
            return find(tool_use_id, sessions, projects_dir)

        monkeypatch.setattr(transcripts, "session_of_tool_use", look)
        one, other = await asyncio.gather(
            call(app, "confirm_ask", retried, tool_use_id="toolu_r1"),
            call(app, "confirm_ask", retried, tool_use_id="toolu_r2"),
        )
    assert sorted(bool(r.get("isError")) for r in (one, other)) == [False, True]
    assert bridge["store"].db.execute("SELECT COUNT(*) FROM owner_requests").fetchone()[0] == 1


async def test_a_receipt_works_only_with_the_arguments_of_the_call_that_gave_it(bridge, monkeypatch):
    # So a receipt read in another session's transcript can't ask the owner something else (review, F230).
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        other = {"summary": "Allow everything", "receipt": first["receipt"]}
        refused = await call(app, "confirm_ask", other, tool_use_id="toolu_2")
    assert refused.get("isError") and "other arguments" in refused["content"][0]["text"]
    assert bridge["store"].db.execute("SELECT COUNT(*) FROM owner_requests").fetchone()[0] == 0


async def test_a_retry_seen_in_another_session_s_transcript_than_its_receipt_s_call_is_refused(
    bridge, monkeypatch, tmp_path
):
    # Another session read sid-7's receipt in its transcript; its own call is on disk in time (review, F230).
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    other = {"pid": 8, "sessionId": "sid-8", "cwd": str(bridge["cwd"]), "name": "other", "status": "idle"}
    (tmp_path / "live" / "8.json").write_text(json.dumps(other), encoding="utf-8")
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        transcript(bridge["projects"], bridge["cwd"], "sid-8", "toolu_taker")
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        taken = await call(app, "confirm_ask", retried, tool_use_id="toolu_taker")
        # The receipt no longer works, for anyone: the session it was given to asks again without it.
        late = await call(app, "confirm_ask", retried, tool_use_id="toolu_held_again")
    assert taken.get("isError") and "another session's call" in taken["content"][0]["text"]
    assert late.get("isError") and "receipt" in late["content"][0]["text"]
    assert bridge["store"].db.execute("SELECT COUNT(*) FROM owner_requests").fetchone()[0] == 0


async def test_a_retry_found_with_its_own_call_is_answered_at_once(bridge, monkeypatch, caplog):
    # A retry whose own call is in the same transcript needs no wait to the search's end.
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        first = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        folder = bridge["projects"] / transcripts.project_key_for_directory(str(bridge["cwd"]))
        both = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": "x"}]}}
            for i in ("toolu_held", "toolu_again")
        ]
        (folder / "sid-7.jsonl").write_text("".join(json.dumps(x) + "\n" for x in both), encoding="utf-8")
        monkeypatch.setattr(confirm, "FIND_SECONDS", 30.0)  # would time the test out if it waited
        retried = {"summary": "Allow it", "receipt": first["receipt"]}
        began = time.monotonic()
        with caplog.at_level("INFO", logger="claude_voice"):
            asked = outcome(await call(app, "confirm_ask", retried, tool_use_id="toolu_again"))
        took = time.monotonic() - began
    assert asked["code"] and took < 5.0
    assert any(
        re.search(r"toolu_held in its transcript by its receipt, \d+\.\d s", r.getMessage()) for r in caplog.records
    )


async def test_a_receipt_works_only_for_the_tool_that_gave_it(bridge, monkeypatch):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        code = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_ask"))["code"]
        # The same arguments, so only the tool tells the two calls apart.
        first = outcome(await call(app, "confirm_ask", {"summary": code}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        crossed = await call(app, "confirm_check", {"code": code, "receipt": first["receipt"]}, tool_use_id="toolu_4")
    assert crossed.get("isError") and "receipt" in crossed["content"][0]["text"]


async def test_a_check_not_yet_in_its_transcript_gets_its_verdict_on_the_retry(bridge, monkeypatch):
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    async with reached(bridge["server"]) as app:
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        code = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_ask"))["code"]
        first = outcome(await call(app, "confirm_check", {"code": code}, tool_use_id="toolu_held"))
        lands_with_its_answer(bridge, "toolu_held")
        checked = outcome(
            await call(app, "confirm_check", {"code": code, "receipt": first["receipt"]}, tool_use_id="toolu_5")
        )
    assert first["status"] == "not_seen_yet" and "confirm_check again" in first["next_step"]
    assert checked["status"] == "pending" and checked["code"] == code


def test_a_receipt_expires_after_two_minutes():
    now = [1_800_000_000.0]
    book = confirm.Receipts(clock=lambda: now[0])
    receipt = book.give("confirm_check", "K4Q2", "toolu_x")
    held = book.take(receipt, "confirm_check", "K4Q2")
    assert held and held.tool_use_id == "toolu_x"
    book.put_back(receipt, held)  # its call still in no transcript: it keeps its first time
    now[0] += 2 * 60 + 1
    assert book.take(receipt, "confirm_check", "K4Q2") is None


async def test_a_call_with_no_call_id_is_refused_at_once(bridge):
    # A voice chat's call carries no Claude Code call id, so there is nothing to look for in a
    # transcript: waiting for one would only keep the relay waiting before the same refusal.
    async with reached(bridge["server"]) as app:
        began = time.monotonic()
        refused = await call(app, "confirm_ask", {"summary": "Allow it"})
        took = time.monotonic() - began
    assert refused.get("isError") and took < 1.0


async def test_the_relay_gets_one_confirmation_line_from_either_tool(bridge):
    # A relay handed only a code still learns the at-the-PC path: the line has one home (owner, 2026-11-15).
    async with reached(bridge["server"]) as app:
        transcript(bridge["projects"], bridge["cwd"], "sid-7", "toolu_ask")
        asked = outcome(await call(app, "confirm_ask", {"summary": "Allow it"}, tool_use_id="toolu_ask"))
        handed = outcome(await call(app, "confirm_link", {"code": asked["code"]}))
    assert "at the PC" in handed["tell_the_relay"] and handed["tell_the_relay"] == asked["tell_the_relay"]
