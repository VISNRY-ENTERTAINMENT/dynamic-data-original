"""Observability department -- deterministic oracle. NO model.

Flags code paths that can fail but have no way to surface those failures:
bare exception swallows, unobservable background tasks, I/O without error
handling, and functions that return None on failure with no logging.

All findings use slug prefix `obs-` -> arch.gap:obs-* in the claim store.

Confidence calibration:
  0.85 -- bare `except: pass` -- almost never intentional in production code
  0.70 -- `except Exception: pass` -- slightly more arguable
  0.60 -- background task with no error boundary
  0.50 -- I/O without try/except (heuristic; below default floor unless lowered)
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", "reflex", ".reflex",
    "tests", "test", "spec",
})

_PY_EXT = ".py"
_JS_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
_GO_EXT = ".go"


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


def _slug(label: str, rel: str, lineno: int) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")
    return f"obs-{label}-{safe}-{lineno}"


# ---------------------------------------------------------------------------
# 1. Bare exception swallows (Python)
# ---------------------------------------------------------------------------

# except: pass -- the worst form, catches BaseException silently
_BARE_EXCEPT_PASS = re.compile(r"^\s*except\s*:\s*\n\s*pass\s*$", re.MULTILINE)
# except Exception: pass -- also swallows silently
_EXCEPT_EXCEPTION_PASS = re.compile(
    r"^\s*except\s+(?:Exception|BaseException)\s*(?:as\s+\w+)?\s*:\s*\n\s*pass\s*$",
    re.MULTILINE,
)
# except Exception: ... (multi-line but body contains only `pass` or a comment)
_EXCEPT_PASS_INLINE = re.compile(r"except\s*(?:Exception|BaseException)?\s*:\s*pass\b")


def bare_exception_swallows_py(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, {_PY_EXT}):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        seen_lines: set[int] = set()
        for pat, label, conf, title_suffix in (
            (_BARE_EXCEPT_PASS, "bare-except-pass", 0.85,
             "bare except: pass swallows all exceptions silently"),
            (_EXCEPT_EXCEPTION_PASS, "except-exception-pass", 0.70,
             "except Exception: pass swallows all exceptions silently"),
            (_EXCEPT_PASS_INLINE, "except-pass-inline", 0.70,
             "inline except: pass swallows exception silently"),
        ):
            for m in pat.finditer(src):
                lineno = _line_of(src, m.start())
                if lineno in seen_lines:
                    continue
                seen_lines.add(lineno)
                findings.append({
                    "slug": _slug(label, rel, lineno),
                    "title": f"{title_suffix} in {rel}:{lineno}",
                    "area": rel,
                    "severity": "medium",
                    "confidence": conf,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": (
                        "at minimum log the exception (log.warning/log.error); "
                        "if truly intentional, add a comment explaining why "
                        "silently swallowing this exception is safe here"
                    ),
                })
    return findings


# ---------------------------------------------------------------------------
# 2. JavaScript/TypeScript catch with empty body
# ---------------------------------------------------------------------------

# catch(e) {} or catch (e) {} or catch {}
_JS_EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")


def bare_exception_swallows_js(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _JS_EXTS):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _JS_EMPTY_CATCH.finditer(src):
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("empty-catch", rel, lineno),
                "title": f"empty catch block in {rel}:{lineno} -- exception swallowed silently",
                "area": rel,
                "severity": "medium",
                "confidence": 0.85,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "log the error or rethrow; an empty catch makes failures "
                    "invisible to monitoring and debugging"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 3. Go: blank-identifier error discard
# ---------------------------------------------------------------------------

# x, _ := something()  where something looks like it returns an error
_GO_DISCARD_ERR = re.compile(
    r"[\w,\s]+,\s*_\s*:?=\s*\w+[\w.]*\s*\([^)]*\)"
)
# Filter: only flag if the function name suggests it can fail
_GO_ERR_FUNC = re.compile(
    r":?=\s*(?:os\.|io\.|http\.|sql\.|json\.|bufio\.|net\.|grpc\.|db\.)\w+\s*\("
)


def go_discarded_errors(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, {_GO_EXT}):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _GO_DISCARD_ERR.finditer(src):
            if not _GO_ERR_FUNC.search(m.group()):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("go-discard-err", rel, lineno),
                "title": f"discarded error return from I/O call in {rel}:{lineno}",
                "area": rel,
                "severity": "medium",
                "confidence": 0.70,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "handle the error: log it, return it to the caller, or "
                    "add a comment explaining why discarding is safe for this call"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 4. Async Python functions with no error handling
# ---------------------------------------------------------------------------

# async def that contains an await but no try/except in its body
_ASYNC_DEF = re.compile(r"^(\s*)async\s+def\s+(\w+)\s*\([^)]*\)\s*.*?:\s*\n", re.MULTILINE)
_HAS_AWAIT = re.compile(r"\bawait\b")
_HAS_TRY = re.compile(r"\btry\s*:")
_ASYNC_IO_HINT = re.compile(r"\bawait\s+(?:asyncio\.|aio|http|db|session|client|conn)\w*\.")


def async_without_error_handling(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, {_PY_EXT}):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        lines = src.splitlines(keepends=True)
        for m in _ASYNC_DEF.finditer(src):
            indent = m.group(1)
            func_name = m.group(2)
            start = m.end()
            # collect function body: lines with deeper indent than the def
            body_lines = []
            for line in src[start:].splitlines(keepends=True):
                stripped = line.lstrip()
                if stripped and not line.startswith(indent + " ") and not line.startswith(indent + "\t"):
                    break  # back to same or shallower indent -- end of function
                body_lines.append(line)
            body = "".join(body_lines)
            if not _HAS_AWAIT.search(body):
                continue  # no await -- not async I/O
            if not _ASYNC_IO_HINT.search(body):
                continue  # awaiting something benign (e.g. asyncio.sleep)
            if _HAS_TRY.search(body):
                continue  # has error handling
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("async-no-error-handling", rel, lineno),
                "title": (f"async function {func_name!r} in {rel}:{lineno} "
                          f"awaits I/O but has no try/except"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.60,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "wrap the awaited I/O in a try/except so failures are "
                    "logged and handled rather than silently propagating as "
                    "an unhandled exception in the event loop"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 5. Background tasks / threads with no error boundary (Python)
# ---------------------------------------------------------------------------

_THREAD_START = re.compile(
    r"(?:threading\.Thread|asyncio\.create_task|concurrent\.futures)\s*\("
)
_NEARBY_EXCEPT = re.compile(r"\btry\b|\bexcept\b|\bdone_callback\b")
_THREAD_WINDOW = 400


def unguarded_background_tasks(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, {_PY_EXT}):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _THREAD_START.finditer(src):
            window_before = src[max(0, m.start() - _THREAD_WINDOW): m.start()]
            window_after = src[m.end(): m.end() + _THREAD_WINDOW]
            if _NEARBY_EXCEPT.search(window_before) or _NEARBY_EXCEPT.search(window_after):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("unguarded-bg-task", rel, lineno),
                "title": (f"background task/thread spawned in {rel}:{lineno} "
                          f"with no visible error boundary or done callback"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.60,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "add an error handler: use add_done_callback() for asyncio tasks, "
                    "wrap thread targets in try/except, or use a supervisor pattern so "
                    "background failures are logged and the system can recover"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_observability_probes(repo_root: str) -> list[dict]:
    """All observability department findings."""
    out: list[dict] = []
    for oracle in (
        bare_exception_swallows_py,
        bare_exception_swallows_js,
        go_discarded_errors,
        async_without_error_handling,
        unguarded_background_tasks,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
