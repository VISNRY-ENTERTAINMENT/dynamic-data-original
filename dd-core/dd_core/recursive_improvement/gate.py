"""Deterministic escalation gate, with THREE severity tiers. NO model in this path.

A model records gaps as claims (predicate "status" = "open"); this counts them
and decides -- with a counter, never a judgment -- what to surface NOW, what to
keep as a real backlog, and what is merely a suggestion. The whole point: the
loop must HELP, not nag. So it splits open findings into three tiers by severity:

  * MAJOR (critical / high)     -> ACT NOW. Surfaced immediately (even one),
                                   because a serious defect should not wait.
  * MEDIUM (medium)             -> BACKLOG (should-do). Never nags, but a pile-up
                                   past a larger threshold still escalates so it
                                   can't be ignored forever.
  * RECOMMENDED (low / unrated) -> OPTIONAL. Advisory only. NEVER escalates and
                                   never counts toward the pile-up threshold. The
                                   main AI may skip these or do them later --
                                   they are suggestions, not obligations.

Gap subjects look like "<prefix><slug>". Status is a LIFECYCLE, resolved by
append-sequence (latest wins), NOT by the store's BELIEVED confidence-max
resolver.
"""

from __future__ import annotations

_OPEN_STATES = {"open", "escalated"}
_COUNTED_STATES = {"open"}
_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}
_ACT_NOW = {"critical", "high"}       # MAJOR: surface immediately
_BACKLOG = {"medium"}                 # MEDIUM: accumulate; escalate on pile-up
_RECOMMENDED = {"low", None}          # RECOMMENDED: optional, never escalates
_TIER = {"critical": "MAJOR", "high": "MAJOR", "medium": "MEDIUM",
         "low": "RECOMMENDED", None: "RECOMMENDED"}


def tier_for(severity) -> str:
    """The tier for a severity: MAJOR (act now), MEDIUM (should do),
    RECOMMENDED (optional)."""
    return _TIER.get(severity, "RECOMMENDED")


_SAME_AS = "same_as"


def _latest(claims):
    ordered = sorted(claims, key=lambda c: (getattr(c, "seq", 0),
                                            getattr(c, "recorded_at", "")))
    return ordered[-1] if ordered else None


def is_collapsed_duplicate(ddb, subject: str) -> bool:
    """True if this finding was collapsed onto a canonical one via same_as.

    record_gaps + manual dedup assert `same_as(duplicate, canonical)` -- the
    DUPLICATE is the subject, the canonical the object. So a finding that has a
    live outgoing same_as edge is not an independent open item; it is a
    re-sighting of the canonical and must NOT clutter the open backlog. This makes
    same_as ALONE suppress the duplicate -- no manual wontfix step, and no
    dup-noise recurring (previously same_as only POOLED status, leaving both open).
    """
    try:
        edges = ddb.history(subject, _SAME_AS)
    except Exception:
        return False
    for c in edges:
        if getattr(c, "retracted", False):
            continue
        target = getattr(c, "obj", None) or getattr(c, "value", None)
        if target and target != subject:
            return True
    return False


def collect_open_gaps(ddb, floor: float, prefix: str = "arch.gap:") -> list[dict]:
    gaps = []
    for subject in ddb.subjects():
        if not subject.startswith(prefix):
            continue
        status_hist = ddb.history(subject, "status")
        latest_status = _latest(status_hist)
        current = getattr(latest_status, "value", None) if latest_status else None
        if current not in _OPEN_STATES:
            continue
        if is_collapsed_duplicate(ddb, subject):
            continue  # a same_as duplicate is not its own open item
        opens = [c for c in status_hist if getattr(c, "value", None) == "open"]
        latest_open = _latest(opens)
        conf = getattr(latest_open, "confidence", 0.0) if latest_open else 0.0
        if conf < floor:
            continue
        latest_sev = _latest(ddb.history(subject, "severity"))
        sev_value = getattr(latest_sev, "value", None) if latest_sev else None
        dims = (latest_open.dims if latest_open else {}) or {}
        gaps.append({
            "subject": subject, "status": current, "confidence": conf,
            "severity": sev_value, "title": dims.get("title") or subject,
            "dims": dims,
        })
    gaps.sort(key=lambda g: (_SEV_RANK.get(g["severity"], 4),
                             -g["confidence"], g["subject"]))
    return gaps


