# Can software edit software without AI? — a research-grounded design

**Research question:** push Dynamic Data to do as much as possible so
the AI doesn't have to — up to and including *editing and creating software
itself, deterministically*, so we depend on AI less.

**Short answer:** Yes for a large, well-defined fraction of real edits — and
Dynamic Data is already holding the two hardest halves of the problem. What's
missing is a third half (actuation). This doc grounds that claim in the
literature, derives the architecture with the *inversion invention method*, and
draws the honest boundary where AI is still required.

---

## 1. The state of the art (what already works without AI)

Five independent research lines each show a piece of "software edits software"
working *deterministically today*:

- **Deterministic semantic transformation.** OpenRewrite rewrites Java/Kotlin at
  scale via recipes over a Lossless Semantic Tree; Coccinelle applies *semantic
  patches* (SmPL) to fix bugs across the Linux kernel; Comby / ast-grep /
  Semgrep-autofix do structural match-and-rewrite across languages. These are
  provably-scoped edits, no model. [OpenRewrite, Coccinelle, ast-grep, Comby]
- **Program synthesis from examples (PBE).** Microsoft PROSE / FlashFill
  synthesize correct string/transform programs from input-output examples;
  SyGuS synthesizes programs to satisfy a logical spec within a grammar. No
  model — search over a constrained space with a checkable spec. [PROSE, SyGuS]
- **Automated program repair (APR).** GenProg (generate-and-validate over edits,
  tests as oracle); SemFix / Angelix / Nopol (constraint-based, synthesize a
  patch consistent with tests). The *test suite is the specification*. [GenProg,
  Angelix]
- **The Plastic Surgery Hypothesis.** Barr, Brun, Devanbu et al.: the code needed
  to fix a bug *usually already exists elsewhere in the same project* — commits
  are largely "graftable" from existing snippets, independent of size. This is
  the theoretical license for AI-free repair: you rarely need to *invent* code,
  you need to *find and transplant* it. [Barr14, Defects4j ingredients study]
- **Equality saturation.** egg / egglog represent all programs equivalent to the
  input in an e-graph via rewrite rules, then extract the best by a cost function
  — deterministic, behavior-preserving optimization/refactoring. [egg, EGRAPHS'24]

The common structure across all five: **a checkable specification + a constrained
edit space + a deterministic validator.** Where those three exist, no AI is
needed. Where they don't, AI is filling one of them in.

---

## 2. The inversion invention method applied

Instead of asking *"how do we make software edit software?"*, ask the inverse:
**"what would GUARANTEE that software can NEVER safely edit itself without an
AI?"** Each guarantee-of-failure, negated, is an invention.

| What guarantees "AI required" | Inversion (the invention) | Do we have it? |
|---|---|---|
| **Intent is ambiguous** — you can't edit toward a goal you can't state, so a human/AI must interpret vague requests. | Make intent an **executable, checkable predicate**. A failing oracle / invariant / contract / test *is* the spec: "the edit is whatever makes this pass." | ✅ The oracles ARE this. `invariants.json`, `contracts.json`, the Wiring Prover — each is machine-checkable intent. |
| **Edit space is unbounded** — infinite ways to change code; only judgment picks one. | Constrain to a **finite library of parameterized, behavior-defined transforms** (codemods / recipes / semantic patches). | ❌ Not yet — this is the missing "actuators" layer. |
| **Fix ingredients must be invented** — writing novel code needs a mind. | **Plastic surgery**: graft the fix from code already in the repo. Most fixes are recombinations, not inventions. | ◑ Partial — CodeFacts already indexes the repo; a grafter is a short step. |
| **You can't tell if an edit is safe** — correctness reasoning needs AI. | **Generate-and-validate** against deterministic acceptance: the oracle that *found* the defect + the full test suite + the differential oracle. Accept only edits that clear the finding and break nothing. | ✅ The whole testkit + oracle suite IS the validator. |
| **Edits are irreversible / risky** — so a human must supervise. | **Append-only ledger + worktree isolation + reversible transforms.** Every edit is a claim with provenance; apply in a throwaway git worktree; revert is free. | ✅ Ledger exists; worktree isolation is available. |
| **You can't tell when you're done** — no stopping rule. | **Fixpoint / loop-until-dry**: apply transforms until every oracle is green and no new findings appear. | ✅ The gate + metrics + loop already do this for findings. |

**The result of the inversion is one clean thesis:**

> **Oracle–Actuator duality.** Every defect an oracle can *detect* deterministically
> can, for a large class, be *repaired* deterministically — because the detection
> already localizes the defect exactly and already defines "fixed" exactly. The
> detector is the specification for the repair. Dynamic Data has spent this whole
> project building world-class *detectors*. Their duals — *actuators* — are the
> missing half, and the detectors hand them their spec and their acceptance test
> for free.

---

## 3. What Dynamic Data already has vs. what's missing

A deterministic self-editing system needs three parts. DD has two:

```
  (1) SPECIFICATION  — what "correct" means, machine-checkable    ✅ the oracles
  (2) ACTUATION      — a constrained library of code edits         ❌ MISSING
  (3) VALIDATION     — deterministic accept/reject of an edit       ✅ oracles + testkit
```

So the build is exactly one new layer, and it is the mirror image of a layer we
already have. `codefacts` reads **syntax → facts**. The new `actuators` layer
writes **facts → syntax**, reusing the same per-language adapters (tree-sitter
gives exact source spans, so edits are precise and formatting-preserving).

```
  codefacts/   source ──parse──▶ CodeFacts           (READ, done)
  actuators/   Finding + CodeFacts ──▶ Patch(span→text)   (WRITE, proposed)
  repair loop  patch ▶ worktree ▶ re-run finding-oracle + tests ▶ commit|revert
```

---

## 4. Proposed architecture: `dd_core/actuators/`

- **`Patch`** — a pure value: `(path, byte-span, replacement-text)`. Deterministic,
  reviewable, reversible. Never a free-text diff from a model.
- **An actuator per finding class** — `actuator(finding, facts) -> Patch | None`.
  Returns None when it can't act safely (conservative: a missed fix is fine, a
  wrong edit is not — same discipline as the oracles).
