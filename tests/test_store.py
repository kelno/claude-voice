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
