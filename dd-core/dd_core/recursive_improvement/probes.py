"""Deterministic structural probes. NO model at all.

The most valuable findings this loop produced were STRUCTURAL: "this hook exists
but nothing constructs it", "this param is accepted but never passed". Those are
not judgment calls -- they are facts about the code, and a model is the wrong
tool for a fact. So they are computed directly and recorded as findings with
source `reflex-probe` (no model in the discovery path either).

`unwired_optional_params` finds the "built but not wired" pattern in Python:
a constructor/function optional parameter (has a default) that is NEVER passed
as a keyword argument anywhere in the tree. That is exactly how a capability
gets added to a class and then silently never reached -- the class in this repo
that kept producing that bug.

Heuristic and language-specific (Python), so it is CONSERVATIVE: it only flags a
param when it can see the definition and finds zero `name=` call sites. False
negatives are fine (a model pass still runs); a false positive is cheap (low
severity, evidence points at the exact def).
"""

from __future__ import annotations

import ast
import os
import re

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
         "build", "vendor", "site-packages", ".idea", ".vscode", "tests",
         "test"}

# The "built but not wired" pattern is specifically an injected DEPENDENCY that
# is optional and never supplied -- a repo/service/coordinator left None
# everywhere. Not every optional param: a default IS "you may omit this". So we
# only flag params whose NAME reads as a dependency AND whose default is
# literally None. This keeps the probe precise (running it live showed the broad
# "any optional param" version floods with framework-injected and
# legitimately-optional params).
_DEP_SUFFIXES = (
    "_repo", "_repository", "_service", "_store", "_coordinator", "_applier",
    "_engine", "_client", "_uow", "_unit_of_work", "_gateway", "_registry",
    "_orchestrator", "_pipeline", "_resolver", "_proposer", "_matcher",
    "_reconstructor", "_projector", "_sender", "_evaluator", "_index",
    "_queue", "_bus", "_provider", "_manager", "_handler", "_processor",
)


def _looks_like_dependency(name: str) -> bool:
    return name.endswith(_DEP_SUFFIXES) or name.endswith("_repo")


def _py_files(repo_root: str):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _is_none_default(default_node) -> bool:
    """True only when the default is literally None (not a Call like Header()/
    Depends()/factory(), not a literal)."""
    return isinstance(default_node, ast.Constant) and default_node.value is None


def _optional_params(tree: ast.AST):
    """Yield (funcname, param, lineno) for OPTIONAL DEPENDENCY params -- name
    reads as a dependency and default is literally None. This is the precise
    'built but not wired' shape; framework-injected params (default = Header()/
    Depends()) and plain optional args are deliberately excluded."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        pos = a.args[len(a.args) - len(a.defaults):] if a.defaults else []
        pos_pairs = list(zip(pos, a.defaults))
        kw_pairs = list(zip(a.kwonlyargs, a.kw_defaults))
        for arg, default in pos_pairs + kw_pairs:
            if arg.arg.startswith("_"):
                continue
            if not _looks_like_dependency(arg.arg):
                continue
            if not _is_none_default(default):
                continue
            yield node.name, arg.arg, arg.lineno


def unwired_optional_params(repo_root: str, min_len: int = 6) -> list[dict]:
    """Findings for optional params defined but never passed as a keyword.

    Returns finding dicts (slug/title/area/severity/confidence/evidence/
    proposed_action) ready for record_gaps -- no model involved.
    """
    # 1. collect every optional param definition
    defs = []                      # (param, file, func, lineno)
    passed_kw = set()              # every `name=` used as a call keyword
    for path in _py_files(repo_root):
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        for func, param, lineno in _optional_params(tree):
            if len(param) >= min_len:
                defs.append((param, rel, func, lineno))
        # 2. every keyword actually passed at a call site
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg:
                        passed_kw.add(kw.arg)

    findings = []
    seen = set()
    for param, rel, func, lineno in defs:
        if param in passed_kw or param in seen:
            continue
        seen.add(param)
        findings.append({
            "slug": f"unwired-param-{param.replace('_','-')}",
            "title": f"optional dependency '{param}' (e.g. {func} in {rel}) "
                     f"defaults to None and is never passed as a keyword -- "
                     f"possibly built but never wired",
            "area": rel,
            # LOW + below the default 0.6 floor on purpose: this is a HEURISTIC
            # hint, not a confirmed defect. It only checks KEYWORD passing, so a
            # dependency injected POSITIONALLY looks unwired to it. So it stays
            # out of the escalation path -- surfaced on demand via `dd_ri.py
            # probe` for a human to scan, never an alarm.
            "severity": "low",
            "confidence": 0.55,
            "evidence": f"{rel}:{lineno}",
            "proposed_action": f"check whether '{param}' is wired (it may be "
                               f"passed positionally); if genuinely unused, "
                               f"remove it",
        })
    return findings


def run_all_probes(repo_root: str) -> list[dict]:
    """Every deterministic, whole-repo oracle's findings, combined.

    This is the single fan-out the loop (and `dd_ri probe`) call. Each oracle is
    isolated in try/except so one failing never suppresses the others, and every
    manifest/log-driven oracle no-ops when its config is absent -- so a project
    with no invariants.json / contracts.json / overrides log gets exactly the
    original probe behaviour and zero added noise. The diff-scoped oracles
    (consequence preview, change-scoped selection) are NOT here -- they need the
    set of changed files and are driven from the CLI.
    """
    from . import wiring, invariants, contracts, ovyero_calibration, attack_pattern_probe
    out = []
    for oracle in (
        lambda r: unwired_optional_params(r),      # LOW keyword-only hint (legacy)
        lambda r: wiring.unwired_capabilities(r),   # strong built-but-not-wired
        lambda r: invariants.check_invariants(r),   # passing-facade (opt-in manifest)
        lambda r: contracts.check_contracts(r),     # payload drift (opt-in manifest)
        lambda r: ovyero_calibration.calibrate(r),  # noisy gate rule (opt-in log)
        lambda r: attack_pattern_probe.run_attack_probes(r),  # known attack-pattern shapes (JS/TS)
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
