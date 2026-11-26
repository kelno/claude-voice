"""`claude-voice texts`: what the bridge says to agents, printed without serving it."""

import io
import json
import re
import sys

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import cli, config, relay_notes, texts
from claude_voice.server import GUIDE_NOTE, RELAYED_MESSAGE_NOTE, build_server, session_note
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
        tools = [{"name": t.name, "description": texts.tool_text(t)} for t in (await c.list_tools()).tools]
        guide = (await c.call_tool("guide", {})).structured_content["guide"]
    return tools, guide


async def test_the_tools_and_the_guide_are_those_a_client_of_the_bridge_gets(tmp_path, settings):
    repos, _ = settings
    tools, guide = await served(tmp_path, repos)

    out = await texts.collect(repos)

    assert out["tools"] == tools
    assert out["guide"] == guide
    assert "Keep it short." in out["guide"]


async def test_a_tools_text_carries_its_parameters_descriptions(tmp_path, settings):
    out = {t["name"]: t["description"] for t in (await texts.collect(settings[0]))["tools"]}
    m = SessionManager(Store(tmp_path / "params.db"), project_root=tmp_path, client_factory=FakeClaude())
    srv = build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", repo_config=settings[0])
    async with Client(srv) as c:
        recap = next(t for t in (await c.list_tools()).tools if t.name == "fleet_recap")
    beads = recap.input_schema["properties"]["beads"]["description"]
    # The description as served, then one line per parameter that has a description.
    assert out["fleet_recap"].startswith(recap.description)
    assert f"\nbeads: {beads}" in out["fleet_recap"]


async def test_the_notes_under_a_relayed_message_have_placeholders_where_values_go(tmp_path, settings):
    out = await texts.collect(settings[0])

    said = {n["when"]: n["text"] for n in out["notes"]}
    assert RELAYED_MESSAGE_NOTE in said.values()
    assert session_note("<sender>", waits=False) in said.values()
    assert session_note("<sender>", waits=True) in said.values()
    assert all(w and not re.search(r"\{\w+\}", t) for w, t in said.items())


async def test_the_notes_for_relays_are_named_by_their_files(settings):
    repos, note = settings

    out = await texts.collect(repos)

    assert out["relay_notes"] == [str(note)]


async def test_what_comes_with_the_guide_besides_it_is_printed_with_when(tmp_path, settings):
    repos, note = settings
    out = (await texts.collect(repos))["relay"]

    assert out["guide_note"]["text"] == GUIDE_NOTE
    # The opening and the unread line are those the bridge wraps the notes for relays in.
    assert relay_notes.text([note]).startswith(out["opening"]["text"] + "\n\n# Relay\n")
    missing = tmp_path / "Gone.md"
    assert relay_notes.UNREAD.format(path=missing, name="Gone") in relay_notes.text([missing])
    assert out["unread"]["text"] == relay_notes.UNREAD.format(path="<path>", name="<name>")
    assert all(e["when"] and not re.search(r"\{\w+\}", e["text"]) for e in out.values())


def test_the_command_prints_them_as_json(monkeypatch, capsys):
    async def fake(repos=None):
        return {"tools": [{"name": "guide", "description": "Read this first."}], "guide": "g", "notes": []}

    monkeypatch.setattr(texts, "collect", fake)

    assert cli.main(["texts"], env={}) == 0
    assert json.loads(capsys.readouterr().out)["tools"][0]["name"] == "guide"


def test_the_texts_print_in_ascii_so_a_pipe_in_any_code_page_carries_them(monkeypatch):
    """Windows writes a pipe in its own code page unless Python is told otherwise, and the guide
    holds characters such as an arrow that cp1252 lacks: seen in the owner's snapshot hook."""

    async def fake(repos=None):
        return {"tools": [], "guide": "send → wait", "notes": []}

    monkeypatch.setattr(texts, "collect", fake)
    pipe = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", pipe)

    assert cli.main(["texts"], env={}) == 0
    pipe.flush()
    assert json.loads(pipe.buffer.getvalue().decode("cp1252"))["guide"] == "send → wait"
