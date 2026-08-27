"""Deterministic auto-close: a commit that says it closed a finding, closes it.

NO model in this path. When a commit message contains
"Closes|Fixes|Resolves <prefix><slug>" (the GitHub-issue convention), the
matching ledger finding is marked `fixed`, cited to that commit's SHA. This is
what keeps the backlog from filling with stale entries: the moment a fix ships
referencing a finding, the finding stops being open -- automatically, with no
human having to remember to update the ledger.

Findings the loop cannot verify as fixed simply stay open (or backlogged). This
never re-opens or invents anything; it only closes what a commit explicitly
claims to have closed.
"""

from __future__ import annotations

import re

# "Closes arch.gap:foo", "fixes ARCH.AUDIT:bar-baz", "resolved arch.gap:x"
_CLOSE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+((?:arch\.gap|arch\.audit):[a-z0-9\-]+)",
    re.IGNORECASE,
)
# also accept a bare subject on its own (e.g. a "Closes:" list) as long as it is
# clearly a finding subject
_SUBJECT_RE = re.compile(r"\b((?:arch\.gap|arch\.audit):[a-z0-9\-]+)", re.IGNORECASE)


def _latest_status(ddb, subject):
    hist = ddb.history(subject, "status")
    if not hist:
        return None
    ordered = sorted(hist, key=lambda c: (getattr(c, "seq", 0),
                                          getattr(c, "recorded_at", "")))
    return getattr(ordered[-1], "value", None)


def subjects_closed_by_message(message: str) -> list[str]:
    """Finding subjects a commit message explicitly claims to close."""
    found = {m.group(1).lower() for m in _CLOSE_RE.finditer(message or "")}
    return sorted(found)


def autoclose_from_commit(ddb, message: str, sha: str,
                          source: str = "reflex-autoclose") -> list[str]:
    """Mark every finding a commit says it closed as `fixed`. Returns the
    subjects actually transitioned (already-closed ones are skipped)."""
    closed = []
    for subject in subjects_closed_by_message(message):
        # only real findings that exist in the ledger and are still open
        if _latest_status(ddb, subject) not in ("open", "escalated"):
            continue
        ddb.assert_claim(
            subject, "status", "fixed", source=source, confidence=1.0,
            author_kind="system",
            evidence=f"auto-closed by commit {sha[:12]}: message referenced "
                     f"this finding with a close verb",
        )
        closed.append(subject)
    return closed
