# Dynamic Data — Recursive Self-Improvement

*Captured 2026-07-16. The first application architecture built ON Dynamic Data:
a system that uses the claim substrate to track and heal its own gaps. This is
the "reflexive/extensible" atom test (doc 02) turned into a running loop — the
store pointed at the codebase that produces it.*

## One-line definition

A **recursive improvement loop** turns "what's wrong with this system?" into a
stream of **claims** in a Dynamic Data ledger, exactly like any other fact:
`{subject, predicate, value, source, confidence, evidence, timestamp,
lifecycle}`. Gaps accumulate, never overwrite. Duplicates collapse by identity.
Fixed gaps are superseded, not deleted. And because the deciding step is a
*count over resolved claims* — not a model's mood — escalation to the human is
deterministic and auditable.

The insight is the same one behind Dynamic Data itself: **don't invent a second
system for a thing you already have a substrate for.** A backlog, a linter's
findings, a TODO list, a "tech-debt register" — these are all partial,
badly-separated re-implementations of an append-only, sourced, confidence-scored,
deduplicated claim log. Which is what a `.ddb` already is.

## Why this belongs in the Dynamic Data repo, not bolted onto one app

Every serious codebase re-grows the same missing limb: a place to record "this
is a known weakness," which rots into a stale `TODO.md`, a graveyard issue
tracker, or tribal memory. Those fail for the reasons static records fail
(doc 00): no source, no confidence, no dedup, no lifecycle, last-write-wins,
no audit. The moment you express a gap as a **claim**, all six come for free —
so the loop is a *property of the substrate*, portable to any project that has a
`.ddb`, not a feature of one engine.

It composes directly with the auto-logger already in the repo (`dd_git_hook.py`,
doc: README): that hook records **what shipped** (ground truth, no model). This
loop records **what shipping left unfinished** (judgment, one model call). Same
ledger, two predicates, one timeline.

## The loop

```
   commit ships ─────────────────────────────────────────────
      │  (dd_git_hook.py already logs the ship: no model)
      ▼
   REVIEW  — one model call reads the diff, proposes gaps        ← only probabilistic step
      │
      ▼
   RECORD  — dedup by identity, then assert_claim (no model)     arch.gap:<slug>  status=open
      │
      ▼
   GATE    — count open, above-floor, deduped gaps (no model)    a counter, not a judgment
      │
      ▼
   ESCALATE at N ── human decides: accept | wontfix | fix        the roadmap is edited by a human, never the loop
      │
      ▼
   a fix ships ── the loop re-sees the area, supersedes the gap  status=fixed  → ledger converges
```

Exactly one step uses a model, and it only *proposes*. Every step that changes
state is a plain function call. This is the same discipline as the git hook:
**no model in the write path** — a model's discretion decides *what might be
wrong*, never *what gets recorded* or *whether to interrupt the human*.

## Two cadences: the diff and the destination

A per-commit review sees one change; it cannot see the system drifting away from
what it is *for*. Drift accumulates across commits — two implementations of one
concept diverging, a roadmap milestone quietly becoming untrue, the architecture
inching away from its north star. So the loop runs at two scales, the second a
strict generalization of the first:

- **Tier 1 — per ship.** Reviews the **diff**. Cheap, frequent. Records
  `arch.gap:` claims; escalates at a threshold. "Did this change leave a hole?"
- **Tier 2 — every *k* major commits.** Audits the **entire codebase against its
  destination** — the roadmap's claims vs. the actual code, and the north star /
  final form the project is aiming at. Records `arch.audit:` claims and emits a
  periodic report. "Is the whole thing still on course?"

The cadence counter is itself derived from the ledger: the *last audited commit*
is a claim (`reflex.audit / last_deep_audit_sha`), and "major commits since" is a
deterministic count over the ship claims / git history since then — no separate
counter to drift. This is the reflexive pattern again: **the loop's own schedule
is stored as Dynamic Data, in the same ledger it writes findings to.** Tier 2 is
where "audit against the roadmap and the north star" lives; without it, a
diff-only loop optimizes local cleanliness while global purpose erodes unseen.

## Mapped onto the eight atoms (doc 02)

A gap is not a new kind of object. It is an ordinary claim; the loop is just a
naming convention plus a counter:

| Atom | How the gap loop uses it |
|---|---|
| **Proposition** | `subject = arch.gap:<slug>`, `predicate ∈ {status, severity}`, `value ∈ {open, fixed, …}`. |
| **Identity** | The `slug` is the gap's *root-cause identity*. The same weakness re-seen on a later commit resolves to the same subject → dedup, not a pile of near-duplicates. (Two reviewers wording it differently → `same_as`.) |
| **Source** | The reviewing agent (`reflex-sonnet`), `author_kind=ai` — so a gap's provenance is explicit and a human override outranks it. |
| **Credence** | The reviewer's honest confidence the gap is real. A floor (e.g. 0.6) separates "escalate-worthy" from "logged but not yet worth interrupting a human." |
| **Context** | `first_seen_sha`, area, evidence location — valid-time and place of the observation. |
| **Lifecycle** | `open → escalated → accepted | wontfix | fixed`. **This is a state machine, so it resolves by *record-time* (latest wins), NOT by the BELIEVED confidence-max resolver** — the one place a status must not be treated as a belief. (See "gotcha" below.) |
| **Derivation** | A gap can cite the ship claim it came from; a fix claim can cite the commit that closed it — the improvement is itself a traceable chain. |
| **Record-time** | Free append-only history: `resolve(as_of=SHA)` answers "what did we believe was broken at that point," and the whole loop is replayable. |

