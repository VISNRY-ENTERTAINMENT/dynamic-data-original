"""Dynamic Data — core value types (v0.2, foundational atoms).

A Claim is the atomic unit: never a bare value, always an asserted fact that
carries the eight foundational atoms —

    1. Proposition   subject · predicate · value/obj
    2. Identity      subject is a *resolved* entity (via same_as claims)
    3. Source        who/what asserts it
    4. Derivation    derived_from — the claims/rules it is based on (belief revision)
    5. Credence      typed: point | interval | unknown  (0.5 != "no idea")
    6. Context       open bag: valid-time, world, timeline, framework, space, ...
    7. Record-time   recorded_at (bitemporal second axis)
    8. Lifecycle     active / superseded / retracted

Plus `dims`: an open extension bag so dimensions nobody imagined yet are added
as data, not as breaking schema changes (reflexivity). See ../02_FOUNDATIONAL_ATOMS.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class Profile(str, Enum):
    """How competing claims resolve. BELIEVED = many sources, confidence wins,
    conflict surfaces. DETERMINED = single authority, confidence pinned to 1.0,
    disagreement is a fault (banks/control)."""

    BELIEVED = "believed"
    DETERMINED = "determined"


class CredenceType(str, Enum):
    """Atom 5 — the *kind* of uncertainty, not just a scalar.

    POINT    — a single credence in [0,1] (the classic confidence).
    INTERVAL — a bounded range [lo, hi] (imprecise probability).
    UNKNOWN  — explicit ignorance. NOT 0.5. An UNKNOWN claim never outranks a
               POINT/INTERVAL claim; it records that we deliberately do not know.
    """

    POINT = "point"
    INTERVAL = "interval"
    UNKNOWN = "unknown"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Canonical predicate used to assert identity (atom 2). `subject same_as obj`.
SAME_AS = "same_as"


@dataclass(frozen=True)
class Claim:
    """A single asserted fact carrying the eight foundational atoms."""

    # --- Atom 1: Proposition ------------------------------------------------
    subject: str
    predicate: str
    value: Any = None
    obj: Optional[str] = None            # target entity for a relationship claim

    # --- Atom 3: Source -----------------------------------------------------
    source: str = "unknown"

    # --- Atom 5: Credence (typed) ------------------------------------------
    confidence: float = 1.0              # meaningful for POINT
    credence_type: CredenceType = CredenceType.POINT
    credence_lo: Optional[float] = None  # for INTERVAL
    credence_hi: Optional[float] = None  # for INTERVAL

    # --- Atom 6: Context (valid-time is one axis of this) ------------------
    observed_at: str = field(default_factory=_utcnow_iso)   # event/valid time
    context: dict = field(default_factory=dict)             # world, timeline, framework, valid_from/to, ...

    # --- Atom 7: Record-time -----------------------------------------------
    recorded_at: str = field(default_factory=_utcnow_iso)

    # --- Atom 4: Derivation -------------------------------------------------
    derived_from: tuple = ()             # tuple[str, ...] of claim_ids this was inferred from
    evidence: str = ""                   # human-readable note (not a substitute for derived_from)

    # --- Atom 8: Lifecycle --------------------------------------------------
    profile: Profile = Profile.BELIEVED
    supersedes: Optional[str] = None
    retracted: bool = False

    # --- Authorship attestation (hivemind) ---------------------------------
    author_kind: str = "unknown"         # human | ai | mixed | system | unknown

    # --- Reflexivity: open extension bag -----------------------------------
    dims: dict = field(default_factory=dict)

    # --- Ledger / tamper-evidence (store-assigned; NOT part of claim identity)
    seq: Optional[int] = None            # position in the append-only ledger
    prev_hash: Optional[str] = None      # entry_hash of the previous ledger entry
    entry_hash: Optional[str] = None     # sha256(content + prev_hash)

    # --- Authenticity: optional Ed25519 signature over entry_hash ----------
    signature: Optional[str] = None      # hex signature
    signer_fp: Optional[str] = None      # signer public-key fingerprint

    claim_id: str = ""

    def content_hash_payload(self) -> str:
        """Deterministic serialization of the claim's CONTENT (everything that is
        not ledger/signature bookkeeping). Used to compute the chain entry_hash,
        so editing any content field breaks the chain."""
        return json.dumps(
            {
                "subject": self.subject, "predicate": self.predicate,
                "value": self.value, "obj": self.obj, "source": self.source,
                "confidence": self.confidence, "credence_type": self.credence_type.value,
                "credence_lo": self.credence_lo, "credence_hi": self.credence_hi,
                "observed_at": self.observed_at, "context": self.context,
                "recorded_at": self.recorded_at, "derived_from": list(self.derived_from),
                "evidence": self.evidence, "profile": self.profile.value,
                "supersedes": self.supersedes,
                # `retracted` is mutable lifecycle state, deliberately excluded so
                # a legitimate retraction does not break the chain. The chain
                # covers the immutable *asserted* content (what was claimed, by
                # whom, when, with what confidence).
                "author_kind": self.author_kind, "dims": self.dims,
                "claim_id": self.claim_id,
            },
            sort_keys=True, default=str,
        )

    def __post_init__(self) -> None:
        if not self.subject or not self.subject.strip():
            raise ValueError("Claim.subject cannot be empty")
        if not self.predicate or not self.predicate.strip():
            raise ValueError("Claim.predicate cannot be empty")

        ct = self.credence_type
        if ct == CredenceType.POINT:
            if not (0.0 <= float(self.confidence) <= 1.0):
                raise ValueError("POINT confidence must be in [0.0, 1.0]")
        elif ct == CredenceType.INTERVAL:
            lo, hi = self.credence_lo, self.credence_hi
            if lo is None or hi is None or not (0.0 <= lo <= hi <= 1.0):
                raise ValueError("INTERVAL requires 0 <= credence_lo <= credence_hi <= 1")
        # UNKNOWN: confidence is ignored.

        if self.profile == Profile.DETERMINED:
            if ct != CredenceType.POINT or float(self.confidence) != 1.0:
                raise ValueError(
                    "DETERMINED claims must be POINT credence == 1.0 "
                    "(a determined domain admits no probabilistic value)."
                )
        if not self.claim_id:
            object.__setattr__(self, "claim_id", self.compute_id())

    def rank(self) -> float:
        """A single comparable credence for resolution.

        POINT -> confidence. INTERVAL -> midpoint. UNKNOWN -> -1 (never wins
        against a claim that actually estimates something)."""
        if self.credence_type == CredenceType.UNKNOWN:
            return -1.0
        if self.credence_type == CredenceType.INTERVAL:
            return (float(self.credence_lo) + float(self.credence_hi)) / 2.0
        return float(self.confidence)

    def compute_id(self) -> str:
        """Content-addressed id over what the claim *asserts* (including context,
        so the same proposition in a different context is a distinct claim)."""
        canonical = json.dumps(
            {
                "subject": self.subject,
                "predicate": self.predicate,
                "value": self.value,
                "obj": self.obj,
                "source": self.source,
                "observed_at": self.observed_at,
                "context": self.context,
                "credence_type": self.credence_type.value,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["profile"] = self.profile.value
        d["credence_type"] = self.credence_type.value
        d["derived_from"] = list(self.derived_from)
        return d


@dataclass(frozen=True)
class Resolution:
    """The result of resolving a (subject, predicate) to its current truth."""

    subject: str
    predicate: str
    chosen: Optional[Claim]
    alternatives: list
    conflict: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "alternatives": [c.to_dict() for c in self.alternatives],
            "conflict": self.conflict,
            "reason": self.reason,
        }


class DeterminedConflictError(Exception):
    """Two disagreeing current claims in a DETERMINED domain — a fault, not a
    value to resolve. The caller must fix the source of truth."""
