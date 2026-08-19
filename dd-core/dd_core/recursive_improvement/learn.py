"""The loop learns from its own history. NO model in this path.

Two feedback signals, both generated deterministically FROM THE LEDGER and
injected into the auditor's prompt so the model reads them (the model still does
the judging; it just gets better priors):

- ANTI-PATTERNS (Phase 2): the categories of defect this loop has actually
  caught and a human confirmed by FIXING them. The single most valuable priming
  a code auditor can have is "here is the kind of bug that has really bitten
  this codebase." Derived from `fixed` findings.

- FALSE POSITIVES (Phase 3): findings a human closed as `wontfix` because they
  were WRONG (overstated / wrong premise). Telling the auditor "the loop has
  historically been wrong about X, be careful before raising it" is how
  precision compounds over time.

Both are capped and summarized so the prompt stays small.
"""

from __future__ import annotations

import re
from collections import Counter

_STOP = frozenset("""
the a an of to in on for and or is are was be been being this that these those
it its as at by with from into via not no only also still yet but if when
where which who whose how why what onto over per than then so such -- ---
""".split())


def _load(cfg):
    from .runner import _load_store
    return _load_store(cfg)


def _latest_status(ddb, subject):
    hist = ddb.history(subject, "status")
    if not hist:
        return None, {}
    ordered = sorted(hist, key=lambda c: (getattr(c, "seq", 0),
                                          getattr(c, "recorded_at", "")))
    dims = next((c.dims for c in reversed(ordered) if c.dims), {}) or {}
    return getattr(ordered[-1], "value", None), dims


def _findings_by_status(ddb, prefixes, wanted: set):
    out = []
    for subject in ddb.subjects():
        if not subject.startswith(tuple(prefixes)):
            continue
        st, dims = _latest_status(ddb, subject)
        if st in wanted:
            out.append((subject, dims))
    return out


def _keyphrases(dimslist, top: int = 8) -> list[str]:
    words = Counter()
    for _s, dims in dimslist:
        text = f"{dims.get('title','')} {dims.get('area','')}".lower()
        for w in re.split(r"[^a-z0-9]+", text):
            if len(w) > 3 and w not in _STOP:
                words[w] += 1
    return [w for w, _ in words.most_common(top)]


def antipattern_hints(cfg, limit: int = 8) -> str:
    """A priming section: 'defects that have really been found + fixed here.'"""
    try:
        ddb = _load(cfg)
    except Exception:
        return ""
    try:
        fixed = _findings_by_status(
            ddb, (cfg.gap_prefix, cfg.audit_prefix), {"fixed"})
    finally:
        ddb.close()
    if not fixed:
        return ""
    titles = [d.get("title", "") for _s, d in fixed if d.get("title")][:limit]
    kp = _keyphrases(fixed)
    body = "\n".join(f"- {t[:120]}" for t in titles)
    return ("\n\nDEFECTS THIS LOOP HAS ALREADY FOUND AND FIXED HERE (look hard "
            "for MORE of the same shape -- these are this codebase's real "
            f"failure modes; recurring themes: {', '.join(kp)}):\n{body}\n")


def false_positive_hints(cfg, limit: int = 8) -> str:
    """A caution section: findings a human rejected as wrong. Reduces repeats."""
    try:
        ddb = _load(cfg)
    except Exception:
        return ""
    try:
        wrong = []
        for subject, dims in _findings_by_status(
                ddb, (cfg.gap_prefix, cfg.audit_prefix), {"wontfix"}):
            # only the ones a human closed as WRONG (not "by design" / deferred)
            note = " ".join(str(v) for v in dims.values()).lower()
            latest = sorted(ddb.history(subject, "status"),
                            key=lambda c: getattr(c, "seq", 0))[-1]
            ev = (getattr(latest, "evidence", "") or "").lower()
            if any(k in (note + ev) for k in
                   ("false positive", "wrong premise", "overstated",
                    "inaccurate", "not a", "already", "by design")):
                wrong.append((subject, dims))
    finally:
        ddb.close()
    if not wrong:
        return ""
    body = "\n".join(f"- {d.get('title','')[:120]}" for _s, d in wrong[:limit])
    return ("\n\nCLAIMS THE LOOP GOT WRONG BEFORE (a human rejected these as "
            "false/overstated -- verify carefully before raising anything "
            f"similar):\n{body}\n")
