"""Promotion of `claimed-fixed` findings to `fixed` -- machine-resolved only.

NO model in this path. A `claimed-fixed` status (set by autoclose.py on a
commit-message mention) is a claim, not a verification. This module is the
only mention-adjacent path that may assert `fixed`, and it does so strictly
by OBSERVING the cited artifact at promotion time:

  * file citation   -- reads the cited file (optionally a line window) and
                       requires the cited substring to actually be present
  * commit citation -- resolves the SHA in the repo and, when a path is
                       cited, requires the commit to actually touch it
  * test citation   -- greps that the cited test exists (verb "resolved"),
                       or runs it (verb "executed") when run_tests=True

Every promotion records which verb produced it ("read" / "resolved" /
"executed"), per MSG-1758: a test that exists and greps clean can still be
red, so the promotion's strength must stay legible in the evidence string.

If the citation does not resolve, NOTHING is written -- a promotion that
accepts an unresolved human-shaped string is the echo rebuilt one layer up.

Design origin: MSG-1754/1755/1759 closure audit (2026-08-28). The retro
demotion helper exists because 24 historical closures were echo-closed as
`fixed`; the honest ledger state is `claimed-fixed` until each is promoted
against a checkable artifact (STATE_ENUMERATION: grandfather-or-migrate,
never illegal-but-tolerated).
"""
from __future__ import annotations

import os
import re
import subprocess

_ECHO_EVIDENCE_RE = re.compile(r"^(auto-closed|claimed closed) by commit ")


def _latest(ddb, subject, predicate="status"):
    hist = ddb.history(subject, predicate)
    if not hist:
        return None
    ordered = sorted(hist, key=lambda c: (getattr(c, "seq", 0),
                                          getattr(c, "recorded_at", "")))
    return ordered[-1]


def _resolve_file(repo_root, citation):
    """citation: {"file": rel, "contains": str, "line": int?, "window": int?}"""
    rel = citation.get("file")
    needle = citation.get("contains")
    if not rel or not needle:
        return None
    path = os.path.join(repo_root, rel)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    line = citation.get("line")
    if line:
        window = citation.get("window", 5)
        lo = max(0, int(line) - 1 - window)
        hi = min(len(lines), int(line) - 1 + window + 1)
        hay = "\n".join(lines[lo:hi])
    else:
        hay = "\n".join(lines)
    if needle in hay:
        where = f"{rel}:{line}" if line else rel
        return ("read", f"read {where}; cited content present")
    return None


def _resolve_commit(repo_root, citation):
    """citation: {"commit": sha, "touches": rel?}"""
    sha = citation.get("commit")
    if not sha or not re.fullmatch(r"[0-9a-f]{6,40}", sha):
        return None
    try:
        r = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                           cwd=repo_root, capture_output=True, timeout=15)
        if r.returncode != 0:
            return None
        touches = citation.get("touches")
        if touches:
            r2 = subprocess.run(
                ["git", "show", "--name-only", "--format=", sha],
                cwd=repo_root, capture_output=True, text=True, timeout=15)
            files = {ln.strip().replace("\\", "/")
                     for ln in r2.stdout.splitlines() if ln.strip()}
            if touches.replace("\\", "/") not in files:
                return None
            return ("resolved",
                    f"commit {sha[:12]} exists and touches {touches}")
        return ("resolved", f"commit {sha[:12]} exists in repo")
    except Exception:
        return None


def _resolve_test(repo_root, citation, run_tests):
    """citation: {"test": rel, "name": str?}"""
    rel = citation.get("test")
    if not rel:
        return None
    path = os.path.join(repo_root, rel)
    if not os.path.isfile(path):
        return None
    name = citation.get("name")
    if name:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                if name not in fh.read():
                    return None
        except OSError:
            return None
    if not run_tests:
        what = f"{rel}::{name}" if name else rel
        return ("resolved", f"test {what} exists (NOT executed)")
    runner = ["python", path] if rel.endswith(".py") else ["node", path]
    try:
        r = subprocess.run(runner, cwd=repo_root, capture_output=True,
                           timeout=300)
        if r.returncode == 0:
            return ("executed", f"test {rel} executed: exit 0")
        return None  # a red test never promotes
    except Exception:
        return None


def promote_claim(ddb, subject: str, citation: dict, repo_root: str,
                  run_tests: bool = False,
                  source: str = "reflex-promote") -> dict:
    """Promote one `claimed-fixed` subject to `fixed` iff the citation
    machine-resolves. Returns {"promoted": bool, "verb": str|None,
    "detail": str}. Writes nothing on failure."""
    latest = _latest(ddb, subject)
    status = getattr(latest, "value", None) if latest else None
    if status != "claimed-fixed":
        return {"promoted": False, "verb": None,
                "detail": f"status is {status!r}, not claimed-fixed"}
    resolved = None
    if "file" in citation:
        resolved = _resolve_file(repo_root, citation)
    elif "commit" in citation:
        resolved = _resolve_commit(repo_root, citation)
    elif "test" in citation:
        resolved = _resolve_test(repo_root, citation, run_tests)
    if not resolved:
        return {"promoted": False, "verb": None,
                "detail": "citation did not machine-resolve; nothing written"}
    verb, detail = resolved
    ddb.assert_claim(
        subject, "status", "fixed", source=source, confidence=1.0,
        author_kind="system",
        evidence=f"promoted from claimed-fixed [{verb}]: {detail}",
    )
    return {"promoted": True, "verb": verb, "detail": detail}


def retro_demote_echo_closures(ddb, prefixes=("arch.gap:", "arch.audit:"),
                               source: str = "reflex-promote") -> list[str]:
    """Demote every subject whose LATEST status is `fixed` on mention-only
    evidence to `claimed-fixed`. Idempotent: already-demoted or genuinely
    verified closures are untouched (probe-rescan closures cite observation
    evidence, not the echo pattern)."""
    demoted = []
    for subject in ddb.subjects():
        if not any(str(subject).startswith(p) for p in prefixes):
            continue
        latest = _latest(ddb, subject)
        if getattr(latest, "value", None) != "fixed":
            continue
        if not _ECHO_EVIDENCE_RE.match(getattr(latest, "evidence", "") or ""):
            continue
        ddb.assert_claim(
            subject, "status", "claimed-fixed", source=source,
            confidence=1.0, author_kind="system",
            evidence=("retro-demoted: prior close cited a commit-message "
                      "mention only (echo evidence); promote against a "
                      "checkable artifact"),
        )
        demoted.append(subject)
    return demoted


def reopen(ddb, subject: str, reason: str,
           source: str = "reflex-promote") -> bool:
    """Reopen a subject whose closure could not be backed by any artifact."""
    latest = _latest(ddb, subject)
    if getattr(latest, "value", None) not in ("fixed", "claimed-fixed"):
        return False
    ddb.assert_claim(
        subject, "status", "open", source=source, confidence=1.0,
        author_kind="system", evidence=f"reopened: {reason}",
    )
    return True
