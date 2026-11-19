"""The voice mailbox while `list_replies` is switched off: the fork's own tests.

The owner said no to the voice mailbox (2026-11-19): nothing here confirms a reply heard, and
only confirmed replies are ever deleted, so the mailbox would keep every reply to the courier
forever. While `list_replies` is switched off, a reply is still news for the phone page's stream,
and the mailbox stays empty.
"""

import pytest
from fakes import FakeClaude
from test_live import live, root  # noqa: F401  (fixtures)

from claude_voice import mailbox, transcripts
from claude_voice.server import SWITCHED_OFF, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SHOWN = "Reply from fuel-mcp: Diesel is 21.90 today."


@pytest.mark.parametrize("switched_off", [SWITCHED_OFF, frozenset()])
def test_the_mailbox_keeps_no_reply_while_list_replies_is_switched_off(
    tmp_path,
    root,  # noqa: F811
    live,  # noqa: F811
    monkeypatch,
    switched_off,
):
    monkeypatch.setattr(transcripts._courier, "on_reply", None)  # put back after the test
    store = Store(tmp_path / "b.db")
    build_server(
        SessionManager(store, project_root=root, client_factory=FakeClaude()),
        conversations=lambda **k: [],
        live_dir=live,
        switched_off=switched_off,
    )
    transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
    kept = mailbox.Mailbox(store.db).unread()
    assert [m["text"] for m in kept] == ([] if "list_replies" in switched_off else [SHOWN])
