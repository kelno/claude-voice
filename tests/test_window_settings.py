"""A session the bridge opens in a window gets its settings from a file, which no terminal re-quotes.

Seen 2026-11-21, on Windows: Windows Terminal dropped the escaped quotes inside the settings JSON
the bridge passes on the command line, and Claude Code, given broken settings, exited at once.
"""

import asyncio
import json
from pathlib import Path

from claude_voice import transcripts, window_settings


def test_a_session_in_a_window_gets_its_settings_from_a_file(tmp_path):
    command = transcripts.background_command(
        "claude", "{bridge: x}", prompt="go", terminal=["wt.exe", "--"], settings='{"hooks": {}}', channel=True
    )
    inline = command[command.index("--settings") + 1]
    out = window_settings.in_a_file(command, "{bridge: x}", tmp_path)
    path = Path(out[out.index("--settings") + 1])
    assert path.parent == tmp_path and json.loads(path.read_text(encoding="utf-8")) == json.loads(inline)
    assert '"' not in " ".join(out)  # nothing left for a terminal to re-quote
    assert out[: out.index("--settings")] == command[: command.index("--settings")]
    assert out[out.index("--settings") + 2 :] == command[command.index("--settings") + 2 :]


async def test_the_bridge_opens_a_window_with_its_settings_in_a_file(tmp_path, monkeypatch):
    ran = []

    class Window:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def exec_(*args, **kwargs):
        ran.append(args)
        return Window()

    monkeypatch.setattr(window_settings, "FOLDER", tmp_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_)
    await transcripts.start_background(str(tmp_path), "job", prompt="go", terminal=["wt.exe", "--"])
    args = ran[0]
    assert Path(args[args.index("--settings") + 1]).parent == tmp_path
