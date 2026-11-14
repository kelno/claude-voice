"""A small tool surface: few tools, few parameters, and a folder list that fits in a voice."""

import json
import os
import time

import pytest
from claude_agent_sdk import project_key_for_directory
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

DAY = 24 * 3600


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "home"
    projects = tmp_path / "projects"
    for d in (root, projects):
        d.mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    srv = build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", projects_dir=projects)
    return root, projects, srv


def folder(world, name, used_days_ago=None, sessions=1):
    root, projects, _ = world
    (root / name).mkdir()
    if used_days_ago is not None:
        key = projects / project_key_for_directory(str(root / name))
        key.mkdir()
        when = time.time() - used_days_ago * DAY
        for i in range(sessions):
            f = key / f"s{i}.jsonl"
            f.write_text("{}\n")
            os.utime(f, (when, when))


async def projects(srv, **args):
    async with Client(srv) as c:
        return (await c.call_tool("list_folders", args)).structured_content["folders"]


async def test_the_folder_list_holds_only_folders_used_lately_newest_first(world):
    folder(world, "billing", used_days_ago=2, sessions=3)
    folder(world, "hydropower", used_days_ago=0)
    folder(world, "old", used_days_ago=60)
    folder(world, "never")
    out = await projects(world[2])
    assert [p["name"] for p in out] == ["hydropower", "billing"]
    assert out[1]["sessions"] == 3 and out[1]["last_used"]


async def test_a_word_finds_any_folder_even_one_never_used(world):
    folder(world, "hydropower", used_days_ago=1)
    folder(world, "hydro-sandbox")
    folder(world, "billing", used_days_ago=1)
    out = await projects(world[2], query="HYDRO")
    assert [p["name"] for p in out] == ["hydropower", "hydro-sandbox"]
    assert out[1]["last_used"] is None and out[1]["sessions"] == 0


async def test_the_folder_list_is_never_longer_than_twenty(world):
    for i in range(25):
        folder(world, f"p{i:02}", used_days_ago=1)
    assert len(await projects(world[2])) == 20
    assert len(await projects(world[2], query="p")) == 20


def used_elsewhere(world, path, days_ago=1):
    """A folder outside the root that Claude Code was used in, as its history records it."""
    _, projects, _ = world
    path.mkdir(parents=True, exist_ok=True)
    key = projects / project_key_for_directory(str(path))
    key.mkdir()
    f = key / "s0.jsonl"
    lines = [{"type": "queue-operation"}, {"type": "user", "cwd": str(path)}]
    f.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    when = time.time() - days_ago * DAY
    os.utime(f, (when, when))


async def projects_named(srv, **args):
    return {p["name"]: p for p in await projects(srv, **args)}


async def test_the_folder_list_includes_folders_claude_was_used_in_anywhere(world):
    root, _, srv = world
    folder(world, "billing", used_days_ago=2)
    notes = root.parent / "notes"
    used_elsewhere(world, notes, days_ago=1)
    used_elsewhere(world, root.parent / "old-notes", days_ago=60)
    used_elsewhere(world, root.parent / "deleted-since", days_ago=1)
    (root.parent / "deleted-since").rmdir()  # its history outlives it
    out = await projects_named(srv)
    assert list(out) == [str(notes.resolve()), "billing"]  # newest first
    assert out[str(notes.resolve())]["access"] == "folder_not_open"
    assert "access" not in out["billing"]
    found = await projects_named(srv, query="notes")
    assert set(found) == {str(notes.resolve()), str((root.parent / "old-notes").resolve())}


async def test_tools_covered_by_others_are_gone_and_the_rest_take_few_parameters(world):
    async with Client(world[2]) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert "recent_activity" not in tools and "get_messages" not in tools
    # Started sessions are in the desktop sidebar already, with Remote Control; moving one into its
    # folder's group took it off the phone (kelno, 2026-11-11).
    assert "hand_to_desktop" not in tools
    assert set(tools["list_active_sessions"].input_schema.get("properties", {})) == {"handle"}
    assert set(tools["search_session_history"].input_schema["properties"]) == {"session", "query", "max_chars"}
    assert set(tools["list_folders"].input_schema["properties"]) == {"query"}
    long = [n for n, t in tools.items() if len((t.description or "").split(". ")) > 3]
    assert long == []


