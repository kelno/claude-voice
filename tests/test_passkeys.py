"""The owner's passkeys: registered once at the PC, then the only way to confirm a request."""

import pytest
from softkey import SoftKey

from claude_voice import passkeys
from claude_voice.store import Store

RP, ORIGIN = "bridge.example.org", "https://bridge.example.org"


@pytest.fixture
def keys(tmp_path):
    now = [1_800_000_000.0]
    return passkeys.Passkeys(Store(tmp_path / "b.db"), RP, ORIGIN, clock=lambda: now[0]), now


def registered(book, name="phone"):
    key = SoftKey(RP, ORIGIN)
    token = book.new_registration()
    book.register(token, key.register(book.registration_options(token)), name)
    return key


def test_a_registered_passkey_confirms_what_it_was_asked_for(keys):
    book, _ = keys
    key = registered(book)
    options = book.challenge("request:ABCD")
    assert book.verify("request:ABCD", key.assert_(options))


def test_a_confirmation_needs_a_passkey_the_bridge_verified(keys):
    book, _ = keys
    registered(book)
    stranger = SoftKey(RP, ORIGIN)  # never registered
    with pytest.raises(ValueError):
        book.verify("request:ABCD", stranger.assert_(book.challenge("request:ABCD")))


def test_an_assertion_counts_only_for_the_request_its_challenge_was_made_for(keys):
    book, _ = keys
    key = registered(book)
    signed = key.assert_(book.challenge("request:ABCD"))
    with pytest.raises(ValueError):
        book.verify("request:WXYZ", signed)


def test_a_challenge_is_used_once(keys):
    book, _ = keys
    key = registered(book)
    signed = key.assert_(book.challenge("request:ABCD"))
    assert book.verify("request:ABCD", signed)
    with pytest.raises(ValueError):
        book.verify("request:ABCD", signed)


def test_a_look_alike_page_gets_nothing_usable(keys):
    book, _ = keys
    key = registered(book)
    # A page on another address: the browser signs its own origin, which the bridge refuses.
    signed = key.assert_(book.challenge("request:ABCD"), origin="https://bridge.example.org.evil.test")
    with pytest.raises(ValueError):
        book.verify("request:ABCD", signed)


def test_the_owner_must_be_verified_not_only_present(keys):
    book, _ = keys
    token = book.new_registration()
    lax = SoftKey(RP, ORIGIN, verified=False)
    with pytest.raises(ValueError):
        book.register(token, lax.register(book.registration_options(token)), "no pin")


def test_a_registration_link_works_once_for_ten_minutes(keys):
    book, now = keys
    token = book.new_registration()
    key = SoftKey(RP, ORIGIN)
    book.register(token, key.register(book.registration_options(token)), "phone")
    with pytest.raises(ValueError):
        book.registration_options(token)
    late = book.new_registration()
    now[0] += 10 * 60 + 1
    with pytest.raises(ValueError):
        book.registration_options(late)


def test_passkeys_are_listed_and_removed(keys):
    book, _ = keys
    registered(book, "phone")
    registered(book, "desktop")
    listed = book.registered()
    assert [p["name"] for p in listed] == ["phone", "desktop"]
    book.remove(listed[0]["id"])
    assert [p["name"] for p in book.registered()] == ["desktop"]
