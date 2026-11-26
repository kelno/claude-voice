"""The bridge's manual: handed over with a conversation's first result, and read again from the guide tool. It holds
only what spans tools; what one tool needs is in that tool's description (tests/test_surface.py)."""

from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import INSTRUCTIONS, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

# The manual as one line of words: where it wraps doesn't matter, so a phrase checked here may fall
# across a line break, and the text can be wrapped evenly.
MANUAL = " ".join(INSTRUCTIONS.lower().split())


async def test_the_guide_tool_returns_the_manual(tmp_path):
    (tmp_path / "src").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    async with Client(build_server(m)) as c:
        out = (await c.call_tool("guide", {})).structured_content
    assert out["guide"] == INSTRUCTIONS


async def test_the_opening_instructions_only_point_to_the_guide(tmp_path):
    # Claude Code keeps a server's instructions cut at 2,048 characters, in every session, used or not,
    # and the Claude app passes none on: the whole guide comes with the first result (owner, 2026-11-15).
    (tmp_path / "src").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    async with Client(build_server(m)) as c:
        said = c.instructions or ""
    assert "first result" in said and "call guide" in said
    assert len(said) < 400 and INSTRUCTIONS[:200] not in said


def test_the_manual_states_its_central_rules():
    for words in (
        "read this first",
        "voice relay",
        "the relay",
        "the desk session",
        "the side session",
        "the owner sees nothing",  # the central principle
        "voice in, screen out",  # when the owner reads on screen, the relay's notes say what's left to say
        "wait_for_session",  # asked to wait, the relay waits again rather than handing back
        "don't ask the owner",  # waiting for a reply is the default
        "wait in silence",  # no heads-up or progress between waits, whatever the app asks
        "next_step",  # what follows for the followed sessions, at the end of a result, for the relay alone
        "when the owner speaks during a wait",  # a call they talk over returns by itself
        "news",  # a result's news line: how the followed sessions stand, and prompts waiting
        "follow_session",  # how a session the owner hands over gets into the news line
        "your handle",  # each relay's follows are kept apart under a handle the bridge gives it
        "not your name",  # the handle is machinery, never said to the owner nor taken for the agent's name
        "nothing new",  # every followed session has an entry, so nothing new isn't mistaken for unfollowed
        "when a topic with the owner wraps up",  # the relay checks the news then, rather than offering to
        "before telling the owner what it said",  # news says something is new, not what
        "start_active_session",  # new work
        "attach_conversation",  # resumed work
        "on remote control",  # sessions the bridge starts can be taken over on the phone or at the PC
        "so never ask one to",  # a session the owner opened in the desktop app or a terminal takes no message
        "a session the bridge started can",  # it starts new work itself
        "the owner's to answer",  # permission prompts
        "settings → connectors",  # where a missing new tool gets refreshed
    ):
        assert words in MANUAL, words


def test_what_moved_to_the_relays_notes_left_the_manual():
    # How a relay works with the owner and the other sessions is in the owner's notes for relays,
    # handed over with the guide (relay_notes.py); the guide stays for every agent on the bridge (owner, 2026-11-15).
    for words in ("keep agents distinct", "the relay's scope", "self-contained brief", "one thing at a time"):
        assert words not in MANUAL, words


def test_the_manual_says_what_the_asterisk_in_a_name_means():
    assert "with an asterisk, such as billing*, is the owner's" in MANUAL and "owner_chose true" in MANUAL
    # A voice rename of the owner's name keeps it protected (owner, 2026-11-26).
    assert "rename of a name that has one keeps it, unless the owner says to drop it" in MANUAL
    assert "curly braces" not in MANUAL
