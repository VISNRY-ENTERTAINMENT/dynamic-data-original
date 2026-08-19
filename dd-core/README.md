# dd-core — Dynamic Data claim store  (v0.3.0)

A small, dependency-light implementation of the **Dynamic Data** primitive (see
`../00_DYNAMIC_DATA_CONCEPT.md` and `../02_FOUNDATIONAL_ATOMS.md`): every fact is
a **claim** carrying the eight foundational atoms, claims **accumulate** (never
overwrite), and truth is **computed on read**.

Its purpose: **project memory for AI.** The assistant records what it learns as
sourced, confidence-weighted, time-stamped claims and later asks *"what do we
currently believe about X, and what changed?"* — instead of re-deriving project
facts every session.

> **Dynamic Data vs a governance/policy gate** — different layers.
> Governance governs *actions* (is this change safe/allowed?) at the gate.
> Dynamic Data stores *beliefs* (what's true, who says so, how sure, since when).
> Governance is the referee; Dynamic Data is the memory.

## The eight atoms (what a claim carries)

1. **Proposition** — subject · predicate · value/obj
2. **Identity** — subject is a resolved entity: `same_as()` + `canonical()`
3. **Source** — who/what asserts it
4. **Derivation** — `derived_from` edges → `provenance()` and belief revision
5. **Credence (typed)** — `point` / `interval` / `unknown` (unknown ≠ 0.5)
6. **Context** — open bag; time-travel (`as_of`) is one axis, plus world/timeline/framework
7. **Record-time** — bitemporal second axis
8. **Lifecycle** — active / superseded / retracted

Plus **reflexivity**: open `dims` bag + `describe()` meta-claims — new dimensions
arrive as data, not as breaking schema changes.

## Layout

```
dd-core/
  dd_core/            # the library (pure stdlib — signing needs cryptography)
    models.py         #   Claim, Profile, CredenceType, Resolution
    store.py          #   DynamicDataStore over SQLite (+ auto-migration + ledger)
    signing.py        #   optional Ed25519 authenticity layer
  dd_cli.py           # command-line access
  dd_mcp_server.py    # MCP server: 18 tools, exposes the store to an AI
  dd_verify.py        # independent tamper-evidence verifier
  seed_exampleapp.py   # example: load a project's facts
  tests/              # 33 tests (atoms + hivemind/trust/tamper-evidence)
  pyproject.toml      # optional: pip install -e .
```

## Hivemind, trust & tamper-evidence (v0.3)

Many agents can share one memory safely — see `../03_HIVEMIND_AND_SECURITY.md`.

```python
from dd_core import DynamicDataStore, signing
ddb = DynamicDataStore("hive.ddb")

key = signing.AgentKey.generate()                 # an agent's Ed25519 identity
ddb.register_agent("scout", trust_ceiling=0.3, public_key=key.public_hex)
ddb.assert_claim("exampleapp", "head", "abc", source="scout",
                 confidence=1.0, signer=key)        # signed + trust-capped
ddb.verify_chain(check_signatures=True)             # integrity + authenticity
```

```bash
python dd_verify.py --db hive.ddb --signatures      # verify it yourself
```

## Library

```python
from dd_core import DynamicDataStore, CredenceType, Profile

ddb = DynamicDataStore("project.ddb")

# proposition + source + confidence
ddb.assert_claim("EZE", "role", "manager", source="HR", confidence=0.90)

# identity: two labels, one entity — claims fuse
ddb.same_as("eze@email.com", "EZE", source="HR")

# derivation + belief revision
p = ddb.assert_claim("dragon", "class", "reptile", source="designer", confidence=1.0)
i = ddb.derive("dragon", "weakness", "ice", derived_from=(p.claim_id,), confidence=0.7)
ddb.retract(p.claim_id, cascade=True)        # pull premise -> conclusion falls

# typed credence: explicit ignorance, not 0.5
ddb.assert_claim("x", "p", None, source="a", credence_type=CredenceType.UNKNOWN)

# context: time-travel + scoping
ddb.resolve("bob", "role", as_of="2002-01-01T00:00:00+00:00")
ddb.resolve("leshy", "weakness", context={"world": "canon"})

# reflexivity: define a dimension from inside the system
ddb.describe("weakness", "unit", "damage_multiplier", source="designer")
```

Two resolution profiles: `Profile.BELIEVED` (default — highest credence wins,
conflict surfaced) and `Profile.DETERMINED` (single authority, confidence pinned
to 1.0, disagreement raises `DeterminedConflictError`).

## CLI

```bash
python dd_cli.py --db project.ddb assert EZE role manager --source HR --confidence 1.0
python dd_cli.py --db project.ddb resolve EZE role
python dd_cli.py --db project.ddb history EZE role
python dd_cli.py --db project.ddb conflicts
```

## MCP server (use it as an AI's memory)

```bash
pip install "mcp[cli]"
claude mcp add dynamic-data -e DD_DB="/abs/project.ddb" -- python "/abs/dd-core/dd_mcp_server.py"
```

18 tools: `assert_claim`, `resolve`, `history`, `list_conflicts`, `search`,
`relationships`, `subjects`, `stats`, `same_as`, `derive`, `provenance`,
`retract`, `assert_unknown`, `describe_predicate`, `whoami`, `register_agent`,
`set_trust`, `verify_chain`.

## Install (optional) & test

```bash
pip install -e .          # optional — the library also works via sys.path
python -m pytest tests/ -q # 24 tests
```

## Compatibility

Opening a `.ddb` from an older version auto-migrates it (adds new columns) with
no data loss. Storage is deliberately boring (SQLite); the invention is the read
semantics — never build a database from scratch.
