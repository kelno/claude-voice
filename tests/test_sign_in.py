"""Claude Code's sign-in named where it explains a failure: a delivery the courier didn't make,
and health.

When the sign-in had run out, every message to a running session failed with only "the courier did
not send it", and health showed the courier warm: about four tries and a guess before a fresh
session's own reply, "Login expired", showed the cause (ledger F72).
"""

from datetime import UTC, datetime, timedelta

import pytest
from mcp.client import Client
from test_ask import Target, bridge

from claude_voice import sign_in, work

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


async def not_sent(name, text):
    return {"delivered": False, "detail": "the courier did not send it"}


@pytest.mark.parametrize("tool", ["message_active_session", "ask_active_session"])
async def test_a_failed_delivery_names_a_sign_in_that_has_run_out(tmp_path, root, live, monkeypatch, tool):
    monkeypatch.setattr(work, "read_login_expiry", lambda: datetime.now(UTC) - timedelta(hours=2))
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, not_sent)) as c:
        res = await c.call_tool(tool, {"session": "billing-ab", "message": "go"})
        health = (await c.call_tool("health", {})).structured_content
    said = str(res.content)
    assert res.is_error and "Could not deliver: the courier did not send it" in said
    assert "sign-in has run out" in said and "/login" in said
    assert health["sign_in"]["state"] == "expired"  # the failure's own look is kept


async def test_a_failed_delivery_says_nothing_of_a_sign_in_still_valid(tmp_path, root, live, monkeypatch):
    monkeypatch.setattr(work, "read_login_expiry", lambda: datetime.now(UTC) + timedelta(days=20))
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, not_sent)) as c:
        res = await c.call_tool("message_active_session", {"session": "billing-ab", "message": "go"})
    assert res.is_error and "sign-in" not in str(res.content)


def look(expires):
    return sign_in.Look(lambda: expires, warning=work.LOGIN_WARNING, now=lambda: NOW)


@pytest.mark.parametrize(
    ("expires", "state"),
    [
        (NOW + timedelta(days=20), "signed_in"),
        (NOW + timedelta(days=2), "running_out"),
        (NOW - timedelta(minutes=1), "expired"),
        (None, "unknown"),
    ],
)
def test_health_shows_the_sign_in_state(expires, state):
    seen = look(expires)
    seen.read()
    assert seen.state() == {
        "state": state,
        "expires_at": expires.isoformat(timespec="seconds") if expires else None,
        "looked_at": NOW.isoformat(timespec="seconds"),
    }


def test_before_any_look_the_sign_in_state_is_unknown():
    assert look(NOW).state() == {"state": "unknown", "expires_at": None, "looked_at": None}


async def test_health_carries_the_sign_in_state(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, not_sent)) as c:
        health = (await c.call_tool("health", {})).structured_content
    assert health["sign_in"]["state"] == "unknown"  # nothing looked yet; the suite's Claude Code can't say
