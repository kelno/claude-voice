"""A passkey in software, for tests: makes registrations and signed assertions as a browser would hand them over."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, AT = 0x01, 0x04, 0x40


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftKey:
    def __init__(self, rp_id: str, origin: str, verified: bool = True) -> None:
        self.rp_id, self.origin, self.verified = rp_id, origin, verified
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = os.urandom(16)
        self.count = 0

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        self.count += 1
        uv = UV if self.verified else 0
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags | UP | uv]) + struct.pack(">I", self.count) + attested

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin}).encode()

    def register(self, options: dict[str, Any], origin: str | None = None) -> dict[str, Any]:
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        attested = bytes(16) + struct.pack(">H", len(self.id)) + self.id + cose
        auth = self._auth_data(AT, attested)
        client = self._client_data("webauthn.create", options["challenge"], origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {
            "id": b64(self.id),
            "rawId": b64(self.id),
            "type": "public-key",
            "response": {"clientDataJSON": b64(client), "attestationObject": b64(attestation)},
            "clientExtensionResults": {},
        }

    def assert_(self, options: dict[str, Any], origin: str | None = None) -> dict[str, Any]:
        auth = self._auth_data(0)
        client = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.id),
            "rawId": b64(self.id),
            "type": "public-key",
            "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth), "signature": b64(signature)},
            "clientExtensionResults": {},
        }