def _render_gap(i: int, g: dict) -> list[str]:
    d = g["dims"]
    out = [f"{i}. [{(g['severity'] or 'unrated').upper()}] {g['title']}",
           f"     confidence : {g['confidence']:.2f}"]
    if d.get("area"):
        out.append(f"     area       : {d['area']}")
    if d.get("evidence") or d.get("first_seen_sha"):
        out.append(f"     evidence   : {d.get('evidence','')}"
                   f"{(' @ ' + d['first_seen_sha']) if d.get('first_seen_sha') else ''}")
    if d.get("proposed_action"):
        out.append(f"     proposal   : {d['proposed_action']}")
    out.append(f"     subject    : {g['subject']}")
    out.append("")
    return out


def render_escalation(act_now: list[dict], backlog: list[dict],
                      recommended: list[dict] | None = None) -> str:
    recommended = recommended or []
    lines = ["=" * 72,
             f"[!] REFLEX GAP LEDGER -- ATTENTION ({len(act_now)} to act on, "
             f"{len(backlog)} should-do, {len(recommended)} optional)", "=" * 72,
             "Nothing has been added to any roadmap. Per finding: [accept] "
             "[wontfix] [fix-now]. Auto-closes when a commit says",
             "'Closes <subject>'.", ""]
    if act_now:
        lines.append("--- MAJOR / ACT NOW (high/critical) ----------------------------")
        for i, g in enumerate(act_now, 1):
            lines += _render_gap(i, g)
    if backlog:
        lines.append(f"--- MEDIUM / SHOULD DO ({len(backlog)}, not urgent) ------------")
        for g in backlog:
            lines.append(f"  - [{(g['severity'] or '?').upper():6}] {g['title']}  "
                         f"({g['subject']})")
        lines.append("")
    if recommended:
        lines.append(f"--- RECOMMENDED / OPTIONAL ({len(recommended)} -- do later or "
                     f"skip) --------")
        lines.append("    Suggestions only. The main AI need NOT act on these now.")
        for g in recommended:
            lines.append(f"  - [{(g['severity'] or 'low').upper():6}] {g['title']}  "
                         f"({g['subject']})")
        lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


def triage(ddb, floor: float, prefix: str = "arch.gap:") -> dict:
    """Split open findings into three tiers: act-now (MAJOR), backlog (MEDIUM,
    should-do), and recommended (OPTIONAL). Pure data, no side effects.
    ``backlog`` retains ONLY medium now (low moved to ``recommended``); callers
    that want the old combined set can use backlog + recommended."""
    gaps = [g for g in collect_open_gaps(ddb, floor, prefix)
            if g["status"] in _COUNTED_STATES]
    return {
        "act_now": [g for g in gaps if g["severity"] in _ACT_NOW],
        "backlog": [g for g in gaps if g["severity"] in _BACKLOG],
        "recommended": [g for g in gaps if g["severity"] in _RECOMMENDED],
        "all": gaps,
    }


def run_gate(ddb, threshold: int, floor: float,
             prefix: str = "arch.gap:",
             backlog_threshold: int | None = None) -> tuple[int, str | None]:
    """Returns (counted_open, escalation_text_or_None).

    Escalates when there is ANY MAJOR (high/critical) finding, OR when the MEDIUM
    should-do backlog has grown past `backlog_threshold` (default 4x the normal
    threshold) so a slow pile-up is still eventually surfaced. RECOMMENDED
    (optional/low) findings NEVER trigger an escalation and never count toward the
    pile-up threshold -- they are surfaced only for context when an escalation
    already fired for another reason. So the loop stays quiet until something
    actually deserves attention.
    """
    t = triage(ddb, floor, prefix)
    act_now, backlog, recommended = t["act_now"], t["backlog"], t["recommended"]
    big = backlog_threshold if backlog_threshold is not None else max(threshold * 4, 8)
    counted = len(t["all"])
    if act_now or len(backlog) >= big:
        return counted, render_escalation(act_now, backlog, recommended)
    return counted, None
