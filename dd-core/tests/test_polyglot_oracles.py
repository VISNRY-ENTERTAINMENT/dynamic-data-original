"""Cross-language proof: the SAME oracles, run against JavaScript, find the same
failure classes. Skipped unless the optional polyglot extra is installed."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

pytest.importorskip("tree_sitter_language_pack")

# Re-import codefacts AFTER the skip so the tree-sitter adapter is registered.
from dd_core import codefacts
from dd_core.codefacts import extract_facts, supported_extensions
from dd_core.recursive_improvement import invariants, contracts, consequence
from dd_core.testkit import selection


def _mk_repo(files: dict):
    root = tempfile.mkdtemp(prefix="poly-")
    for rel, src in files.items():
        ap = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write(src)
    return root


def test_js_extension_is_registered():
    assert ".js" in supported_extensions()
    assert ".ts" in supported_extensions()


def test_js_facts_extraction():
    src = (
        "import {c} from 'a/b';\n"
        "class S {\n"
        "  read(id) { guard(); return {id: 1, name: 2}; }\n"
        "}\n"
        "function use(o) { return o.truth_mode_state; }\n"
        "const y = obj['legacy_field'];\n"
    )
    root = _mk_repo({"m.js": src})
    f = extract_facts(os.path.join(root, "m.js"), root)
    assert f.language == "javascript"
    assert "a.b" in f.imports
    names = {fn.name for fn in f.functions}
    assert {"read", "use"} <= names
    read = next(fn for fn in f.functions if fn.name == "read")
    assert "guard" in read.calls
    assert frozenset({"id", "name"}) in read.returned_dict_keysets
    assert "truth_mode_state" in f.consumed_attributes
    assert ("obj", "legacy_field", 6) in f.subscript_reads


def test_invariant_guard_enforced_on_javascript():
    files = {
        "routes/api.js": (
            "function guard(){}\n"
            "function readEntities(){ guard(); return []; }\n"   # guarded
            "function readEntity(id){ return {}; }\n"            # MISSING guard
        ),
        "invariants.json": json.dumps({"invariants": [{
            "name": "reads-guarded", "kind": "require_call_in_functions",
            "require_call": "guard", "files": ["routes/*.js"],
            "exclude_functions": [],
        }]}),
    }
    root = _mk_repo(files)
    found = invariants.check_invariants(root)
    slugs = {f["slug"] for f in found}
    assert any("readEntity" in s for s in slugs)
    assert not any("readEntities" in s for s in slugs)


def test_contract_drift_on_javascript():
    files = {
        "serializers.js": "function toPayload(e){ return {id: e.id, name: e.name}; }\n",
        "contracts.json": json.dumps({"contracts": [{
            "name": "entity-payload",
            "required_keys": ["id", "name", "confidence"],
            "producers": ["*.js"], "consumers": [],
        }]}),
    }
    root = _mk_repo(files)
    found = contracts.check_contracts(root)
    assert any("missing" in f["slug"] and "confidence" in f["title"] for f in found)


def test_import_graph_and_blast_radius_on_javascript():
    files = {
        "core.js": "export const V = 1;\n",
        "a.js": "import {V} from 'core';\n",
        "b.js": "import {a} from 'a';\n",
        "c.js": "import {a} from 'a';\n",
        "d.js": "import {b} from 'b';\n",
        "e.js": "import {c} from 'c';\n",
    }
    root = _mk_repo(files)
    radius = consequence.blast_radius(root, ["core.js"])
    deps = set(radius["core"]["dependents"])
    assert {"a", "b", "c", "d", "e"} <= deps


def test_change_scoped_selection_on_javascript():
    files = {
        "core.js": "export const V = 1;\n",
        "api.js": "import {V} from 'core';\n",
        "api.test.js": "import {api} from 'api';\n",
        "unrelated.test.js": "import {z} from 'zzz';\n",
    }
    root = _mk_repo(files)
    covering = selection.tests_covering(root, ["core.js"])
    assert "api.test.js" in covering
    assert "unrelated.test.js" not in covering


# --- the full language matrix: extraction works in every registered language ---
# (filename, source) -- each defines a function `r` that imports "a/b"-ish and
# calls check(); we assert the import and the call are recovered.
_MATRIX = {
    "m.go":    'package m\nimport "a/b"\nfunc R(){ check() }\n',
    "m.rs":    'use a::b::c;\nfn r(){ check(); }\n',
    "m.rb":    'require "a/b"\ndef r\n check()\nend\n',
    "m.java":  'import a.b.C;\nclass S{ void r(){ check(); } }\n',
    "m.cs":    'using A.B;\nclass S{ void R(){ check(); } }\n',
    "m.kt":    'import a.b.c\nfun r(){ check() }\n',
    "m.c":     '#include "a/b.h"\nint r(){ check(); return 0; }\n',
    "m.cpp":   '#include "a/b.hpp"\nint r(){ check(); return 0; }\n',
    "m.php":   '<?php\nrequire "a/b";\nfunction r(){ check(); }\n',
    "m.swift": 'import a.b\nfunc r(){ check() }\n',
    "m.scala": 'import a.b.c\ndef r() = { check() }\n',
    "m.ts":    'import {c} from "a/b";\nfunction r(){ check(); }\n',
    "m.tsx":   'import {c} from "a/b";\nfunction r(){ check(); }\n',
    "m.js":    'import {c} from "a/b";\nfunction r(){ check(); }\n',
}


@pytest.mark.parametrize("fname", sorted(_MATRIX))
def test_extraction_across_every_language(fname):
    root = _mk_repo({fname: _MATRIX[fname]})
    f = extract_facts(os.path.join(root, fname), root)
    assert f is not None, f"no adapter for {fname}"
    assert f.imports, f"{f.language}: no import extracted"
    calls = set().union(*[fn.calls for fn in f.functions]) if f.functions else set()
    assert any(c.lower() == "check" for c in calls), f"{f.language}: call not found in {calls}"


def test_invariant_guard_enforced_on_go():
    files = {
        "svc/handlers.go": (
            "package svc\n"
            "func guard(){}\n"
            "func ReadOk(){ guard() }\n"       # guarded
            "func ReadBad(){ return }\n"       # MISSING guard
        ),
        "invariants.json": json.dumps({"invariants": [{
            "name": "reads-guarded", "kind": "require_call_in_functions",
            "require_call": "guard", "files": ["svc/*.go"],
            "exclude_functions": ["guard"],
        }]}),
    }
    root = _mk_repo(files)
    slugs = {f["slug"] for f in invariants.check_invariants(root)}
    assert any("ReadBad" in s for s in slugs)
    assert not any("ReadOk" in s for s in slugs)


def test_import_graph_selection_on_go():
    files = {
        "core.go": "package core\nfunc V() int { return 1 }\n",
        "api.go":  'package api\nimport "core"\n',
        "api_test.go": 'package api\nimport "api"\n',
        "other_test.go": 'package other\nimport "zzz"\n',
    }
    root = _mk_repo(files)
    covering = selection.tests_covering(root, ["core.go"])
    assert "api_test.go" in covering
    assert "other_test.go" not in covering
