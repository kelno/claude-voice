import json

import pytest

from claude_voice import permissions
from claude_voice.permissions import Grant, session_grant
from claude_voice.transcripts import project_key_for_directory

SID = "d1026496-d888-4eb9-b922-a439482abffc"


def desktop_record(root, session_id, **fields):
    folder = root / "claude_x" / "claude-code-sessions" / "account" / "org"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"local_{session_id}.json").write_text(json.dumps(fields), encoding="utf-8")


def transcript(projects, cwd, session_id, *entries):
    folder = projects / project_key_for_directory(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


@pytest.fixture
def dirs(tmp_path):
    return {"desktop_roots": (tmp_path / "desktop",), "projects_dir": tmp_path / "projects"}


def test_a_resumed_session_keeps_the_permissions_it_has_where_the_owner_runs_it(tmp_path, dirs):
    desktop_record(
        dirs["desktop_roots"][0],
        SID,
        permissionMode="auto",
        sessionPermissionUpdates=[
            {"type": "addDirectories", "directories": [r"D:\work\voice-bridge"], "destination": "session"},
            {"type": "addDirectories", "directories": [r"D:\elsewhere"], "destination": "userSettings"},
            {"type": "addRules", "rules": [], "destination": "session"},
        ],
    )

    grant = session_grant(SID, str(tmp_path), new_mode="default", **dirs)

    assert grant == Grant("auto", (r"D:\work\voice-bridge",))


def test_a_terminal_session_keeps_the_last_mode_its_transcript_records(tmp_path, dirs):
    transcript(
        dirs["projects_dir"],
        str(tmp_path),
        SID,
        {"type": "permission-mode", "permissionMode": "default"},
        {"type": "user", "message": {"content": "hi"}},
        {"type": "permission-mode", "permissionMode": "acceptEdits"},
    )

    assert session_grant(SID, str(tmp_path), new_mode="auto", **dirs) == Grant("acceptEdits")


def test_a_mode_recorded_on_a_prompt_counts_too(tmp_path, dirs):
    # `claude -p` and background sessions record the mode on each prompt, not as an entry of its own.
    transcript(
        dirs["projects_dir"],
        str(tmp_path),
        SID,
        {"type": "permission-mode", "permissionMode": "default"},
        {"type": "user", "permissionMode": "acceptEdits", "message": {"content": "hi"}},
    )

    assert session_grant(SID, str(tmp_path), new_mode="auto", **dirs) == Grant("acceptEdits")


def test_a_new_session_gets_the_mode_set_for_new_sessions(tmp_path, dirs):
    assert session_grant(None, str(tmp_path), new_mode="auto", **dirs) == Grant("auto")


def test_a_session_with_no_recorded_mode_gets_the_mode_for_new_sessions(tmp_path, dirs):
    assert session_grant(SID, str(tmp_path), new_mode="auto", **dirs) == Grant("auto")


def test_an_unknown_recorded_mode_is_not_used(tmp_path, dirs):
    desktop_record(dirs["desktop_roots"][0], SID, permissionMode="everything")

    assert session_grant(SID, str(tmp_path), new_mode="default", **dirs) == Grant("default")


def test_only_a_session_id_is_ever_looked_up(tmp_path, dirs):
    desktop_record(dirs["desktop_roots"][0], "not an id", permissionMode="auto")

    assert session_grant("not an id", str(tmp_path), new_mode="default", **dirs) == Grant("default")


def test_every_mode_the_sdk_knows_is_accepted():
    assert permissions.MODES == {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"}