Five capabilities (dedup, provenance, audit, time-travel, conflict-surfacing)
fall out of the atoms without new code — the doc-02 promise, demonstrated on the
loop's own data.

## Why a counter — not the model — decides to interrupt the human

The failure mode of every "AI reviews your code" tool is that it nags. If the
same intelligence that *finds* issues also decides *whether to bother you*, it
optimizes for looking useful. Splitting them removes the incentive:

- The model may surface anything; being wrong is cheap (it's a low-confidence
  claim that never crosses the floor).
- A **deterministic gate** counts open, above-floor, deduped gaps and escalates
  only at a threshold the human set. It cannot inflate its own importance.

This is the Dynamic Data stance applied to the system's own operation: the
model contributes *credence*, the substrate contributes *resolution*, and the
human keeps *authority*.

## The reflexivity that makes it "recursive"

The store observes the repository that builds the store. A fix, once shipped, is
re-observed and supersedes the gap that motivated it — so the ledger **converges
toward zero open gaps** rather than growing forever. The system's improvement is
recorded in the same substrate it improves, which means the *history of getting
better* is itself a first-class, queryable Dynamic Data timeline. That is the
"future data" / reflexive-extensibility test (doc 02) not as a claim about the
design, but as a running proof.

## Anti-goals (derived by inversion — how to guarantee it fails)

Design was done by inverting the goal: *how would I guarantee the architecture
rots and this loop quietly fails?* Each failure inverted is a rule:

- Run it constantly → **event-driven** (only after a ship), never a timer.
- Let the loop edit the roadmap/code → **propose-only**; humans commit gaps.
- Keep findings in chat → **persist as claims** (sourced, chained).
- Let duplicates pile up → **dedup by identity** before insert — *including
  SEMANTIC dedup*: a re-worded finding under a new slug is collapsed onto the
  original via `same_as` (deterministic token/area/file match, every collapse
  logged), so successive audits stop re-proposing the same open issue.
- Never close gaps → **supersede on fix**; and **auto-close**: a commit whose
  message says `Closes <subject>` marks that finding fixed in the ledger,
  deterministically, so the backlog self-drains as fixes ship.
- Let evidence-free gaps escalate → **evidence-or-silence**, honest credence.
- Nag on every gap → **severity triage, not just a counter**: `high`/`critical`
  surface immediately; `medium`/`low` accumulate as a quiet backlog that only
  surfaces on a large pile-up. The loop must be a managed backlog, not a
  firehose — it should only ever *help*.
- Let it review its own output → **scope guard**; the ledger lives outside the
  reviewed repo, so recording a gap creates no commit and cannot re-trigger.
- Run on broken state → only **governed, green** ships trigger it.
- Let the model decide escalation → **the gate is deterministic**.
- Make it un-auditable → free: the ledger is append-only and hash-chained.

## Reference implementation

The loop is now a **portable part of dd-core** — config-driven and project-
agnostic, so any repo gains a self-healing gap ledger by wiring one hook line:

```
dd-core/
  dd_git_hook.py         # records what shipped          (Tier 0: ground truth, no model)
  dd_reflex_hook.py      # records what shipping missed   (post-commit entry)
  dd_reflex.py           # CLI: init / gate / run / audit / status
  dd_core/reflex/
    config.py            # ReflexConfig — the ONLY place paths/models/thresholds/anchors live
    gate.py              # deterministic escalation       (no model)
    record.py            # dedup + tolerant-parse + assert (no model)
    runner.py            # Tier 1 (diff review) + Tier 2 (whole-codebase audit)
    charters/
      reviewer_charter.template.md   # per-project: what a gap is
      auditor_charter.template.md    # per-project: what drift / on-course means
```

`python dd_reflex.py init --repo-root . --gap-db ../proj.ddb --anchors ROADMAP.md`
scaffolds a `reflex.config.json`, copies the two charters in for you to
customize, and appends the (backgrounded, fail-soft) post-commit hook. Nothing
in the loop is project-specific except the two charters and the config — the
gate, recorder, and runner are generic over any `.ddb`. Full tunables and setup:
`dd-core/SETUP_FOR_ANOTHER_PROJECT.md`.

The reference / first consumer is the ExampleProject engine (`tools/reflex/`, wired
into its `post-commit` hook right after `dd_git_hook.py`); its charters show a
fully-specialized example — Rule-11 domain purity, live-Postgres coverage, the
41 cannot-add-later components — that a new project adapts to its own invariants.
That instance predates the extraction and still runs its own copy; new projects
should use the `dd_core.reflex` module directly.
