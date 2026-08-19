"""Consequence Preview -- retire 'blast-radius blindness'.

The failure class: a change is made judging only the file in front of you, blind
to everything downstream that depends on it, so a 'small' edit to a core module
silently breaks distant call sites and isn't future-proof. The antidote is to
make the blast radius VISIBLE before the edit: what transitively depends on the
thing you're about to change, and whether its covering tests are in the change.

This computes reverse reachability over the same static import graph the
change-scoped selector builds (one graph, two directions -- selection walks it
forward to find covering tests; this walks it backward to find dependents). For
a set of changed files it returns the impacted modules and a fan-in count, and
emits a finding when a high-fan-in module is changed but NONE of its covering
tests are in the change -- exactly the 'edited a load-bearing module without
touching a test that exercises it' move that ships regressions.

Deterministic, no model, no run history.
"""

from __future__ import annotations

import os

from dd_core.codefacts import supported_extensions
from dd_core.testkit.selection import (
    build_import_graph, module_for_path, tests_covering, _is_test_file,
)


def _is_source(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in supported_extensions()


def _reverse(edges: dict) -> dict:
    rev: dict = {m: set() for m in edges}
    for mod, deps in edges.items():
        for d in deps:
            rev.setdefault(d, set()).add(mod)
    return rev


def _dependents(mod: str, rev: dict) -> set:
    """All modules that transitively import `mod`."""
    out, stack = set(), [mod]
    while stack:
        cur = stack.pop()
        for parent in rev.get(cur, ()):
            if parent not in out:
                out.add(parent)
                stack.append(parent)
    return out


def blast_radius(repo_root: str, changed_paths: list[str]) -> dict:
    """Return {changed_module: {"dependents": [...], "fan_in": n}} for each
    changed .py file -- the modules that would be affected by changing it."""
    edges, _is_test = build_import_graph(repo_root)
    rev = _reverse(edges)
    out = {}
    for cp in changed_paths:
        if not _is_source(cp):
            continue
        ap = cp if os.path.isabs(cp) else os.path.join(repo_root, cp)
        mod = module_for_path(repo_root, ap)
        deps = sorted(_dependents(mod, rev))
        out[mod] = {"dependents": deps, "fan_in": len(deps)}
    return out


def preview(repo_root: str, changed_paths: list[str], *, high_fan_in: int = 5) -> list[dict]:
    """Findings for changes that touch a high-fan-in module without a covering
    test in the same change. Returns finding dicts for record_gaps."""
    radius = blast_radius(repo_root, changed_paths)
    covering = set(tests_covering(repo_root, changed_paths))
    changed_rel = {
        os.path.relpath(cp if os.path.isabs(cp) else os.path.join(repo_root, cp),
                        repo_root).replace("\\", "/")
        for cp in changed_paths if _is_source(cp)
    }
    changed_tests = {r for r in changed_rel if _is_test_file(os.path.basename(r))}

    findings = []
    for mod, info in radius.items():
        if info["fan_in"] < high_fan_in:
            continue
        # is any test that covers this change actually part of the change?
        if changed_tests & covering:
            continue
        findings.append({
            "slug": f"blast-radius-untested-{mod.replace('.', '-')}",
            "title": (f"change to high-fan-in module '{mod}' affects {info['fan_in']} "
                      f"downstream module(s) but no covering test is in the change -- "
                      f"blast-radius risk"),
            "area": mod.replace(".", "/") + ".py",
            "severity": "medium",
            "confidence": 0.7,
            "evidence": f"fan_in={info['fan_in']}; e.g. {info['dependents'][:5]}",
            "proposed_action": (
                f"before editing '{mod}', review its {info['fan_in']} dependents and "
                f"add/update a covering test in this change; run the change-scoped "
                f"subset (dd_core.testkit.selection.tests_covering) for fast feedback"),
        })
    return findings
