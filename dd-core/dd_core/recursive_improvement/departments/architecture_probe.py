"""Architecture department -- deterministic oracle. NO model.

Reads an `architecture_rules.json` manifest (checked into the repo) and enforces
stated design rules against the actual source tree. Four rule types:

  no_import       -- module A must never import from module B (layer violations,
                     circular deps, boundary violations)
  no_pattern      -- a regex must not appear in files matching a glob (e.g. no
                     direct DB calls from the presentation layer)
  naming_rule     -- files matching a glob must follow a naming pattern
  required_file   -- certain files must exist given a condition

All findings use slug prefix `arch-` -> arch.gap:arch-* in the claim store.

Manifest schema (architecture_rules.json at repo root, or configured path):

    {
      "rules": [
        {
          "id": "no-ui-to-db",
          "type": "no_import",
          "description": "UI layer must not import directly from db/ module",
          "from_glob": "ui/**/*.py",
          "forbidden_import": "db.",
          "severity": "high"
        },
        {
          "id": "no-raw-sql-in-routes",
          "type": "no_pattern",
          "pattern": "cursor\\.execute|SELECT.*FROM",
          "in_glob": "routes/**/*.py",
          "description": "raw SQL must not appear in route handlers",
          "severity": "high"
        },
        {
          "id": "handlers-named-handler",
          "type": "naming_rule",
          "in_glob": "handlers/**/*.py",
          "pattern": ".*_handler\\.py$",
          "description": "all files in handlers/ must end with _handler.py",
          "severity": "low"
        },
        {
          "id": "must-have-readme",
          "type": "required_file",
          "path": "README.md",
          "description": "root README must exist",
          "severity": "low"
        }
      ]
    }
"""
from __future__ import annotations

import fnmatch
import json
import os
import re

_DEFAULT_MANIFEST = "architecture_rules.json"


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


def _slug_from_id(rule_id: str, suffix: str = "") -> str:
    safe_id = re.sub(r"[^a-z0-9]+", "-", rule_id.lower()).strip("-")
    safe_suf = re.sub(r"[^a-z0-9]+", "-", suffix.lower()).strip("-")
    base = f"arch-{safe_id}"
    return f"{base}-{safe_suf}" if safe_suf else base


def _iter_matching(repo_root: str, glob_pattern: str):
    """Yield (abs_path, rel_path) for files matching a glob, relative to repo_root."""
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv",
                                "venv", "dist", "build", "vendor"}
                   and not d.startswith(".")]
        for f in files:
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, repo_root).replace("\\", "/")
            if fnmatch.fnmatch(rel_p, glob_pattern):
                yield abs_p, rel_p


# ---------------------------------------------------------------------------
# Rule checkers
# ---------------------------------------------------------------------------

# Import patterns per language
_PY_IMPORT = re.compile(r"^\s*(?:import|from)\s+([\w.]+)", re.MULTILINE)
_JS_IMPORT = re.compile(r"""(?:import|require)\s*\(?[\s\n]*['"]([^'"]+)['"]""", re.MULTILINE)
_GO_IMPORT = re.compile(r'"([^"]+)"')
_JAVA_IMPORT = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)


def _imports_from(src: str, ext: str) -> list[str]:
    if ext == ".py":
        return [m.group(1) for m in _PY_IMPORT.finditer(src)]
    if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
        return [m.group(1) for m in _JS_IMPORT.finditer(src)]
    if ext == ".go":
        return [m.group(1) for m in _GO_IMPORT.finditer(src)]
    if ext in (".java", ".kt"):
        return [m.group(1) for m in _JAVA_IMPORT.finditer(src)]
    return []


def check_no_import(repo_root: str, rule: dict) -> list[dict]:
    findings: list[dict] = []
    from_glob = rule.get("from_glob", "**/*")
    forbidden = rule.get("forbidden_import", "")
    rule_id = rule.get("id", "no-import")
    sev = rule.get("severity", "high")
    desc = rule.get("description", f"forbidden import: {forbidden}")
    if not forbidden:
        return []
    for abs_p, rel_p in _iter_matching(repo_root, from_glob):
        src = _read(abs_p)
        if not src:
            continue
        ext = os.path.splitext(rel_p)[1].lower()
        for imp in _imports_from(src, ext):
            if imp.startswith(forbidden) or forbidden in imp:
                # find approx line
                pat = re.compile(re.escape(imp))
                for m in pat.finditer(src):
                    lineno = _line_of(src, m.start())
                    findings.append({
                        "slug": _slug_from_id(rule_id, f"{rel_p}-{lineno}"),
                        "title": f"[{rule_id}] {desc}: {rel_p}:{lineno} imports {imp!r}",
                        "area": rel_p,
                        "severity": sev,
                        "confidence": 0.90,
                        "evidence": f"{rel_p}:{lineno}",
                        "proposed_action": (
                            f"remove the import of {imp!r} from {rel_p}; "
                            f"this violates the stated architecture rule: {desc}"
                        ),
                    })
                    break  # one finding per file per forbidden import
    return findings


