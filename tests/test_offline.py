"""The sessions the Claude app shows offline: closed by Claude Code on its own, still on Remote Control."""

import json
from datetime import UTC, datetime, timedelta

from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def stamp(ago: timedelta) -> str:
    """A time stamp as Claude Code's daemon log writes it, `ago` before now."""
    return (datetime.now(UTC) - ago).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def job(jobs_dir, sid, name, cwd, remote=True):
    """A background session's job record, as Claude Code keeps it once the session has closed."""
    folder = jobs_dir / sid[:8]
    folder.mkdir(parents=True)
    record = {"sessionId": sid, "name": name, "cwd": cwd, "state": "done"}
    if remote:
        record["bridgeSessionId"] = "cse_017aWUqgaoE7NFCEoEjYFvfc"
    (folder / "state.json").write_text(json.dumps(record), encoding="utf-8")


async def test_only_a_session_on_remote_control_is_shown_offline_in_the_claude_app(tmp_path):
    # Seen 2026-11-14: the owner typed to two sessions the app showed offline; the messages waited
    # there for hours, and nothing on the PC could see them (ledger Q113).
    root = tmp_path / "home"
    root.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    jobs_dir = tmp_path / "jobs"
    review = "e33956a5-eb04-4f6a-a693-3e7cdf3be64f"
    scan = "ae536c14-0000-4000-8000-000000000001"
    local = "11112222-0000-4000-8000-000000000002"  # never on Remote Control: the app doesn't list it
    old = "33334444-0000-4000-8000-000000000003"  # closed a week ago
    back = "55556666-0000-4000-8000-000000000004"  # running again since
    for sid, name in (
        (review, "Bridge code review"),
        (scan, "Old accounts password scan"),
        (old, "Old"),
        (back, "Back"),
    ):
        job(jobs_dir, sid, name, str(root))
    job(jobs_dir, local, "Local", str(root), remote=False)
    log = tmp_path / "daemon.log"
    log.write_text(
        f"[{stamp(timedelta(days=7))}] [bg] bg retire {old[:8]}: settled, idle 9h\n"
        f"[{stamp(timedelta(hours=9))}] [bg] bg retire {review[:8]}: settled, idle 8h [low memory]\n"
        f"[{stamp(timedelta(hours=5))}] [bg] bg retire {scan[:8]}: idle-prompt, idle 16h\n"
        f"[{stamp(timedelta(hours=5))}] [bg] bg retire {local[:8]}: settled, idle 2h\n"
        f"[{stamp(timedelta(hours=5))}] [bg] bg retire {back[:8]}: settled, idle 8h\n"
        f"[{stamp(timedelta(hours=1))}] [bg] bg spawned {back[:8]} (fleet)\n",
        encoding="utf-8",
    )
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    server = build_server(m, live_dir=live, daemon_log=log, jobs_dir=jobs_dir)
    async with Client(server) as c:
        out = (await c.call_tool("list_active_sessions", {})).structured_content
    offline = out["offline_in_app"]
    assert [(s["name"], s["claude_session_id"], s["closed"]) for s in offline] == [
        ("Old accounts password scan", scan, "while idle"),  # the latest close first
        ("Bridge code review", review, "for low memory"),
    ]
    assert offline[0]["folder"] == str(root)


async def test_a_session_the_bridge_left_down_while_parked_counts_as_closed(tmp_path):
    # Seen 2026-11-21: a parked session, left down after a restart, was in no list the relay reads,
    # while the Claude app still listed it for the owner to type to (owner, 2026-11-21).
    from claude_voice import parking

    root = tmp_path / "home"
    root.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    jobs_dir = tmp_path / "jobs"
    backup = "7dd3b732-0000-4000-8000-000000000001"
    back = "99998888-0000-4000-8000-000000000002"  # left down too, but resumed since
    job(jobs_dir, backup, "Nightly backup", str(root))
    job(jobs_dir, back, "Back", str(root))
    store = Store(tmp_path / "b.db")
    for sid in (backup, back):
        parking.leave_down(store, sid, stamp(timedelta(hours=2)), {})
    log = tmp_path / "daemon.log"
    log.write_text(f"[{stamp(timedelta(hours=1))}] [bg] bg spawned {back[:8]} (fleet)\n", encoding="utf-8")
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    server = build_server(m, live_dir=live, daemon_log=log, jobs_dir=jobs_dir)
    async with Client(server) as c:
        out = (await c.call_tool("list_active_sessions", {})).structured_content
    assert [(s["name"], s["claude_session_id"], s["closed"]) for s in out["offline_in_app"]] == [
        ("Nightly backup", backup, "at the PC's shutdown, while parked"),
    ]
