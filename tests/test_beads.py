"""A beads store in fleet_recap: the open issues and those closed lately, read from the store's export.

beads (a git-backed issue tracker for agents) writes its issues to an export file, one JSON object a
line. A top-level `beads_export` in config.toml names that file, and fleet_recap then carries what it
holds, so a page showing the sessions can show the work they are on without being rebuilt.
"""

import json
import os
from datetime import UTC, datetime, timedelta

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import beads, config
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


def stamp(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def export(tmp_path, *issues):
    path = tmp_path / "issues.jsonl"
    path.write_text("".join(json.dumps(i) + "\n" for i in issues), encoding="utf-8")
    return path


WAITING = {
    "id": "co-1",
    "title": "billing: fix the totals",
    "status": "in_progress",
    "issue_type": "task",
    "labels": ["waiting:owner"],
    "metadata": {"session": "4d0a5840-aaaa", "what": "Merge it now?", "since": "2026-11-19"},
}
EPIC = {"id": "co-2", "title": "Q4", "status": "open", "issue_type": "epic"}
CLOSED_LATELY = {
    "id": "co-3",
    "title": "export: tidy",
    "status": "closed",
    "issue_type": "task",
    "close_reason": "Done.",
    "closed_at": stamp(2),
}
CLOSED_LONG_AGO = {**CLOSED_LATELY, "id": "co-4", "closed_at": stamp(30)}


def config_file(tmp_path, export_path=None):
    file = tmp_path / "config.toml"
    top = f"beads_export = {json.dumps(str(export_path))}\n" if export_path else ""
    file.write_text(top + "[budgets]\nclaude_app = 180\n", encoding="utf-8")
    return file


def test_the_export_is_named_in_config_toml(tmp_path):
    path = tmp_path / "issues.jsonl"
    assert beads.configured(config_file(tmp_path, path)) == path


def test_without_the_key_or_the_file_there_is_no_beads_store(tmp_path):
    assert beads.configured(None) is None
    assert beads.configured(config_file(tmp_path)) is None


def test_open_issues_and_those_closed_in_the_last_day_are_listed(tmp_path):
    path = export(tmp_path, WAITING, EPIC, CLOSED_LATELY, CLOSED_LONG_AGO)
    board = beads.board(path, now=NOW)
    assert board["open"] == [
        {
            "id": "co-1",
            "title": "billing: fix the totals",
            "status": "in_progress",
            "labels": ["waiting:owner"],
            "metadata": WAITING["metadata"],
        }
    ]
    assert board["closed"] == [{"id": "co-3", "title": "export: tidy", "close_reason": "Done.", "closed_at": stamp(2)}]
    assert board["closed_within_hours"] == beads.CLOSED_WITHIN_HOURS


def test_the_newest_closed_issue_comes_first(tmp_path):
    older = {**CLOSED_LATELY, "id": "co-5", "closed_at": stamp(5)}
    board = beads.board(export(tmp_path, older, CLOSED_LATELY), now=NOW)
    assert [i["id"] for i in board["closed"]] == ["co-3", "co-5"]


def test_the_board_says_when_the_export_was_last_written(tmp_path):
    path = export(tmp_path, WAITING)
    written = (NOW - timedelta(minutes=3)).timestamp()
    os.utime(path, (written, written))
    assert datetime.fromisoformat(beads.board(path, now=NOW)["exported_at"]) == NOW - timedelta(minutes=3)


def test_an_export_that_cannot_be_read_is_said(tmp_path):
    missing = tmp_path / "gone.jsonl"
    assert str(missing) in beads.board(missing, now=NOW)["error"]


def test_a_line_that_is_not_json_is_said_with_its_number(tmp_path):
    path = export(tmp_path, WAITING)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    assert "line 2" in beads.board(path, now=NOW)["error"]


async def test_fleet_recap_carries_the_beads_store_when_one_is_named(tmp_path):
    path = export(tmp_path, WAITING)
    (tmp_path / "live").mkdir()
    srv = build_server(
        SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude()),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        projects_dir=tmp_path / "projects",
        repo_config=config.load(config_file(tmp_path, path), env={"HOME": str(tmp_path)}),
    )
    async with Client(srv) as c:
        fleet = (await c.call_tool("fleet_recap", {})).structured_content
    assert [i["id"] for i in fleet["beads"]["open"]] == ["co-1"]


async def test_fleet_recap_has_no_beads_without_a_store(tmp_path):
    (tmp_path / "live").mkdir()
    srv = build_server(
        SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude()),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        projects_dir=tmp_path / "projects",
        repo_config=config.load(config_file(tmp_path), env={"HOME": str(tmp_path)}),
    )
    async with Client(srv) as c:
        fleet = (await c.call_tool("fleet_recap", {})).structured_content
    assert "beads" not in fleet
