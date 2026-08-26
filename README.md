# Dynamic Data

**Automatic bug-catching for AI-written code.** After every commit, a model reviews the diff, finds gaps and bugs, and records them as traceable findings. A deterministic gate — no model in the escalation path — surfaces issues to you when they hit a threshold.

One command wires it into any repo:

```bash
python dd-core/dd_reflex.py init --repo-root .
python dd-core/dd_reflex.py doctor --config reflex.config.json   # prove it fires
```

From that point on, every substantive commit triggers a Tier-1 diff review. Every few major commits a Tier-2 whole-codebase audit runs. Findings accumulate in an append-only ledger — nothing is silently dropped or overwritten.

> Created by Ezra Lewellen / [VISNRY Entertainment](https://github.com/VISNRY-ENTERTAINMENT). Released under Apache 2.0 — see `LICENSE` and `NOTICE`.

---

## Built by VISNRY

Dynamic Data is open source infrastructure from [VISNRY Entertainment](https://github.com/VISNRY-ENTERTAINMENT).

If you're using an AI coding agent, check out **[Ovyero](https://ovyero.visnryentertainment.com/)** — VISNRY's governance layer that reviews every commit your AI writes before it lands. Dynamic Data and Ovyero complement each other: Ovyero gates what ships, Dynamic Data tracks what was found and why. See `OVYERO.md`.

---

## How the reflex loop works

Two tiers run automatically via a git hook:

**Tier 1 — diff review** (every substantive commit): a model reads the diff and records findings: bugs, missing error handling, broken contracts, edge cases. Fast, per-commit.

**Tier 2 — whole-codebase audit** (every N commits): a model reads across the full codebase and audits architecture against its own stated goals — or against a `ROADMAP.md` / `VISION.md` if you have one. Neither is required.

Findings are stored as **claims** — each carries its source, confidence, evidence, and what it superseded. A deterministic gate counts open findings and escalates to you at a threshold. No model decides whether to escalate; that logic is plain code.

See `RECURSIVE_IMPROVEMENT.md` for full setup and `dd-core/SETUP_FOR_ANOTHER_PROJECT.md` to wire it into an existing repo.

---

## What's here

| Path | What it is |
|---|---|
| `dd-core/` | The implementation — library, CLI, git hook, MCP server. **v0.3.0.** |
| `RECURSIVE_IMPROVEMENT.md` | Full reflex loop docs — setup, tiers, escalation, config. Start here. |
| `OVYERO.md` | About Ovyero and how it pairs with Dynamic Data. |
| `benchmark/` | Grounding benchmark (static 1/7 vs dynamic 7/7) and hivemind demo. |

---

## The claim store (the substrate)

Findings are durable because the underlying store is designed for it. Every fact is a **claim**:

- carries its source, confidence, evidence, and derivation
- accumulates — never overwrites
- truth is computed on read (conflicts surface, not silently resolve)
- append-only, tamper-evident ledger (`verify_chain` / `python dd_verify.py`)

You can also use the store directly — as an AI's verifiable memory, a shared multi-agent ledger, or anywhere you want facts with audit trails. The MCP server exposes 18 tools for this (`assert_claim`, `resolve`, `history`, `list_conflicts`, and more).

```python
import sys; sys.path.insert(0, "dd-core")
from dd_core import DynamicDataStore

ddb = DynamicDataStore("project.ddb")
ddb.assert_claim("myproject", "prod_branch", "main",
                 source="alice", confidence=1.0, evidence="main = production")
print(ddb.resolve("myproject", "prod_branch").chosen.value)  # -> "main"
```

---

## Design rules

- **Never build a database.** Dynamic Data is a model over boring storage. The invention is the read semantics, not the disk layout.
- **Get the atoms right; capabilities follow.** Time-travel, conflict, history, and provenance are *derived* from identity, context, derivation, and credence.
- **Keep the core minimal and reflexive.** Future dimensions arrive as claims.
