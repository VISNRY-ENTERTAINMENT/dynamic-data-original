"""Invariant Manifests -- retire 'the passing facade'.

The failure class: an invariant that must hold across a WHOLE surface is
implemented on PART of it, the tests cover the part that works, and it reports
as done. WorldStak's canonical-read guard is the canonical example -- it was
wired on 2 of 7 endpoints, every test passed, and the hole was invisible until a
Tier-2 audit read all seven by hand. A hand-written test enumerating today's
seven endpoints would STILL miss the eighth someone adds next month.

A manifest declares the invariant's full surface as data, and this analyzer
enforces it against the live tree: every function in the declared file set (optionally
filtered to those carrying a given decorator, e.g. route methods) MUST contain a
call to the required guard. A function in-surface that lacks the call is a
finding -- deterministically, and for every function that will ever match, not
just the ones a human remembered to list.

Manifest is JSON (stdlib only). Default location: ``<repo>/invariants.json``.

    {
      "invariants": [
        {
          "name": "canonical-reads-guarded",
          "kind": "require_call_in_functions",
          "require_call": "assert_canonical_reads_allowed",
          "files": ["src/api/routes/*.py"],
          "decorated_with": ["get", "post", "put", "delete", "patch"],
          "exclude_functions": ["health", "healthz"],
          "severity": "high"
        }
      ]
    }

``decorated_with`` and ``exclude_functions`` are optional. When ``decorated_with``
is omitted, every module-level function in the file set is in-surface.
"""

from __future__ import annotations

import fnmatch
import json
import os

from dd_core.codefacts import extract_facts

_DEFAULT_MANIFEST = "invariants.json"


def load_manifest(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _iter_files(repo_root: str, patterns: list[str]):
    """Yield (abs_path, rel_path) for every file matching any glob pattern,
    relative to repo_root. Patterns use forward slashes; matching is
    slash-normalized so they work on Windows too."""
    seen = set()
    for root, _dirs, files in os.walk(repo_root):
        for f in files:
            ap = os.path.join(root, f)
            rel = os.path.relpath(ap, repo_root).replace("\\", "/")
            if rel in seen:
                continue
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                seen.add(rel)
                yield ap, rel


def _check_require_call(repo_root: str, inv: dict) -> list[dict]:
    require = inv["require_call"]
    files = inv.get("files", [])
    decorated_with = set(inv.get("decorated_with", []))
    exclude = set(inv.get("exclude_functions", []))
    severity = inv.get("severity", "high")
    name = inv["name"]

    findings = []
    matched_any = False
    for ap, rel in _iter_files(repo_root, files):
        facts = extract_facts(ap, repo_root)
        if facts is None:
            continue
        for fn in facts.functions:
            if fn.name in exclude or fn.name.startswith("_"):
                continue
            if decorated_with and not (fn.decorators & decorated_with):
                continue
            matched_any = True
            if require not in fn.calls:
                findings.append({
                    "slug": f"invariant-{name}-{rel.replace('/', '-')}-{fn.name}",
                    "title": (f"invariant '{name}' violated: {rel}:{fn.name}() is "
                              f"in-surface but never calls '{require}' -- a partial "
                              f"invariant / passing facade"),
                    "area": rel,
                    "severity": severity,
                    "confidence": 0.9,
                    "evidence": f"{rel}:{fn.lineno}",
                    "proposed_action": (
                        f"call '{require}' in {fn.name}(), or if this function is "
                        f"legitimately exempt add it to this invariant's "
                        f"'exclude_functions'"),
                })

    if not matched_any and files:
        # The manifest points at a surface that matched no function -- itself a
        # defect (renamed files, wrong glob), reported so the invariant can't
        # silently cover nothing.
        findings.append({
            "slug": f"invariant-{name}-empty-surface",
            "title": (f"invariant '{name}' matched zero functions -- its file set "
                      f"{files} or decorator filter is stale; it is enforcing nothing"),
            "area": (files[0] if files else "."),
            "severity": "medium",
            "confidence": 0.8,
            "evidence": _DEFAULT_MANIFEST,
            "proposed_action": "fix the invariant's 'files'/'decorated_with' so it "
                               "matches the intended surface",
        })
    return findings


_KINDS = {"require_call_in_functions": _check_require_call}


def check_invariants(repo_root: str, manifest_path: str | None = None) -> list[dict]:
    """Check every invariant in the manifest against the tree. Returns findings
    ready for record_gaps. No manifest -> no findings (the oracle is opt-in per
    project, by dropping an invariants.json)."""
    path = manifest_path or os.path.join(repo_root, _DEFAULT_MANIFEST)
    if not os.path.exists(path):
        return []
    try:
        manifest = load_manifest(path)
    except (OSError, ValueError):
        return [{
            "slug": "invariant-manifest-unreadable",
            "title": f"invariants manifest at {path} is missing or not valid JSON",
            "area": os.path.relpath(path, repo_root).replace("\\", "/"),
            "severity": "medium", "confidence": 0.9, "evidence": path,
            "proposed_action": "fix the JSON syntax of the invariants manifest",
        }]

    findings = []
    for inv in manifest.get("invariants", []):
        kind = inv.get("kind", "require_call_in_functions")
        handler = _KINDS.get(kind)
        if handler is None:
            continue
        try:
            findings += handler(repo_root, inv)
        except KeyError:
            continue  # malformed invariant entry -- skip, don't crash the run
    return findings
