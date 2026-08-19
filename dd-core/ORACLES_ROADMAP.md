# Deterministic Oracles — Roadmap

**Goal:** make an AI reliable and autonomous at software by giving it *external,
deterministic oracles* it cannot fool — each catching a specific failure class
the model is structurally blind to. (Research basis: an LLM cannot reliably find
its own errors; the evaluator shares the generator's blind spots. So the leverage
is external checks, not a better inner critic.)

## One system, two faces

To keep this to the fewest moving parts, **all new oracles live inside Dynamic
Data**, not as separate tools. A finding is already a claim, so the oracles reuse
the whole existing pipeline:

```
oracle.run(repo) -> [findings] -> record_gaps (dedup) -> ledger
                                       -> gate (MAJOR/MEDIUM/RECOMMENDED triage)
                                       -> metrics (self-calibration: precision, duplicate_open)
```

Two faces, both under `dd_core`:

1. **Static analyzers** — pure `run(repo_root) -> list[finding]` functions in
   `dd_core/recursive_improvement/` (next to `probes.py`). They emit claims. No
   model in the discovery path. *Wiring Prover, Invariant Manifests, Consequence
   Preview.*
2. **Test-kit** — one pytest plugin, `dd_core/testkit/`, providing fixtures and
   autouse hooks. Runtime tools. *Differential Oracle, State-Leak Detector,
   Change-scoped selection.*

`self-calibration` is not a new tool — it is a property every oracle already gets
for free: its findings that get **fixed** are true positives, its **wontfix**s
are false positives, and `metrics.py` reports each source's precision + noise.

## The eight → where each lands

| # | Oracle | Face | Failure class it retires | Status |
|---|--------|------|--------------------------|--------|
| 1 | **Wiring Prover** | analyzer | built-but-not-wired | ✅ shipped |
| 2 | **Differential Oracle** | testkit | backend-only drift (in-memory green, real backend breaks) | ✅ shipped |
| 3 | **Invariant Manifests** | analyzer | the passing façade (partial invariant reported as done) | ✅ shipped |
| 4 | **Ovyero self-calibration** | (Ovyero + ledger) | the checker that rots into a nag / rubber stamp | ✅ shipped |
| 5 | **State-Leak Detector** | testkit | nondeterministic / order-dependent tests | ✅ shipped |
| 6 | **Change-scoped verify** | testkit | slow, coarse feedback → risky batches | ✅ shipped |
| 7 | **Spec-derived contracts** | analyzer | silent producer/consumer interface drift | ✅ shipped |
| 8 | **Consequence Preview** | analyzer | blast-radius blindness / not future-proof | ✅ shipped |

Ordered by leverage × how often it bit the ExampleApp build. #1–#3 retire the two
costliest classes (built-but-not-wired, backend drift) and the credibility killer
(the passing façade).

## What shipped — module + command map

| Oracle | Module | How to run |
|--------|--------|-----------|
| Wiring Prover | `dd_core/recursive_improvement/wiring.py` | `dd_ri wiring [--record]`, also in `dd_ri probe` |
| Invariant Manifests | `dd_core/recursive_improvement/invariants.py` | `invariants.json` + `dd_ri probe` |
| Spec-derived contracts | `dd_core/recursive_improvement/contracts.py` | `contracts.json` + `dd_ri probe` |
| Ovyero self-calibration | `dd_core/recursive_improvement/ovyero_calibration.py` | `.ovyero/overrides.jsonl` + `dd_ri probe` |
| Consequence Preview | `dd_core/recursive_improvement/consequence.py` | `dd_ri blast [--changed ...]` |
| Differential Oracle | `dd_core/testkit/differential.py` | `differential` fixture (plugin) |
| State-Leak Detector | `dd_core/testkit/state_leak.py` | autouse (plugin); `--dd-leak-fail` to enforce |
| Change-scoped verify | `dd_core/testkit/selection.py` | `dd_ri select [--changed ...]` |

`dd_ri probe` fans out every whole-repo analyzer (Wiring, Invariants, Contracts,
Ovyero calibration) in one call; the manifest/log-driven ones no-op with zero
noise until a project opts in by adding the config file. Enable the testkit with
`pytest -p dd_core.testkit.plugin`.

## Phase plan

