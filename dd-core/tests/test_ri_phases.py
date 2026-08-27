"""Recursive Improvement -- Phases 1-4 (precision, detection, learning, metrics).
All deterministic; no model in any path exercised here.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_core import DynamicDataStore
from dd_core.recursive_improvement import (
    evidence, probes, metrics, record, gate, learn, wiring,
)


def _repo(files: dict) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    return d


def _store():
    fd, path = tempfile.mkstemp(suffix=".ddb")
    os.close(fd)
    os.remove(path)
    return DynamicDataStore(path), path


# --- Phase 1: evidence validation -------------------------------------------

def test_evidence_verdicts():
    r = _repo({"real.py": "a\nb\nc\n"})
    assert evidence.validate_evidence(r, "real.py:2").verdict == "VERIFIED"
    assert evidence.validate_evidence(r, "real.py").verdict == "VERIFIED"
    assert evidence.validate_evidence(r, "ghost.py:5").verdict == "UNVERIFIED"
    assert evidence.validate_evidence(r, "just prose").verdict == "NO_LOCATOR"


def test_hallucinated_evidence_is_downranked_below_the_floor():
    r = _repo({"real.py": "x\n"})
    d, path = _store()
    try:
        n, dup, reop, supp = record.record_gaps(d, [
            {"slug": "real", "title": "real", "area": "a", "severity": "high",
             "confidence": 0.9, "evidence": "real.py:1"},
            {"slug": "fake", "title": "ghost", "area": "b", "severity": "high",
             "confidence": 0.9, "evidence": "nope.py:5"},
        ], "s", "reflex-auditor", "arch.audit:", repo_root=r)
        assert any(s.get("reason") == "unverified-evidence" for s in supp)
        # the hallucinated one is recorded but shoved below the 0.6 floor
        act = gate.triage(d, 0.6, "arch.audit:")["act_now"]
        assert [g["subject"] for g in act] == ["arch.audit:real"]
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


# --- Phase 2: unwired-param probe -------------------------------------------

def test_probe_finds_built_but_not_wired_param():
    r = _repo({"engine.py":
               "class Engine:\n"
               "    def __init__(self, entity_repo=None, identity_coordinator=None):\n"
               "        self.entity_repo = entity_repo\n"
               "        self.coordinator = coordinator\n"
               "e = Engine(entity_repo=object())\n"})
    found = probes.unwired_optional_params(r)
    slugs = {g["slug"] for g in found}
    assert "unwired-param-identity-coordinator" in slugs
    # entity_repo IS passed -> must not be flagged
    assert "unwired-param-entity-repo" not in slugs


def test_probe_ignores_a_param_that_is_passed():
    r = _repo({"m.py":
               "def build(processor=None):\n    return processor\n"
               "build(processor=1)\n"})
    assert probes.unwired_optional_params(r) == []


# --- Wiring Prover: declared & consumed & never-provided --------------------

def test_wiring_prover_flags_unwired_param_and_capability_field():
    r = _repo({"app.py":
        "from dataclasses import dataclass\n"
        "from typing import Optional, Any\n"
        "@dataclass\n"
        "class Engine:\n"
        "    good_service: Any = None\n"
        "    truth_mode_state: Optional[Any] = None\n"   # capability field
        "    base_value: Any = None\n"                    # data field (ignored)
        "class Builder:\n"
        "    def __init__(self, identity_coordinator=None, missing_repo=None):\n"
        "        self.identity_coordinator = identity_coordinator\n"
        "        self.missing_repo = missing_repo\n"
        "def use(e, b):\n"
        "    if e.truth_mode_state is not None: pass\n"
        "    z = e.base_value\n"
        "    b.missing_repo.query()\n"
        "    e.good_service.run()\n"
        "def build():\n"
        "    e = Engine()\n"
        "    e.good_service = RealService()\n"
        "    c = RealCoord()\n"
        "    return Builder(identity_coordinator=c)\n"})
    slugs = {f["slug"] for f in wiring.unwired_capabilities(r)}
    # the unwired param and the unwired capability field are caught
    assert "unwired-missing-repo" in slugs
    assert "unwired-truth-mode-state" in slugs
    # provided deps + a plain DATA field must NOT be flagged (no false positives)
    assert "unwired-identity-coordinator" not in slugs  # keyword-provided
    assert "unwired-good-service" not in slugs           # assignment-provided
    assert "unwired-base-value" not in slugs             # data field, not a capability


def test_wiring_prover_keyword_forward_of_a_local_counts_as_provided():
    """A keyword pass of a bare local (Result(x=x)) IS provision -- the local
    holds a real value -- so it must not read as unwired (the false-positive
    class that made the naive keyword-only probe unusable at medium confidence)."""
    r = _repo({"m.py":
        "class R:\n"
        "    def __init__(self, source_repo=None):\n"
        "        self.source_repo = source_repo\n"
        "def go(sr):\n"
        "    r = R(source_repo=sr)\n"      # keyword forward of a local -> provided
        "    return r.source_repo\n"})     # consumed
    assert wiring.unwired_capabilities(r) == []


# --- Phase 3: learning from dispositions ------------------------------------

def test_antipattern_and_false_positive_hints_from_ledger():
    d, path = _store()
    try:
        # a fixed finding -> becomes an anti-pattern hint
        d.assert_claim("arch.audit:merge-wipe", "status", "open", source="r",
                       confidence=0.9, author_kind="ai",
                       dims={"title": "wholesale replace destroys merge bindings",
                             "area": "core"})
        d.assert_claim("arch.audit:merge-wipe", "status", "fixed", source="ezra",
                       confidence=1.0, author_kind="human")
        # a wrong-wontfix -> becomes a false-positive caution
        d.assert_claim("arch.audit:bogus", "status", "open", source="r",
                       confidence=0.9, author_kind="ai",
                       dims={"title": "confidence is a float somewhere", "area": "x"})
        d.assert_claim("arch.audit:bogus", "status", "wontfix", source="ezra",
                       confidence=1.0, author_kind="human",
                       evidence="false positive, wrong premise")

        class _Cfg:
            gap_prefix = "arch.gap:"
            audit_prefix = "arch.audit:"
            gap_db = path
            dd_core_path = ""
            def abspath(self, p): return p

        ap = learn.antipattern_hints(_Cfg())
        fp = learn.false_positive_hints(_Cfg())
        assert "destroys merge bindings" in ap
        assert "confidence is a float" in fp
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


# --- Phase 4: metrics -------------------------------------------------------

def test_metrics_precision_and_counts():
    d, path = _store()
    try:
        # 2 true positives
        for s in ("a", "b"):
            d.assert_claim(f"arch.audit:{s}", "status", "open", source="r",
                           confidence=0.9, author_kind="ai", dims={"title": s})
            d.assert_claim(f"arch.audit:{s}", "status", "fixed", source="ezra",
                           confidence=1.0, author_kind="human")
        # 1 false positive
        d.assert_claim("arch.audit:c", "status", "open", source="r",
                       confidence=0.9, author_kind="ai", dims={"title": "c"})
        d.assert_claim("arch.audit:c", "status", "wontfix", source="ezra",
                       confidence=1.0, author_kind="human",
                       evidence="overstated, not a real bug")
        # 1 not-a-bug (by design)
        d.assert_claim("arch.audit:d", "status", "open", source="r",
                       confidence=0.9, author_kind="ai", dims={"title": "d"})
        d.assert_claim("arch.audit:d", "status", "wontfix", source="ezra",
                       confidence=1.0, author_kind="human", evidence="by design")
        # 1 still open
        d.assert_claim("arch.audit:e", "status", "open", source="r",
                       confidence=0.9, author_kind="ai", dims={"title": "e"})

        m = metrics.compute(d, ("arch.audit:",))
        assert m["true_positive"] == 2
        assert m["false_positive"] == 1
        assert m["not_a_bug"] == 1
        assert m["pending"] == 1
        assert m["precision"] == round(2 / 3, 3)   # TP/(TP+FP)
        assert "VERDICT" in metrics.render(m)
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_metrics_surface_near_duplicate_open_noise():
    """Precision is blind to stale/duplicate re-flags -- a clean precision with a
    high duplicate_open still means the loop is over-generating. The metric must
    surface that noise explicitly."""
    d, path = _store()
    try:
        # two OPEN findings that are the SAME root cause under different slugs
        d.assert_claim("arch.audit:kill-switch-partial-wiring", "status", "open",
                       source="r", confidence=0.9, author_kind="ai",
                       dims={"title": "centralized kill switch consulted at only "
                                      "a subset of capability entry points"})
        d.assert_claim("arch.audit:kill-switch-coverage-partial", "status", "open",
                       source="r", confidence=0.9, author_kind="ai",
                       dims={"title": "kill switch consulted at only a subset of "
                                      "capability entry points; several unguarded"})
        # one unrelated open finding (must NOT be counted as a duplicate)
        d.assert_claim("arch.audit:unrelated", "status", "open", source="r",
                       confidence=0.9, author_kind="ai",
                       dims={"title": "ledger checksum decimal scale round trip"})

        m = metrics.compute(d, ("arch.audit:",))
        assert m["duplicate_open"] == 2, m
        assert m["duplicate_open_rate"] == round(2 / 3, 3)
        out = metrics.render(m)
        assert "near-duplicate open" in out
        assert "over-generating" in out   # the honest caveat is shown
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_known_findings_context_lists_closed_first_and_carries_a_full_ledger():
    """The auditor must be told about ALL already-handled findings (esp. closed
    ones) so it does not re-flag fixed work. Closed items come first and are not
    truncated away by the limit."""
    from dd_core.recursive_improvement import runner
    from dd_core.recursive_improvement.config import ReflexConfig

    d, path = _store()
    try:
        d.assert_claim("arch.audit:done-thing", "status", "open", source="r",
                       confidence=0.9, author_kind="ai", dims={"title": "done"})
        d.assert_claim("arch.audit:done-thing", "status", "fixed", source="ezra",
                       confidence=1.0, author_kind="human")
        d.assert_claim("arch.audit:still-open", "status", "open", source="r",
                       confidence=0.9, author_kind="ai", dims={"title": "open"})
    finally:
        d.close()
    cfg = ReflexConfig(repo_root=".", gap_db=path)
    ctx = runner._known_findings_context(cfg)
    # both the fixed and the open finding are present; fixed appears before open
    assert "arch.audit:done-thing" in ctx and "arch.audit:still-open" in ctx
    assert ctx.index("done-thing") < ctx.index("still-open")
    assert "including fixed/accepted" in ctx
    os.path.exists(path) and os.remove(path)


def test_empty_or_bad_repo_does_not_downrank_findings():
    """A misconfigured repo_root (empty index) is a config error, not a mass
    hallucination -- validation must SKIP, not down-rank every finding."""
    d, path = _store()
    try:
        n, dup, reop, supp = record.record_gaps(d, [
            {"slug": "x", "title": "x", "area": "a", "severity": "high",
             "confidence": 0.9, "evidence": "real.py:1"},
        ], "s", "reflex-auditor", "arch.audit:",
            repo_root="/definitely/not/a/real/path")
        # no unverified-evidence suppression, confidence untouched -> still act-now
        assert not any(s.get("reason") == "unverified-evidence" for s in supp)
        assert gate.triage(d, 0.6, "arch.audit:")["act_now"]
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_config_roundtrips_all_fields():
    """to_dict/load must preserve every field incl. the tuple/provider ones."""
    import json
    import tempfile as _t
    from dd_core.recursive_improvement.config import ReflexConfig
    c = ReflexConfig(provider="generic", cli="llm",
                     cmd_template=("{cli}", "-m", "{model}"),
                     substantive_prefixes=("engine/",),
                     north_star_anchors=("ROADMAP.md",), threshold=5)
    fd, p = _t.mkstemp(suffix=".json")
    os.close(fd)
    with open(p, "w") as fh:
        json.dump(c.to_dict(), fh)
    loaded = ReflexConfig.load(p)
    os.remove(p)
    assert loaded.provider == "generic"
    assert loaded.cli == "llm"
    assert loaded.cmd_template == ("{cli}", "-m", "{model}")
    assert loaded.substantive_prefixes == ("engine/",)
    assert loaded.threshold == 5
