"""The bridge's manual: sent as server instructions, and read again from the guide tool."""

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
        "voice relay",
        "the relay",
        "the desk session",
        "the side session",
        "the owner sees nothing",  # the central principle
        "the desk session says",  # agents stay distinct
        "message_active_session",  # send when the next step doesn't need the answer
        "ask_active_session",
        "wait_for_session",
        "peek_session",
        "digest_session",
        "ask_forked_session",
        "folder_not_open",
        "side_chat",
        "keep waiting",  # asked to wait, the relay reads again rather than handing back
    ):
        assert words in manual, words
