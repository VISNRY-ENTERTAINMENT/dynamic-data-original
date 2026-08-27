# Recursive Improvement

*A system that gets an AI to check its own work — not the AI itself, the **work
it produces in a repo** — catch the hidden bugs it can't see, and make sure its
improvements actually land. Built on [Dynamic Data](README.md); deterministic
everywhere except the one step where a model reads code.*

---

## The idea, in one paragraph

An AI writing code is blind to its own mistakes for the same reason a writer
can't proofread their own draft: it just made the decision, so it can't see the
gap. **Recursive Improvement is a second pass that isn't the doer.** After a
commit ships, a model reviews the diff; every few major commits, it audits the
whole codebase against the project's roadmap and north star. What it finds
becomes append-only *claims* in a Dynamic Data ledger. Then **deterministic**
machinery — no model — dedups, validates the evidence, triages by severity,
auto-closes on fix, and reports whether the loop is actually helping. The AI
only ever *proposes*; it never decides what gets recorded, hidden, escalated, or
closed. A human keeps authority; the substrate keeps the record.

This is not a linter and not "AI reviews your PR." It is a **self-healing
backlog that lives in the same claim store as the project's memory**, and it is
reflexive: the store observes the repository that builds the store, and fixes
supersede the gaps that motivated them, so the ledger converges.

## Why it works (evidence, not theory)

On its first real deployment it caught **two data-loss/correctness bugs a full
test suite missed** and **four "built-but-not-wired" mistakes the working AI had
just made** — the exact class of error the doer is blind to. Measured on its own
history: **precision ~0.84** ("HELPING — high signal"), computed deterministically
from the ledger.

## Two tiers

- **Tier 1 — per commit.** Reviews the **diff**. Cheap model, runs on every
  substantive commit. "Did this change leave a hole?"
- **Tier 2 — every *k* major commits.** Audits the **whole codebase against its
  destination** (roadmap + north star). Deeper model, rare. "Is the whole thing
  still on course?"

## What makes it *help*, not hinder

Everything below is **deterministic — the model is only in discovery**:

| Mechanism | What it does |
|---|---|
| **Evidence validation** | A finding whose cited `file:line` doesn't exist is likely hallucinated → down-ranked below the action floor. Kills fabricated findings for free. |
| **Semantic dedup** | A re-worded finding under a new slug collapses onto the original via `same_as` (token+area+file match). Every collapse is logged — never silent. |
| **Severity triage** | `high`/`critical` surface now; `medium`/`low` sit as a quiet backlog that doesn't nag until it piles up. You aren't interrupted for small stuff. |
| **Auto-close** | A commit saying `Closes <subject>` marks that finding fixed, cited to the SHA. The backlog self-drains as fixes ship. |
| **Structural probes** | Deterministic detectors (no model) for facts like "this optional param is built but never wired." A fact is not a judgment call. |
| **Learning** | The auditor is primed with anti-patterns this loop has *actually caught + fixed*, and cautioned about *findings it got wrong before*. Precision compounds. |
| **Metrics** | Precision, false-positive rate, mean-time-to-close, backlog — all read from the ledger. Tells you if the loop is worth running. |

## Setup (any repo, any AI)

```bash
python dd-core/dd_ri.py init --repo-root . --anchors ROADMAP.md VISION.md
python dd-core/dd_ri.py doctor --config reflex.config.json   # PROVE it will fire
# then customize reflex/reviewer_charter.md + reflex/auditor_charter.md
```

- **Repo-agnostic** — no assumption about `src/`; `doctor` makes a mismatch loud
  (a silent no-op is the worst failure).
- **AI-agnostic** — a `claude` preset ships; any CLI works via `provider:
  generic` + `cmd_template` (prompt over stdin). Tier 1 defaults to the **cheap**
  model tier since it runs on every commit.

## Commands

```bash
dd_ri.py run       # run the loop once (both tiers)
dd_ri.py audit     # force a Tier-2 whole-codebase audit
dd_ri.py backlog   # triaged view: act-now vs parked
dd_ri.py status    # every finding + state
dd_ri.py metrics   # is the loop worth running? precision / FP rate / MTTC
dd_ri.py probe     # deterministic structural probes (no model)
dd_ri.py autoclose # mark findings a commit says it closed (auto on every commit)
dd_ri.py doctor    # prove the config would actually fire
```

## Where it lives

The implementation is `dd-core/dd_core/recursive_improvement/` (config, gate,
record, runner, autoclose, evidence, probes, learn, metrics + charter
templates). CLI: `dd-core/dd_ri.py`; post-commit hook: `dd-core/dd_reflex_hook.py`.
It is a **consumer of Dynamic Data** — findings are just claims — so it inherits
provenance, time-travel, conflict-surfacing, and tamper-evidence for free.
Architecture + the inversion-derived design rules: `04_RECURSIVE_IMPROVEMENT.md`.
Per-project setup + all tunables: `dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

> **Naming.** Dynamic Data is the *substrate* (claim-native memory). Recursive
> Improvement is an *application built on it*. The dependency only flows one way
> (RI needs DD; DD never needs RI) — which is exactly why they have separate
> names. `dd_core.reflex` remains as a compatibility alias for
> `dd_core.recursive_improvement`.
