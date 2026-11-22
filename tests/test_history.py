"""Earlier conversations for a page listing every session: older ones page by page, a search by
name over all of them, and what kind of session each was (a fork feature: UPSTREAM.md)."""

import json
import time

import pytest
from claude_agent_sdk import SDKSessionInfo, project_key_for_directory
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "src"
    (r / "app").mkdir(parents=True)
    return r


def conversation(sid, cwd, title, minutes_ago=1):
    return SDKSessionInfo(
        session_id=sid,
        summary=title,
        last_modified=int((time.time() - minutes_ago * 60) * 1000),
        cwd=cwd,
        first_prompt="hi",
    )


class Conversations:
    """The SDK's session index; it is asked for every conversation, newest first."""

    def __init__(self, items):
        self.items = items

    def __call__(self, directory=None, limit=None):
        found = [c for c in self.items if directory is None or c.cwd == directory]
        return found[:limit]


async def listed(tmp_path, root, items, **args):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    srv = build_server(
        m,
        conversations=Conversations(items),
        live_dir=tmp_path / "live",
        jobs_dir=tmp_path / "jobs",
        projects_dir=tmp_path / "projects",
    )
    async with Client(srv) as client:
        res = await client.call_tool("list_claude_conversations", args)
    assert not res.is_error, res.content
    return res.structured_content


def many(root, n):
    return [conversation(f"t-{i}", str(root / "app"), f"Work {i}", minutes_ago=i) for i in range(n)]


async def test_older_conversations_come_page_by_page(tmp_path, root):
    first = await listed(tmp_path, root, many(root, 5), limit=2)
    assert [c["claude_session_id"] for c in first["conversations"]] == ["t-0", "t-1"]
    assert first["next_offset"] == 2
    last = await listed(tmp_path, root, many(root, 5), limit=2, offset=4)
    assert [c["claude_session_id"] for c in last["conversations"]] == ["t-4"]
    assert last["next_offset"] is None


async def test_a_search_finds_a_conversation_by_name_however_old(tmp_path, root):
    items = [*many(root, 300), conversation("old", str(root / "app"), "Billing EXPORT fix", minutes_ago=9999)]
    found = await listed(tmp_path, root, items, query="billing export")
    assert [c["claude_session_id"] for c in found["conversations"]] == ["old"]


async def test_a_conversation_claude_code_kept_a_job_record_for_is_background(tmp_path, root):
    record = tmp_path / "jobs" / "t-0abcde" / "state.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"sessionId": "t-0", "name": "x", "cwd": str(root / "app")}), encoding="utf-8")
    out = await listed(tmp_path, root, many(root, 1))
    assert out["conversations"][0]["kind"] == "background"


def transcript(tmp_path, root, sid, entrypoint):
    path = tmp_path / "projects" / project_key_for_directory(str(root / "app")) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "summary"}, {"type": "user", "entrypoint": entrypoint}]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


async def test_a_conversation_says_whether_it_ran_in_the_desktop_app_or_a_terminal(tmp_path, root):
    transcript(tmp_path, root, "t-0", "claude-desktop")
    transcript(tmp_path, root, "t-1", "cli")
    out = await listed(tmp_path, root, many(root, 3))
    assert [c["kind"] for c in out["conversations"]] == ["desktop", "terminal", None]
