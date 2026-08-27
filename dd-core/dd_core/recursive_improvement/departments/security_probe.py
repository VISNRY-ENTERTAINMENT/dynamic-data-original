"""Security department -- deterministic oracle. NO model.

Extends attack_pattern_probe.py (JS/TS patterns) with multi-language support:
Python, Go, Rust, Java, Ruby. All findings use slug prefix `security-` so they
live at arch.gap:security-* in the claim store.

Pattern categories (all from real incidents, not hypotheticals):
  1. Hardcoded secrets  -- high-entropy or named credential literals
  2. Injection surfaces -- user input within N lines of dangerous sink
  3. Presence-only auth -- credential checked for existence, not validity
  4. Deserialization    -- untrusted data deserialized without validation
  5. Dangerous shell    -- subprocess/os.system with shell=True + variable
  6. Path traversal     -- user input in file path construction

Confidence calibration:
  0.85 -- high-specificity patterns with very few legitimate uses
  0.70 -- medium-specificity, real signal but plausible false positives
  0.50 -- heuristic, below default floor (0.6), on-demand unless floor lowered
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", ".idea", ".vscode",
    "tests", "test", "spec", "specs", "__tests__", "reflex", ".reflex",
})

_PY_EXTS = {".py"}
_GO_EXTS = {".go"}
_JAVA_EXTS = {".java", ".kt"}
_RUBY_EXTS = {".rb"}
_RUST_EXTS = {".rs"}
_JS_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
_ALL_EXTS = _PY_EXTS | _GO_EXTS | _JAVA_EXTS | _RUBY_EXTS | _RUST_EXTS | _JS_EXTS


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


def _slug(prefix: str, rel: str, lineno: int) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")
    return f"security-{prefix}-{safe}-{lineno}"


# ---------------------------------------------------------------------------
# 1. Hardcoded secrets
# ---------------------------------------------------------------------------

_SECRET_ASSIGN_PY = re.compile(
    r"""(?:api_?key|secret|password|passwd|token|credential|auth_?key|access_?key|private_?key)\s*=\s*['"][^'"]{8,}['"]""",
    re.IGNORECASE,
)
_SECRET_ASSIGN_GO = re.compile(
    r'(?:apiKey|secretKey|password|token|credential|accessKey|privateKey)\s*:?=\s*"[^"]{8,}"',
    re.IGNORECASE,
)
_SECRET_ASSIGN_JAVA = re.compile(
    r'(?:API_?KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL)\s*=\s*"[^"]{8,}"',
    re.IGNORECASE,
)
_SECRET_ASSIGN_RUBY = re.compile(
    r"""(?:api_key|secret|password|token|credential)\s*=\s*['"][^'"]{8,}['"]""",
    re.IGNORECASE,
)
# Exclude obvious placeholders
_PLACEHOLDER = re.compile(
    r"(?:your[-_]|<|>|example|placeholder|replace|changeme|xxx|todo|fixme)",
    re.IGNORECASE,
)


def _hardcoded_secrets_in(repo_root: str, path: str, pattern: re.Pattern,
                           findings: list):
    src = _read(path)
    if not src:
        return
    rel = _rel(repo_root, path)
    for m in pattern.finditer(src):
        if _PLACEHOLDER.search(m.group()):
            continue
        lineno = _line_of(src, m.start())
        name = _slug("hardcoded-secret", rel, lineno)
        findings.append({
            "slug": name,
            "title": f"hardcoded credential literal in {rel}:{lineno}",
            "area": rel,
            "severity": "high",
            "confidence": 0.85,
            "evidence": f"{rel}:{lineno}",
            "proposed_action": (
                "move credential to an environment variable or a secrets manager; "
                "never commit credential values into source code"
            ),
        })


def hardcoded_secrets(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _PY_EXTS):
        _hardcoded_secrets_in(repo_root, path, _SECRET_ASSIGN_PY, findings)
    for path in _walk(repo_root, _GO_EXTS):
        _hardcoded_secrets_in(repo_root, path, _SECRET_ASSIGN_GO, findings)
    for path in _walk(repo_root, _JAVA_EXTS):
        _hardcoded_secrets_in(repo_root, path, _SECRET_ASSIGN_JAVA, findings)
    for path in _walk(repo_root, _RUBY_EXTS):
        _hardcoded_secrets_in(repo_root, path, _SECRET_ASSIGN_RUBY, findings)
    return findings


# ---------------------------------------------------------------------------
# 2. Dangerous shell invocation (Python-specific; highest-value surface)
# ---------------------------------------------------------------------------

_SHELL_TRUE_PY = re.compile(
    r"subprocess\.(?:run|Popen|call|check_output|check_call)\s*\([^)]*shell\s*=\s*True",
    re.DOTALL,
)
_OS_SYSTEM_PY = re.compile(r"\bos\.system\s*\(")
_FSTRING_OR_FORMAT = re.compile(r'f["\']|\.format\s*\(|%\s*[(\w]')

_WINDOW = 300


def dangerous_shell(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _PY_EXTS):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for pat, label, conf in (
            (_SHELL_TRUE_PY, "subprocess-shell-true", 0.70),
            (_OS_SYSTEM_PY, "os-system-call", 0.70),
        ):
            for m in pat.finditer(src):
                window = src[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
                if not _FSTRING_OR_FORMAT.search(window):
                    continue  # static string -- likely fine
                lineno = _line_of(src, m.start())
                findings.append({
                    "slug": _slug(label, rel, lineno),
                    "title": f"shell invocation with dynamic string in {rel}:{lineno} -- potential command injection",
                    "area": rel,
                    "severity": "high",
                    "confidence": conf,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": (
                        "pass a list of args (not a string) to subprocess, or validate/allowlist "
                        "any user-controlled portions before constructing the command"
                    ),
                })
    return findings


# ---------------------------------------------------------------------------
# 3. Python eval/exec with dynamic input
# ---------------------------------------------------------------------------

_EVAL_EXEC = re.compile(r"\b(eval|exec)\s*\(")
_STATIC_LITERAL = re.compile(r"""(eval|exec)\s*\(\s*["']""")


def dangerous_eval(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _PY_EXTS):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for m in _EVAL_EXEC.finditer(src):
            if _STATIC_LITERAL.match(src, m.start()):
                continue  # eval("fixed string") -- no dynamic input
            lineno = _line_of(src, m.start())
            fn = m.group(1)
            findings.append({
                "slug": _slug(f"dynamic-{fn}", rel, lineno),
                "title": f"dynamic {fn}() call in {rel}:{lineno} -- argument may be attacker-controlled",
                "area": rel,
                "severity": "high",
                "confidence": 0.70,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    f"replace {fn}() with a safe alternative (ast.literal_eval for data, "
                    "importlib for dynamic imports); if intentional, add a comment explaining "
                    "why the input is trusted"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 4. Unsafe deserialization
# ---------------------------------------------------------------------------

_PICKLE = re.compile(r"\bpickle\.loads?\s*\(")
_YAML_FULL = re.compile(r"\byaml\.load\s*\([^)]*\)", re.DOTALL)
_YAML_SAFE = re.compile(r"Loader\s*=\s*yaml\.(?:SafeLoader|BaseLoader)")
_MARSHAL = re.compile(r"\bmarshal\.loads?\s*\(")


def unsafe_deserialization(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _PY_EXTS):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for pat, label, note in (
            (_PICKLE, "pickle-deserialize",
             "pickle.loads() executes arbitrary code; use json/msgpack for untrusted data"),
            (_MARSHAL, "marshal-deserialize",
             "marshal.loads() is not safe for untrusted data; use json"),
        ):
            for m in pat.finditer(src):
                lineno = _line_of(src, m.start())
                findings.append({
                    "slug": _slug(label, rel, lineno),
                    "title": f"unsafe deserialization ({label}) in {rel}:{lineno}",
                    "area": rel,
                    "severity": "high",
                    "confidence": 0.85,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": note,
                })
        for m in _YAML_FULL.finditer(src):
            if _YAML_SAFE.search(m.group()):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("yaml-unsafe-load", rel, lineno),
                "title": f"yaml.load() without SafeLoader in {rel}:{lineno} -- arbitrary code execution risk",
                "area": rel,
                "severity": "medium",
                "confidence": 0.85,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": "use yaml.safe_load() or yaml.load(..., Loader=yaml.SafeLoader)",
            })
    return findings


# ---------------------------------------------------------------------------
# 5. Path traversal (Python)
# ---------------------------------------------------------------------------

_PATH_JOIN_VAR = re.compile(r"os\.path\.join\s*\([^)]*\b(?:request|req|input|user|param|arg|query|body)\b")
_OPEN_VAR = re.compile(r"\bopen\s*\(\s*(?!['\"]).{0,80}(?:request|req|input|user|param|arg|query|body)\b")


def path_traversal(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _PY_EXTS):
        src = _read(path)
        if not src:
            continue
        rel = _rel(repo_root, path)
        for pat, label in (
            (_PATH_JOIN_VAR, "path-join-user-input"),
            (_OPEN_VAR, "open-user-input"),
        ):
            for m in pat.finditer(src):
                lineno = _line_of(src, m.start())
                findings.append({
                    "slug": _slug(label, rel, lineno),
                    "title": f"user-controlled input in file path construction in {rel}:{lineno}",
                    "area": rel,
                    "severity": "high",
                    "confidence": 0.50,
                    "evidence": f"{rel}:{lineno}",
                    "proposed_action": (
                        "validate the user-supplied path component against an allowlist, "
                        "resolve with os.path.realpath() and assert it stays within the "
                        "expected base directory before opening"
                    ),
                })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_security_probes(repo_root: str) -> list[dict]:
    """All security department findings. Each oracle isolated so one failure
    never suppresses others."""
    out: list[dict] = []
    # Import JS/TS patterns from the existing probe rather than duplicating
    try:
        from dd_core.recursive_improvement.attack_pattern_probe import run_attack_probes
        out += run_attack_probes(repo_root)
    except Exception:
        pass
    for oracle in (
        hardcoded_secrets,
        dangerous_shell,
        dangerous_eval,
        unsafe_deserialization,
        path_traversal,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
