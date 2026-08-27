"""Tests for Consequence Preview and Spec-derived contracts (Phase F)."""

from __future__ import annotations

import json
import os
import tempfile

from dd_core.recursive_improvement import consequence, contracts


def _mk_repo(files: dict):
    root = tempfile.mkdtemp(prefix="phasef-")
    for rel, src in files.items():
        ap = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write(src)
    return root


# --- Consequence Preview -------------------------------------------------------
_GRAPH = {
    "pkg/__init__.py": "",
    "pkg/core.py": "VALUE = 1\n",
    "pkg/a.py": "from pkg import core\n",
    "pkg/b.py": "from pkg import a\n",
    "pkg/c.py": "from pkg import a\n",
    "pkg/d.py": "from pkg import b\n",
    "pkg/e.py": "from pkg import c\n",
    "tests/test_core.py": "from pkg import core\n",
}


def test_blast_radius_counts_transitive_dependents():
    root = _mk_repo(_GRAPH)
    radius = consequence.blast_radius(root, ["pkg/core.py"])
    deps = set(radius["pkg.core"]["dependents"])
    # a,b,c,d,e and the test all transitively import core
    assert {"pkg.a", "pkg.b", "pkg.c", "pkg.d", "pkg.e", "tests.test_core"} <= deps


def test_high_fan_in_change_without_test_is_flagged():
    root = _mk_repo(_GRAPH)
    # change core.py alone (no test file in the change) -> flagged
    found = consequence.preview(root, ["pkg/core.py"], high_fan_in=5)
    assert any(f["slug"] == "blast-radius-untested-pkg-core" for f in found)


def test_change_including_covering_test_is_not_flagged():
    root = _mk_repo(_GRAPH)
    found = consequence.preview(
        root, ["pkg/core.py", "tests/test_core.py"], high_fan_in=5)
    assert found == []


def test_low_fan_in_change_is_not_flagged():
    root = _mk_repo(_GRAPH)
    found = consequence.preview(root, ["pkg/e.py"], high_fan_in=5)
    assert found == []


# --- Spec-derived contracts ----------------------------------------------------
def test_producer_missing_required_key_is_flagged():
    files = {
        "src/serializers.py": (
            "def to_payload(e):\n"
            "    return {'id': e.id, 'name': e.name}\n"   # missing 'confidence'
        ),
        "contracts.json": json.dumps({"contracts": [{
            "name": "entity-payload",
            "required_keys": ["id", "name", "confidence"],
            "producers": ["src/*.py"],
            "consumers": [],
        }]}),
    }
    root = _mk_repo(files)
    found = contracts.check_contracts(root)
    assert any("missing" in f["slug"] and "confidence" in f["title"] for f in found)


def test_complete_producer_is_clean():
    files = {
        "src/serializers.py": (
            "def to_payload(e):\n"
            "    return {'id': e.id, 'name': e.name, 'confidence': e.c}\n"
        ),
        "contracts.json": json.dumps({"contracts": [{
            "name": "entity-payload",
            "required_keys": ["id", "name", "confidence"],
            "producers": ["src/*.py"], "consumers": [],
        }]}),
    }
    root = _mk_repo(files)
    assert contracts.check_contracts(root) == []


def test_consumer_off_contract_key_is_flagged():
    files = {
        "src/route.py": (
            "def handler(payload):\n"
            "    return payload['id'] + payload['legacy_field']\n"  # off-contract
        ),
        "contracts.json": json.dumps({"contracts": [{
            "name": "entity-payload",
            "required_keys": ["id", "name"],
            "producers": [],
            "consumers": ["src/*.py"],
            "subscript_vars": ["payload"],
        }]}),
    }
    root = _mk_repo(files)
    found = contracts.check_contracts(root)
    assert any("offkey" in f["slug"] and "legacy_field" in f["title"] for f in found)


def test_no_manifest_means_no_findings():
    root = _mk_repo({"src/x.py": "def f(): return {'a': 1}\n"})
    assert contracts.check_contracts(root) == []
