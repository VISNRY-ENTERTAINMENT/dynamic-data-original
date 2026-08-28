"""Reachability department -- deterministic oracle. NO model.

Finds code that is DECLARED PUBLIC but NEVER CONSUMED: the "correct, tested,
reviewed, deployed -- and never executed" failure class. Complements the
existing wiring check (declared+consumed but never provided); this is the
inverse (defined and never called).

Origin: a real P0 where a security-critical identity path (`tok:` records)
was fully implemented, unit-tested (142/142), and independently reviewed
three times -- but the HTTP handler never extracted the field from the
request body, so no real request could ever reach it. Every review verified
the logic was correct; none verified the logic was reachable. This probe
automates the backward-reachability check that would have caught it.

Precision bar (design constraint, not aspiration): a probe that fires 200
times on a mature codebase is a lint rule, not a governance finding, and it
buries the signal. Every oracle here restricts itself to EXPLICIT public-API
declarations (module.exports / exports.X / __all__) rather than every
top-level definition, specifically to stay low-volume and high-signal.

Oracles:
  1. JS/TS exported-never-referenced  -- names in `module.exports = {...}`
     or `exports.name =` with zero references in any OTHER non-test file
  2. Python __all__-never-referenced  -- names listed in `__all__` with zero
     references in any OTHER non-test file

All findings use slug prefix `reach-` so they live at arch.gap:reach-* in
the claim store.

Confidence calibration:
  0.70 -- the name genuinely appears nowhere else; real signal, but the
          consumer may be external to this repo (a published package's API),
          so this stays below the auto-escalate certainty of 0.85
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", ".idea", ".vscode", "reflex",
    ".reflex", "coverage", "_shelved",
})

# Directories whose references DON'T count as consumption -- a function whose
# only caller is its own test file is exactly the failure this probe exists
# to catch (unit tests exercised the logic; nothing real ever did).
_TEST_DIRS = frozenset({
    "tests", "test", "spec", "specs", "__tests__", "proofs", "e2e",
})
_TEST_FILE_RE = re.compile(r"(?:\.test\.|\.spec\.|_test\.|test_|selftest)", re.IGNORECASE)

_JS_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
_PY_EXTS = {".py"}

# Common names that are exported as framework/protocol entry points and are
# legitimately "unreferenced" in-repo (loaded by convention, not by name).
_ENTRYPOINT_NAMES = frozenset({
    "default", "main", "handler", "middleware", "setup", "teardown",
    "up", "down", "register", "activate", "deactivate", "init",
})


def _walk(repo_root: str, exts: set, include_tests: bool = True):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        if not include_tests:
            dirs[:] = [d for d in dirs if d.lower() not in _TEST_DIRS]
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


def _slug(prefix: str, rel: str, name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", f"{rel}-{name}".lower()).strip("-")
    return f"reach-{prefix}-{safe}"


def _is_test_path(rel: str) -> bool:
    parts = rel.lower().split("/")
    if any(p in _TEST_DIRS for p in parts):
        return True
    return bool(_TEST_FILE_RE.search(parts[-1]))


# ---------------------------------------------------------------------------
# export collection
# ---------------------------------------------------------------------------

# module.exports = { foo, bar: baz, qux, }
_MODULE_EXPORTS_BLOCK = re.compile(
    r"module\.exports\s*=\s*\{([^}]*)\}", re.DOTALL)
# exports.foo = ...   /   module.exports.foo = ...
_EXPORTS_ASSIGN = re.compile(
    r"(?:module\.)?exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=")
# export { foo, bar }   /   export function foo   /   export const foo (TS/ESM)
_ESM_NAMED_BLOCK = re.compile(r"\bexport\s*\{([^}]*)\}")
_ESM_DECL = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function|const|let|var|class)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)")

_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _js_exports(src: str) -> list[tuple[str, int]]:
    """Extract (name, lineno) for every explicitly exported name."""
    out: list[tuple[str, int]] = []
    for m in _MODULE_EXPORTS_BLOCK.finditer(src):
        body = m.group(1)
        base = m.start(1)
        for entry in body.split(","):
            offset = body.find(entry)
            # "foo" or "foo: bar" -> exported name is the LEFT side
            name = entry.split(":")[0].strip()
            # strip spread/comments/shorthand noise
            name = name.split("//")[0].strip()
            if name.startswith("..."):
                continue
            if _IDENT.match(name):
                out.append((name, _line_of(src, base + max(offset, 0))))
    for m in _EXPORTS_ASSIGN.finditer(src):
        out.append((m.group(1), _line_of(src, m.start())))
    for m in _ESM_NAMED_BLOCK.finditer(src):
        body = m.group(1)
        base = m.start(1)
        for entry in body.split(","):
            offset = body.find(entry)
            # "foo as bar" -> the consumer-visible name is the RIGHT side
            parts = entry.strip().split()
            name = parts[-1] if parts else ""
            if _IDENT.match(name):
                out.append((name, _line_of(src, base + max(offset, 0))))
    for m in _ESM_DECL.finditer(src):
        out.append((m.group(1), _line_of(src, m.start())))
    return out


_PY_ALL_BLOCK = re.compile(r"__all__\s*=\s*[\[\(]([^\]\)]*)[\]\)]", re.DOTALL)
_PY_STR = re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")


def _py_all_exports(src: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for m in _PY_ALL_BLOCK.finditer(src):
        body = m.group(1)
        base = m.start(1)
        for sm in _PY_STR.finditer(body):
            out.append((sm.group(1), _line_of(src, base + sm.start())))
    return out


# ---------------------------------------------------------------------------
# reference counting
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _build_corpus(repo_root: str, exts: set) -> list[tuple[str, str, bool, frozenset]]:
    """[(rel, src, is_test, token_set), ...] for every file of the given
    extensions. The per-file identifier set makes each reference check an
    O(1) membership test instead of a regex scan of the whole corpus per
    export -- required for per-commit use on large repos."""
    corpus = []
    for path in _walk(repo_root, exts):
        src = _read(path)
        if src is None:
            continue
        rel = _rel(repo_root, path)
        tokens = frozenset(_TOKEN.findall(src))
        corpus.append((rel, src, _is_test_path(rel), tokens))
    return corpus


def _referenced_outside(name: str, defining_rel: str,
                        corpus: list[tuple[str, str, bool, frozenset]]) -> bool:
    """True if `name` appears as an identifier in any non-test file other
    than the one that defines it."""
    for rel, _src, is_test, tokens in corpus:
        if rel == defining_rel or is_test:
            continue
        if name in tokens:
            return True
    return False


# ---------------------------------------------------------------------------
# oracles
# ---------------------------------------------------------------------------

def js_exported_never_referenced(repo_root: str) -> list[dict]:
    corpus = _build_corpus(repo_root, _JS_EXTS)
    findings: list[dict] = []
    for rel, src, is_test in corpus:
        if is_test:
            continue
        for name, lineno in _js_exports(src):
            if len(name) < 4 or name.lower() in _ENTRYPOINT_NAMES:
                continue  # short/framework names are too collision-prone
            if _referenced_outside(name, rel, corpus):
                continue
            findings.append({
                "slug": _slug("js-export", rel, name),
                "title": (f"`{name}` is exported from {rel}:{lineno} but "
                          f"referenced by no non-test file in this repo -- "
                          f"declared public, never consumed"),
                "area": rel,
                "severity": "high",
                "confidence": 0.70,
                "evidence": f"{rel}:{lineno} export `{name}`",
                "proposed_action": (
                    f"trace BACKWARD from the real entry point (HTTP route / CLI / "
                    f"event handler) that should reach `{name}`: either wire the "
                    f"missing call, delete the dead export, or -- if the consumer "
                    f"is external to this repo -- record that explicitly so this "
                    f"claim can be closed as wontfix with a named consumer"
                ),
            })
    return findings


def py_all_never_referenced(repo_root: str) -> list[dict]:
    corpus = _build_corpus(repo_root, _PY_EXTS)
    findings: list[dict] = []
    for rel, src, is_test in corpus:
        if is_test:
            continue
        for name, lineno in _py_all_exports(src):
            if len(name) < 4 or name.lower() in _ENTRYPOINT_NAMES:
                continue
            if _referenced_outside(name, rel, corpus):
                continue
            findings.append({
                "slug": _slug("py-all", rel, name),
                "title": (f"`{name}` is listed in __all__ of {rel}:{lineno} but "
                          f"referenced by no non-test file in this repo -- "
                          f"declared public, never consumed"),
                "area": rel,
                "severity": "high",
                "confidence": 0.70,
                "evidence": f"{rel}:{lineno} __all__ entry `{name}`",
                "proposed_action": (
                    f"trace backward from the real entry point that should reach "
                    f"`{name}`: wire the missing call, remove it from __all__, or "
                    f"record the external consumer explicitly and close as wontfix"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_reachability_probes(repo_root: str) -> list[dict]:
    """All reachability department findings."""
    out: list[dict] = []
    for oracle in (
        js_exported_never_referenced,
        py_all_never_referenced,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
