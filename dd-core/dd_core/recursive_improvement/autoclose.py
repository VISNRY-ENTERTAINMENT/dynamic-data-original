"""Deterministic auto-close: a commit that says it closed a finding, CLAIMS it.

NO model in this path. When a commit message contains
"Closes|Fixes|Resolves <prefix><slug>" (the GitHub-issue convention), the
matching ledger finding is marked `claimed-fixed`, cited to that commit's SHA.
This keeps the backlog from filling with stale entries without pretending the
mention was a verification.

Why `claimed-fixed` and not `fixed` (2026-08-28, MSG-1754 closure audit): a
commit-message mention is the AUTHOR restating their own claim -- the evidence
string produced here quotes the claim, it does not observe the fix. Treating
mention as verification made the ledger's evidence channel an echo: any commit
saying "Closes X" closed X, verified by nothing. A full backward-trace of 24
such closures found one false-by-default and five with no locatable artifact.

Promotion to `fixed` requires machine-resolved evidence -- see promote.py:
the promoter reads the cited file:line, resolves the cited commit, or
greps/executes the cited test, and records WHICH verb was used so the
promotion's strength stays legible. Probe-owned findings have a second honest
path: departments/runner._autoclose_resolved marks `fixed` directly when the
owning probe re-scans cleanly and no longer detects the slug -- that IS an
observation, not a mention.

Findings the loop cannot verify as fixed simply stay open (or backlogged). This
never re-opens or invents anything; it only records what a commit explicitly
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
    """Mark every finding a commit says it closed as `claimed-fixed`.
    Returns the subjects actually transitioned (already-closed ones are
    skipped). Promotion to `fixed` happens in promote.py against
    machine-resolved evidence, never here."""
    closed = []
    for subject in subjects_closed_by_message(message):
        # only real findings that exist in the ledger and are still open
        if _latest_status(ddb, subject) not in ("open", "escalated"):
            continue
        ddb.assert_claim(
            subject, "status", "claimed-fixed", source=source, confidence=1.0,
            author_kind="system",
            evidence=f"claimed closed by commit {sha[:12]}: message referenced "
                     f"this finding with a close verb (mention-only -- promote "
                     f"to fixed via machine-resolved evidence, see promote.py)",
        )
        closed.append(subject)
    return closed
