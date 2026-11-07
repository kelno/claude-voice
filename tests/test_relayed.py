"""What a session is told about a message the relay sends it: who sees what, and where to answer."""

import pytest
from mcp.client import Client
from test_ask import Target, bridge, call

from claude_voice.server import RELAYED_MESSAGE_NOTE


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
    note = RELAYED_MESSAGE_NOTE
    assert "relay" in note
    assert "owner" in note and "see" in note
    assert "self-contained" in note
    assert "SendMessage" in RELAYED_MESSAGE_NOTE and "inbox" in RELAYED_MESSAGE_NOTE
    # A relayed approval isn't the owner's; the note says where to get the owner's own.
    assert "confirm_ask" in RELAYED_MESSAGE_NOTE
    # A refusal from auto mode is asked about at once, not reported back first.
    assert "right away" in RELAYED_MESSAGE_NOTE and "auto mode" in RELAYED_MESSAGE_NOTE


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
