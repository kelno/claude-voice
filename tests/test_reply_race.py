"""A reply written just before a turn ends is not lost to the moment the bridge looks.

Claude Code marks a session idle in its registry about when it writes the turn's last
message to the transcript, and the bridge may read the transcript a moment too early: it
then saw an idle session with nothing new, and answered "idle" with an empty reply while
the reply was already there (seen live, 120 ms apart). A turn that seems to have ended
with nothing new is now believed only once it has looked that way for a short while.
"""

import asyncio
import inspect

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


def bridge(tmp_path, root, live, read, deliver=None):  # noqa: F811
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


def test_the_bridge_looks_at_a_session_four_times_a_second():
    # A reply reaches the relay at most a look after the session's turn ends.
    assert inspect.signature(build_server).parameters["poll_seconds"].default == 0.25
