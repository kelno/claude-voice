"""The bridge's manual: sent as server instructions, and read again from the guide tool. It holds
only what spans tools; what one tool needs is in that tool's description (tests/test_surface.py)."""

from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import INSTRUCTIONS, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


async def test_the_guide_tool_returns_the_same_manual_as_the_server_instructions(tmp_path):
    (tmp_path / "src").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    async with Client(build_server(m)) as c:
        out = (await c.call_tool("guide", {})).structured_content
    assert out["guide"] == INSTRUCTIONS


def test_the_manual_states_its_central_rules():
    manual = INSTRUCTIONS.lower()
    for words in (
        "read this first",
        "voice relay",
        "the relay",
        "the desk session",
        "the side session",
        "the owner sees nothing",  # the central principle
        "the desk session says",  # agents stay distinct
        "that session's call",  # the relay relays; how to do the work is the session's
        "marked as yours",  # its own ideas go along as suggestions
        "wait_for_session",  # asked to wait, the relay waits again rather than handing back
        "don't ask the owner",  # waiting for a reply is the default
        "wait in silence",  # no heads-up or progress between waits, whatever the app asks
        "when the owner speaks during a wait",  # a call they talk over returns by itself
        "news",  # a result's news line: unread turns of the sessions you follow, and prompts waiting
        "before telling the owner what it said",  # news says something is new, not what
        "already approved",  # work done as agreed is confirmed briefly, not walked through again
        "start_active_session",  # new work
        "attach_conversation",  # resumed work
        "self-contained brief",  # what a session's first task holds
        "propose changes before applying",  # delegated work is proposed first, unless the owner says otherwise
        "never ask a desktop",  # the app refuses a start from a relayed message
        "the owner's to answer",  # permission prompts
        "settings → connectors",  # where a missing new tool gets refreshed
    ):
        assert words in manual, words
