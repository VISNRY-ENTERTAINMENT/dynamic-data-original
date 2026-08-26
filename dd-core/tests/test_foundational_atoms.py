"""Tests for the eight foundational atoms (dd-core v0.2).

Each test exercises one atom as a real capability, not just a stored field.
See ../../02_FOUNDATIONAL_ATOMS.md.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dd_core import DynamicDataStore, CredenceType, Profile  # noqa: E402


@pytest.fixture
def ddb():
    s = DynamicDataStore(":memory:")
    yield s
    s.close()


# --- Atom 2: Identity ------------------------------------------------------
def test_identity_fuses_claims_across_aliases(ddb):
    # Two names for the same entity; claims made under each must resolve together.
    ddb.assert_claim("alice@example.com", "role", "employee", source="hr", confidence=0.72,
                     observed_at="2000-01-24T00:00:00+00:00")
    ddb.assert_claim("alice", "role", "manager", source="hr", confidence=0.90,
                     observed_at="2002-01-24T00:00:00+00:00")
    ddb.same_as("alice@example.com", "alice", source="hr")
    res = ddb.resolve("alice@example.com", "role")     # query one alias...
    assert res.chosen.value == "manager"           # ...sees the other alias's claim
    assert len(ddb.history("alice", "role")) == 2


def test_canonical_is_stable_across_aliases(ddb):
    ddb.same_as("A", "B")
    ddb.same_as("B", "C")
    assert ddb.canonical("A") == ddb.canonical("C")  # transitive identity


# --- Atom 4: Derivation + belief revision ----------------------------------
def test_derivation_records_edges_and_provenance(ddb):
    p1 = ddb.assert_claim("dragon", "class", "reptile", source="designer", confidence=1.0)
    p2 = ddb.assert_claim("reptile", "trait", "cold_blooded", source="biology", confidence=1.0)
    inf = ddb.derive("dragon", "weakness", "ice",
                     derived_from=(p1.claim_id, p2.claim_id),
                     source="inference", confidence=0.7,
                     evidence="cold-blooded -> weak to ice")
    prov = ddb.provenance(inf.claim_id)
    roots = {d["value"] for d in prov["derived_from"]}
    assert roots == {"reptile", "cold_blooded"}


def test_belief_revision_cascade_retract(ddb):
    p = ddb.assert_claim("dragon", "class", "reptile", source="designer", confidence=1.0)
    inf = ddb.derive("dragon", "weakness", "ice", derived_from=(p.claim_id,),
                     source="inference", confidence=0.7)
    assert ddb.resolve("dragon", "weakness").chosen.value == "ice"
    # Pull the premise -> the conclusion must fall too.
    retracted = ddb.retract(p.claim_id, source="designer", reason="dragons are not reptiles",
                            cascade=True)
    assert inf.claim_id in retracted
    assert ddb.resolve("dragon", "weakness").chosen is None


# --- Atom 5: Typed credence ------------------------------------------------
def test_unknown_is_not_half(ddb):
    # UNKNOWN must not outrank a real estimate, and must be distinguishable.
    ddb.assert_claim("x", "p", None, source="a", credence_type=CredenceType.UNKNOWN)
    ddb.assert_claim("x", "p", "v", source="b", confidence=0.4)
    res = ddb.resolve("x", "p")
    assert res.chosen.value == "v"          # a 0.4 estimate beats "unknown"
    # With only-unknown, resolution says so explicitly.
    ddb.assert_claim("y", "p", None, source="a", credence_type=CredenceType.UNKNOWN)
    r2 = ddb.resolve("y", "p")
    assert r2.chosen.credence_type == CredenceType.UNKNOWN
    assert "do not know" in r2.reason


def test_interval_credence_ranks_by_midpoint(ddb):
    ddb.assert_claim("t", "p", "wide", source="a", credence_type=CredenceType.INTERVAL,
                     credence_lo=0.1, credence_hi=0.5)          # mid 0.30
    ddb.assert_claim("t", "p", "point", source="b", confidence=0.6)
    assert ddb.resolve("t", "p").chosen.value == "point"       # 0.60 > 0.30


# --- Atom 6: Context (time-travel is one axis) -----------------------------
def test_time_travel_axis(ddb):
    ddb.assert_claim("bob", "role", "employee", source="hr", confidence=1.0,
                     observed_at="2000-01-15T00:00:00+00:00")
    ddb.assert_claim("bob", "role", "manager", source="hr", confidence=1.0,
                     observed_at="2005-06-01T00:00:00+00:00")
    assert ddb.resolve("bob", "role", as_of="2002-01-01T00:00:00+00:00").chosen.value == "employee"


def test_context_scoping_beyond_time(ddb):
    # Same fact, different worlds — context scopes resolution.
    ddb.assert_claim("leshy", "weakness", "fire", source="designer", confidence=1.0,
                     context={"world": "canon"})
    ddb.assert_claim("leshy", "weakness", "poison", source="designer", confidence=1.0,
                     context={"world": "mod"})
    assert ddb.resolve("leshy", "weakness", context={"world": "canon"}).chosen.value == "fire"
    assert ddb.resolve("leshy", "weakness", context={"world": "mod"}).chosen.value == "poison"


# --- Reflexivity -----------------------------------------------------------
def test_reflexive_describe_dimension(ddb):
    # The vocabulary is itself data: describe a predicate from inside the system.
    ddb.describe("weakness", "unit", "damage_multiplier", source="designer")
    ddb.describe("weakness", "range", "0..3", source="designer")
    res = ddb.resolve("predicate:weakness", "unit")
    assert res.chosen.value == "damage_multiplier"


def test_open_dims_bag_carries_future_dimensions(ddb):
    # A dimension nobody built a column for still round-trips via `dims`.
    c = ddb.assert_claim("m", "value", 42, source="s", confidence=1.0,
                         dims={"jurisdiction": "EU", "sensitivity": "high"})
    got = ddb.get(c.claim_id)
    assert got.dims["jurisdiction"] == "EU"
    assert got.dims["sensitivity"] == "high"


# --- Determined profile still holds ---------------------------------------
def test_determined_still_faults_on_disagreement(ddb):
    from dd_core import DeterminedConflictError
    ddb.assert_claim("acct", "balance", 500, source="A", confidence=1.0,
                     observed_at="2026-01-01T00:00:00+00:00", profile=Profile.DETERMINED)
    ddb.assert_claim("acct", "balance", 600, source="B", confidence=1.0,
                     observed_at="2026-01-01T00:00:00+00:00", profile=Profile.DETERMINED)
    with pytest.raises(DeterminedConflictError):
        ddb.resolve("acct", "balance", profile=Profile.DETERMINED)
