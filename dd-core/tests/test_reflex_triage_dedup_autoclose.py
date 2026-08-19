"""The reflex loop must be a managed backlog, not a firehose.

Covers the three behaviors that make it help instead of hinder:
  1. SEMANTIC dedup  -- a re-worded finding (new slug, same issue) collapses.
  2. SEVERITY triage -- high/critical surface now; medium/low stay backlog.
  3. AUTO-CLOSE       -- a commit that says it closed a finding closes it.
All deterministic; no model in any of these paths.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_core import DynamicDataStore
from dd_core.recursive_improvement import record, gate, autoclose, metrics


def _store():
    fd, path = tempfile.mkstemp(suffix=".ddb")
    os.close(fd)
    os.remove(path)
    return DynamicDataStore(path), path


# --- 1. semantic dedup ------------------------------------------------------

def test_reworded_duplicate_collapses_instead_of_spawning_a_new_finding():
    d, path = _store()
    try:
        n, dup, reop, supp = record.record_gaps(d, [{
            "slug": "character-default-in-core-canonization",
            "title": "domain concept character hardcoded as default canonical "
                     "entity type inside core canonization",
            "area": "core/canonization",
            "severity": "medium", "confidence": 0.8,
            "evidence": "canonization_service.py:529",
        }], "sha1", "reflex-auditor", "arch.audit:")
        assert n == 1 and supp == []

        # a later audit describes the SAME issue with a DIFFERENT slug + wording
        n, dup, reop, supp = record.record_gaps(d, [{
            "slug": "domain-vocab-leaked-into-core-canonization",
            "title": "WorldTimer domain vocabulary character leaked into the "
                     "core canonization truth path as the default entity type",
            "area": "core/canonization",
            "severity": "medium", "confidence": 0.8,
            "evidence": "canonization_service.py default entity type",
        }], "sha2", "reflex-auditor", "arch.audit:")

        assert n == 0, "a reworded duplicate must not become a new open finding"
        assert len(supp) == 1
        assert supp[0]["matched"] == "arch.audit:character-default-in-core-canonization"
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_genuinely_different_finding_is_not_suppressed():
    d, path = _store()
    try:
        record.record_gaps(d, [{
            "slug": "checksum-rounding", "title": "checksum quantize rounding "
            "mismatch vs postgres numeric", "area": "core/events",
            "severity": "high", "confidence": 0.9, "evidence": "models.py:560",
        }], "s1", "reflex-auditor", "arch.audit:")
        # unrelated issue, different area/tokens -> must NOT collapse
        n, dup, reop, supp = record.record_gaps(d, [{
            "slug": "api-key-not-rotated", "title": "external adapter API keys "
            "are never rotated", "area": "api/security",
            "severity": "medium", "confidence": 0.7, "evidence": "auth.py:22",
        }], "s2", "reflex-auditor", "arch.audit:")
        assert n == 1 and supp == [], "distinct finding was wrongly suppressed"
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_near_identical_titles_collapse_even_with_different_evidence():
    """The stricter TITLE-only path: two findings whose titles are near-identical
    are the same root cause even when their evidence/area differ enough to dilute
    the combined-token similarity below the threshold (the kill-switch pair)."""
    d, path = _store()
    try:
        record.record_gaps(d, [{
            "slug": "kill-switch-partial-wiring",
            "title": "centralized kill switch consulted at only a subset of "
                     "capability entry points several unguarded",
            "area": "api/guards", "severity": "medium", "confidence": 0.85,
            "evidence": "guards.py:14 override.py webhook.py udc.py",
        }], "s1", "reflex-auditor", "arch.audit:")
        # same root cause, different slug/area/evidence wording
        n, dup, reop, supp = record.record_gaps(d, [{
            "slug": "kill-switch-coverage-partial-5-of-8",
            "title": "kill switch consulted at only a subset of capability entry "
                     "points several unguarded",
            "area": "core/safety/modes", "severity": "medium", "confidence": 0.85,
            "evidence": "modes.py:93 KNOWN_SWITCHES connectors projections",
        }], "s2", "reflex-auditor", "arch.audit:")
        assert n == 0, "near-identical-title duplicate must collapse, not spawn"
        assert len(supp) == 1
        assert supp[0]["matched"] == "arch.audit:kill-switch-partial-wiring"
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_title_path_does_not_merge_moderately_similar_distinct_titles():
    """Guard the title path's boundary: two findings that share some words but are
    about DIFFERENT root causes (title Jaccard below the strict 0.72 bar, combined
    below 0.55) must NOT collapse -- a false merge hides a real bug."""
    d, path = _store()
    try:
        record.record_gaps(d, [{
            "slug": "sensor-loads-whole-ledger",
            "title": "integrity sensor loads the entire ledger into memory each tick",
            "area": "core/safety", "severity": "high", "confidence": 0.85,
            "evidence": "integrity_sensor.py:63"}], "s1", "reflex-auditor", "arch.audit:")
        n, dup, reop, supp = record.record_gaps(d, [{
            "slug": "reads-guard-partial",
            "title": "canonical read enforcement guards only two of seven endpoints",
            "area": "api/routes/entities", "severity": "high", "confidence": 0.85,
            "evidence": "entities.py:395"}], "s2", "reflex-auditor", "arch.audit:")
        assert n == 1 and supp == [], "two distinct findings were wrongly merged"
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


# --- 2. severity triage -----------------------------------------------------

def test_three_tiers_major_medium_recommended():
    d, path = _store()
    try:
        record.record_gaps(d, [
            {"slug": "big", "title": "serious bug", "area": "core",
             "severity": "high", "confidence": 0.9, "evidence": "x.py:1"},
            {"slug": "small", "title": "tidy up naming", "area": "util",
             "severity": "low", "confidence": 0.8, "evidence": "y.py:2"},
            {"slug": "mid", "title": "missing test", "area": "svc",
             "severity": "medium", "confidence": 0.8, "evidence": "z.py:3"},
        ], "s1", "reflex-auditor", "arch.audit:")

        t = gate.triage(d, 0.6, "arch.audit:")
        assert [g["subject"] for g in t["act_now"]] == ["arch.audit:big"]     # MAJOR
        assert {g["subject"] for g in t["backlog"]} == {"arch.audit:mid"}      # MEDIUM only
        assert {g["subject"] for g in t["recommended"]} == {"arch.audit:small"}  # low -> optional

        # tier_for exposes the mapping
        assert gate.tier_for("high") == "MAJOR"
        assert gate.tier_for("medium") == "MEDIUM"
        assert gate.tier_for("low") == "RECOMMENDED"

        # a high finding escalates immediately; the low one is shown as OPTIONAL
        counted, esc = gate.run_gate(d, threshold=99, floor=0.6,
                                     prefix="arch.audit:")
        assert esc is not None and "ACT NOW" in esc and "OPTIONAL" in esc
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


_DISTINCT = [
    ("dead-config-flag", "unused feature flag left in settings", "config"),
    ("stale-docstring", "module docstring references removed function", "docs"),
    ("magic-timeout", "hardcoded 30s timeout should be configurable", "net"),
    ("verbose-logging", "debug logging left enabled on hot path", "logging"),
    ("missing-type-hint", "public function lacks return annotation", "typing"),
    ("todo-in-parser", "TODO marker in the csv parser branch", "parser"),
    ("duplicate-import", "redundant import of datetime", "imports"),
    ("naming-inconsistency", "mixedCase variable in snake_case module", "style"),
    ("unpinned-dependency", "requirements entry has no version bound", "deps"),
    ("empty-except", "bare except swallows errors in retry helper", "errors"),
    ("off-by-one-comment", "comment says inclusive but range is exclusive", "loop"),
    ("dead-branch", "unreachable elif after an exhaustive match", "control"),
]


def test_recommended_low_never_nags_even_piled_up():
    """RECOMMENDED (low) findings are optional: they NEVER escalate, no matter how
    many accumulate. The main AI can do them later or skip them."""
    d, path = _store()
    try:
        record.record_gaps(d, [
            {"slug": s, "title": t, "area": a, "severity": "low",
             "confidence": 0.8, "evidence": f"{a}.py:1"}
            for s, t, a in _DISTINCT], "s", "reflex-auditor", "arch.audit:")
        counted, esc = gate.run_gate(d, threshold=3, floor=0.6,
                                     prefix="arch.audit:", backlog_threshold=8)
        assert counted == len(_DISTINCT)
        assert esc is None, "a pure RECOMMENDED (low) pile-up must never nag"
        t = gate.triage(d, 0.6, "arch.audit:")
        assert len(t["recommended"]) == len(_DISTINCT) and not t["backlog"]
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_same_as_duplicate_drops_out_of_the_open_backlog():
    """same_as ALONE suppresses a duplicate finding from the open backlog -- no
    manual wontfix needed. Both findings stay 'open', but the one collapsed via
    same_as(dup, canonical) is not counted as its own item."""
    d, path = _store()
    try:
        for slug, title in (("canonical", "kill switch consulted at only a subset "
                             "of entry points"),
                            ("dup", "kill switch coverage partial across entry "
                             "points")):
            d.assert_claim(f"arch.audit:{slug}", "status", "open", source="r",
                           confidence=0.9, author_kind="ai",
                           dims={"title": title, "severity": "medium"})
            d.assert_claim(f"arch.audit:{slug}", "severity", "medium", source="r",
                           confidence=0.9, author_kind="ai")

        # both open before collapsing
        assert len(gate.triage(d, 0.6, "arch.audit:")["all"]) == 2

        # declare the duplicate the same as the canonical
        d.same_as("arch.audit:dup", "arch.audit:canonical", source="ezra")

        t = gate.triage(d, 0.6, "arch.audit:")
        subs = {g["subject"] for g in t["all"]}
        assert subs == {"arch.audit:canonical"}, subs   # dup suppressed
        assert gate.is_collapsed_duplicate(d, "arch.audit:dup") is True
        assert gate.is_collapsed_duplicate(d, "arch.audit:canonical") is False
        # metrics no longer counts the duplicate as a finding
        m = metrics.compute(d, ("arch.audit:",))
        assert m["total"] == 1 and m["duplicate_open"] == 0
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_medium_should_do_backlog_escalates_on_pileup():
    """MEDIUM (should-do) is quiet while small, but a pile-up past the threshold
    still surfaces so it can't be ignored forever."""
    d, path = _store()
    try:
        def rec(items):
            return record.record_gaps(d, [
                {"slug": s, "title": t, "area": a, "severity": "medium",
                 "confidence": 0.8, "evidence": f"{a}.py:1"}
                for s, t, a in items], "s", "reflex-auditor", "arch.audit:")

        rec(_DISTINCT[:3])   # a few medium -> quiet
        _, esc = gate.run_gate(d, threshold=3, floor=0.6, prefix="arch.audit:")
        assert esc is None, "a small should-do backlog must not nag"

        rec(_DISTINCT[3:])   # pile up past the backlog threshold -> surfaces
        counted, esc = gate.run_gate(d, threshold=3, floor=0.6,
                                     prefix="arch.audit:", backlog_threshold=8)
        assert counted >= 8
        assert esc is not None and "SHOULD DO" in esc
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


