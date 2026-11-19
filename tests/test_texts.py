"""`claude-voice texts`: what the bridge says to agents, printed without serving it."""

import json
import re

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import cli, config, texts
from claude_voice.server import RELAYED_MESSAGE_NOTE, build_server, session_note
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def settings(tmp_path):
    """A settings file with the owner's own voice rules and one note for relays."""
    rules = tmp_path / "rules.md"
    rules.write_text("Keep it short.\n", encoding="utf-8")
    note = tmp_path / "Relay.md"
    note.write_text("Be kind.\n", encoding="utf-8")
    file = tmp_path / "config.toml"
    file.write_text(
        f"relay_notes = [{json.dumps(str(note))}]\nvoice_instructions_file = {json.dumps(str(rules))}\n",
        encoding="utf-8",
    )
    return config.load(file, env={"HOME": str(tmp_path)}), note


async def served(tmp_path, repos):
    """The tool list and the guide as a client of a running bridge gets them."""
    m = SessionManager(Store(tmp_path / "served.db"), project_root=tmp_path, client_factory=FakeClaude())
    srv = build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", repo_config=repos)
    async with Client(srv) as c:
        tools = [{"name": t.name, "description": t.description} for t in (await c.list_tools()).tools]
        guide = (await c.call_tool("guide", {})).structured_content["guide"]
    return tools, guide


async def test_the_tools_and_the_guide_are_those_a_client_of_the_bridge_gets(tmp_path, settings):
    repos, _ = settings
    tools, guide = await served(tmp_path, repos)

    out = await texts.collect(repos)

    assert out["tools"] == tools
    assert out["guide"] == guide
    assert "Keep it short." in out["guide"]


async def test_the_notes_under_a_relayed_message_have_placeholders_where_values_go(tmp_path, settings):
    out = await texts.collect(settings[0])

    said = {n["when"]: n["text"] for n in out["notes"]}
    assert RELAYED_MESSAGE_NOTE in said.values()
    assert session_note("<sender>", waits=False) in said.values()
    assert session_note("<sender>", waits=True) in said.values()
    checks = [t for t in said.values() if "about once an hour" in t]
    assert len(checks) == 2 and all('"<session name>"' in t for t in checks)
    assert all(w and not re.search(r"\{\w+\}", t) for w, t in said.items())


async def test_the_notes_for_relays_are_named_by_their_files(settings):
    repos, note = settings

    out = await texts.collect(repos)

    assert out["relay_notes"] == [str(note)]


def test_the_command_prints_them_as_json(monkeypatch, capsys):
    async def fake(repos=None):
        return {"tools": [{"name": "guide", "description": "Read this first."}], "guide": "g", "notes": []}

    monkeypatch.setattr(texts, "collect", fake)

    assert cli.main(["texts"], env={}) == 0
    assert json.loads(capsys.readouterr().out)["tools"][0]["name"] == "guide"
