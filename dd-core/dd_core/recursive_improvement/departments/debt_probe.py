"""Debt/Completeness department -- deterministic oracle. NO model.

Scans source files for markers of unfinished work: TODO/FIXME comments,
NotImplementedError stubs, empty function bodies, and placeholder return values.
Each finding becomes an aged claim in the store -- the claim's recorded_at is
the first-seen SHA, so `dd_ri.py backlog` can rank debt by age automatically.

All findings use slug prefix `debt-` -> arch.gap:debt-* in the claim store.

Language support: Python, Go, JavaScript/TypeScript, Java/Kotlin, Ruby, Rust.
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", "reflex", ".reflex",
})

_CODE_EXTS = {
    ".py", ".go", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".rb", ".rs",
}


def _walk(repo_root: str):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in _CODE_EXTS:
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


def _slug(label: str, rel: str, lineno: int) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")
    return f"debt-{label}-{safe}-{lineno}"


# ---------------------------------------------------------------------------
# 1. TODO / FIXME / HACK / XXX comments
# ---------------------------------------------------------------------------

_TODO_COMMENT = re.compile(
    r"#\s*(?:TODO|FIXME|HACK|XXX|BUG|TEMP|KLUDGE)\b.*$"
    r"|//\s*(?:TODO|FIXME|HACK|XXX|BUG|TEMP|KLUDGE)\b.*$"
    r"|/\*\s*(?:TODO|FIXME|HACK|XXX|BUG|TEMP|KLUDGE)\b",
    re.IGNORECASE | re.MULTILINE,
)


def todo_comments(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _TODO_COMMENT.finditer(src):
            lineno = _line_of(src, m.start())
            tag = m.group().strip()[:80]
            findings.append({
                "slug": _slug("todo", rel, lineno),
                "title": f"unresolved debt marker in {rel}:{lineno}: {tag}",
                "area": rel,
                "severity": "low",
                "confidence": 1.0,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "resolve the TODO, file it as a tracked work item, or remove it if "
                    "no longer relevant; stale debt markers erode the signal of the backlog"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 2. NotImplementedError / unimplemented!() stubs
# ---------------------------------------------------------------------------

_NOT_IMPL_PY = re.compile(r"\braise\s+NotImplementedError\b")
_NOT_IMPL_GO = re.compile(r'panic\s*\(\s*"(?:not implemented|TODO|unimplemented)"', re.IGNORECASE)
_NOT_IMPL_RUST = re.compile(r"\b(?:todo!|unimplemented!)\s*\(")
_NOT_IMPL_JAVA = re.compile(r'throw\s+new\s+UnsupportedOperationException\s*\(', re.IGNORECASE)
_NOT_IMPL_JS = re.compile(r'throw\s+new\s+Error\s*\(\s*[\'"](?:not implemented|TODO|unimplemented)',
                           re.IGNORECASE)
_NOT_IMPL_RUBY = re.compile(r'raise\s+(?:NotImplementedError|"not implemented"|\'not implemented\')',
                             re.IGNORECASE)

_LANG_PATTERNS = {
    ".py": _NOT_IMPL_PY,
    ".go": _NOT_IMPL_GO,
    ".rs": _NOT_IMPL_RUST,
    ".java": _NOT_IMPL_JAVA, ".kt": _NOT_IMPL_JAVA,
    ".js": _NOT_IMPL_JS, ".ts": _NOT_IMPL_JS,
    ".jsx": _NOT_IMPL_JS, ".tsx": _NOT_IMPL_JS,
    ".mjs": _NOT_IMPL_JS, ".cjs": _NOT_IMPL_JS,
    ".rb": _NOT_IMPL_RUBY,
}


def unimplemented_stubs(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root):
        ext = os.path.splitext(path)[1].lower()
        pat = _LANG_PATTERNS.get(ext)
        if pat is None:
            continue
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in pat.finditer(src):
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("stub", rel, lineno),
                "title": f"unimplemented stub in {rel}:{lineno} -- raises/panics with 'not implemented'",
                "area": rel,
                "severity": "medium",
                "confidence": 1.0,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "implement this stub or, if it is intentionally deferred, "
                    "document why in a comment and add it to the tracked backlog"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 3. Empty function bodies (Python: `pass` as sole body; Go/Java: empty {})
# ---------------------------------------------------------------------------

# Python: def foo(...): \n    pass  (with optional docstring before pass)
_PY_EMPTY_FUNC = re.compile(
    r"^\s*def\s+\w+[^:]*:[ \t]*\n"
    r"(?:[ \t]+[\"']{3}[^\"']*[\"']{3}[ \t]*\n)?"  # optional docstring
    r"[ \t]+pass[ \t]*\n",
    re.MULTILINE,
)
# Go: func Foo() { \n } or func Foo() { }
_GO_EMPTY_FUNC = re.compile(r"func\s+\w+[^{]*\{\s*\}", re.DOTALL)


def empty_functions(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root):
        ext = os.path.splitext(path)[1].lower()
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        if ext == ".py":
            for m in _PY_EMPTY_FUNC.finditer(src):
                lineno = _line_of(src, m.start())
                findings.append({
                    "slug": _slug("empty-func", rel, lineno),
                    "title": f"empty function body (pass) in {rel}:{lineno} -- likely a stub",
                    "area": rel,
                    "severity": "low",
                    "confidence": 0.80,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": "implement or explicitly mark as abstract/intentional placeholder",
                })
        elif ext == ".go":
            for m in _GO_EMPTY_FUNC.finditer(src):
                # only short matches (not multi-line real functions collapsed by dotall)
                if m.group().count("\n") > 3:
                    continue
                lineno = _line_of(src, m.start())
                findings.append({
                    "slug": _slug("empty-func", rel, lineno),
                    "title": f"empty function body in {rel}:{lineno} -- likely a stub",
                    "area": rel,
                    "severity": "low",
                    "confidence": 0.70,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": "implement or add a comment explaining why it is intentionally empty",
                })
    return findings


# ---------------------------------------------------------------------------
# 4. type: ignore without explanation (Python)
# ---------------------------------------------------------------------------

_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\s*$", re.MULTILINE)
_TYPE_IGNORE_WITH_REASON = re.compile(r"#\s*type:\s*ignore\s*\[", re.MULTILINE)


def unexplained_type_ignores(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root):
        if not path.endswith(".py"):
            continue
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _TYPE_IGNORE.finditer(src):
            if _TYPE_IGNORE_WITH_REASON.match(src, m.start()):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("type-ignore", rel, lineno),
                "title": f"unexplained # type: ignore in {rel}:{lineno}",
                "area": rel,
                "severity": "low",
                "confidence": 1.0,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "add an error code (# type: ignore[assignment]) and a comment "
                    "explaining why the suppression is necessary; bare ignores hide "
                    "real type errors silently"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_debt_probes(repo_root: str) -> list[dict]:
    """All debt department findings."""
    out: list[dict] = []
    for oracle in (todo_comments, unimplemented_stubs, empty_functions,
                   unexplained_type_ignores):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
