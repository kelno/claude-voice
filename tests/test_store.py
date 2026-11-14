import contextlib

from claude_voice.store import Store


def test_the_database_folder_is_made_when_missing(tmp_path):
    path = tmp_path / "state" / "claude-voice" / "bridge.db"
    Store(path)
    assert path.is_file()


def test_a_started_session_is_found_by_id_or_name_and_the_newest_wins(tmp_path):
    store = Store(tmp_path / "bridge.db")
    store.record_started("sid-1", "billing", "/src/billing")
    store.record_started("sid-2", "billing", "/src/billing")

    assert store.was_started("sid-1") and not store.was_started("sid-3")
    assert store.started_session("sid-1") == {"claude_session_id": "sid-1", "name": "billing", "cwd": "/src/billing"}
    assert store.started_session("billing")["claude_session_id"] == "sid-2"
    assert store.started_session("nobody") is None


def test_state_survives_reopening_the_database(tmp_path):
    path = tmp_path / "bridge.db"
    Store(path).record_started("sid-1", "billing", "/src/billing")
    assert Store(path).was_started("sid-1")


def test_a_write_from_one_thread_never_lands_inside_another(tmp_path):
    # The bridge writes its one database from the event loop and from the watcher's thread.
    # On one shared connection, a second write in the middle of a first joins its transaction:
    # its commit commits the first one's half, and the first one's rollback is lost.
    import threading

    store = Store(tmp_path / "s.db")
    inside, other_done = threading.Event(), threading.Event()

    def first():
        with contextlib.suppress(RuntimeError), store.db:
            store.db.execute("INSERT INTO settings VALUES ('half', '1', 0)")
            inside.set()
            other_done.wait(0.5)  # the other write either lands now, or waits for this one
            raise RuntimeError("the first write fails, so its half must not be kept")

    def second():
        inside.wait(5)
        store.set_setting("whole", 2)
        other_done.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert store.get_setting("half") is None
    assert store.get_setting("whole") == 2
