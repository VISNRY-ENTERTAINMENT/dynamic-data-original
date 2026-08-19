#!/usr/bin/env python3
"""dd-mcp — expose a Dynamic Data claim store to any MCP-capable AI (Claude, etc.)
as read/write project memory.

This lets an assistant RECORD what it learns as sourced, confidence-weighted,
time-stamped claims and later ASK what is currently believed, what changed, and
where beliefs conflict — instead of re-deriving project facts every session.

Run:
    pip install "mcp[cli]"
    python dd_mcp_server.py            # uses ./dynamic_data.ddb
    DD_DB=/path/to/project.ddb python dd_mcp_server.py

Register with Claude Code (from the dd-core folder):
    claude mcp add dynamic-data -- python /abs/path/to/dd_mcp_server.py

The DB path can also be pinned per project via the DD_DB environment variable.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core import (  # noqa: E402
    DynamicDataStore,
    Profile,
    CredenceType,
    DeterminedConflictError,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - dependency hint
    sys.stderr.write(
        "The MCP SDK is not installed. Run:  pip install \"mcp[cli]\"\n"
        "(dd_core itself needs no dependencies; only this server does.)\n"
    )
    raise

_DB_PATH = os.environ.get("DD_DB", "dynamic_data.ddb")
_store = DynamicDataStore(_DB_PATH)

# Authenticated authorship: when DD_AGENT is set, the server STAMPS this agent id
# as the source of every claim it writes — the connecting AI cannot spoof who it
# is (the identity comes from the environment/connection, not the tool call).
# In a hivemind, each agent connects with its own DD_AGENT. Unset = "assistant".
_AGENT = os.environ.get("DD_AGENT", "").strip() or None
_AUTHOR_KIND = os.environ.get("DD_AUTHOR_KIND", "ai").strip() or "ai"

mcp = FastMCP(
    "dynamic-data",
    instructions=(
        "Dynamic Data project memory. Every fact is a CLAIM with a source, a "
        "confidence in [0,1], a time, and optional evidence — claims accumulate "
        "and never overwrite. Record durable, non-obvious project facts and "
        "decisions with assert_claim (cite the source and set an honest "
        "confidence). Read current truth with resolve, the timeline with "
        "history, and disagreements with list_conflicts. Prefer recording a "
        "claim over asserting a fact you cannot source."
    ),
)


@mcp.tool()
def assert_claim(
    subject: str,
    predicate: str,
    value: str = "",
    obj: str = "",
    source: str = "assistant",
    confidence: float = 0.8,
    observed_at: str = "",
    evidence: str = "",
    profile: str = "believed",
) -> dict:
    """Record a fact as a claim (append-only; never overwrites).

    subject/predicate/value is the fact (e.g. subject='exampleapp',
    predicate='prod_branch', value='blue'). Set `obj` instead of/along with
    value to record a relationship (subject --predicate--> obj). Always give a
    `source` and an honest `confidence` (1.0 only for things you directly
    verified or a human stated). `profile`='determined' pins confidence to 1.0
    and treats disagreement as a fault (use for authoritative single-source facts).
    """
    claim = _store.assert_claim(
        subject, predicate,
        value if value != "" else None,
        obj=obj or None,
        source=_AGENT or source,          # authenticated agent id wins if set
        confidence=confidence,
        observed_at=observed_at or None,
        evidence=evidence,
        profile=Profile(profile),
        author_kind=_AUTHOR_KIND,
    )
    return claim.to_dict()


@mcp.tool()
def resolve(subject: str, predicate: str, as_of: str = "") -> dict:
    """Return the currently-believed truth for a (subject, predicate): the chosen
    claim, the alternatives, whether beliefs conflict, and why it was chosen.
    Pass an ISO timestamp as `as_of` to time-travel ('what did we believe then')."""
    try:
        return _store.resolve(subject, predicate, as_of=as_of or None).to_dict()
    except DeterminedConflictError as e:
        return {"error": "determined_conflict", "detail": str(e)}


@mcp.tool()
def history(subject: str, predicate: str) -> list[dict]:
    """The full accumulated timeline for a (subject, predicate) — who claimed
    what, when, with what confidence, including superseded/retracted claims."""
    return [c.to_dict() for c in _store.history(subject, predicate)]


@mcp.tool()
def list_conflicts(subject: str = "") -> list[dict]:
    """Every (subject, predicate) where current claims disagree on the value,
    with the currently-chosen claim and the competing alternatives."""
    return _store.conflicts(subject=subject or None)


@mcp.tool()
def search(subject: str = "", predicate: str = "", source: str = "", text: str = "") -> list[dict]:
    """Find claims by any combination of subject, predicate, source, or free text."""
    return [c.to_dict() for c in _store.search(
        subject=subject or None, predicate=predicate or None,
        source=source or None, text=text or None)]


@mcp.tool()
def relationships(subject: str) -> list[dict]:
    """Resolved relationship claims originating at `subject` (subject -> obj edges)."""
    return [c.to_dict() for c in _store.relationships(subject)]


@mcp.tool()
def subjects() -> list[str]:
    """List every subject the memory knows about."""
    return _store.subjects()


@mcp.tool()
def stats() -> dict:
    """Store statistics: total claims, distinct subjects, retracted, identity links."""
    return _store.stats()


# --- Foundational atoms (v0.2) --------------------------------------------- #

@mcp.tool()
def same_as(a: str, b: str, source: str = "assistant", confidence: float = 1.0) -> dict:
    """Assert that two subjects are the SAME entity (atom: identity). After this,
    claims made under either name resolve together. Use when you discover two
    labels refer to one thing (e.g. an id and a display name)."""
    return _store.same_as(a, b, source=source, confidence=confidence).to_dict()


@mcp.tool()
def derive(subject: str, predicate: str, value: str, derived_from: list[str],
           source: str = "inference", confidence: float = 0.7, evidence: str = "") -> dict:
    """Record an INFERRED claim, citing the claim_ids it was derived from (atom:
    derivation). This enables belief revision: if a premise is later retracted
    with cascade, this conclusion is retracted too. Pass derived_from as a list
    of claim_ids."""
    return _store.derive(subject, predicate, value, derived_from=tuple(derived_from),
                         source=source, confidence=confidence, evidence=evidence).to_dict()


@mcp.tool()
def provenance(claim_id: str) -> dict:
    """Trace a claim to its roots through its derivation edges (atom: derivation).
    Returns a tree of what this claim was inferred from."""
    return _store.provenance(claim_id)


@mcp.tool()
def retract(claim_id: str, source: str = "assistant", reason: str = "", cascade: bool = True) -> dict:
    """Retract a claim (it stops competing in resolution; history is kept). With
    cascade=True, everything derived from it is retracted too (belief revision).
    Returns the list of retracted claim_ids."""
    return {"retracted": _store.retract(claim_id, source=source, reason=reason, cascade=cascade)}


@mcp.tool()
def assert_unknown(subject: str, predicate: str, source: str = "assistant", evidence: str = "") -> dict:
    """Record explicit IGNORANCE about a (subject, predicate) (atom: typed
    credence). This is NOT confidence 0.5 — it says 'we deliberately do not
    know', and it never outranks a real estimate. Prefer this over guessing."""
    return _store.assert_claim(subject, predicate, None, source=source,
                              credence_type=CredenceType.UNKNOWN, evidence=evidence).to_dict()


