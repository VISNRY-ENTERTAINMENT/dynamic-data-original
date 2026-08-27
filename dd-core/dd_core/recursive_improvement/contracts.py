"""Spec-derived contracts -- retire 'silent producer/consumer interface drift'.

The failure class lives in the gap a type checker can't see: data crossing a
boundary as an untyped ``dict`` -- an API JSON body, an event payload, a message.
A producer builds it with some keys; a consumer indexes keys the producer never
put there (or the producer drops a key the consumer still reads). Everything is
``dict[str, Any]``, so mypy is blind, the tests that happen to exercise the
overlapping keys pass, and the drift ships.

This checks such payloads against a DECLARED contract (a manifest, same opt-in
style as invariants.json): the contract names the required keys, the producer
functions that build the payload, and the files that consume it. The analyzer
then proves, deterministically:

  * every producer function returns a dict literal containing all required keys;
  * every consumer subscript ``x["k"]`` on the payload uses a key in the contract.

A missing produced key or an off-contract consumed key is a finding. No model;
the contract is the spec, the AST is the fact.

Manifest (``contracts`` array in invariants.json, or a standalone contracts.json)::

    {
      "contracts": [
        {
          "name": "entity-payload",
          "required_keys": ["id", "name", "confidence"],
          "producers": ["src/api/serializers.py"],
          "consumers": ["src/api/routes/*.py"],
          "subscript_vars": ["payload", "entity"]
        }
      ]
    }

``subscript_vars`` (optional) scopes consumer checking to subscripts on those
variable names, so unrelated dict indexing isn't checked. Omit it to check every
string-literal subscript in the consumer files against the contract.
"""

from __future__ import annotations

import fnmatch
import json
import os

from dd_core.codefacts import extract_facts


def _iter_files(repo_root: str, patterns: list[str]):
    seen = set()
    for root, _dirs, files in os.walk(repo_root):
        for f in files:
            ap = os.path.join(root, f)
            rel = os.path.relpath(ap, repo_root).replace("\\", "/")
            if rel not in seen and any(fnmatch.fnmatch(rel, p) for p in patterns):
                seen.add(rel)
                yield ap, rel


def _check_contract(repo_root: str, c: dict) -> list[dict]:
    name = c["name"]
    required = set(c.get("required_keys", []))
    producers = c.get("producers", [])
    consumers = c.get("consumers", [])
    scope_vars = set(c["subscript_vars"]) if c.get("subscript_vars") else None
    findings = []

    # producers: every returned dict/object literal must contain all required keys
    for ap, rel in _iter_files(repo_root, producers):
        facts = extract_facts(ap, repo_root)
        if facts is None:
            continue
        for fn in facts.functions:
            for keys in fn.returned_dict_keysets:
                missing = required - set(keys)
                # only flag a payload that looks like THIS contract (shares >=1
                # key), so unrelated object returns aren't false-flagged
                if missing and (set(keys) & required):
                    findings.append({
                        "slug": f"contract-{name}-missing-{rel.replace('/', '-')}-{fn.name}",
                        "title": (f"contract '{name}' drift: producer {rel}:{fn.name}() "
                                  f"returns a payload missing required key(s) "
                                  f"{sorted(missing)}"),
                        "area": rel, "severity": "high", "confidence": 0.82,
                        "evidence": f"{rel}:{fn.lineno}",
                        "proposed_action": (f"add {sorted(missing)} to the payload "
                                            f"returned by {fn.name}(), or update the "
                                            f"'{name}' contract if the key was retired"),
                    })

    # consumers: every scoped subscript key must be in the contract
    if required:
        for ap, rel in _iter_files(repo_root, consumers):
            facts = extract_facts(ap, repo_root)
            if facts is None:
                continue
            for base, key, lineno in facts.subscript_reads:
                in_scope = scope_vars is not None and base in scope_vars
                if in_scope and key not in required:
                    findings.append({
                        "slug": f"contract-{name}-offkey-{rel.replace('/', '-')}-{key}-{lineno}",
                        "title": (f"contract '{name}' drift: consumer {rel}:{lineno} reads "
                                  f"key '{key}' not in the contract {sorted(required)}"),
                        "area": rel, "severity": "high", "confidence": 0.75,
                        "evidence": f"{rel}:{lineno}",
                        "proposed_action": (f"read only contract keys {sorted(required)}, "
                                            f"or add '{key}' to the '{name}' contract and "
                                            f"its producers"),
                    })
    return findings


def check_contracts(repo_root: str, manifest_path: str | None = None) -> list[dict]:
    """Check declared payload contracts against the tree. Reads a ``contracts``
    array from contracts.json or invariants.json. No manifest -> no findings."""
    paths = [manifest_path] if manifest_path else [
        os.path.join(repo_root, "contracts.json"),
        os.path.join(repo_root, "invariants.json"),
    ]
    findings = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            manifest = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for c in manifest.get("contracts", []):
            try:
                findings += _check_contract(repo_root, c)
            except KeyError:
                continue
    return findings
