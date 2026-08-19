"""Tests for the hivemind + trust layer (dd-core v0.3):
authenticated authorship, trust ceilings, tamper-evident hash chain, and
optional Ed25519 signatures. See ../../03_HIVEMIND_AND_SECURITY.md.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dd_core import DynamicDataStore, signing  # noqa: E402


@pytest.fixture
def ddb():
    s = DynamicDataStore(":memory:")
    yield s
    s.close()


# --- Tamper-evident hash chain (integrity) ---------------------------------
def test_chain_verifies_intact(ddb):
    for i in range(5):
        ddb.assert_claim("s", "p", f"v{i}", source="a", confidence=1.0,
                         observed_at=f"2026-01-0{i+1}T00:00:00+00:00")
    res = ddb.verify_chain()
    assert res["ok"] is True
    assert res["entries"] == 5
    assert ddb.head() is not None


def test_tampering_breaks_the_chain(ddb):
    c0 = ddb.assert_claim("s", "p", "original", source="a", confidence=1.0)
    ddb.assert_claim("s", "q", "second", source="a", confidence=1.0)
    ddb.assert_claim("s", "r", "third", source="a", confidence=1.0)
    # Tamper directly in the DB: rewrite a past claim's value.
    ddb._conn.execute("UPDATE claims SET value_json = ? WHERE claim_id = ?",
                      ('"HACKED"', c0.claim_id))
    ddb._conn.commit()
    res = ddb.verify_chain()
    assert res["ok"] is False
    assert res["broken_at"] == 0            # the altered entry
    assert "altered" in res["detail"]


# --- Authenticated authorship + trust ceilings -----------------------------
def test_trust_ceiling_caps_low_trust_agent(ddb):
    ddb.register_agent("planner", kind="ai", trust_ceiling=0.95)
    ddb.register_agent("scout", kind="ai", trust_ceiling=0.30)
    # Scout asserts with full confidence, but is only trusted to 0.30.
    ddb.assert_claim("exampleapp", "head", "abc", source="scout", confidence=1.0)
    ddb.assert_claim("exampleapp", "head", "def", source="planner", confidence=0.5)
    res = ddb.resolve("exampleapp", "head")
    assert res.chosen.source == "planner"   # 0.50 (planner) > capped 0.30 (scout)
    assert res.conflict is True


def test_unregistered_source_defaults_to_full_trust(ddb):
    assert ddb.trust_ceiling("nobody") == 1.0


def test_author_kind_recorded(ddb):
    c = ddb.assert_claim("x", "p", "v", source="ezra", confidence=1.0, author_kind="human")
    assert ddb.get(c.claim_id).author_kind == "human"


# --- Authenticity: Ed25519 signatures --------------------------------------
@pytest.mark.skipif(not signing.available(), reason="cryptography not installed")
def test_signed_claims_verify(ddb):
    key = signing.AgentKey.generate()
    ddb.register_agent("planner", trust_ceiling=0.95, public_key=key.public_hex)
    ddb.assert_claim("exampleapp", "head", "abc", source="planner", confidence=0.9,
                     author_kind="ai", signer=key)
    res = ddb.verify_chain(check_signatures=True)
    assert res["ok"] is True and "signatures valid" in res["detail"]


@pytest.mark.skipif(not signing.available(), reason="cryptography not installed")
def test_forged_signature_fails(ddb):
    key = signing.AgentKey.generate()
    other = signing.AgentKey.generate()
    # Register the agent with the WRONG public key -> its signature won't verify.
    ddb.register_agent("planner", trust_ceiling=0.95, public_key=other.public_hex)
    ddb.assert_claim("exampleapp", "head", "abc", source="planner", confidence=0.9, signer=key)
    res = ddb.verify_chain(check_signatures=True)
    assert res["ok"] is False and "signature invalid" in res["detail"]


@pytest.mark.skipif(not signing.available(), reason="cryptography not installed")
def test_signature_roundtrip_primitive():
    key = signing.AgentKey.generate()
    sig = key.sign("hello")
    assert signing.verify("hello", sig, key.public_hex) is True
    assert signing.verify("tampered", sig, key.public_hex) is False


# --- Chain survives legitimate retraction ----------------------------------
def test_retraction_does_not_break_chain(ddb):
    c = ddb.assert_claim("s", "p", "v", source="a", confidence=1.0)
    ddb.assert_claim("s", "q", "w", source="a", confidence=1.0)
    ddb.retract(c.claim_id, source="a", reason="mistake")
    assert ddb.verify_chain()["ok"] is True   # asserted content immutable
    assert ddb.resolve("s", "p").chosen is None
