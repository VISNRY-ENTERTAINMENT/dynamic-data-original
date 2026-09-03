"""Dynamic Data — authenticity layer (optional).

Integrity (the hash chain in store.py) proves content wasn't changed. But a
tamperer can recompute hashes to look consistent again — so integrity alone is
not enough (this is exactly the point a production trust model makes). Authenticity
closes that hole: an Ed25519 signature over each ledger entry proves WHO wrote
it, and only the holder of the private key can produce a valid signature.

Model (same shape as a production /trust endpoint):
  - Each agent has an Ed25519 keypair. The private key stays with the agent.
  - The agent signs each claim's entry_hash. The store keeps only the signature
    and the public-key fingerprint.
  - Anyone with the agent's PUBLIC key can verify — no shared secret, so the
    store itself cannot forge an agent's signature.

Requires the `cryptography` package. If it is not installed, signing is simply
unavailable and the core hash-chain integrity still works (import guarded).
"""

from __future__ import annotations

import hashlib
from typing import Optional

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _AVAILABLE = False


def available() -> bool:
    return _AVAILABLE


def _require():
    if not _AVAILABLE:
        raise RuntimeError(
            "Signing requires the 'cryptography' package: pip install cryptography. "
            "Core hash-chain integrity works without it; only Ed25519 authenticity needs it."
        )


def fingerprint(public_key_hex: str) -> str:
    """A short, stable id for a public key (first 16 bytes of its SHA-256)."""
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:32]


class AgentKey:
    """An agent's Ed25519 identity. Keep `private_hex` secret; publish `public_hex`."""

    def __init__(self, private_key: "Ed25519PrivateKey"):
        _require()
        self._sk = private_key

    @classmethod
    def generate(cls) -> "AgentKey":
        _require()
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_hex(cls, private_hex: str) -> "AgentKey":
        _require()
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex)))

    @property
    def private_hex(self) -> str:
        raw = self._sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return raw.hex()

    @property
    def public_hex(self) -> str:
        raw = self._sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_hex)

    def sign(self, message: str) -> str:
        return self._sk.sign(message.encode("utf-8")).hex()


def verify(message: str, signature_hex: str, public_hex: str) -> bool:
    """Verify a signature against a public key. Returns True iff it was produced
    by the matching private key over exactly this message."""
    _require()
    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        pk.verify(bytes.fromhex(signature_hex), message.encode("utf-8"))
        return True
    except Exception:
        return False
