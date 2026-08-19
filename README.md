# Dynamic Data

**Every fact is a claim — carrying its source, confidence, time, evidence, and
relationships — and truth is computed on read.** Claims accumulate; they are
never overwritten. This is a data *primitive*, not a database: it runs as a thin
layer over ordinary storage (SQLite here).

Built for the AI era: it gives an assistant a **verifiable memory** where every
belief knows where it came from, how sure it is, when, and what it superseded —
the opposite of a static, confident, sourceless string.

> Author: Ezra Lewellen (VISNRY). Released under Apache 2.0 — see `LICENSE` and
> `NOTICE`.

---

## What's here

| Path | What it is |
|---|---|
| `00_DYNAMIC_DATA_CONCEPT.md` | The idea: claims, the two-class model, "static is dynamic-data collapsed", the AI-memory framing. Start here. |
| `01_HOW_TO_MAKE_IT_REAL.md` | How to test whether it's better, use it personally, and the training angle. |
| `02_FOUNDATIONAL_ATOMS.md` | The design spec: the eight atoms vs derived capabilities, reflexivity, and the map to ExampleApp's Claim. |
| `03_HIVEMIND_AND_SECURITY.md` | Multi-agent trust: authenticated authorship, trust ceilings, hash-chain tamper-evidence, Ed25519 signatures, and the security roadmap. |
| `04_RECURSIVE_IMPROVEMENT.md` | The first application built ON the substrate: a self-healing gap ledger that records what a codebase's ships leave unfinished as claims, with a deterministic (no-model) escalation gate. The reflexivity test turned into a running loop. |
| `dd-core/` | The implementation — a small, dependency-light Python library + CLI + MCP server. **v0.3.0.** |
| `RECURSIVE_IMPROVEMENT.md` | **Recursive Improvement** front door: the self-review loop built ON this substrate -- an AI checks its own work in a repo, deterministically. Start here for that. |
| `dd-core/dd_core/recursive_improvement/` | The portable loop (Tier 1 diff review + Tier 2 whole-codebase audit; evidence validation, dedup, triage, auto-close, probes, learning, metrics). CLI: `dd-core/dd_ri.py`; hook: `dd-core/dd_reflex_hook.py`. (`dd_core.reflex` is a compat alias.) |
| `benchmark/` | The grounding benchmark (static 1/7 vs dynamic 7/7) and the hivemind demo. |

---

## Quickstart (60 seconds, zero dependencies)

```python
import sys; sys.path.insert(0, "dd-core")
from dd_core import DynamicDataStore, CredenceType

ddb = DynamicDataStore("project.ddb")            # one SQLite file, or ":memory:"

# Record facts as claims (they accumulate; they never overwrite)
ddb.assert_claim("myproject", "prod_branch", "main",
                 source="ezra", confidence=1.0, evidence="main = production")

# Truth is computed on read
print(ddb.resolve("myproject", "prod_branch").chosen.value)     # -> "main"
```

Or the CLI:

```bash
cd dd-core
python dd_cli.py --db project.ddb assert EZE role manager --source HR --confidence 1.0
python dd_cli.py --db project.ddb resolve EZE role
python dd_cli.py --db project.ddb history EZE role
```

Run the tests and the benchmark:

```bash
cd dd-core && python -m pytest tests/ -q          # 24 tests
cd ../benchmark && python grounding_benchmark.py  # static 1/7 vs dynamic 7/7
```

---

## The eight foundational atoms (v0.2)

A claim carries everything needed to be self-describing and reasoned-over:

1. **Proposition** — subject · predicate · value/obj
2. **Identity** — the subject is a *resolved* entity (`same_as` + `canonical`)
3. **Source** — who/what asserts it
4. **Derivation** — what it was inferred from (enables belief revision)
5. **Credence (typed)** — `point` / `interval` / `unknown` (not just a scalar)
6. **Context** — conditions it holds under; time-travel is one axis
7. **Record-time** — when it entered the system (bitemporal)
8. **Lifecycle** — active / superseded / retracted

Plus **reflexivity**: an open `dims` bag and `describe()` meta-claims, so
dimensions nobody imagined are added as data, not as breaking schema changes.

See `02_FOUNDATIONAL_ATOMS.md` for the full spec.

---

## Use it as an AI's memory (MCP)

```bash
cd dd-core
pip install "mcp[cli]"
claude mcp add dynamic-data -e DD_DB="/abs/path/to/project.ddb" -- python "/abs/path/to/dd-core/dd_mcp_server.py"
```

The assistant then has 18 tools: `assert_claim`, `resolve`, `history`,
`list_conflicts`, `search`, `relationships`, `subjects`, `stats`, `same_as`,
`derive`, `provenance`, `retract`, `assert_unknown`, `describe_predicate`,
`whoami`, `register_agent`, `set_trust`, `verify_chain`.

To use it in a *different* project, see `dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

## Hivemind & tamper-evidence (v0.3)

Many agents can share one memory safely. Each claim is chained into an
append-only, **tamper-evident** ledger (`verify_chain` / `python dd_verify.py`),
optionally **Ed25519-signed** for authenticity, and stamped with an
**authenticated author** (`DD_AGENT`). Agents have **trust ceilings** that cap
how much their claims weigh, so a low-trust agent can't outshout a trusted one.
See `03_HIVEMIND_AND_SECURITY.md` and run `benchmark/hivemind_demo.py`.

---

## Recursive self-improvement (the reflex loop)

The first application built *on* the substrate: a self-healing gap ledger. After
a substantive commit, a model reviews the diff (**Tier 1**) and — every few
major commits — audits the whole codebase against your roadmap + north star
(**Tier 2**). Findings become append-only `arch.gap:` / `arch.audit:` claims; a
**deterministic** gate (no model in the decision path) escalates to you at a
threshold. Nothing edits your roadmap — you do, after being asked. It's the
reflexivity test turned into a running loop: the store observes the repo that
builds the store, and fixes supersede their gaps so the ledger converges.

```bash
# add a self-healing gap ledger to any repo in one command:
python dd-core/dd_reflex.py init --repo-root . --anchors ROADMAP.md VISION.md
python dd-core/dd_reflex.py doctor --config reflex.config.json   # prove it fires
# then customize reflex/reviewer_charter.md + reflex/auditor_charter.md
```

Repo-agnostic (works on any layout — it does not assume `src/`) and AI-agnostic
(a `claude` preset ships; any CLI works via `provider: generic` + `cmd_template`,
prompt over stdin). Tier 1 runs on every commit, so it defaults to the **cheap**
model tier. Run `doctor` after setup: a misconfigured loop is otherwise silent.

Lives in `dd_core/reflex/` (config-driven, project-agnostic) with entry points
`dd_reflex.py` (CLI) and `dd_reflex_hook.py` (post-commit). Design + doctrine in
`04_RECURSIVE_IMPROVEMENT.md`; per-project setup in
`dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

---

## Design rules

- **Never build a database.** Dynamic Data is a model over boring storage. The
  invention is the read semantics, not the disk layout.
- **Get the atoms right; capabilities follow.** Time-travel, conflict, history,
  and provenance are *derived* from identity, context, derivation, and credence.
- **Keep the core minimal and reflexive.** Future dimensions arrive as claims.
