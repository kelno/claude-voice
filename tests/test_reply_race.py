"""A reply written just before a turn ends is not lost to the moment the bridge looks.

Claude Code marks a session idle in its registry about when it writes the turn's last
message to the transcript, and the bridge may read the transcript a moment too early: it
then saw an idle session with nothing new, and answered "idle" with an empty reply while
the reply was already there (seen live, 120 ms apart). A turn that seems to have ended
with nothing new is now believed only once it has looked that way for a short while.
"""

import asyncio
import inspect
import json

from fakes import FakeClaude
from mcp.client import Client
from test_ask import Target, call, live, root  # noqa: F401  (pytest fixtures)
from test_live import msg

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


class Late:
    """The transcript as read while the turn's last message is still on its way: hidden for a few reads."""

    def __init__(self, target, reads):
        self.target, self.reads = target, reads

    def __call__(self, sid, directory):
        turns = list(self.target.transcript)
        if self.reads > 0 and turns and turns[-1].type == "assistant":
            self.reads -= 1
            return turns[:-1]
        return turns


def bridge(tmp_path, root, live, read, deliver=None, **settings):  # noqa: F811
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())

    async def nothing(name, text):
        return {"delivered": True, "detail": ""}

    return build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver or nothing,
        read_transcript=read,
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
        **settings,
    )


async def test_a_turn_that_seems_to_end_with_nothing_new_is_believed_only_after_a_short_while(tmp_path, root, live):  # noqa: F811
    target = Target(live, str(root / "billing"))
    target.transcript.append(msg("user", "Is the export fixed?"))  # turn 2: the relay's message
    target.says("The export is fixed.")  # turn 3, its reply
    target.set("idle")
    late = Late(target, reads=15)
    async with Client(bridge(tmp_path, root, live, late)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "idle"
    assert out["reply"] == "The export is fixed."


async def test_a_reply_written_as_the_turn_ends_is_still_returned_by_an_ask(tmp_path, root, live):  # noqa: F811
    target = Target(live, str(root / "billing"))
    late = Late(target, reads=0)

    async def deliver(name, text):
        async def work():
            await asyncio.sleep(0.05)
            target.set("busy")
            target.transcript.append(msg("user", text))
            await asyncio.sleep(0.05)
            late.reads = 15  # the reply is on its way as the session goes idle
            target.says("The export is fixed.")
            target.set("idle")

        asyncio.get_running_loop().create_task(work())
        return {"delivered": True, "detail": ""}

    async with Client(bridge(tmp_path, root, live, late, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="Is the export fixed?", wait_seconds=5)
    assert out["status"] == "answered"
    assert out["reply"] == "The export is fixed."


async def test_a_turn_that_really_ended_with_nothing_new_still_ends_the_wait(tmp_path, root, live):  # noqa: F811
    target = Target(live, str(root / "billing"))
    target.transcript.append(msg("user", "Just note this."))
    target.set("idle")
    async with Client(bridge(tmp_path, root, live, lambda sid, d: list(target.transcript))) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "idle" and out["reply"] == ""


def mid_turn(target, first_words):
    """A turn that has said something, then called a tool and got its result: not over yet."""
    target.says(first_words)
    target.transcript.append(msg("assistant", [{"type": "tool_use", "id": "t9", "name": "Write", "input": {}}]))
    target.transcript.append(msg("user", [{"type": "tool_result", "tool_use_id": "t9", "content": "ok"}]))


async def test_a_session_listed_idle_while_its_transcript_stops_mid_turn_is_still_working(tmp_path, root, live):  # noqa: F811
    # Claude Code listed the Arborist idle about 0.35 s before its turn's last message reached the
    # transcript, and the wait returned the turn's first words as its final reply (ledger F250).
    target = Target(live, str(root / "billing"))
    target.transcript.append(msg("user", "Where is the draft?"))  # turn 2: the relay's message
    mid_turn(target, "The draft hadn't started, and I'm writing it now.")
    target.says("Here is the full proposal.")
    target.set("idle")
    late = Late(target, reads=15)
    async with Client(bridge(tmp_path, root, live, late)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "idle" and out["reply_final"] is True
    assert out["reply"] == "Here is the full proposal."


async def test_a_turn_that_really_stopped_after_a_tool_still_ends_the_wait_after_a_while(tmp_path, root, live):  # noqa: F811
    # A tool refused at the PC can end a turn there: the session is believed idle after a few seconds.
    target = Target(live, str(root / "billing"))
    target.transcript.append(msg("user", "Delete the old exports."))
    mid_turn(target, "Deleting them.")
    target.set("idle")
    server = bridge(tmp_path, root, live, lambda sid, d: list(target.transcript), writing_grace=0.3)
    async with Client(server) as c:
        began = asyncio.get_running_loop().time()
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
        took = asyncio.get_running_loop().time() - began
    assert out["status"] == "idle" and out["reply"] == "Deleting them."
    assert 0.3 <= took < 4


async def test_an_ask_waits_for_the_last_message_of_a_turn_listed_idle_mid_turn(tmp_path, root, live):  # noqa: F811
    target = Target(live, str(root / "billing"))
    late = Late(target, reads=0)

    async def deliver(name, text):
        async def work():
            await asyncio.sleep(0.05)
            target.set("busy")
            target.transcript.append(msg("user", text))
            await asyncio.sleep(0.05)
            mid_turn(target, "Looking at the export.")
            late.reads = 15  # the turn's last message is on its way as the session goes idle
            target.says("The export is fixed.")
            target.set("idle")

        asyncio.get_running_loop().create_task(work())
        return {"delivered": True, "detail": ""}

    async with Client(bridge(tmp_path, root, live, late, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="Is the export fixed?", wait_seconds=5)
    assert out["status"] == "answered"
    assert out["reply"] == "The export is fixed."


def test_a_transcript_stops_mid_turn_on_a_tool_call_or_its_result_not_on_the_turn_s_last_words(tmp_path):
    # As Claude Code writes them: each block of Claude's message on a line, with the message's stop reason.
    from claude_voice import transcripts

    path = tmp_path / transcripts.project_key_for_directory("D:/w") / "sid.jsonl"
    path.parent.mkdir(parents=True)

    def claude(block, stop):
        return {"type": "assistant", "message": {"role": "assistant", "content": [block], "stop_reason": stop}}

    lines = [
        {"type": "user", "message": {"role": "user", "content": "Where is the draft?"}},
        claude({"type": "text", "text": "I'm writing it now."}, "tool_use"),
        claude({"type": "tool_use", "id": "t1", "name": "Write", "input": {}}, "tool_use"),
    ]
    result = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]}}
    for added, mid in (
        (lines, True),
        ([result], True),
        # The turn's last message, its thinking on disk before its words: they come next.
        ([claude({"type": "thinking", "thinking": "Short."}, "end_turn")], True),
        ([claude({"type": "text", "text": "Done."}, "end_turn")], False),
    ):
        with path.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(x) + "\n" for x in added))
        assert transcripts.ends_mid_turn(transcripts.read_raw_transcript("sid", "D:/w", tmp_path)) is mid


def test_the_bridge_looks_at_a_session_four_times_a_second():
    # A reply reaches the relay at most a look after the session's turn ends.
    assert inspect.signature(build_server).parameters["poll_seconds"].default == 0.25
