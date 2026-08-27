"""Dynamic Data — the claim store (v0.2, foundational atoms).

Append-only, SQLite-backed. Storage is deliberately boring; the invention is the
read semantics. This version implements the eight foundational atoms:
identity resolution, derivation + belief revision, typed credence, general
context (time-travel is one axis), record-time, lifecycle, and a reflexive
extension bag. See ../02_FOUNDATIONAL_ATOMS.md.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Optional

from .models import (
    Claim,
    CredenceType,
    DeterminedConflictError,
    Profile,
    Resolution,
    SAME_AS,
    _utcnow_iso,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    subject       TEXT NOT NULL,
    predicate     TEXT NOT NULL,
    value_json    TEXT,
    obj           TEXT,
    source        TEXT NOT NULL,
    confidence    REAL NOT NULL,
    credence_type TEXT NOT NULL DEFAULT 'point',
    credence_lo   REAL,
    credence_hi   REAL,
    observed_at   TEXT NOT NULL,
    context_json  TEXT NOT NULL DEFAULT '{}',
    recorded_at   TEXT NOT NULL,
    derived_from  TEXT NOT NULL DEFAULT '[]',
    evidence      TEXT NOT NULL DEFAULT '',
    profile       TEXT NOT NULL DEFAULT 'believed',
    supersedes    TEXT,
    retracted     INTEGER NOT NULL DEFAULT 0,
    dims_json     TEXT NOT NULL DEFAULT '{}',
    author_kind   TEXT NOT NULL DEFAULT 'unknown',
    seq           INTEGER,
    prev_hash     TEXT,
    entry_hash    TEXT,
    signature     TEXT,
    signer_fp     TEXT
);
CREATE INDEX IF NOT EXISTS ix_claims_sp ON claims (subject, predicate);
CREATE INDEX IF NOT EXISTS ix_claims_subject ON claims (subject);
CREATE INDEX IF NOT EXISTS ix_claims_obj ON claims (obj);
CREATE INDEX IF NOT EXISTS ix_claims_pred ON claims (predicate);
"""

_GENESIS = "0" * 64


def _row_to_claim(row: sqlite3.Row) -> Claim:
    return Claim(
        subject=row["subject"],
        predicate=row["predicate"],
        value=json.loads(row["value_json"]) if row["value_json"] is not None else None,
        obj=row["obj"],
        source=row["source"],
        confidence=row["confidence"],
        credence_type=CredenceType(row["credence_type"]),
        credence_lo=row["credence_lo"],
        credence_hi=row["credence_hi"],
        observed_at=row["observed_at"],
        context=json.loads(row["context_json"]),
        recorded_at=row["recorded_at"],
        derived_from=tuple(json.loads(row["derived_from"])),
        evidence=row["evidence"],
        profile=Profile(row["profile"]),
        supersedes=row["supersedes"],
        retracted=bool(row["retracted"]),
        dims=json.loads(row["dims_json"]),
        author_kind=row["author_kind"] if "author_kind" in row.keys() else "unknown",
        seq=row["seq"] if "seq" in row.keys() else None,
        prev_hash=row["prev_hash"] if "prev_hash" in row.keys() else None,
        entry_hash=row["entry_hash"] if "entry_hash" in row.keys() else None,
        signature=row["signature"] if "signature" in row.keys() else None,
        signer_fp=row["signer_fp"] if "signer_fp" in row.keys() else None,
        claim_id=row["claim_id"],
    )


