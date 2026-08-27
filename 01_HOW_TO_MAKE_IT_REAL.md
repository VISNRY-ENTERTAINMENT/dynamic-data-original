# Dynamic Data — How to Make It Real, Test It, and Use It

*Captured 2026-07-14. Answers two questions: (1) how do we prove it's better and
make it personally useful in the AI era, and (2) what about training on it.*

---

## Rule 0 — Do NOT start by building a database

Dynamic Data is a **model / primitive**, not a storage engine. Building a
crash-safe database from scratch means reinventing 40 years of durability,
concurrency, and recovery engineering — and losing on the one thing a truth
system can't afford (silent data loss). WorldStak already proves you don't need
to: it runs the Dynamic-Data model as a **layer over Postgres** (the repository
pattern). Keep storage boring; put the invention in the *model and the read
semantics*.

So the test is never "is my database better than Postgres." It's **"is the
Dynamic-Data *model* better than the static-record model, for the domains it
targets."** Test the idea, not the infrastructure.

---

## Question 1 — How do we prove it's better and make it useful?

### Step 1 — Extract a tiny standalone library (the "dd-core")

Pull the Dynamic-Data core out of WorldStak into a small, domain-agnostic
library you can use anywhere:

- `claim(subject, predicate, object, value, source, confidence, t, evidence)`
- `assert_claim(...)` — append-only, never overwrites
- `resolve(subject, predicate, as_of=now)` — returns the chosen claim + why
- `history(subject, predicate)` — the full accumulated timeline
- `conflicts(subject, predicate)` — surfaced disagreements
- Backend: SQLite to start (one file, zero ops). Same interface can later point
  at Postgres/Datomic. Profiles: `determined` (pin confidence, conflict=error)
  vs `believed` (weighted resolution).

This is a weekend-sized artifact, not a database. It's the thing every
experiment below plugs into.

### Step 2 — Define "better" operationally (so it's falsifiable)

"Foundation of computer science" is unfalsifiable as stated. Pick concrete claims
Dynamic Data should **beat static on**, and benchmark those:

| Capability | Static baseline | Dynamic Data | How to measure |
|---|---|---|---|
| Provenance ("where did this come from?") | manual/impossible | native field | time-to-answer, % answerable |
| Time-travel ("what did we believe on date X?") | schema gymnastics | native `as_of` | can it answer at all; correctness |
| Conflict handling | silent last-write-wins | surfaced + ranked | # of silent data-loss events avoided |
| **AI grounding** | LLM + vector chunks | LLM + claim store | answer accuracy, citation correctness, calibration |

If it doesn't clearly win on these, you've learned something cheaply. If it does,
you have evidence — not vibes.

### Step 3 — The two experiments that actually settle it

**A. Dogfood it (personal utility — cheapest, fastest signal).**
Use dd-core as *your* memory/knowledge system for real work: project decisions,
facts, "who said what and how sure." If capturing claims and asking "what do we
currently believe about X, and what changed" makes your work easier, that's real
signal — and it costs almost nothing.

**B. The AI-grounding benchmark (the "is it better" proof, in the domain you
built it for).**
Take a question-answering / RAG task with facts that (i) come from multiple
sources, (ii) conflict, and (iii) change over time.

- **Baseline:** LLM + vector store of static text chunks.
- **Treatment:** LLM + dd-core (claims with source, confidence, time).
- **Measure:** answer accuracy, citation/provenance correctness, calibrated
  "I don't know," and behavior when facts conflict or get updated.

Prediction worth testing: the Dynamic-Data-grounded model **cites sources,
expresses calibrated confidence, and handles updates/conflicts** measurably
better — because the substrate carries exactly the signals a static chunk store
throws away. That result *is* your proof, in the exact place the static record
is most catastrophically wrong.

### Step 4 — Make it easy to use personally, TODAY (the tooling)

Adoption dies on friction. To make Dynamic Data help your work now, you need
three cheap things:

1. **Capture** — a dead-simple "add a claim" (a CLI, a hotkey, a chat command).
2. **Ask** — `resolve` / `history` / `conflicts` from wherever you work.
3. **Plug into where you already are** — expose dd-core as an **MCP server** so
   any AI assistant (Claude, etc.) can read *and write* your claim store as its
   memory. WorldStak already lives in an MCP-friendly world.

That last one is the unlock: an AI that uses your Dynamic-Data store as memory
makes *your* work easier immediately **and** generates a growing corpus of
real claims-with-confidence — which feeds Question 2.

**Smallest first build (one artifact, three payoffs):** `dd-core` over SQLite +
an MCP server exposing `assert_claim` / `resolve` / `history`. It's personally
useful day one, it's the substrate for the AI benchmark, and it produces the
training corpus. Build this before anything bigger.

---

## Question 2 — What about training on Dynamic Data?

"Training on it" has two very different meanings. Both are interesting; sequence
them.

### 2a. AI *using* Dynamic Data at inference (retrieval / memory) — do this first

No training required. The model reads the claim store as grounded, sourced,
time-aware memory and writes new beliefs back. This is the near-term, high-value
win — it's Step 4 above, and it's where hallucination actually gets reduced. Ship
this first; it proves value with zero model changes.

### 2b. *Training / fine-tuning a model on Dynamic-Data-structured data* — the research bet

This targets the root problem you identified: **models are trained on flat text
where everything looks equally true, so they emit everything with false
confidence.** If instead the training signal carries confidence and provenance,
the model can learn to *express* uncertainty and *cite* — calibration and
grounding become **learned behaviors, not bolted-on guardrails.**

- Format examples as claims: `(statement, source, confidence, as_of,
  supersedes)`. The label carries the confidence, so the model can learn the
  mapping **evidence → calibrated confidence**, and learn that facts have
  provenance and can be revised.
- Plausible gains: better-calibrated "I'm 70% sure," source citation as a native
  habit, graceful belief revision instead of contradiction.
- **Honest caveat:** calibration-via-training is hard; RLHF already tries and
  partly fails. This is a genuine research direction, not a sure thing. Treat it
  as an experiment with a clear baseline (does a model fine-tuned on
  Dynamic-Data-structured examples produce more calibrated, better-cited answers
  than the same model on flat text?).

### The flywheel (why 2a and 2b connect)

Using Dynamic Data as AI memory (2a) **produces the very dataset** you'd need for
2b: a growing, real corpus of claims with sources, confidences, conflicts, and
*outcomes* (which claims turned out right). That corpus is exactly the training
signal a model needs to learn to reason in Dynamic Data natively.

```
use it as AI memory  →  accumulate claims + confidence + outcomes
      ↑                              ↓
train a model that      ←   that corpus becomes training data
reasons in DD natively
```

So the order is: **dogfood + MCP memory (now) → grounding benchmark (proves it) →
accumulate corpus (free byproduct) → fine-tune experiment (the research bet).**
Each step is cheap, each de-risks the next, and none of them requires building a
database.

---

## The one-sentence plan

Build a tiny `dd-core` library + MCP server over SQLite, use it as your own
AI-connected memory, benchmark AI grounding with-vs-without it, and let the
claims you accumulate become the corpus for a later "train a model to think in
Dynamic Data" experiment — proving the primitive on the domain it was born for
(AI) before ever touching database infrastructure.
