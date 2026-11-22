"""A held folder on every system, Windows included, where the bridge runs here.

Upstream's tests of the held folder (`test_held_folder.py`) run only where there is flock. On
Windows the lock is a file its holder keeps open, which no one can delete meanwhile; the sweep
removes a folder whose lock it can delete. These tests hold on both kinds of system.
"""

import os
import sys
import time

import pytest

from claude_voice import held_folder

PREFIX = "claude-voice-test-"
AN_HOUR_AGO = time.time() - 2 * 60 * 60


def killed(folder):
    """What a killed holder leaves: its folder, with a lock nobody holds."""
    os.close(folder._lock)
    folder._lock = None
    return folder.path


def test_a_folder_a_killed_helper_left_is_swept_on_every_system(tmp_path):
    left = killed(held_folder.HeldFolder(PREFIX, dir=tmp_path))
    assert held_folder.sweep(tmp_path, PREFIX) == [left.name]
    assert not left.exists()


def test_a_folder_in_use_is_kept_on_every_system(tmp_path):
    held = held_folder.HeldFolder(PREFIX, dir=tmp_path)
    os.utime(held.path, (AN_HOUR_AGO, AN_HOUR_AGO))
    try:
        assert held_folder.sweep(tmp_path, PREFIX) == []
        assert held.path.is_dir() and (held.path / held_folder.LOCK).exists()
    finally:
        held.remove()
    assert not held.path.exists()


def test_a_folder_with_no_lock_is_swept_once_old_on_every_system(tmp_path):
    """Made by a bridge from before the lock existed here, or killed before it locked."""
    old, new = tmp_path / f"{PREFIX}old", tmp_path / f"{PREFIX}new"
    for place in (old, new):
        place.mkdir()
    os.utime(old, (AN_HOUR_AGO, AN_HOUR_AGO))
    assert held_folder.sweep(tmp_path, PREFIX) == [old.name]
    assert (old.exists(), new.exists()) == (False, True)


@pytest.mark.skipif(sys.platform != "win32", reason="the lock is flock elsewhere")
def test_on_windows_the_lock_is_a_file_its_holder_keeps_open(tmp_path):
    held = held_folder.HeldFolder(PREFIX, dir=tmp_path)
    try:
        with pytest.raises(PermissionError):
            os.unlink(held.path / held_folder.LOCK)
    finally:
        held.remove()
