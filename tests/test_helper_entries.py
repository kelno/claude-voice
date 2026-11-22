"""Claude Code's entries for the bridge's helper folders, dropped once the folders are gone.

Claude Code writes an entry in its config file for every folder it runs in, and never removes
one; each message helper runs in a folder of its own, so the file grew by one entry per helper.
"""

import json

from claude_voice import folder_access

PREFIX = "claude-voice-msg-"


def config_with(path, projects):
    path.write_text(json.dumps({"numStartups": 7, "projects": projects}), encoding="utf-8")
    return path


def test_only_entries_of_helper_folders_that_are_gone_are_dropped(tmp_path):
    alive = tmp_path / f"{PREFIX}side-alive"
    alive.mkdir()
    gone = (tmp_path / f"{PREFIX}side-gone").as_posix()
    mine = (tmp_path / "my-project").as_posix()  # also gone, but not a helper's
    config = config_with(
        tmp_path / "claude.json",
        {gone: {"hasTrustDialogAccepted": True}, alive.as_posix(): {"lastCost": 0}, mine: {"lastCost": 1}},
    )
    assert folder_access.forget_gone_helpers(PREFIX, config) == 1
    kept = json.loads(config.read_text(encoding="utf-8"))
    assert set(kept["projects"]) == {alive.as_posix(), mine} and kept["numStartups"] == 7


def test_nothing_to_drop_leaves_the_file_untouched(tmp_path):
    config = config_with(tmp_path / "claude.json", {(tmp_path / "mine").as_posix(): {}})
    before = config.read_bytes()
    assert folder_access.forget_gone_helpers(PREFIX, config) == 0
    assert config.read_bytes() == before


def test_a_missing_or_broken_config_drops_nothing(tmp_path):
    assert folder_access.forget_gone_helpers(PREFIX, tmp_path / "missing.json") == 0
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert folder_access.forget_gone_helpers(PREFIX, broken) == 0
    assert broken.read_text(encoding="utf-8") == "{not json"