# The Claude app shows an assistant each connector tool by the first line of its description,
# cut at about 79 characters, until the assistant loads the tool. kelno measured it in the
# app's voice mode on 2026-11-01. So the first line has to say, on its own, what the tool is for.
FIRST_LINE_CHARS = 78


async def test_every_tool_says_what_it_is_for_in_its_first_line_within_the_cut(world):
    async with Client(world[2]) as c:
        tools = (await c.list_tools()).tools
    for t in tools:
        first = (t.description or "").split("\n")[0]
        assert first.endswith("."), f"{t.name}: the first line is not a whole sentence: {first!r}"
        assert len(first) <= FIRST_LINE_CHARS, f"{t.name}: {len(first)} characters: {first!r}"


async def test_the_sessions_the_bridge_starts_are_never_presented_as_background(world):
    # Agents repeated "in the background" to the owner, while these sessions are on Remote
    # Control and in the desktop sidebar, visible on the phone and at the PC.
    _, _, srv = world
    async with Client(srv) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for name in ("start_active_session", "attach_conversation", "stop_active_session"):
        tool = tools[name]
        assert "background" not in f"{tool.title} {tool.description}".lower(), name
        assert "remote control" in tool.description.lower() or name == "stop_active_session", name


# What concerns one parameter goes in that parameter's own description: Claude Code and the
# Claude app pass it to the model, while a result's schema never reaches it (ledger Q100,
# tested 2026-11-11). A parameter several tools share is described the same way in each.
async def test_every_parameter_is_described_in_its_own_description_and_alike_everywhere(world):
    async with Client(world[2]) as c:
        tools = (await c.list_tools()).tools
    undescribed, texts = [], {}
    for t in tools:
        for name, prop in t.input_schema.get("properties", {}).items():
            if not prop.get("description"):
                undescribed.append(f"{t.name}.{name}")
            texts.setdefault(name, {}).setdefault(prop.get("description"), []).append(t.name)
    assert undescribed == []
    for name in ("session", "wait_seconds", "max_chars", "language"):
        assert len(texts.get(name, {})) <= 1, (name, texts.get(name))


# Tool search's result also gives a one-line summary per parameter, its description cut after
# about 117 characters; the full schema comes with it (a relay in the Claude app's voice mode,
# 2026-11-11). So a parameter description's first sentence says what matters, within that.
PARAM_FIRST_SENTENCE_CHARS = 115


async def test_a_parameter_descriptions_first_sentence_fits_before_the_cut(world):
    async with Client(world[2]) as c:
        tools = (await c.list_tools()).tools
    long = {}
    for t in tools:
        for name, prop in t.input_schema.get("properties", {}).items():
            first = prop.get("description", "").split(". ")[0]
            if len(first) > PARAM_FIRST_SENTENCE_CHARS:
                long[f"{t.name}.{name}"] = len(first)
    assert long == {}


async def test_a_wait_s_description_gives_the_limit_the_bridge_is_configured_with(tmp_path):
    # The Claude app's cap went from 20 s to 180 s in the config while the descriptions still
    # said 20 s (2026-11-11): the text says what the bridge it runs in is set to.
    from claude_voice import config

    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude())
    budgets = config.Budgets({"claude_app": 180})
    repos = config.Config(config.Repos(), config.Paths(None, None, None, None), None, budgets=budgets)
    srv = build_server(
        m, conversations=lambda **k: [], live_dir=tmp_path / "live", projects_dir=tmp_path, repo_config=repos
    )
    async with Client(srv) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for name in ("wait_for_session", "ask_active_session", "ask_forked_session", "read_forked_session"):
        wait = tools[name].input_schema["properties"]["wait_seconds"]["description"]
        assert "180 s through the claude.ai connector" in wait
        assert "20 s" not in (tools[name].description or "")