def check_no_pattern(repo_root: str, rule: dict) -> list[dict]:
    findings: list[dict] = []
    in_glob = rule.get("in_glob", "**/*")
    pattern_str = rule.get("pattern", "")
    rule_id = rule.get("id", "no-pattern")
    sev = rule.get("severity", "medium")
    desc = rule.get("description", f"forbidden pattern: {pattern_str}")
    if not pattern_str:
        return []
    try:
        pat = re.compile(pattern_str, re.MULTILINE | re.IGNORECASE)
    except re.error:
        return []
    for abs_p, rel_p in _iter_matching(repo_root, in_glob):
        src = _read(abs_p)
        if not src:
            continue
        for m in pat.finditer(src):
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug_from_id(rule_id, f"{rel_p}-{lineno}"),
                "title": f"[{rule_id}] {desc} in {rel_p}:{lineno}",
                "area": rel_p,
                "severity": sev,
                "confidence": 0.85,
                "evidence": f"{rel_p}:{lineno}",
                "proposed_action": (
                    f"remove or refactor the code at {rel_p}:{lineno} to comply "
                    f"with the architecture rule: {desc}"
                ),
            })
    return findings


def check_naming_rule(repo_root: str, rule: dict) -> list[dict]:
    findings: list[dict] = []
    in_glob = rule.get("in_glob", "**/*")
    name_pattern_str = rule.get("pattern", "")
    rule_id = rule.get("id", "naming-rule")
    sev = rule.get("severity", "low")
    desc = rule.get("description", f"naming convention: {name_pattern_str}")
    if not name_pattern_str:
        return []
    try:
        name_pat = re.compile(name_pattern_str)
    except re.error:
        return []
    for _, rel_p in _iter_matching(repo_root, in_glob):
        filename = os.path.basename(rel_p)
        if not name_pat.match(filename):
            findings.append({
                "slug": _slug_from_id(rule_id, rel_p),
                "title": f"[{rule_id}] {desc}: {rel_p!r} does not match {name_pattern_str!r}",
                "area": rel_p,
                "severity": sev,
                "confidence": 1.0,
                "evidence": rel_p,
                "proposed_action": (
                    f"rename {rel_p} to match the pattern {name_pattern_str!r} "
                    f"per the architecture rule: {desc}"
                ),
            })
    return findings


def check_required_file(repo_root: str, rule: dict) -> list[dict]:
    path = rule.get("path", "")
    rule_id = rule.get("id", "required-file")
    sev = rule.get("severity", "low")
    desc = rule.get("description", f"required file: {path}")
    if not path:
        return []
    abs_p = os.path.join(repo_root, path)
    if os.path.exists(abs_p):
        return []
    return [{
        "slug": _slug_from_id(rule_id),
        "title": f"[{rule_id}] required file missing: {path}",
        "area": path,
        "severity": sev,
        "confidence": 1.0,
        "evidence": f"file not found: {path}",
        "proposed_action": f"create {path} as required by the architecture rule: {desc}",
    }]


_CHECKERS = {
    "no_import": check_no_import,
    "no_pattern": check_no_pattern,
    "naming_rule": check_naming_rule,
    "required_file": check_required_file,
}


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_architecture_probes(repo_root: str, manifest_path: str | None = None) -> list[dict]:
    """All architecture department findings from the manifest."""
    manifest_path = manifest_path or os.path.join(repo_root, _DEFAULT_MANIFEST)
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except Exception:
        return []
    rules = manifest.get("rules", [])
    out: list[dict] = []
    for rule in rules:
        rule_type = rule.get("type", "")
        checker = _CHECKERS.get(rule_type)
        if checker is None:
            continue
        try:
            out += checker(repo_root, rule)
        except Exception:
            pass
    return out
