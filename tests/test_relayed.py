"""What a session is told about a message the relay sends it: who sees what, and where to answer."""

import pytest
from fakes import FakeClaude, init, result
from mcp.client import Client
from test_ask import Target, bridge, call

from claude_voice.server import RELAYED_MESSAGE_NOTE
from claude_voice.sessions import RELAYED_TASK_NOTE, SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


def test_the_note_says_who_sees_what_and_where_to_answer():
    for note in (RELAYED_MESSAGE_NOTE, RELAYED_TASK_NOTE):
        assert "relay" in note
        assert "owner" in note and "see" in note
        assert "self-contained" in note
    assert "SendMessage" in RELAYED_MESSAGE_NOTE and "inbox" in RELAYED_MESSAGE_NOTE


async def test_a_relayed_message_carries_the_note_after_its_own_words(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    sent = []

    async def deliver(name, text):
        sent.append(text)
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        await call(c, "message_active_session", session="billing-ab", message="Is the export fixed?")
        await call(c, "ask_active_session", session="billing-ab", message="And the import?", wait_seconds=0)
    assert sent[0].startswith("Is the export fixed?") and RELAYED_MESSAGE_NOTE in sent[0]
    assert sent[1].startswith("And the import?") and RELAYED_MESSAGE_NOTE in sent[1]


async def test_a_session_the_bridge_starts_is_told_once_in_its_instructions(tmp_path, root):
    claude = FakeClaude([init("c-1"), result("ok", "c-1")])
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=claude)
    s = m.create("billing")
    await m.send(s["id"], "fix the export")
    await m.wait(s["id"])
    options = claude.clients[0].options
    assert options.system_prompt == {"type": "preset", "preset": "claude_code", "append": RELAYED_TASK_NOTE}
    assert claude.clients[0].prompts == ["fix the export"]
