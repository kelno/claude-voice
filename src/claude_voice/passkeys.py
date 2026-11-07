"""The owner's passkeys: the proof that the owner, and no model, confirmed a request.

A passkey is registered once, from a one-time link made at the PC. To confirm a request, the
bridge makes a challenge for it; the owner's device signs it after checking the owner (fingerprint
or PIN), and for the bridge's own address only, so a look-alike page gets nothing usable. The
bridge then checks the signature itself. Design: docs/owner-confirmation.md.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .store import Store

REGISTRATION_SECONDS = 10 * 60
CHALLENGE_SECONDS = 5 * 60
USER_ID = b"claude-voice-owner"

SCHEMA = """
-- The owner's passkeys: each a public key the bridge checks signatures with.
CREATE TABLE IF NOT EXISTS passkeys(
  id TEXT PRIMARY KEY,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_at REAL NOT NULL
);
-- One-time links to register a passkey, made at the PC.
CREATE TABLE IF NOT EXISTS passkey_registrations(
  token TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  challenge TEXT,
  used INTEGER NOT NULL DEFAULT 0
);
-- Challenges handed out, each for one purpose and used once.
CREATE TABLE IF NOT EXISTS passkey_challenges(
  challenge TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


class Passkeys:
    def __init__(self, store: Store, rp_id: str, origin: str, clock: Callable[[], float] = time.time) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.rp_id, self.origin, self.clock = rp_id, origin, clock

    # -- registering, once, from a link made at the PC ------------------------------------

    def new_registration(self) -> str:
        token = secrets.token_urlsafe(24)
        with self.db:
            self.db.execute(
                "INSERT INTO passkey_registrations(token, expires_at) VALUES(?,?)",
                (token, self.clock() + REGISTRATION_SECONDS),
            )
        return token

    def _open_registration(self, token: str) -> tuple[str | None]:
        row = self.db.execute(
            "SELECT challenge, expires_at, used FROM passkey_registrations WHERE token=?", (token,)
        ).fetchone()
        # RULE: a registration link works once for ten minutes
        if row is None or row[2] or self.clock() > row[1]:
            raise ValueError("This link to add a passkey is used or expired: make a new one at the PC.")
        return (row[0],)

    def check_registration(self, token: str) -> None:
        self._open_registration(token)

    def registration_options(self, token: str) -> dict[str, Any]:
        self._open_registration(token)
        options = webauthn.generate_registration_options(
            rp_id=self.rp_id,
            rp_name="claude-voice",
            user_name="owner",
            user_id=USER_ID,
            user_display_name="The bridge's owner",
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=self._descriptors(),
        )
        with self.db:
            self.db.execute(
                "UPDATE passkey_registrations SET challenge=? WHERE token=?",
                (bytes_to_base64url(options.challenge), token),
            )
        return dict(json.loads(webauthn.options_to_json(options)))

    def register(self, token: str, credential: dict[str, Any], name: str) -> None:
        (challenge,) = self._open_registration(token)
        if not challenge:
            raise ValueError("Ask for the registration options first.")
        try:
            done = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except Exception as exc:  # the library raises several kinds for a bad response
            raise ValueError(f"The passkey couldn't be added: {exc}") from None
        with self.db:
            self.db.execute("UPDATE passkey_registrations SET used=1 WHERE token=?", (token,))
            self.db.execute(
                "INSERT INTO passkeys(id, public_key, sign_count, name, created_at) VALUES(?,?,?,?,?)",
                (
                    bytes_to_base64url(done.credential_id),
                    done.credential_public_key,
                    done.sign_count,
                    name.strip()[:60] or "passkey",
                    self.clock(),
                ),
            )

    def registered(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, name, created_at FROM passkeys ORDER BY created_at, rowid").fetchall()
        return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]

    def remove(self, key_id: str) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM passkeys WHERE id=?", (key_id,)).rowcount > 0

    def _descriptors(self) -> list[PublicKeyCredentialDescriptor]:
        return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["id"])) for p in self.registered()]

    # -- confirming: a challenge for one purpose, signed by a registered passkey ---------

    def challenge(self, purpose: str) -> dict[str, Any]:
        if not self.registered():
            raise ValueError("No passkey is registered yet: add one at the PC with `claude-voice passkey add`.")
        options = webauthn.generate_authentication_options(
            rp_id=self.rp_id,
            allow_credentials=self._descriptors(),
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        with self.db:
            self.db.execute(
                "INSERT INTO passkey_challenges(challenge, purpose, expires_at) VALUES(?,?,?)",
                (bytes_to_base64url(options.challenge), purpose, self.clock() + CHALLENGE_SECONDS),
            )
        return dict(json.loads(webauthn.options_to_json(options)))

    def verify(self, purpose: str, credential: dict[str, Any]) -> bool:
        """Whether a registered passkey signed a live challenge made for `purpose`; raises otherwise."""
        try:
            client = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
            challenge = str(client["challenge"])
            key_id = str(credential["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Not a passkey response.") from None
        with self.db:  # a challenge is used once, whatever comes of it
            row = self.db.execute(
                "DELETE FROM passkey_challenges WHERE challenge=? RETURNING purpose, expires_at", (challenge,)
            ).fetchone()
        # RULE: an assertion counts only for the request its challenge was made for
        if row is None or row[0] != purpose or self.clock() > row[1]:
            raise ValueError("This confirmation was made for something else, or expired: try again.")
        key = self.db.execute("SELECT public_key, sign_count FROM passkeys WHERE id=?", (key_id,)).fetchone()
        # RULE: a confirmation needs a passkey the bridge verified
        if key is None:
            raise ValueError("This passkey isn't registered with the bridge.")
        try:
            done = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=key[0],
                credential_current_sign_count=key[1],
                require_user_verification=True,
            )
        except Exception as exc:  # the library raises several kinds for a bad signature
            raise ValueError(f"The passkey's answer didn't check out: {exc}") from None
        with self.db:
            self.db.execute("UPDATE passkeys SET sign_count=? WHERE id=?", (done.new_sign_count, key_id))
        return True


def for_url(store: Store, public_url: str) -> Passkeys:
    """The owner's passkeys for the bridge published at `public_url`: bound to its host name."""
    parts = urlsplit(public_url)
    return Passkeys(store, parts.hostname or "localhost", f"{parts.scheme}://{parts.netloc}")
