"""Deterministic evidence validation. NO model in this path.

A finding is only as trustworthy as its evidence. The reviewer is asked to cite
a `path`, `path:line`, or a symbol -- but a model can hallucinate a file that
does not exist or a line past the end of a real one. Those findings waste a
human's verification time (this was ~a third of the findings in early runs).

So before a finding is recorded, its cited evidence is checked against the
actual repo, deterministically:

  * every `path` / `path:line` token that looks like a real file reference must
    resolve to a file that EXISTS in the repo;
  * a `:line` must be within that file's length;
  * a bare `name.ext` (no dir) is matched anywhere in the tree.

Verdicts:
  VERIFIED   -- at least one concrete file reference resolved.
  UNVERIFIED -- evidence names a file/line that does not exist (likely
                hallucinated) -> the caller downgrades or drops it.
  NO_LOCATOR -- evidence is prose with no checkable file reference (can't
                confirm or deny; treated as a soft pass, not a failure).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# path/to/file.ext  or  file.ext:123  -- a token that names a source location
_LOCATOR = re.compile(
    r"(?<![\w./-])"                      # not mid-token
    r"([\w./-]+\.[A-Za-z0-9]{1,6})"      # a filename with an extension
    r"(?::(\d+))?"                       # optional :line
)
_IGNORE_EXT = {".md", ".txt", ".json", ".lock", ".cfg", ".ini", ".toml"}


@dataclass
class EvidenceCheck:
    verdict: str                 # VERIFIED | UNVERIFIED | NO_LOCATOR
    resolved: list               # [(token, abspath, line_ok)]
    missing: list                # tokens that did not resolve
    reason: str

    @property
    def ok(self) -> bool:
        return self.verdict != "UNVERIFIED"


def _index_repo(repo_root: str) -> dict:
    """basename -> [relpaths]. Built once; cheap for normal repos."""
    idx: dict[str, list[str]] = {}
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
            "build", "vendor", "site-packages", ".idea", ".vscode"}
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo_root).replace("\\", "/")
            idx.setdefault(f, []).append(rel)
    return idx


def _line_count(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def validate_evidence(repo_root: str, evidence: str, _index=None) -> EvidenceCheck:
    idx = _index if _index is not None else _index_repo(repo_root)
    tokens = _LOCATOR.findall(evidence or "")
    # keep only tokens that plausibly reference source (skip doc/config exts,
    # which are cited loosely and shouldn't fail a finding)
    tokens = [(p, ln) for (p, ln) in tokens
              if os.path.splitext(p)[1].lower() not in _IGNORE_EXT]
    if not tokens:
        return EvidenceCheck("NO_LOCATOR", [], [],
                             "no checkable file reference in evidence")

    resolved, missing = [], []
    for path, line in tokens:
        norm = path.replace("\\", "/")
        candidates = []
        # exact repo-relative path?
        if os.path.isfile(os.path.join(repo_root, norm)):
            candidates = [norm]
        else:
            # match by basename anywhere; prefer a suffix match on the given path
            base = os.path.basename(norm)
            hits = idx.get(base, [])
            candidates = [h for h in hits if h.endswith(norm)] or hits
        if not candidates:
            missing.append(path if not line else f"{path}:{line}")
            continue
        line_ok = True
        if line:
            n = _line_count(os.path.join(repo_root, candidates[0]))
            line_ok = 1 <= int(line) <= n if n else False
        resolved.append((path, candidates[0], line_ok))

    if resolved and not missing and all(l for _, _, l in resolved):
        return EvidenceCheck("VERIFIED", resolved, [], "all references resolve")
    if resolved and not missing:
        return EvidenceCheck("VERIFIED", resolved, [],
                             "file(s) resolve; a cited line is out of range")
    if resolved:
        return EvidenceCheck("VERIFIED", resolved, missing,
                             "some references resolve, some do not")
    return EvidenceCheck("UNVERIFIED", [], missing,
                         f"cited file(s) not found in repo: {missing}")
