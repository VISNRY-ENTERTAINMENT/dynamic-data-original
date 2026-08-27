"""Is the loop worth running? Compute it from the ledger. NO model.

The ledger is append-only with timestamps and dispositions, so every quality
metric is a deterministic read -- no separate telemetry needed. This is how you
know whether the loop is HELPING (finding real bugs) or just NOISE, and whether
to tighten the charter / raise the floor.

Definitions (a finding's disposition is its latest status):
  fixed, accepted           -> TRUE POSITIVE   (real; acted on)
  wontfix (wrong/overstated) -> FALSE POSITIVE  (loop was wrong)
  wontfix (by design/dup)    -> NOT-A-BUG        (valid to raise, not actionable)
  open, escalated            -> pending

  precision      = TP / (TP + FP)      -- of the findings we could judge, how
                                          many were real
  actionable_rate= TP / all-closed     -- how much of the output was worth acting on
  mttc           = mean(closed_at - first_seen) over fixed findings
"""

from __future__ import annotations

import re
from datetime import datetime

_TRUE = {"fixed", "accepted"}
_STOP = frozenset("the a an of to in on for and or is are was were be with from "
                  "not no only also into via than then so such at by as".split())
# Two OPEN findings whose title tokens overlap this much are almost certainly the
# same root cause raised twice -- the "noise" that precision alone cannot see.
_DUP_TITLE_SIM = 0.6


def _title_tokens(title: str) -> set:
    return {w for w in re.split(r"[^a-z0-9]+", (title or "").lower())
            if len(w) > 2 and w not in _STOP}


def _count_near_duplicate_open(open_titles: list[tuple[str, str]]) -> int:
    """How many OPEN findings have at least one near-duplicate peer (by title
    token Jaccard). A direct, model-free measure of stale/duplicate noise that
    the precision metric structurally cannot capture."""
    toks = [(_subj, _title_tokens(t)) for _subj, t in open_titles]
    dup = set()
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i][1], toks[j][1]
            if not a or not b:
                continue
            sim = len(a & b) / len(a | b)
            if sim >= _DUP_TITLE_SIM:
                dup.add(toks[i][0]); dup.add(toks[j][0])
    return len(dup)
_WRONG_MARKERS = ("false positive", "wrong premise", "overstated", "inaccurate",
                  "not a real", "not a bug")
_DUP_MARKERS = ("same_as", "duplicate", "by design", "expected", "not applicable")


def _history(ddb, subject):
    return sorted(ddb.history(subject, "status"),
                  key=lambda c: (getattr(c, "seq", 0), getattr(c, "recorded_at", "")))


def _parse_ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def compute(ddb, prefixes=("arch.gap:", "arch.audit:")) -> dict:
    tp = fp = notbug = pending = 0
    mttc_days = []
    by_sev_open = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unrated": 0}
    total = 0
    open_titles: list[tuple[str, str]] = []

    from dd_core.recursive_improvement.gate import is_collapsed_duplicate

    for subject in ddb.subjects():
        if not subject.startswith(tuple(prefixes)):
            continue
        if is_collapsed_duplicate(ddb, subject):
            continue  # a same_as duplicate is not an independent finding
        hist = _history(ddb, subject)
        if not hist:
            continue
        total += 1
        latest = hist[-1]
        st = latest.value

        if st in ("open", "escalated"):
            pending += 1
            sev_hist = sorted(ddb.history(subject, "severity"),
                              key=lambda c: getattr(c, "seq", 0))
            sev = (sev_hist[-1].value if sev_hist else "unrated")
            by_sev_open[sev if sev in by_sev_open else "unrated"] += 1
            dims = next((c.dims for c in reversed(hist) if c.dims), {}) or {}
            open_titles.append((subject, dims.get("title", subject)))
            continue

        if st in _TRUE:
            tp += 1
            # mean time to close: first 'open' -> the closing claim
            opened = next((c for c in hist if c.value == "open"), hist[0])
            t0 = _parse_ts(getattr(opened, "recorded_at", None))
            t1 = _parse_ts(getattr(latest, "recorded_at", None))
            if t0 and t1 and t1 >= t0:
                mttc_days.append((t1 - t0).total_seconds() / 86400.0)
        elif st == "wontfix":
            note = ((getattr(latest, "evidence", "") or "") + " "
                    + " ".join(str(v) for v in (latest.dims or {}).values())).lower()
            if any(m in note for m in _WRONG_MARKERS) and not any(
                    m in note for m in _DUP_MARKERS):
                fp += 1
            else:
                notbug += 1

    judged = tp + fp
    closed = tp + fp + notbug
    dup_open = _count_near_duplicate_open(open_titles)
    return {
        "total": total,
        "pending": pending,
        "true_positive": tp,
        "false_positive": fp,
        "not_a_bug": notbug,
        "precision": round(tp / judged, 3) if judged else None,
        "actionable_rate": round(tp / closed, 3) if closed else None,
        "mean_time_to_close_days": round(sum(mttc_days) / len(mttc_days), 2)
        if mttc_days else None,
        "open_by_severity": by_sev_open,
        # Noise signals precision cannot see: near-duplicate OPEN findings (same
        # root cause raised twice) and the share of the open backlog they are.
        "duplicate_open": dup_open,
        "duplicate_open_rate": round(dup_open / pending, 3) if pending else None,
    }


def render(m: dict) -> str:
    p = m["precision"]
    dup = m.get("duplicate_open", 0)
    dup_rate = m.get("duplicate_open_rate")
    verdict = ("no closed findings yet" if p is None else
               "HELPING (high signal)" if p >= 0.75 else
               "MIXED -- consider tightening the charter/floor" if p >= 0.5 else
               "NOISY -- precision low; sharpen the charter or raise the floor")
    # Honest caveat: precision only sees findings explicitly judged wrong. It
    # CANNOT see stale re-flags of already-closed work or near-duplicate open
    # findings -- so a clean precision with high duplicate_open still means noise.
    caveat = ("" if not dup else
              f"  NOTE: {dup} open finding(s) look like near-duplicates of each "
              f"other ({dup_rate} of the open backlog). precision does not count "
              f"these -- the loop is over-generating; tune dedup / raise the floor.")
    L = ["=" * 60, "Recursive Improvement -- ledger metrics", "=" * 60,
         f"  findings total        : {m['total']}",
         f"  pending (open)        : {m['pending']}",
         f"  true positives        : {m['true_positive']}  (fixed/accepted)",
         f"  false positives       : {m['false_positive']}  (rejected as wrong)",
         f"  not-a-bug / dup        : {m['not_a_bug']}",
         f"  precision             : {p if p is not None else 'n/a'}"
         "   (only counts explicitly-judged findings; blind to stale re-flags)",
         f"  near-duplicate open   : {dup}"
         + (f"  ({dup_rate} of open)" if dup_rate is not None else ""),
         f"  actionable rate       : {m['actionable_rate'] if m['actionable_rate'] is not None else 'n/a'}",
         f"  mean time to close    : "
         f"{m['mean_time_to_close_days'] if m['mean_time_to_close_days'] is not None else 'n/a'} days",
         f"  open by severity      : " + ", ".join(
             f"{k}={v}" for k, v in m["open_by_severity"].items() if v),
         "-" * 60, f"  VERDICT: {verdict}"]
    if caveat:
        L.append(caveat)
    L.append("=" * 60)
    return "\n".join(L)