# --- 3. auto-close ----------------------------------------------------------

def test_commit_message_autocloses_referenced_findings():
    d, path = _store()
    try:
        # two genuinely different findings (distinct areas/tokens so dedup does
        # not collapse them -- that behavior is tested elsewhere)
        record.record_gaps(d, [
            {"slug": "checksum-rounding", "title": "checksum quantize rounding "
             "mismatch vs postgres numeric", "area": "core/events",
             "severity": "high", "confidence": 0.9, "evidence": "models.py:560"},
            {"slug": "override-wipes-merge", "title": "override wholesale replace "
             "destroys entity merge bindings", "area": "api/routes/override",
             "severity": "high", "confidence": 0.9, "evidence": "override.py:301"},
        ], "s1", "reflex-auditor", "arch.audit:")

        msg = ("fix(M8): checksum rounding\n\n"
               "Closes arch.audit:checksum-rounding\nCo-Authored-By: x")
        closed = autoclose.autoclose_from_commit(d, msg, "deadbeef1234")
        assert closed == ["arch.audit:checksum-rounding"]

        t = gate.triage(d, 0.6, "arch.audit:")
        remaining = {g["subject"] for g in t["all"]}
        assert remaining == {"arch.audit:override-wipes-merge"}, \
            "auto-close didn't drain the referenced finding"
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)


def test_autoclose_recognizes_close_verbs_but_not_bare_mentions():
    # "Closes/Fixes/Resolves X" close; a bare mention (e.g. "see X") does not.
    assert autoclose.subjects_closed_by_message(
        "Fixes arch.gap:foo and resolves arch.audit:bar") == [
        "arch.audit:bar", "arch.gap:foo"]
    assert autoclose.subjects_closed_by_message(
        "related to arch.gap:foo (not closing it)") == []


def test_autoclose_ignores_unknown_or_already_closed_findings():
    d, path = _store()
    try:
        # references a finding that isn't in the ledger -> no-op, no crash
        closed = autoclose.autoclose_from_commit(
            d, "Closes arch.gap:never-recorded", "sha")
        assert closed == []
    finally:
        d.close()
        os.path.exists(path) and os.remove(path)
