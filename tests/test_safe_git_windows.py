"""Safe git on Windows, checked with real git: the fork's own tests.

Upstream's tests of safe git that run git use a shell-script marker program, so they skip on
Windows, where Git for Windows's own settings switch the file-system monitor on. These run real
git here and check that no monitor daemon starts in the repository.
"""

import subprocess

from test_reposync import check, git, origin_and_clone

from claude_voice import safe_git


def no_daemon_in(clone):
    """True when no monitor daemon has started in the clone; stops one that did."""
    started = (clone / ".git" / "fsmonitor--daemon").exists()
    if started:
        subprocess.run(["git", "-C", str(clone), "fsmonitor--daemon", "stop"], capture_output=True, check=False)
    return not started


def test_a_safe_git_status_starts_no_file_system_monitor(tmp_path):
    _, clone = origin_and_clone(tmp_path)
    git(clone, "config", "core.fsmonitor", "true")
    safe_git.run("status", "--porcelain", cwd=clone, capture_output=True, check=True)
    assert no_daemon_in(clone)


def test_a_repos_check_starts_no_file_system_monitor(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    git(clone, "config", "core.fsmonitor", "true")
    check(tmp_path, bare)
    assert no_daemon_in(clone)