class DynamicDataStore:
    """An append-only store of claims with confidence-weighted, identity- and
    context-aware resolution, and derivation-based belief revision."""

    # Columns added after v0.1 — applied to pre-existing .ddb files on open so a
    # v0.1 store upgrades to v0.2 without losing data.
    _V02_COLUMNS = (
        ("credence_type", "TEXT NOT NULL DEFAULT 'point'"),
        ("credence_lo", "REAL"),
        ("credence_hi", "REAL"),
        ("context_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("derived_from", "TEXT NOT NULL DEFAULT '[]'"),
        ("dims_json", "TEXT NOT NULL DEFAULT '{}'"),
        # v0.3 — hivemind + tamper-evidence
        ("author_kind", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("seq", "INTEGER"),
        ("prev_hash", "TEXT"),
        ("entry_hash", "TEXT"),
        ("signature", "TEXT"),
        ("signer_fp", "TEXT"),
    )

    def __init__(self, db_path: str = ":memory:", default_profile: Profile = Profile.BELIEVED):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        self.default_profile = default_profile

    def _migrate(self) -> None:
        """Add any columns introduced after the .ddb file was first created."""
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(claims)")}
        for name, decl in self._V02_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE claims ADD COLUMN {name} {decl}")
        # Index on the ledger sequence — created after the column is guaranteed.
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_claims_seq ON claims (seq)")

    # ------------------------------------------------------------------ write
    def assert_claim(
        self,
        subject: str,
        predicate: str,
        value: Any = None,
        *,
        obj: Optional[str] = None,
        source: str = "unknown",
        confidence: float = 1.0,
        credence_type: CredenceType = CredenceType.POINT,
        credence_lo: Optional[float] = None,
        credence_hi: Optional[float] = None,
        observed_at: Optional[str] = None,
        context: Optional[dict] = None,
        evidence: str = "",
        derived_from: tuple = (),
        supersedes: Optional[str] = None,
        profile: Optional[Profile] = None,
        dims: Optional[dict] = None,
        author_kind: str = "unknown",
        signer: Any = None,
    ) -> Claim:
        """Record a fact. Never overwrites — the store only ever grows.

        Each claim is chained into a tamper-evident append-only ledger
        (entry_hash = sha256(content + prev_hash)). If `signer` (an
        AgentKey) is given, the entry is also Ed25519-signed for authenticity.
        """
        claim = Claim(
            subject=subject, predicate=predicate, value=value, obj=obj,
            source=source, confidence=confidence, credence_type=credence_type,
            credence_lo=credence_lo, credence_hi=credence_hi,
            observed_at=observed_at or _utcnow_iso(), context=context or {},
            evidence=evidence, derived_from=tuple(derived_from),
            profile=profile or self.default_profile, supersedes=supersedes,
            dims=dims or {}, author_kind=author_kind,
        )

        # Idempotency: if this exact claim already exists, return it unchanged
        # (no new ledger entry).
        existing = self.get(claim.claim_id)
        if existing is not None:
            return existing

        # --- chain the entry into the append-only ledger --------------------
        head = self._conn.execute(
            "SELECT seq, entry_hash FROM claims WHERE seq IS NOT NULL "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq = (head["seq"] + 1) if head else 0
        prev_hash = head["entry_hash"] if head else _GENESIS
        entry_hash = hashlib.sha256(
            (claim.content_hash_payload() + prev_hash).encode("utf-8")
        ).hexdigest()

        signature = signer_fp = None
        if signer is not None:
            signature = signer.sign(entry_hash)
            signer_fp = signer.fingerprint

        object.__setattr__(claim, "seq", seq)
        object.__setattr__(claim, "prev_hash", prev_hash)
        object.__setattr__(claim, "entry_hash", entry_hash)
        object.__setattr__(claim, "signature", signature)
        object.__setattr__(claim, "signer_fp", signer_fp)

        self._conn.execute(
            """
            INSERT INTO claims
                (claim_id, subject, predicate, value_json, obj, source,
                 confidence, credence_type, credence_lo, credence_hi,
                 observed_at, context_json, recorded_at, derived_from, evidence,
                 profile, supersedes, retracted, dims_json, author_kind,
                 seq, prev_hash, entry_hash, signature, signer_fp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(claim_id) DO NOTHING
            """,
            (
                claim.claim_id, claim.subject, claim.predicate,
                json.dumps(claim.value, default=str) if claim.value is not None else None,
                claim.obj, claim.source, float(claim.confidence),
                claim.credence_type.value, claim.credence_lo, claim.credence_hi,
                claim.observed_at, json.dumps(claim.context, default=str),
                claim.recorded_at, json.dumps(list(claim.derived_from)),
                claim.evidence, claim.profile.value, claim.supersedes,
                1 if claim.retracted else 0, json.dumps(claim.dims, default=str),
                claim.author_kind, claim.seq, claim.prev_hash, claim.entry_hash,
                claim.signature, claim.signer_fp,
            ),
        )
        self._conn.commit()
        return claim

    # --- Atom 2: Identity ---------------------------------------------------
    def same_as(self, a: str, b: str, *, source: str = "unknown", confidence: float = 1.0) -> Claim:
        """Assert two subjects are the same entity. Identity is itself data:
        stored as a `same_as` claim and honored by resolution."""
        return self.assert_claim(a, SAME_AS, b, obj=b, source=source, confidence=confidence,
                                 evidence=f"identity: {a} == {b}")

    def _alias_set(self, subject: str) -> set[str]:
        """All subjects fused to `subject` by same_as claims (transitive)."""
        rows = self._conn.execute(
            "SELECT subject, obj FROM claims WHERE predicate = ? AND retracted = 0",
            (SAME_AS,),
        ).fetchall()
        # union-find over the same_as edges
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            parent[find(x)] = find(y)

        for r in rows:
            if r["obj"]:
                union(r["subject"], r["obj"])
        if subject not in parent:
            return {subject}
        root = find(subject)
        return {x for x in parent if find(x) == root} | {subject}

    def canonical(self, subject: str) -> str:
        """A stable representative for subject's identity set (lexicographically
        smallest alias) — so callers can key on one id per real entity."""
        return sorted(self._alias_set(subject))[0]

    # --- Atom 4: Derivation + belief revision ------------------------------
    def derive(
        self, subject: str, predicate: str, value: Any, *,
        derived_from: tuple, source: str = "inference", confidence: float = 0.8,
        evidence: str = "", **kw,
    ) -> Claim:
        """Assert a claim as an inference from other claims (records the edges)."""
        return self.assert_claim(subject, predicate, value, source=source,
                                 confidence=confidence, evidence=evidence,
                                 derived_from=tuple(derived_from), **kw)

    def dependents(self, claim_id: str) -> list[Claim]:
        """Claims that were derived (directly) from claim_id."""
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE retracted = 0", ()
        ).fetchall()
        return [c for c in (_row_to_claim(r) for r in rows) if claim_id in c.derived_from]

    def retract(self, claim_id: str, *, source: str = "unknown", reason: str = "",
                cascade: bool = False) -> list[str]:
        """Retract a claim. With cascade=True, transitively retract everything
        derived from it — belief revision: pull a premise, its conclusions fall.
        Returns the list of retracted claim_ids. History is preserved."""
        retracted: list[str] = []
        frontier = [claim_id]
        seen: set[str] = set()
        while frontier:
            cid = frontier.pop()
            if cid in seen:
                continue
            seen.add(cid)
            # Only the mutable `retracted` flag changes; asserted content (and
            # therefore the chain) is untouched, so verify_chain still passes.
            # (Append-only retraction *events* are future work — see the security
            # doc's inversion list.)
            cur = self._conn.execute(
                "UPDATE claims SET retracted = 1 WHERE claim_id = ? AND retracted = 0",
                (cid,),
            )
            if cur.rowcount > 0:
                retracted.append(cid)
                if cascade:
                    frontier.extend(d.claim_id for d in self.dependents(cid))
        self._conn.commit()
        return retracted

    # --- Reflexivity --------------------------------------------------------
    def describe(self, dimension: str, key: str, value: Any, *,
                 source: str = "unknown", confidence: float = 1.0) -> Claim:
        """Describe a dimension/predicate from inside the system (meta-claim).
        e.g. describe('weakness', 'unit', 'damage_multiplier'). The vocabulary is
        itself dynamic data — this is how future dimensions are added as data."""
        return self.assert_claim(f"predicate:{dimension}", key, value,
                                 source=source, confidence=confidence)

    # --- Hivemind: authenticated authorship, trust, tamper-evidence --------
    def register_agent(self, agent_id: str, *, kind: str = "ai",
                       trust_ceiling: float = 0.9, public_key: Optional[str] = None,
                       source: str = "system") -> dict:
        """Register an agent as reflexive claims (`agent:<id>` kind / trust_ceiling
        / pubkey). The hivemind's own roster is itself dynamic data."""
        subj = f"agent:{agent_id}"
        self.assert_claim(subj, "kind", kind, source=source, confidence=1.0)
        self.assert_claim(subj, "trust_ceiling", float(trust_ceiling), source=source, confidence=1.0)
        if public_key is not None:
            self.assert_claim(subj, "pubkey", public_key, source=source, confidence=1.0)
        return {"agent": agent_id, "kind": kind, "trust_ceiling": trust_ceiling,
                "pubkey": public_key}

    def trust_ceiling(self, agent_id: str) -> float:
        """Trust ceiling for a source/agent (default 1.0 if unregistered). A
        claim's effective credence is capped by this, so a low-trust agent
        asserting confidence 1.0 cannot outrank a trusted agent."""
        row = self._conn.execute(
            "SELECT value_json FROM claims WHERE subject = ? AND predicate = 'trust_ceiling' "
            "AND retracted = 0 ORDER BY recorded_at DESC LIMIT 1",
            (f"agent:{agent_id}",),
        ).fetchone()
        if row and row["value_json"] is not None:
            try:
                return float(json.loads(row["value_json"]))
            except Exception:
                return 1.0
        return 1.0

    def agent_pubkey(self, agent_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value_json FROM claims WHERE subject = ? AND predicate = 'pubkey' "
            "AND retracted = 0 ORDER BY recorded_at DESC LIMIT 1",
            (f"agent:{agent_id}",),
        ).fetchone()
        return json.loads(row["value_json"]) if row and row["value_json"] else None

    def _eff_rank(self, c: Claim) -> float:
        """Credence rank capped by the source's trust ceiling (hivemind trust)."""
        r = c.rank()
        if r < 0:            # UNKNOWN — never wins
            return r
        return min(r, self.trust_ceiling(c.source))

    def head(self) -> Optional[str]:
        """The current ledger head (entry_hash of the last chained claim)."""
        row = self._conn.execute(
            "SELECT entry_hash FROM claims WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else None

    def verify_chain(self, *, check_signatures: bool = False) -> dict:
        """Walk the append-only ledger and confirm no entry was altered — the
        tamper-evidence guarantee. Editing any past claim changes its entry_hash,
        which breaks every link after it: the chain snaps visibly. With
        check_signatures, also verify each signed entry against its agent's
        registered Ed25519 public key (authenticity)."""
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE seq IS NOT NULL ORDER BY seq ASC"
        ).fetchall()
        prev = _GENESIS
        for r in rows:
            c = _row_to_claim(r)
            if c.prev_hash != prev:
                return {"ok": False, "entries": len(rows), "broken_at": c.seq,
                        "detail": f"chain link broken at seq {c.seq} (prev_hash mismatch)"}
            recomputed = hashlib.sha256(
                (c.content_hash_payload() + (c.prev_hash or "")).encode("utf-8")
            ).hexdigest()
            if recomputed != c.entry_hash:
                return {"ok": False, "entries": len(rows), "broken_at": c.seq,
                        "detail": f"content altered at seq {c.seq} (entry_hash mismatch)"}
            if check_signatures and c.signature:
                from . import signing
                pub = self.agent_pubkey(c.source) or c.dims.get("pubkey")
                if not pub or not signing.verify(c.entry_hash, c.signature, pub):
                    return {"ok": False, "entries": len(rows), "broken_at": c.seq,
                            "detail": f"signature invalid at seq {c.seq}"}
            prev = c.entry_hash
        detail = "chain intact" + (" + signatures valid" if check_signatures else "")
        return {"ok": True, "entries": len(rows), "broken_at": None, "detail": detail,
                "head": prev if rows else None}

    # ------------------------------------------------------------------- read
    def get(self, claim_id: str) -> Optional[Claim]:
        row = self._conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return _row_to_claim(row) if row else None

    def history(self, subject: str, predicate: str) -> list[Claim]:
        """Full accumulated timeline across the subject's whole identity set."""
        aliases = self._alias_set(subject)
        placeholders = ",".join("?" * len(aliases))
        rows = self._conn.execute(
            f"SELECT * FROM claims WHERE subject IN ({placeholders}) AND predicate = ? "
            "ORDER BY observed_at DESC, recorded_at DESC",
            (*aliases, predicate),
        ).fetchall()
        return [_row_to_claim(r) for r in rows]

    def _context_match(self, claim: Claim, query: dict) -> bool:
        """A claim matches a context query if, for each queried key, the claim is
        either silent on it (more general) or agrees. Extra claim context is fine."""
        for k, v in query.items():
            cv = claim.context.get(k)
            if cv is not None and cv != v:
                return False
        return True

    def _candidates(self, subject: str, predicate: str,
                    as_of: Optional[str], context: Optional[dict]) -> list[Claim]:
        aliases = self._alias_set(subject)
        placeholders = ",".join("?" * len(aliases))
        rows = self._conn.execute(
            f"SELECT * FROM claims WHERE subject IN ({placeholders}) "
            "AND predicate = ? AND retracted = 0",
            (*aliases, predicate),
        ).fetchall()
        claims = [_row_to_claim(r) for r in rows]
        if as_of is not None:
            claims = [c for c in claims if c.observed_at <= as_of]
        if context:
            claims = [c for c in claims if self._context_match(c, context)]
        superseded = {c.supersedes for c in claims if c.supersedes}
        claims = [c for c in claims if c.claim_id not in superseded]
        return claims

    def resolve(
        self, subject: str, predicate: str, *,
        as_of: Optional[str] = None, context: Optional[dict] = None,
        profile: Optional[Profile] = None,
    ) -> Resolution:
        """Compute current truth for (subject, predicate), honoring identity,
        context scoping (valid-time via as_of is one axis), typed credence, and
        lifecycle.

        BELIEVED: highest credence rank wins (POINT confidence / INTERVAL
        midpoint; UNKNOWN never outranks a real estimate); ties -> most recent.
        DETERMINED: latest authoritative observation; disagreement at the latest
        time is a DeterminedConflictError.
        """
        candidates = self._candidates(subject, predicate, as_of, context)
        eff_profile = profile or (candidates[0].profile if candidates else self.default_profile)
        if not candidates:
            return Resolution(subject, predicate, None, [], False, "no claims")

        def val_key(c: Claim) -> str:
            return json.dumps(c.value, sort_keys=True, default=str)

        # Conflict is disagreement among claims that actually estimate a value.
        estimating = [c for c in candidates if c.credence_type != CredenceType.UNKNOWN]
        distinct_values = {val_key(c) for c in estimating}
        conflict = len(distinct_values) > 1

        if eff_profile == Profile.DETERMINED:
            latest = max(c.observed_at for c in candidates)
            top = [c for c in candidates if c.observed_at == latest]
            if len({val_key(c) for c in top}) > 1:
                raise DeterminedConflictError(
                    f"DETERMINED conflict on {subject}.{predicate}: disagreeing "
                    f"authoritative claims at {latest}. Fix the source of truth."
                )
            chosen = sorted(top, key=lambda c: c.recorded_at)[-1]
            others = [c for c in candidates if c.claim_id != chosen.claim_id]
            return Resolution(subject, predicate, chosen, others, conflict,
                              f"determined: latest authoritative observation at {latest}")

        chosen = sorted(candidates, key=lambda c: (self._eff_rank(c), c.observed_at, c.recorded_at))[-1]
        others = [c for c in candidates if c.claim_id != chosen.claim_id]
        if chosen.credence_type == CredenceType.UNKNOWN:
            reason = "believed: only UNKNOWN claims — we explicitly do not know"
        else:
            eff = self._eff_rank(chosen)
            capped = "" if eff == chosen.rank() else f" (capped from {chosen.rank():.2f} by trust)"
            reason = (f"believed: top credence {eff:.2f} "
                      f"({chosen.credence_type.value}) from '{chosen.source}'{capped}"
                      + (" (conflict surfaced)" if conflict else ""))
        return Resolution(subject, predicate, chosen, others, conflict, reason)

    def conflicts(self, subject: Optional[str] = None) -> list[dict[str, Any]]:
        q = "SELECT DISTINCT subject, predicate FROM claims WHERE retracted = 0 AND predicate != ?"
        params: list[Any] = [SAME_AS]
        if subject is not None:
            q += " AND subject = ?"; params.append(subject)
        pairs = self._conn.execute(q, params).fetchall()
        seen: set[tuple] = set()
        out = []
        for r in pairs:
            key = (self.canonical(r["subject"]), r["predicate"])
            if key in seen:
                continue
            seen.add(key)
            res = self.resolve(r["subject"], r["predicate"])
            if res.conflict and res.chosen is not None:
                out.append({
                    "subject": r["subject"], "predicate": r["predicate"],
                    "chosen": res.chosen.to_dict(),
                    "alternatives": [c.to_dict() for c in res.alternatives],
                })
        return out

    def relationships(self, subject: str, *, as_of: Optional[str] = None) -> list[Claim]:
        aliases = self._alias_set(subject)
        placeholders = ",".join("?" * len(aliases))
        rows = self._conn.execute(
            f"SELECT DISTINCT subject, predicate FROM claims "
            f"WHERE subject IN ({placeholders}) AND obj IS NOT NULL "
            "AND retracted = 0 AND predicate != ?",
            (*aliases, SAME_AS),
        ).fetchall()
        out: list[Claim] = []
        for r in rows:
            res = self.resolve(r["subject"], r["predicate"], as_of=as_of)
            if res.chosen is not None and res.chosen.obj is not None:
                out.append(res.chosen)
        return out

    def provenance(self, claim_id: str) -> dict[str, Any]:
        """Trace a claim to its roots via derivation edges (atom 4)."""
        c = self.get(claim_id)
        if c is None:
            return {}
        return {
            "claim_id": c.claim_id,
            "value": c.value,
            "source": c.source,
            "evidence": c.evidence,
            "derived_from": [self.provenance(d) for d in c.derived_from],
        }

    def search(self, *, subject: Optional[str] = None, predicate: Optional[str] = None,
               source: Optional[str] = None, text: Optional[str] = None,
               limit: int = 100) -> list[Claim]:
        q = "SELECT * FROM claims WHERE 1=1"
        params: list[Any] = []
        if subject is not None:
            q += " AND subject = ?"; params.append(subject)
        if predicate is not None:
            q += " AND predicate = ?"; params.append(predicate)
        if source is not None:
            q += " AND source = ?"; params.append(source)
        if text is not None:
            q += " AND (subject LIKE ? OR predicate LIKE ? OR value_json LIKE ? OR evidence LIKE ?)"
            like = f"%{text}%"; params.extend([like, like, like, like])
        q += " ORDER BY recorded_at DESC LIMIT ?"; params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [_row_to_claim(r) for r in rows]

    def subjects(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT subject FROM claims WHERE predicate != ? ORDER BY subject",
            (SAME_AS,),
        ).fetchall()
        return [r["subject"] for r in rows]

    def stats(self) -> dict[str, int]:
        c = self._conn.execute
        return {
            "claims": c("SELECT COUNT(*) n FROM claims").fetchone()["n"],
            "subjects": c("SELECT COUNT(DISTINCT subject) n FROM claims WHERE predicate != ?",
                          (SAME_AS,)).fetchone()["n"],
            "retracted": c("SELECT COUNT(*) n FROM claims WHERE retracted = 1").fetchone()["n"],
            "identity_links": c("SELECT COUNT(*) n FROM claims WHERE predicate = ?",
                                (SAME_AS,)).fetchone()["n"],
        }

    def close(self) -> None:
        self._conn.close()
