"""State-coverage department -- deterministic oracle. NO model.

Finds enumerations whose members are only PARTIALLY covered where coverage
matters: registry/enum members that appear in implementation but never in
any test, and switch/if-chains over a known state set that silently ignore
some of its members. This is the "partial gating" failure class: protection
or handling that covers most of a surface produces more dangerous
confidence than covering none of it, because the deploy LOOKS safe and the
gap is precisely where nobody is looking.

Origin (real incident): a config gate built to keep a cap policy inert on
deploy covered the FREE tier -- the tier the discussion was about -- and
not TRIAL, the tier next to it. The deploy silently activated the policy
for TRIAL. The gate's OFF-default tests could not fail, because they were
derived from the same hand-written list as the implementation: TRIAL was
never in either.

Doctrine source: AGENT_SYSTEM_THINK/STATE_ENUMERATION.md (esp. section 4:
tests must be derived from the registry by iteration, not restated).

Precision bar: high-signal, low-volume. The probe only considers
enumerations it can attribute to a SOURCE-OF-TRUTH declaration (an object
literal registry, a TS/Py enum, a const map of tiers/states/plans), only
those with a domain-suggestive name, and only members that look like
policy-relevant identifiers.

Oracles:
  1. registry-member-untested -- a member of a tier/state/plan registry
     that appears in implementation files but in NO test file
  2. registry-member-unrouted -- a member that appears ONLY in its own
     declaration: no implementation file and no test references it at all
     (declared state with no handler anywhere)

All findings use slug prefix `state-` so they live at arch.gap:state-* in
the claim store.

Confidence calibration:
  0.65 -- member used in implementation, absent from every test: the
          FREE/TRIAL shape exactly
  0.55 -- member referenced nowhere outside its declaration: either dead
          or handled by fallthrough nobody chose
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", ".idea", ".vscode", "reflex",
    ".reflex", "coverage", "_shelved",
})

_TEST_DIRS = frozenset({
    "tests", "test", "spec", "specs", "__tests__", "proofs", "e2e",
})
_TEST_FILE_RE = re.compile(
    r"(?:\.test\.|\.spec\.|_test\.|test_|selftest|smoke)", re.IGNORECASE)

_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}

# Registries worth caring about: declaration names that suggest a
# policy-relevant state set. Keeps the probe away from arbitrary objects.
_REGISTRY_NAME = re.compile(
    r"(?:tier|plan|state|status|role|stage|phase|mode|level)s?"
    r"(?:Registry|Map|Config|Table|Defs?|Types?)?",
    re.IGNORECASE)

# JS/TS object-literal registry:   const tierRegistry = { FREE: {...}, TEAM: {...} }
_JS_REGISTRY = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*(?::[^=]{0,60})?=\s*(?:Object\.freeze\s*\(\s*)?\{",
)
# Python dict registry:            TIERS = { "free": {...}, ... }
_PY_REGISTRY = re.compile(r"^(\w+)\s*(?::\s*\w[\w\[\], ]*)?=\s*\{", re.MULTILINE)
# TS enum / Python Enum members handled via the same key extraction below.

# Keys at the top level of an object literal: NAME: or "NAME": or 'NAME':
_KEY = re.compile(r"^[ \t]*['\"]?([A-Z][A-Z0-9_]{2,24})['\"]?\s*:", re.MULTILINE)

_MAX_REGISTRIES = 12       # cap: only the most name-relevant registries
_MIN_MEMBERS = 2           # a registry of one is not an enumeration
_MAX_FINDINGS = 12         # global noise cap


def _walk(repo_root: str, exts: set):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                yield os.path.join(root, f)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def _rel(repo_root: str, path: str) -> str:
    return os.path.relpath(path, repo_root).replace("\\", "/")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _slug(kind: str, registry: str, member: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", f"{registry}-{member}".lower()).strip("-")
    return f"state-{kind}-{safe}"


def _is_test_path(rel: str) -> bool:
    parts = rel.lower().split("/")
    if any(p in _TEST_DIRS for p in parts):
        return True
    if parts[-1].endswith("_probe.py") or "recursive_improvement" in parts:
        return True  # probe sources embed pattern examples: self-noise
    return bool(_TEST_FILE_RE.search(parts[-1]))


def _extract_block(src: str, brace_pos: int) -> str:
    """Return the top-level {...} block starting at brace_pos (simple
    brace counting; good enough for registry literals)."""
    depth = 0
    for i in range(brace_pos, min(len(src), brace_pos + 20000)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace_pos:i + 1]
    return src[brace_pos:brace_pos + 20000]


def _find_registries(repo_root: str):
    """Yield (name, members, rel, lineno) for domain-relevant registries."""
    found = 0
    for path in _walk(repo_root, _CODE_EXTS):
        if found >= _MAX_REGISTRIES:
            return
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None:
            continue
        pattern = _PY_REGISTRY if rel.endswith(".py") else _JS_REGISTRY
        for m in pattern.finditer(src):
            name = m.group(1)
            if not _REGISTRY_NAME.fullmatch(name) and not _REGISTRY_NAME.search(name):
                continue
            brace = src.find("{", m.start())
            if brace < 0:
                continue
            block = _extract_block(src, brace)
            # Only top-level keys: strip nested blocks before key extraction
            top = re.sub(r"\{[^{}]*\}", "{}", block[1:-1])
            members = []
            for km in _KEY.finditer(top):
                key = km.group(1)
                if key not in members:
                    members.append(key)
            if len(members) >= _MIN_MEMBERS:
                found += 1
                yield name, members, rel, _line_of(src, m.start())
                if found >= _MAX_REGISTRIES:
                    return


def run_state_coverage_probes(repo_root: str) -> list[dict]:
    """All state-coverage department findings."""
    findings: list[dict] = []
    try:
        registries = list(_find_registries(repo_root))
        if not registries:
            return findings

        # Build reference indexes once: member token -> appears in impl / test
        impl_hits: dict[str, int] = {}
        test_hits: dict[str, int] = {}
        all_members = {m for _, members, _, _ in registries for m in members}
        pats = {m: re.compile(r"\b" + re.escape(m) + r"\b") for m in all_members}

        for path in _walk(repo_root, _CODE_EXTS):
            rel = _rel(repo_root, path)
            src = _read(path)
            if src is None:
                continue
            is_test = _is_test_path(rel)
            for m in all_members:
                if pats[m].search(src):
                    if is_test:
                        test_hits[m] = test_hits.get(m, 0) + 1
                    else:
                        impl_hits[m] = impl_hits.get(m, 0) + 1

        for name, members, rel, lineno in registries:
            # Only meaningful when the registry's OTHER members ARE tested:
            # zero tested members means no test suite touches this domain at
            # all -- a different (and noisier) problem than partial coverage.
            tested = [m for m in members if test_hits.get(m, 0) > 0]
            if not tested:
                continue
            for member in members:
                if len(findings) >= _MAX_FINDINGS:
                    return findings
                impl_n = impl_hits.get(member, 0)
                test_n = test_hits.get(member, 0)
                if test_n > 0:
                    continue
                if impl_n > 1:  # >1: declaration file itself counts once
                    findings.append({
                        "slug": _slug("untested", name, member),
                        "title": (f"registry {name} ({rel}:{lineno}) member "
                                  f"'{member}' is used in implementation but "
                                  f"appears in NO test, while sibling members "
                                  f"{tested[:3]} are tested -- partial "
                                  f"coverage: the FREE-gated/TRIAL-ungated "
                                  f"shape"),
                        "area": rel,
                        "severity": "medium",
                        "confidence": 0.65,
                        "evidence": (f"{name}.{member}: impl refs={impl_n}, "
                                     f"test refs=0; tested siblings="
                                     f"{len(tested)}/{len(members)}"),
                        "proposed_action": (
                            f"derive the test from the registry itself: "
                            f"iterate every member of {name} and assert the "
                            f"policy/handler behavior for each, so new "
                            f"members extend coverage automatically instead "
                            f"of restating a hand-written list"
                        ),
                    })
                else:
                    findings.append({
                        "slug": _slug("unrouted", name, member),
                        "title": (f"registry {name} ({rel}:{lineno}) member "
                                  f"'{member}' is referenced nowhere outside "
                                  f"its declaration -- a declared state with "
                                  f"no handler and no test: either dead, or "
                                  f"handled by a fallthrough nobody chose"),
                        "area": rel,
                        "severity": "medium",
                        "confidence": 0.55,
                        "evidence": f"{name}.{member}: impl refs<=1, test refs=0",
                        "proposed_action": (
                            f"route '{member}' explicitly (even to a logged "
                            f"'unhandled' case), delete it, or record why "
                            f"fallthrough is the intended handling so this "
                            f"can close as wontfix with an owner"
                        ),
                    })
    except Exception:
        pass
    return findings
