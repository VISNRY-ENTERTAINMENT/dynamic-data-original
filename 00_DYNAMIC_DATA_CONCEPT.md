# Dynamic Data — The Concept

*Author: Ezra Lewellen (VISNRY). Captured 2026-07-14.*

## One-line definition

**Dynamic Data** makes the atomic unit of data a **claim**, not a value. Every
fact carries `{value, source, confidence, timestamp, evidence, relationships}`.
Facts never overwrite — they **accumulate** with time. Conflicts don't silently
last-write-win — the higher-confidence claim wins and the conflict **surfaces**.
Reading a fact isn't a lookup; it's a **traversal + confidence resolution** that
returns a "chosen claim."

```
Static record:                 Dynamic Data claim:
bob.role = manager             {
                                 subject:    "bob",
                                 predicate:  "role",
                                 object:     "manager",
                                 source:     "HR",
                                 confidence: 1.0,
                                 timestamp:  "2005-06-01",
                                 evidence:   "HR system, promotion record",
                                 supersedes: <2000 employee claim>
                               }
```

## Why the static record is one primitive behind

The static record throws away everything except the value: who said it, when,
how sure, what it depends on, what it used to be. The proof this is the *wrong*
default is that **every serious system reinvents the missing pieces, badly and
separately**: `updated_at` columns, soft-deletes, audit logs, data-lineage
tools, event sourcing, ML confidence scores, master-data "golden record"
reconciliation. Those are all partial re-implementations of what Dynamic Data
carries natively. When an industry keeps re-growing the same missing limbs, the
skeleton is wrong.

Humans reason in Dynamic-Data terms — we hold beliefs with sources, confidence,
and revision. The static record was a 1970s optimization for scarce hardware and
transactional business apps. As computing shifts from **recording transactions**
to **representing and reasoning about knowledge** (the AI era), the primitive
that matches how reasoning works becomes more valuable.

## The key insight: static data is Dynamic Data *collapsed*

You do **not** need two systems. A "static" value is a **projection** over
Dynamic Data — the "chosen claim." And the world already proves this:

- **Banking** — a balance is not a stored number. It's a fold over an
  append-only, immutable, fully-sourced, timestamped ledger (double-entry
  bookkeeping). Every entry has a source, time, and authority, and is never
  overwritten. The balance is the chosen-claim projection. **Banking is Dynamic
  Data wearing a static mask — and has been for 500 years.**
- **Git** — immutable commits → working tree.
- **CPU memory** — a "static" cell is a cache over a stream of writes.

Static data was always a *view*. The static record just discarded the ledger
underneath and kept the projection. Dynamic Data keeps both.

## Two classes, one primitive (tunable profiles)

One primitive, with a few tunable axes. A domain declares a **profile**:

| Axis | Determined profile (banks, control) | Believed profile (AI, knowledge) |
|---|---|---|
| **Authority** | single authoritative source | many competing sources |
| **Certainty** | pinned to 1.0 (no probabilities) | confidence ∈ [0,1] |
| **Conflict policy** | hard error, reject/rollback | surfaced, resolved by weight |
| **Resolution** | trivial (one current authority) | weighted traversal |
| **History** | accumulate (immutable ledger) | accumulate (immutable ledger) |

- **Determined claims** (bank profile): confidence locked to certain, one
  authority may write, conflicting claims = a fault that rolls back. Gets all the
  audit / time-travel / provenance benefits **with zero probabilistic overhead in
  the read path** (resolution is trivial when there's one authority).
- **Believed claims** (AI profile): many sources, real confidence, conflict
  surfaced, weighted resolution.

"Static" is simply the profile where certainty is pinned and conflict is fatal.
Same primitive; the domain turns on only the axes it needs. (ExampleProject already
has these axes as `reality_context`, `immutability_class`, `confidence_category`.)

## The real invention

Not "a better database." The deepest version is:

> **Make *confidence* and *change* first-class citizens of computation — not
> just storage.**

Programs today cannot natively say "I'm 70% sure" or "this was true until
Tuesday." That information is flattened into a boolean or bare value the instant
it enters a system, and every application re-invents ways to smuggle it back.
Dynamic Data proposes uncertainty and provenance be part of the *type* of every
fact — the way nullability or async became things the language/runtime handles
instead of things each programmer hacks around. Banks, AI, science, medicine,
and games become the *same* system running different profiles.

## The origin and the killer app: memory for AI

Dynamic Data was designed for AI. An LLM's output is the **purest possible
static-confident-sourceless data**: a string, stated with total confidence, no
source, no timestamp, no record of what it superseded, no way to check it.
**Hallucination isn't a bug on top of that — it's the shape of that data.**
Dynamic Data is exactly the hole that fits.

The framing that makes it click for people — not "a new database" but:

> **Verifiable memory for AI.** Every fact a model knows is a claim carrying
> where it came from, how sure it is, when, and what it replaced. The model
> emits "I believe X (source, confidence, as-of, superseding Y)" — not a bare
> confident string.

This shifts the mental model from **"data is what's true"** to **"data is what's
been claimed, and truth is computed on read."** And it gives an AI **belief
revision**: when a higher-confidence source arrives, the old claim isn't deleted,
it's re-weighted, and the change itself is a learnable record. That's continual
learning with an audit trail — something today's models fundamentally lack.

## Honest caveats (build the right thing)

1. **Overhead must be optional.** A determined claim must *compile away* the
   probabilistic machinery — bank reads must be as cheap as a row lookup, or the
   idea loses.
2. **Confidence is easy to fake and dangerous to trust.** A `.72` is meaningful
   only if the *method* that produced it is calibrated. Confidence must be
   *derived from source authority*, not asserted. Garbage confidence is worse
   than none because it looks rigorous.
3. **"Truth computed on read" fights caching.** You need materialized
   projections (the chosen claim) that are cheap to read and correctly
   invalidated — which is exactly what a bank balance is. Lean into that pattern.

## Verdict

- "Replace all databases with Dynamic Data" — overshoots. The static record
  stays correct for transactional, single-authoritative-value data.
- "Dynamic Data is the right primitive for knowledge / truth / integration /
  uncertainty systems, it is directionally ahead of mainstream data systems, and
  its unified form (datom + source-authority confidence + surfaced conflict +
  relationships + time) is genuinely novel" — **that thesis holds.**

The sharpest version: **as computing shifts from recording transactions to
representing knowledge, the default primitive should shift with it — and
static-record databases are stuck one primitive behind.**

## Prior art to stand on (not to fear)

The pieces exist; the *unification* is the contribution. Knowing these makes the
idea stronger:

- **Datomic** (Rich Hickey) — immutable, time-indexed datoms `(entity,
  attribute, value, transaction)`; accumulate-only; query "as of" any time. The
  closest shipped realization. Has time, lacks confidence.
- **Bitemporal / temporal databases** (Snodgrass) — "truth changes over time."
- **RDF / triple stores + reification** — the relationship graph.
- **Probabilistic databases** (Dalvi/Suciu) — native confidence.
- **W3C PROV** — provenance as a first-class model.
- **Truth Maintenance Systems** (de Kleer, 1980s AI) — belief revision, conflict.

None unified all axes with **confidence-weighted resolution as the default read
semantic**. That composition is the invention.
