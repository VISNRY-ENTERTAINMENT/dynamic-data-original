# Dynamic Data

**Every fact is a claim — carrying its source, confidence, time, evidence, and
relationships — and truth is computed on read.** Claims accumulate; they are
never overwritten. This is a data *primitive*, not a database: it runs as a thin
layer over ordinary storage (SQLite here).

Built for the AI era: it gives an assistant a **verifiable memory** where every
belief knows where it came from, how sure it is, when, and what it superseded —
the opposite of a static, confident, sourceless string.

> Created by Ezra Lewellen / [VISNRY Entertainment](https://github.com/VISNRY-ENTERTAINMENT). Released under Apache 2.0 — see `LICENSE` and `NOTICE`.

---

## What's here

| Path | What it is |
|---|---|
| `dd-core/` | The implementation — a small, dependency-light Python library + CLI + MCP server. **v0.3.0.** |
| `RECURSIVE_IMPROVEMENT.md` | The self-review loop built on this substrate — an AI checks its own work in a repo, deterministically. Start here for that. |
| `OVYERO.md` | About Ovyero, VISNRY's AI governance tool, and how Dynamic Data fits into that workflow. |
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
python dd_cli.py --db project.ddb assert alice role engineer --source HR --confidence 1.0
python dd_cli.py --db project.ddb resolve alice role
python dd_cli.py --db project.ddb history alice role
```

Run the tests and the benchmark:

```bash
cd dd-core && python -m pytest tests/ -q          # 24 tests
cd ../benchmark && python grounding_benchmark.py  # static 1/7 vs dynamic 7/7
```

---

## The eight foundational atoms

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

To use it in a different project, see `dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

---

## Hivemind & tamper-evidence (v0.3)

Many agents can share one memory safely. Each claim is chained into an
append-only, **tamper-evident** ledger (`verify_chain` / `python dd_verify.py`),
optionally **Ed25519-signed** for authenticity, and stamped with an
**authenticated author** (`DD_AGENT`). Agents have **trust ceilings** that cap
how much their claims weigh, so a low-trust agent can't outshout a trusted one.
Run `benchmark/hivemind_demo.py` to see it in action.

---

## Recursive self-improvement (the reflex loop)

The first application built *on* the substrate: a self-healing gap ledger. After
a substantive commit, a model reviews the diff (**Tier 1**) and — every few
major commits — audits the whole codebase against your roadmap + north star
(**Tier 2**). Findings become append-only claims; a **deterministic** gate
(no model in the decision path) escalates to you at a threshold.

```bash
# add a self-healing gap ledger to any repo in one command:
python dd-core/dd_reflex.py init --repo-root . --anchors ROADMAP.md VISION.md
python dd-core/dd_reflex.py doctor --config reflex.config.json   # prove it fires
```

Repo-agnostic and AI-agnostic. See `RECURSIVE_IMPROVEMENT.md` and
`dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

---

## Built by VISNRY

Dynamic Data is open source infrastructure from [VISNRY Entertainment](https://github.com/VISNRY-ENTERTAINMENT).

If you're using an AI coding agent, check out **[Ovyero](https://ovyero.visnryentertainment.com/)** — VISNRY's governance layer that reviews every commit your AI writes before it lands. Dynamic Data and Ovyero work well together: Ovyero governs what ships, Dynamic Data gives your AI a memory of what was decided and why. See `OVYERO.md`.

---

## Design rules

- **Never build a database.** Dynamic Data is a model over boring storage. The invention is the read semantics, not the disk layout.
- **Get the atoms right; capabilities follow.** Time-travel, conflict, history, and provenance are *derived* from identity, context, derivation, and credence.
- **Keep the core minimal and reflexive.** Future dimensions arrive as claims.
