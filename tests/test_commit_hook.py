"""The commit-msg hook in .githooks, run the way git runs it.

The hook refuses a message the gates would refuse later, while it is still being written.
A fixup, amend or squash commit is the one exception: it is a note to fold into an earlier
commit before the branch is pushed (`git commit --fixup`, then `git rebase --autosquash`), so
refusing it at commit time made that way of working impossible. Continuous integration still
refuses one, so none can reach main.
"""

import os
import pathlib
import shutil
import subprocess
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "commit-msg"


def posix_shell() -> str | None:
    """The sh git runs the hook with, or None. On Windows that is Git for Windows' own, found from
    git itself: a PowerShell or cmd window has no sh on its PATH, and a bash there may be WSL's,
    a Linux without this project's tools."""
    if os.name != "nt":
        return shutil.which("sh")
    git = shutil.which("git")
    for folder in pathlib.Path(git).resolve().parents if git else []:
        if (folder / "bin" / "sh.exe").is_file():
            return str(folder / "bin" / "sh.exe")
    return None


SH = posix_shell()
needs_sh = pytest.mark.skipif(SH is None, reason="no sh to run the hook with (on Windows, Git for Windows' sh)")


def run_hook(tmp_path, message):
    file = tmp_path / "COMMIT_EDITMSG"
    file.write_text(message, encoding="utf-8")
    return subprocess.run([str(SH), str(HOOK), str(file)], cwd=ROOT, capture_output=True, text=True)


@needs_sh
@pytest.mark.parametrize("marker", ["fixup!", "amend!", "squash!"])
def test_a_commit_to_fold_in_later_is_let_through_on_a_branch(tmp_path, marker):
    assert run_hook(tmp_path, f"{marker} fix: Say what to do next\n").returncode == 0


@needs_sh
def test_a_commit_to_fold_in_later_is_let_through_after_the_comments(tmp_path):
    message = "# Please enter the commit message\n\nfixup! fix: Say what to do next\n"
    assert run_hook(tmp_path, message).returncode == 0


@needs_sh
def test_an_ordinary_message_the_gates_refuse_is_still_refused(tmp_path):
    assert run_hook(tmp_path, "docs: Added a thing\n").returncode != 0


@needs_sh
def test_a_message_that_only_mentions_a_fixup_is_still_checked(tmp_path):
    assert run_hook(tmp_path, "docs: Added a fixup! note\n").returncode != 0


def test_continuous_integration_still_refuses_a_fixup():
    config = tomllib.loads((ROOT / "committed.toml").read_text(encoding="utf-8"))
    assert config["no_fixup"] is True
