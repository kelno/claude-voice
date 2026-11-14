"""'Has anything happened since I last asked?' — a cheap delta for polling voice clients."""

import json
import os

import pytest
from fakes import FakeClaude

from claude_voice.events import LiveWatcher
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

ALIVE = os.getpid()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "lib").mkdir()
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


def put(live, name, status, cwd, pid=ALIVE, sid=None):
    (live / f"{name}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": sid or f"id-{name}",
                "cwd": cwd,
                "name": name,
                "status": status,
            }
        )
    )


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "b.db")


def kinds(items):
    return [(i["session"], i["kind"]) for i in items]


def test_first_look_records_nothing_but_sets_the_baseline(store, live, root):
    put(live, "app-1", "busy", str(root / "app"))
    w = LiveWatcher(store, live, root)
    assert w.check() == []


def test_status_changes_become_events(store, live, root):
    put(live, "app-1", "busy", str(root / "app"))
    put(live, "lib-1", "idle", str(root / "lib"))
    w = LiveWatcher(store, live, root)
    w.check()

    put(live, "app-1", "idle", str(root / "app"))  # busy -> idle: finished
    put(live, "lib-1", "waiting", str(root / "lib"))  # needs the user
    put(live, "new-1", "busy", str(root / "app"))  # appeared
    events = w.check()

    assert sorted(kinds(events)) == [
        ("app-1", "finished"),
        ("lib-1", "needs_input"),
        ("new-1", "started"),
    ]


def test_a_session_that_goes_away_is_reported_as_ended(store, live, root):
    put(live, "app-1", "idle", str(root / "app"))
    w = LiveWatcher(store, live, root)
    w.check()
    (live / "app-1.json").unlink()
    assert kinds(w.check()) == [("app-1", "ended")]


def test_sessions_outside_the_root_are_ignored(store, live, root):
    w = LiveWatcher(store, live, root)
    w.check()
    put(live, "secret", "busy", "/elsewhere")
    assert w.check() == []


def test_baseline_survives_a_restart(store, live, root, tmp_path):
    put(live, "app-1", "busy", str(root / "app"))
    LiveWatcher(store, live, root).check()
    put(live, "app-1", "idle", str(root / "app"))
    again = LiveWatcher(Store(tmp_path / "b.db"), live, root)
    assert kinds(again.check()) == [("app-1", "finished")]


# -- the MCP tool ------------------------------------------------------------------


def server(store, live, root):
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    return build_server(m, conversations=lambda **k: [], live_dir=live)


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


def test_a_feed_kept_from_before_the_rename_says_folder(store, live, root):
    store.db.execute(
        "CREATE TABLE feed(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
        " session TEXT NOT NULL, kind TEXT NOT NULL, text TEXT, project TEXT)"
    )
    w = LiveWatcher(store, live, root)
    w.check()
    put(live, "app-1", "busy", str(root / "app"))
    assert [(e["session"], e["folder"]) for e in w.check()] == [("app-1", "app")]


def test_a_reply_to_the_courier_is_news_the_voice_reads(store, live, root, monkeypatch):
    from claude_voice import transcripts

    monkeypatch.setattr(transcripts._courier, "on_reply", None)
    # The fork has no whats_new: the watcher's events reach the companion API through event_feed.
    srv = server(store, live, root)
    first = srv.event_feed()
    transcripts._courier.on_reply("billing", "Pong. The build is green.")
    out = srv.event_feed(first["cursor"])
    assert kinds(out["events"]) == [("billing", "reply")]
    assert out["events"][0]["text"] == "Reply from billing: Pong. The build is green."
