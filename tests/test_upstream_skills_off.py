"""The fork's agents follow their owner's rules, not upstream's skills.

Claude Code lists every skill in `.claude/skills/` to every session in this
repository, so each one upstream ships is switched off by name in
`.claude/settings.json` (`skillOverrides`, which takes no wildcard). This test
is what makes a resync that brings a new upstream skill stop by itself, so no
agent has to remember to switch it off. See `UPSTREAM.md`, "What steers agents".
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
SKILLS = ROOT / ".claude" / "skills"
SETTINGS = ROOT / ".claude" / "settings.json"
NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def _skill_names() -> set[str]:
    names = set()
    for skill in SKILLS.glob("*/SKILL.md"):
        front = skill.read_text(encoding="utf-8").split("---")[1]
        match = NAME.search(front)
        names.add(match.group(1) if match else skill.parent.name)
    return names


def _overrides() -> dict[str, str]:
    return json.loads(SETTINGS.read_text(encoding="utf-8")).get("skillOverrides", {})


def test_every_skill_in_the_repository_is_switched_off() -> None:
    on = sorted(name for name in _skill_names() if _overrides().get(name) != "off")
    assert not on, (
        f"Skills not switched off: {', '.join(on)}. They come from upstream, and the fork's "
        'agents don\'t follow upstream\'s rules for agents: add each to "skillOverrides" as "off" '
        'in .claude/settings.json and list it in UPSTREAM.md, "What steers agents".'
    )


def test_no_skill_is_switched_off_that_no_longer_exists() -> None:
    gone = sorted(set(_overrides()) - _skill_names())
    assert not gone, (
        f'"skillOverrides" in .claude/settings.json names skills that no longer exist: {", ".join(gone)}. '
        'Remove them, and from UPSTREAM.md, "What steers agents".'
    )