- **Phase A — Wiring Prover (now).** `wiring.py`: declared ∧ consumed ∧
  never-provided across the whole tree. Upgrades the keyword-only `probes.py`
  primitive with positional/assignment provider detection + a consumer
  requirement, so findings are real (medium confidence), not LOW hints. CLI:
  `dd_ri wiring`. Validated against ExampleApp.
- **Phase B — testkit skeleton + Differential Oracle.** `dd_core/testkit/`
  pytest plugin; a `differential` fixture that runs one scenario against two
  backends and asserts equivalence.
- **Phase C — Invariant Manifests.** Declare an invariant's full surface
  (e.g. `invariants.toml`: `canonical_reads = [<endpoints>]`); analyzer checks
  the code covers the whole set. Generalizes the hand-written wiring-lock tests.
- **Phase D — State-Leak Detector + Change-scoped selection** (testkit).
- **Phase E — Ovyero self-calibration.** Feed every `--no-verify`/override back
  as a per-rule false-positive claim; Ovyero rules get a precision meter.
- **Phase F — Spec-derived contracts, Consequence Preview.** Highest ceiling,
  highest effort; do once the deterministic base is load-bearing.

## Agnostic on three axes

The system is deliberately agnostic where it should be, and honest about the
one adapter seam that carries language specifics.

- **Domain / industry agnostic.** No oracle knows what the software *does*. They
  reason about code structure (a capability read but never provided; a blast
  radius; a declared invariant's surface), never business meaning — the same
  Rule-11 discipline ExampleApp itself holds. A legal, medical, or game codebase
  gets identical treatment; project-specific rules live in that project's
  `invariants.json` / `contracts.json`, never in the engine.

- **AI / vendor agnostic.** The only place a model appears is the loop's
  *discovery* step (Tier-1/Tier-2 review), and it goes through `runner._run_model`
  — a provider-neutral call: the prompt streams over stdin to whatever CLI a
  `cmd_template` names (provider presets + `flag`/`prepend` charter modes).
  "Nothing here assumes Claude." Every oracle's own discovery path uses **no
  model at all** — a fact about code is computed, never judged — so swapping the
  reviewing AI changes nothing downstream.

- **Language agnostic.** Every analyzer is written once against the
  language-neutral `dd_core.codefacts.CodeFacts` model; a per-language *adapter*
  produces those facts from source. The **Python adapter** uses the stdlib `ast`
  (zero deps, always on). The optional **tree-sitter adapter**
  (`pip install dd-core[polyglot]`) produces the *same* CodeFacts for **15
  languages across 26 extensions**: Python, JavaScript, TypeScript, TSX, Go,
  Rust, Ruby, Java, C#, Kotlin, C, C++, PHP, Swift, Scala. One spec-driven
  extractor; adding a language is one `LangSpec` entry.

  Per-language fact coverage is honest, not uniform: **imports, functions,
  calls, member reads, subscript reads work in all 15** — so change-scoped
  Selection, Consequence Preview, Invariant Manifests, and the consumed/call
  side of the Wiring Prover are fully polyglot. Returned-object keys (Contracts
  producers) cover js/ts/ruby/go; optional-null params + provided attributes
  (Wiring's declared/provided side) are fullest on Python + JS/TS. A grammar
  that doesn't expose a fact yields empty, never wrong. (Known limit: Ruby bare
  method calls without parens are indistinguishable from locals — `guard()` is
  detected, `guard` is not.)

  Proof: the Wiring Prover, Invariant Manifests, Contracts, Consequence Preview,
  and change-scoped Selection pass their suites against JavaScript **and** the
  invariant + import-graph oracles pass against Go, with **no oracle code
  changed** — only adapters added. Adding a language is writing one spec, never
  touching an oracle.

The testkit is **framework-lean, not framework-bound**: `run_differential` and
the state-leak `check_leak` core are plain pytest-free functions usable from any
harness; only the thin `plugin.py` binds them to pytest fixtures.

## Invariants for the oracle layer itself

- **Deterministic discovery.** No model in any oracle's discovery path — a fact
  about code is computed, not judged.
- **Conservative by default.** A false positive costs trust; findings carry
  honest confidence and land in the RECOMMENDED tier unless strongly evidenced.
- **Self-accountable.** Every oracle's output flows through `metrics.py`, so a
  noisy oracle is surfaced by its own precision, not by a human's patience.
- **One ledger.** Everything writes claims to the same store. The shared bus is
  the recursion.