@mcp.tool()
def describe_predicate(predicate: str, key: str, value: str, source: str = "assistant") -> dict:
    """Describe a predicate/dimension from inside the system (reflexivity). e.g.
    describe_predicate('weakness','unit','damage_multiplier'). The vocabulary is
    itself dynamic data — this is how new dimensions are defined as data."""
    return _store.describe(predicate, key, value, source=source).to_dict()


# --- Hivemind + trust (v0.3) ----------------------------------------------- #

@mcp.tool()
def whoami() -> dict:
    """Report the authenticated agent identity this connection writes as, and
    the memory it is attached to. Source is stamped by the server, not chosen by
    the model."""
    return {"agent": _AGENT or "assistant", "author_kind": _AUTHOR_KIND,
            "db": _DB_PATH, "trust_ceiling": _store.trust_ceiling(_AGENT or "assistant")}


@mcp.tool()
def register_agent(agent_id: str, kind: str = "ai", trust_ceiling: float = 0.9,
                   public_key: str = "") -> dict:
    """Register an agent in the shared memory (roster is itself dynamic data):
    its kind (human/ai/mixed), trust ceiling (caps how much its claims can
    weigh), and optional Ed25519 public key for signature verification."""
    return _store.register_agent(agent_id, kind=kind, trust_ceiling=trust_ceiling,
                                 public_key=public_key or None)


@mcp.tool()
def set_trust(agent_id: str, trust_ceiling: float) -> dict:
    """Set/update an agent's trust ceiling. A claim's effective credence is
    capped by its author's trust, so a low-trust agent cannot outweigh a trusted
    one no matter how confident it claims to be."""
    _store.assert_claim(f"agent:{agent_id}", "trust_ceiling", float(trust_ceiling),
                        source=_AGENT or "system", confidence=1.0)
    return {"agent": agent_id, "trust_ceiling": _store.trust_ceiling(agent_id)}


@mcp.tool()
def verify_chain(check_signatures: bool = False) -> dict:
    """Verify the append-only ledger is tamper-free (integrity). Editing any past
    claim breaks its hash and every entry after it — the chain snaps visibly.
    With check_signatures, also verify Ed25519 signatures against registered
    agent public keys (authenticity)."""
    return _store.verify_chain(check_signatures=check_signatures)


if __name__ == "__main__":
    mcp.run()
