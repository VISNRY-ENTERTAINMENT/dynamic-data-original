"""Tests for Ovyero self-calibration (dd_core.recursive_improvement.ovyero_calibration)."""

from __future__ import annotations

import json
import os
import tempfile

from dd_core.recursive_improvement import ovyero_calibration as oc


_VERDICTS = """# Ovyero Verdicts

## 2026-07-14 commit A
### Violations
- SIG-005 in api/upload.py: possible unvalidated input
- SIG-005 in api/media.py: possible unvalidated input

## 2026-07-14 commit B
### Violations
None.

## 2026-07-15 commit C
### Violations
- SIG-005 in api/attach.py
- OWASP-012 in auth.py: real hardcoded secret
"""


def test_parse_violations_counts_rules_and_ignores_none():
    counts = oc.parse_violations(_VERDICTS)
    assert counts["SIG-005"] == 3
    assert counts["OWASP-012"] == 1


def test_rule_stats_precision():
    raised = oc.parse_violations(_VERDICTS)
    overrides = [
        {"rule": "SIG-005", "sha": "a", "reason": "multipart upload FP"},
        {"rule": "SIG-005", "sha": "b", "reason": "multipart upload FP"},
        {"rule": "SIG-005", "sha": "c", "reason": "multipart upload FP"},
    ]
    stats = oc.rule_stats(raised, overrides)
    assert stats["SIG-005"] == {"raised": 3, "false_positives": 3, "precision": 0.0}
    assert stats["OWASP-012"]["precision"] == 1.0


def _mk(verdicts: str, overrides: list[dict] | None):
    root = tempfile.mkdtemp(prefix="ovcal-")
    with open(os.path.join(root, "OVYERO_VERDICTS.md"), "w", encoding="utf-8") as fh:
        fh.write(verdicts)
    if overrides is not None:
        os.makedirs(os.path.join(root, ".ovyero"), exist_ok=True)
        with open(os.path.join(root, ".ovyero", "overrides.jsonl"), "w", encoding="utf-8") as fh:
            for o in overrides:
                fh.write(json.dumps(o) + "\n")
    return root


def test_noisy_rule_is_flagged_clean_rule_is_not():
    root = _mk(_VERDICTS, [
        {"rule": "SIG-005", "sha": "a", "reason": "FP"},
        {"rule": "SIG-005", "sha": "b", "reason": "FP"},
        {"rule": "SIG-005", "sha": "c", "reason": "FP"},
    ])
    found = oc.calibrate(root)
    slugs = {f["slug"] for f in found}
    assert "ovyero-rule-noisy-SIG-005" in slugs
    # OWASP-012 raised once (below min_raised) and never overridden -> not flagged
    assert not any("OWASP-012" in s for s in slugs)
    finding = next(f for f in found if f["slug"] == "ovyero-rule-noisy-SIG-005")
    assert finding["severity"] == "low"          # RECOMMENDED tier
    assert "pure nag" in finding["title"]


def test_no_overrides_means_no_findings():
    root = _mk(_VERDICTS, overrides=None)
    assert oc.calibrate(root) == []


def test_no_verdicts_log_means_no_findings():
    root = tempfile.mkdtemp(prefix="ovcal-")
    assert oc.calibrate(root) == []