- **The graft engine** (plastic surgery) — when a fix needs a value/statement the
  actuator can't template, search the repo (via CodeFacts) for a sibling that
  already does it and transplant its exact AST span. E.g. the guarded peer
  endpoint's `assert_canonical_reads_allowed(engine)` line is copied verbatim
  into the unguarded one.
- **The repair loop** — generate-and-validate, straight from APR:
  1. run oracles → findings; 2. for each finding with an actuator, produce a
  Patch; 3. apply in an isolated **git worktree**; 4. **re-run the exact oracle
  that raised it** (must now be clean) **and the change-scoped test subset**
  (must stay green) **and all other oracles** (no regressions); 5. commit iff all
  pass, recording the edit as a **claim with full provenance** in the ledger;
  6. loop until dry.

Nothing in that loop is a model. Acceptance is the same deterministic machinery
that found the problem.

---

## 5. Taxonomy of edits by how AI-free they can be

The honest scientific claim isn't "all editing"; it's a *spectrum*, and a large
mass sits on the deterministic end:

| Tier | Character | Example | AI-free? |
|---|---|---|---|
| **Templatable** | local, fixed shape | insert missing guard call; add missing dict key; add teardown to leaky test | ✅ fully deterministic |
| **Graftable** | fix exists elsewhere in repo (plastic surgery) | wire an unprovided dependency by copying a sibling builder's construction | ✅ deterministic search + transplant |
| **Synthesizable** | small spec, searchable space | a pure transform pinned by input-output examples or a contract (PBE/SyGuS) | ◑ deterministic but bounded; solver-dependent |
| **Novel** | genuinely new algorithm/design | a new resolution policy, an architectural change | ❌ needs AI (or a human) |

The point for reducing AI dependence: **the first two tiers dominate real
maintenance.** This project's own history says so — "built but not wired" was the
#1 recurring defect, and it is squarely graftable. Every hour of AI time spent on
templatable/graftable edits is an hour a deterministic actuator could reclaim,
leaving AI for the Novel tier where it's actually irreplaceable.

---

## 6. Why this is safe *because* it's paired with the oracles

The reason deterministic self-editing is usually dangerous — you can't trust the
edit — is inverted here: **the edit's acceptance test is the oracle that demanded
it, plus the differential oracle, plus the change-scoped tests, run in a throwaway
worktree, with an append-only provenance trail.** An actuator can be dumb and
still safe, because it cannot commit anything that fails the validators. The worst
case is "no fix proposed," never "a wrong fix shipped." That asymmetry is exactly
what makes it appropriate to run without a human or AI in the inner loop.

---

## 7. First buildable step (proposed)

The **invariant guard-inserter** — the ideal proof of concept:
- **Spec is already live** (`invariants.json`, verified this session: it catches
  an unguarded canonical-read endpoint at the exact line).
- **Actuator is trivial and safe**: insert one statement at the top of the flagged
  function, grafted verbatim from a guarded sibling.
- **Validation is airtight**: re-run the invariant (must clear) + the change-scoped
  tests (must stay green). If either fails, revert — zero risk.
- **It closes the loop end-to-end**: detect → actuate → validate → commit-as-claim,
  with no AI in the path, on a real defect class that actually shipped here.

If that one works, the same skeleton generalizes to the contract key-adder, the
wiring grafter, and the state-leak teardown-wrapper — and Dynamic Data becomes a
system that not only finds its defects but *deterministically repairs the boring
majority of them*, reserving AI for genuine novelty.

---

## Sources
- OpenRewrite — https://github.com/openrewrite/rewrite ; https://moderne.ai/openrewrite
- Coccinelle — https://coccinelle.gitlabpages.inria.fr/website/
- ast-grep — https://ast-grep.github.io/ ; Comby — structural rewrite
- PROSE / FlashFill (Microsoft), SyGuS — https://sygus.org/
- GenProg — "A Generic Method for Automatic Software Repair"; SemFix / Angelix / Nopol
- Plastic Surgery Hypothesis — Barr, Brun, Devanbu, FSE'14 — https://people.cs.umass.edu/~brun/pubs/pubs/Barr14fse.pdf ; Defects4j ingredients — https://www.darkrsw.net/papers/EMSE2021.pdf
- egg / equality saturation — https://egraphs-good.github.io/ ; EGRAPHS'24
