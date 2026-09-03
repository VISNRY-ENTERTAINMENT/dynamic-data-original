"""Tests for dd-core: the Dynamic Data claim store.

These lock the primitive's promises: claims accumulate (never overwrite),
truth is resolved by confidence, conflicts surface, time-travel works, and the
DETERMINED profile refuses to guess.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dd_core import DynamicDataStore, Profile, DeterminedConflictError  # noqa: E402


@pytest.fixture
def ddb():
    store = DynamicDataStore(":memory:")
    yield store
    store.close()


def test_assert_and_resolve_single_claim(ddb):
    ddb.assert_claim("EZE", "role", "manager", source="HR", confidence=1.0)
    res = ddb.resolve("EZE", "role")
    assert res.chosen.value == "manager"
    assert res.conflict is False


def test_highest_confidence_wins(ddb):
    ddb.assert_claim("EZE", "email", "eze@email.com", source="signup", confidence=0.72,
                     observed_at="2000-01-24T00:00:00+00:00")
    ddb.assert_claim("EZE", "email", "mangeb@email.com", source="directory", confidence=0.90,
                     observed_at="2002-01-24T00:00:00+00:00")
    res = ddb.resolve("EZE", "email")
    assert res.chosen.value == "mangeb@email.com"   # .90 beats .72
    assert res.conflict is True                     # disagreement surfaced
    assert len(res.alternatives) == 1


def test_claims_accumulate_not_overwrite(ddb):
    ddb.assert_claim("bob", "role", "employee", source="HR", confidence=1.0,
                     observed_at="2000-01-15T00:00:00+00:00")
    ddb.assert_claim("bob", "role", "manager", source="HR", confidence=1.0,
                     observed_at="2005-06-01T00:00:00+00:00")
    # Full history is preserved.
    hist = ddb.history("bob", "role")
    assert len(hist) == 2
    assert {c.value for c in hist} == {"employee", "manager"}


def test_time_travel(ddb):
    ddb.assert_claim("bob", "role", "employee", source="HR", confidence=1.0,
                     observed_at="2000-01-15T00:00:00+00:00")
    ddb.assert_claim("bob", "role", "manager", source="HR", confidence=1.0,
                     observed_at="2005-06-01T00:00:00+00:00")
    # "What did we believe in 2002?"  -> employee (manager not yet observed)
    past = ddb.resolve("bob", "role", as_of="2002-01-01T00:00:00+00:00")
    assert past.chosen.value == "employee"
    # "What do we believe now?" -> manager
    now = ddb.resolve("bob", "role", as_of="2026-01-01T00:00:00+00:00")
    assert now.chosen.value == "manager"


def test_supersedes_drops_old_claim(ddb):
    old = ddb.assert_claim("x", "status", "draft", source="a", confidence=1.0)
    ddb.assert_claim("x", "status", "final", source="a", confidence=1.0,
                     supersedes=old.claim_id)
    res = ddb.resolve("x", "status")
    assert res.chosen.value == "final"
    assert res.conflict is False   # superseded claim no longer competes


def test_retract_removes_from_resolution_but_keeps_history(ddb):
    c = ddb.assert_claim("y", "flag", "on", source="a", confidence=1.0)
    ddb.retract(c.claim_id, source="a", reason="mistake")
    assert ddb.resolve("y", "flag").chosen is None
    assert len(ddb.history("y", "flag")) == 1   # still on the record


def test_idempotent_assert(ddb):
    ddb.assert_claim("a", "b", "c", source="s", confidence=1.0,
                     observed_at="2020-01-01T00:00:00+00:00")
    ddb.assert_claim("a", "b", "c", source="s", confidence=1.0,
                     observed_at="2020-01-01T00:00:00+00:00")
    assert ddb.stats()["claims"] == 1


def test_relationships(ddb):
    ddb.assert_claim("EZE", "part_of", None, obj="Managers", source="HR", confidence=0.90)
    rels = ddb.relationships("EZE")
    assert len(rels) == 1
    assert rels[0].obj == "Managers"
    assert rels[0].predicate == "part_of"


def test_conflicts_listing(ddb):
    ddb.assert_claim("leshy", "weakness", "fire", source="designer", confidence=1.0)
    ddb.assert_claim("leshy", "weakness", "ice", source="telemetry", confidence=0.30)
    conflicts = ddb.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["chosen"]["value"] == "fire"   # designer authority wins


def test_determined_profile_rejects_low_confidence(ddb):
    with pytest.raises(ValueError):
        ddb.assert_claim("acct", "balance", 500, source="ledger",
                         confidence=0.9, profile=Profile.DETERMINED)


def test_determined_conflict_is_a_fault(ddb):
    # Two disagreeing authoritative claims at the SAME observation time = fault.
    ddb.assert_claim("acct", "balance", 500, source="ledgerA", confidence=1.0,
                     observed_at="2026-01-01T00:00:00+00:00", profile=Profile.DETERMINED)
    ddb.assert_claim("acct", "balance", 600, source="ledgerB", confidence=1.0,
                     observed_at="2026-01-01T00:00:00+00:00", profile=Profile.DETERMINED)
    with pytest.raises(DeterminedConflictError):
        ddb.resolve("acct", "balance", profile=Profile.DETERMINED)


def test_determined_latest_observation_wins(ddb):
    ddb.assert_claim("acct", "balance", 500, source="ledger", confidence=1.0,
                     observed_at="2026-01-01T00:00:00+00:00", profile=Profile.DETERMINED)
    ddb.assert_claim("acct", "balance", 750, source="ledger", confidence=1.0,
                     observed_at="2026-02-01T00:00:00+00:00", profile=Profile.DETERMINED)
    res = ddb.resolve("acct", "balance", profile=Profile.DETERMINED)
    assert res.chosen.value == 750


def test_search_and_subjects(ddb):
    ddb.assert_claim("example_project", "test_pool", "NullPool", source="ezra", confidence=1.0,
                     evidence="conftest sets APP_ENGINE_NULLPOOL=1")
    ddb.assert_claim("example_project", "prod_branch", "blue", source="ezra", confidence=1.0)
    assert "example_project" in ddb.subjects()
    hits = ddb.search(text="NullPool")
    assert len(hits) == 1 and hits[0].value == "NullPool"
