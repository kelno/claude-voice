"""The bridge's housekeeping runs in the background every few seconds; a step that fails says so in
the log, once, rather than being dropped without a word."""

import asyncio
import logging

from fakes import FakeClaude
from mcp.client import Client
from test_start import Starter

from claude_voice import transcripts
from claude_voice.server import background_step, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def test_a_failed_background_step_is_logged_once_until_it_changes(caplog):
    failures: dict[str, str] = {}

    def run(error: Exception | None) -> None:
        with background_step("renaming", failures):
            if error is not None:
                raise error

    with caplog.at_level(logging.WARNING, logger="claude_voice"):
        run(OSError("job record locked"))
        run(OSError("job record locked"))  # the loop comes round again: no second line
        run(ValueError("bad name"))  # a different failure is said
        run(None)  # recovered
        run(ValueError("bad name"))  # failing again after a recovery is said again
    said = [r.getMessage() for r in caplog.records if "renaming" in r.getMessage()]
    assert len(said) == 3
    assert "job record locked" in said[0] and "bad name" in said[1] and "bad name" in said[2]


def test_a_cancelled_background_step_is_not_taken_for_a_failure(caplog):
    # Stopping the bridge cancels its loop; that must go through, not be logged and swallowed.
    failures: dict[str, str] = {}
    with caplog.at_level(logging.WARNING, logger="claude_voice"):
        try:
            with background_step("look", failures):
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("the cancellation was swallowed")
    assert not caplog.records


async def test_a_wake_up_that_fails_is_logged(tmp_path, monkeypatch, caplog):
    class Reader:  # the served bridge warms its side-question reader; no real one here
        async def warm(self):
            pass

    monkeypatch.setattr(transcripts, "shared_reader", Reader)
    work = tmp_path / "work"
    work.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    store = Store(tmp_path / "b.db")

    async def jobs():
        raise RuntimeError("claude agents listing broke")

    async def up():
        return True

    m = SessionManager(store, project_root=work, client_factory=FakeClaude())
    srv = build_server(
        m,
        live_dir=live,
        watch_interval=60.0,
        start_background=Starter(tmp_path, register=False),
        background_jobs=jobs,
        connectors_reachable=up,
    )
    with caplog.at_level(logging.WARNING, logger="claude_voice"):
        async with Client(srv) as c:
            for _ in range(100):
                if any("listing broke" in r.getMessage() for r in caplog.records):
                    break
                await asyncio.sleep(0.01)
            await c.list_tools()
    assert any("wake" in r.getMessage() and "listing broke" in r.getMessage() for r in caplog.records)
