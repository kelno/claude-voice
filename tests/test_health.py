"""health(): the bridge's own state, cheap enough to poll."""

import asyncio

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import transcripts
from claude_voice.metrics import METRICS
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def server(tmp_path, **extra):
    (tmp_path / "src").mkdir(exist_ok=True)
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    return build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", **extra)


async def test_health_reports_the_bridges_state_and_its_calls(tmp_path, monkeypatch):
    class Reader:  # the served bridge warms its side-question reader; no real one here
        async def warm(self):
            pass

    async def no_jobs():
        return []

    monkeypatch.setattr(transcripts, "shared_reader", Reader)
    METRICS.reset()
    srv = server(
        tmp_path,
        restart={"at": "2026-10-08T10:00:00+00:00", "reason": "auto-restart", "total": 2},
        watch_interval=60.0,  # served: the watcher looks at once, then every minute
        background_jobs=no_jobs,
    )
    async with Client(srv) as c:
        await c.call_tool("guide", {})
        await c.call_tool("read_session_output", {"session": "nope"}, meta={"trace_id": "t9"})
        for _ in range(100):  # the watcher's first look runs behind, on a thread of its own
            h = (await c.call_tool("health", {})).structured_content
            if h["watcher"]["last_check_at"]:
                break
            await asyncio.sleep(0.05)
    now = h["now"]
    assert now["up_since"] and now["version"] and now["memory_rss_mb"] > 0
    assert now["last_restart"] == {"at": "2026-10-08T10:00:00+00:00", "reason": "auto-restart"}
    assert now["restarts_total"] == 2
    tools = {t["name"]: t for t in h["tools"]}
    assert tools["guide"]["calls"] == 1
    assert h["errors"][0]["trace_id"] == "t9" and h["errors"][0]["kind"] == "tool_error"
    assert h["courier"] == {"warm": False, "last_delivery_ms": None}
    assert h["watcher"]["last_check_at"] and h["watcher"]["tree_version"]


async def test_without_restart_knowledge_the_reason_is_unknown(tmp_path):
    async with Client(server(tmp_path)) as c:
        h = (await c.call_tool("health", {})).structured_content
    assert h["now"]["last_restart"]["reason"] is None
    assert h["watcher"]["last_check_at"] is None  # nothing has been checked yet
