"""A permission prompt a background session waits on, read from its screen."""

import json
import os

from fakes import FakeClaude
from mcp.client import Client

from claude_voice.jobs import prompt_from_screen
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

ESC = "\x1b"
SCREEN = (
    f"{ESC}[38;2;153;153;153m● I'll fetch that page now.{ESC}[m\r\n"
    f"● Fetch(https://example.org){ESC}[182C\r\n"
    + "╌" * 60
    + " url: https://example.org/ │ prompt: What is the page's title?\r\n"
    + "╌" * 60
    + " Do you want to allow Claude to fetch this content?\r\n"
    f" {ESC}[1m❯ 1. Yes{ESC}[m\r\n"
    "   2. Yes, and don't ask again for example.org\r\n"
    "   3. No, and tell Claude what to do differently (esc)\r\n"
)


def test_a_permission_prompt_is_read_from_the_screen_without_its_drawing():
    prompt = prompt_from_screen(SCREEN)
    assert prompt.startswith("Fetch(https://example.org)")
    assert "Do you want to allow Claude to fetch this content?" in prompt
    assert "1. Yes" in prompt and "3. No" in prompt
    assert ESC not in prompt and "╌" not in prompt and "I'll fetch" not in prompt


def test_a_screen_with_no_prompt_gives_none():
    assert prompt_from_screen("● Done. The title is Example Domain.\r\n❯ \r\n") is None


async def test_a_background_session_waiting_on_a_prompt_shows_what_it_asks(tmp_path):
    root = tmp_path / "home"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    entry = {
        "pid": os.getpid(),
        "sessionId": "sid-1",
        "cwd": str(root / "billing"),
        "name": "fetcher",
        "kind": "bg",
        "jobId": "job-1",
        "status": "waiting",
        "waitingFor": "permission prompt",
    }
    (live / "1.json").write_text(json.dumps(entry))
    seen = []

    def read_prompt(job_id):
        seen.append(job_id)
        return "Fetch(https://example.org) Do you want to allow Claude to fetch this content? 1. Yes 3. No"

    async def deliver(name, text):
        raise AssertionError("a message would only queue behind the prompt")

    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    srv = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver,
        read_transcript=lambda sid, directory: [],
        projects_dir=tmp_path / "projects",
        read_prompt=read_prompt,
    )
    async with Client(srv) as c:
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
        asked = (await c.call_tool("ask_active_session", {"session": "fetcher", "message": "hi"})).structured_content
    [row] = listed["sessions"]
    assert row["waiting_for"] == "permission prompt" and "Fetch(https://example.org)" in row["prompt"]
    assert asked["status"] == "needs_input" and "Do you want to allow" in asked["prompt"]
    assert set(seen) == {"job-1"}


def test_a_waiting_prompt_is_read_with_the_claude_code_the_bridge_finds(monkeypatch):
    # The bridge finds Claude Code where the service's PATH may not reach (procs.find); reading a
    # prompt must use the same program as starting or compacting a session does.
    import subprocess

    from claude_voice import jobs, procs

    ran = []

    class Done:
        stdout = SCREEN.encode()

    def run(command, **kwargs):
        ran.append(command)
        return Done()

    monkeypatch.setattr(procs, "find", lambda name: "C:/Users/owner/.local/bin/claude.exe" if name == "claude" else None)
    monkeypatch.setattr(subprocess, "run", run)
    assert "Fetch(https://example.org)" in (jobs.pending_prompt("job-1") or "")
    assert ran == [["C:/Users/owner/.local/bin/claude.exe", "logs", "job-1"]]
