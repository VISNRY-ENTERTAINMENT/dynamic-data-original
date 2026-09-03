"""a production governance gate self-calibration -- retire 'the checker that rots into a nag'.

A governance gate (a production governance gate) earns its authority only if its violations are
mostly real. Left uncalibrated, a rule that misfires trains the builder to
reach for ``--no-verify``, and once that reflex forms the gate protects nothing
-- every real finding is waved through with the noise. The fix is the same
self-accountability the recursive-improvement loop applies to its own findings:
measure each rule's PRECISION and make a noisy rule surface itself.

Two deterministic inputs, no model:

  * the gate's verdicts log (``GATE_VERDICTS.md``) -- every rule ID that has
    RAISED a violation, and how often.
  * an overrides ledger (``.gate/overrides.jsonl``) -- one JSON object per line
    recording a confirmed false positive: ``{"rule": "SIG-005", "sha": "...",
    "reason": "multipart upload endpoint, not a real issue"}``. a production governance gate (or the
    human, on every ``--no-verify``) appends here; that is the whole feedback
    loop.

Per rule: precision = 1 - false_positives / raised. A rule raised often but
overridden most of the time is a nag; this emits a finding recommending it be
tuned or scoped, landing (like all self-calibration output) in the RECOMMENDED
tier -- advice about the toolchain, never a build blocker.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

_RULE_RE = re.compile(r"\b([A-Z]{2,}-\d{2,})\b")   # SIG-005, OWASP-012, ...


def parse_violations(verdicts_text: str) -> Counter:
    """Count rule IDs that appear in ``### Violations`` sections of the verdicts
    log. A section whose body is 'None.' contributes nothing."""
    counts: Counter = Counter()
    # split on headings so a rule ID mentioned in prose elsewhere isn't counted
    for block in re.split(r"^#{2,3}\s+Violations\s*$", verdicts_text, flags=re.M)[1:]:
        # a Violations block runs until the next heading
        body = re.split(r"^#{1,6}\s", block, maxsplit=1, flags=re.M)[0]
        if body.strip().lower().startswith("none"):
            continue
        for rule in _RULE_RE.findall(body):
            counts[rule] += 1
    return counts


def load_overrides(path: str) -> list[dict]:
    """Read the JSONL overrides ledger. Missing file -> no overrides. Malformed
    lines are skipped, not fatal."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("rule"):
                out.append(obj)
    return out


def rule_stats(raised: Counter, overrides: list[dict]) -> dict:
    """Per-rule {raised, false_positives, precision}. A rule may be overridden
    more times than it was raised in the retained log window; precision is
    floored at 0."""
    fp = Counter(o["rule"] for o in overrides)
    rules = set(raised) | set(fp)
    stats = {}
    for rule in sorted(rules):
        r = raised.get(rule, 0)
        f = fp.get(rule, 0)
        precision = 1.0 - min(f, r) / r if r else 0.0
        stats[rule] = {"raised": r, "false_positives": f, "precision": round(precision, 3)}
    return stats


def calibrate(repo_root: str, *, verdicts_path: str | None = None,
              overrides_path: str | None = None, fp_threshold: float = 0.5,
              min_raised: int = 3) -> list[dict]:
    """Emit a finding for each rule whose false-positive rate is high enough to
    be training the ``--no-verify`` reflex. Returns finding dicts for record_gaps.

    No verdicts log -> no findings (the calibration is opt-in per project)."""
    vpath = verdicts_path or _find_verdicts(repo_root)
    if not vpath or not os.path.exists(vpath):
        return []
    opath = overrides_path or os.path.join(repo_root, ".governance-gate", "overrides.jsonl")

    try:
        raised = parse_violations(open(vpath, encoding="utf-8", errors="ignore").read())
    except OSError:
        return []
    overrides = load_overrides(opath)
    stats = rule_stats(raised, overrides)

    findings = []
    for rule, s in stats.items():
        if s["raised"] < min_raised:
            continue
        fp_rate = s["false_positives"] / s["raised"] if s["raised"] else 0.0
        if fp_rate < fp_threshold:
            continue
        pure_nag = s["false_positives"] >= s["raised"]
        findings.append({
            "slug": f"governance-gate-rule-noisy-{rule}",
            "title": (f"a production governance gate rule {rule} is {'a pure nag' if pure_nag else 'noisy'}: "
                      f"raised {s['raised']}x, {s['false_positives']} confirmed false "
                      f"positive(s) (precision {s['precision']}) -- it is training the "
                      f"--no-verify reflex"),
            "area": "governance-gate",
            "severity": "low",           # advice about tooling -> RECOMMENDED tier
            "confidence": round(min(0.85, 0.5 + fp_rate / 2), 3),
            "evidence": os.path.relpath(vpath, repo_root).replace("\\", "/"),
            "proposed_action": (
                f"tune or scope rule {rule} (a path profile / exclusion for the "
                f"pattern it misfires on), or retire it; a gate that cries wolf "
                f"gets its real findings ignored too"),
        })
    return findings


def _find_verdicts(repo_root: str) -> str | None:
    for cand in ("GATE_VERDICTS.md",
                 os.path.join("gate_artifacts", "GATE_VERDICTS.md")):
        p = os.path.join(repo_root, cand)
        if os.path.exists(p):
            return p
    return None
