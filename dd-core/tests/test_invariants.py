"""Tests for the Invariant Manifests analyzer (dd_core.recursive_improvement.invariants)."""

from __future__ import annotations

import json
import os
import tempfile

from dd_core.recursive_improvement import invariants


def _mk_repo(files: dict, manifest: dict | None):
    root = tempfile.mkdtemp(prefix="inv-")
    for rel, src in files.items():
        ap = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write(src)
    if manifest is not None:
        with open(os.path.join(root, "invariants.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    return root


_ROUTES = {
    "src/api/routes/entities.py": (
        "def guard(): ...\n"
        "@router.get('/entities')\n"
        "def read_entities():\n"
        "    guard()\n"                     # guarded -- OK
        "    return []\n"
        "@router.get('/entities/{id}')\n"
        "def read_entity(id):\n"
        "    return {}\n"                    # MISSING guard -- must be flagged
        "@router.post('/internal')\n"
        "def health():\n"
        "    return 'ok'\n"                  # excluded by name
    ),
}

_MANIFEST = {
    "invariants": [{
        "name": "reads-guarded",
        "kind": "require_call_in_functions",
        "require_call": "guard",
        "files": ["src/api/routes/*.py"],
        "decorated_with": ["get", "post"],
        "exclude_functions": ["health"],
        "severity": "high",
    }]
}


def test_flags_only_the_unguarded_in_surface_function():
    root = _mk_repo(_ROUTES, _MANIFEST)
    found = invariants.check_invariants(root)
    slugs = {f["slug"] for f in found}
    # read_entity is unguarded -> flagged
    assert any("read_entity" in s for s in slugs)
    # read_entities is guarded, health is excluded -> NOT flagged
    assert not any("read_entities" in s for s in slugs)
    assert not any("health" in s for s in slugs)
    bad = next(f for f in found if "read_entity" in f["slug"])
    assert bad["severity"] == "high"


def test_no_manifest_means_no_findings():
    root = _mk_repo(_ROUTES, manifest=None)
    assert invariants.check_invariants(root) == []


def test_empty_surface_is_itself_flagged():
    root = _mk_repo(_ROUTES, {"invariants": [{
        "name": "stale", "kind": "require_call_in_functions",
        "require_call": "guard", "files": ["src/does/not/exist/*.py"],
    }]})
    found = invariants.check_invariants(root)
    assert any(f["slug"] == "invariant-stale-empty-surface" for f in found)


def test_fully_guarded_surface_is_clean():
    files = {"src/api/routes/x.py": (
        "def guard(): ...\n"
        "@router.get('/a')\n"
        "def a():\n    guard()\n    return 1\n"
        "@router.get('/b')\n"
        "def b():\n    guard()\n    return 2\n"
    )}
    root = _mk_repo(files, _MANIFEST)
    assert invariants.check_invariants(root) == []


def test_unreadable_manifest_is_reported_not_crashed():
    root = tempfile.mkdtemp(prefix="inv-")
    with open(os.path.join(root, "invariants.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json ")
    found = invariants.check_invariants(root)
    assert any(f["slug"] == "invariant-manifest-unreadable" for f in found)
