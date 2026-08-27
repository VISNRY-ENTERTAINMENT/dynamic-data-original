# Using Dynamic Data (dd-core) in Another Project

A drop-in guide for an AI assistant (Claude, Cursor, etc.) that wants to use
Dynamic Data as **project memory** in a *different* project. Each project gets
its **own isolated `.ddb` file** — one project's memory never touches another's.

> **Read first:** `../00_DYNAMIC_DATA_CONCEPT.md` (what the primitive is) and
> `README.md` (the library/CLI/MCP surface).

---

## What you get

A claim store where every fact carries a **source, confidence, time, evidence,
and relationships**, claims **accumulate** (never overwrite), and truth is
**resolved on read**. Use it to remember durable, non-obvious project facts and
decisions so you don't re-derive them every session.

It is memory, not a governance gate — it stores *beliefs*, it does not police
*actions*.

---

## Setup (5 minutes)

### 1. Point at dd-core

`dd-core` is self-contained. Either:

- **Reference it in place** (simplest): use the copy at
  `worldStak_resources/dynamic data/dd-core`, or
- **Copy the `dd-core/` folder** into the other project (e.g. `tools/dd-core/`).

The library (`dd_core/`) and CLI (`dd_cli.py`) are **pure Python stdlib — zero
dependencies**. Only the MCP server needs a package.

### 2. Give the project its own memory file

Each project uses one `.ddb` file (SQLite). Pick a stable path, e.g.
`<project>/.memory/project.ddb`. Nothing else to provision.

### 3a. Use it directly (no MCP) — always available

```python
import sys; sys.path.insert(0, "/abs/path/to/dd-core")
from dd_core import DynamicDataStore, Profile

ddb = DynamicDataStore("/abs/path/to/project/.memory/project.ddb")
ddb.assert_claim("<subject>", "<predicate>", "<value>",
                 source="assistant", confidence=0.8, evidence="...")
ddb.resolve("<subject>", "<predicate>").chosen
```

Or the CLI:

```bash
python dd_cli.py --db /abs/.memory/project.ddb assert app db postgres --source human --confidence 1.0
python dd_cli.py --db /abs/.memory/project.ddb resolve app db
```

### 3b. Use it as MCP memory (recommended for an assistant)

```bash
pip install "mcp[cli]"
# Register, pinning THIS project's DB via the DD_DB env var:
#   Windows (PowerShell):
$env:DD_DB="C:\path\to\project\.memory\project.ddb"
claude mcp add dynamic-data -- python "C:\path\to\dd-core\dd_mcp_server.py"
#   macOS/Linux:
DD_DB=/path/to/project/.memory/project.ddb \
  claude mcp add dynamic-data -- python /path/to/dd-core/dd_mcp_server.py
```

The assistant then has these tools: `assert_claim`, `resolve`, `history`,
`list_conflicts`, `search`, `relationships`, `subjects`, `stats`.

---

## How an assistant should use it (conventions)

Consistency is what makes the memory useful. Suggested rules:

1. **Record durable, non-obvious facts** — decisions, constraints, "why we did
   X", gotchas. Not things trivially re-derivable from the code.
2. **Always cite a source and set an honest confidence:**
   - `1.0` — a human stated it, or you directly verified it (test passed, ran it).
   - `0.7–0.9` — you inferred it confidently from strong evidence.
   - `0.3–0.6` — a hypothesis / observed-but-unverified.
3. **Use a consistent `subject` vocabulary** (e.g. the component or entity name)
   so `resolve` and `history` line up over time.
4. **Prefer recording a claim over stating an unsourced fact.**
5. **On a decision reversal, assert the new claim** (optionally `supersedes` the
   old id). Don't delete — the change *is* the value.
6. **Check `list_conflicts` periodically** — disagreements are signal.

A tiny starter convention block you can paste into the other project's assistant
instructions:

```
This project has a Dynamic Data memory (MCP tool: dynamic-data).
- Record durable, non-obvious facts/decisions with assert_claim (cite source,
  set honest confidence; 1.0 only if a human said it or you verified it).
- Before stating a project fact, resolve it from memory; if unknown, find out
  and record it.
- Review list_conflicts when beliefs seem to disagree.
```

---

## Making it automatic (don't rely on the AI remembering)

An AI can always choose to skip a tool call, no matter what its instructions
say — there is no way to make a tool call *mandatory* from inside a model's
reasoning. The real fix: for facts a script can capture on its own, don't ask
the AI at all. `dd_git_hook.py` wires into `.git/hooks/post-commit` and logs
every commit — sha, branch, author, message, files changed — automatically, no
AI in the write path, works no matter which assistant (or human) made the
commit. Model-agnostic, no third-party service, pure git + stdlib.

```bash
# In the target repo's post-commit hook (append; don't remove existing hooks):
python "/abs/path/to/dd-core/dd_git_hook.py" --db "/abs/path/to/project.ddb" --repo "myproject" || true
```

This covers *structural* facts completely. It does **not** cover judgment calls
("why did we choose X") — those still need an assistant to call `assert_claim`,
and for those, prompting is the best available lever (the MCP server's
`instructions` field already asks for it). If a harness supports its own hook
system (e.g. Claude Code's `Stop`/`PostToolUse` hooks), that's a stronger,
harness-level way to nudge or require logging of judgment calls too — but it is
harness-specific, unlike the git hook.

## Recursive self-improvement (the reflex loop)

The next level up from logging *what shipped* is auditing *what shipping left
unfinished* — automatically, and recording it in the same ledger. That is the
**reflex loop** (`dd_core.reflex`, architecture in
`../04_RECURSIVE_IMPROVEMENT.md`). After a substantive commit, a model reviews
the diff (**Tier 1**) and, every few major commits, audits the whole codebase
against your roadmap + north star (**Tier 2**). Findings become append-only
`arch.gap:` / `arch.audit:` claims; a **deterministic** gate escalates to you at
a threshold. No model is ever in the write or escalate path — same doctrine as
the git hook.

### One-command setup

```bash
python /abs/path/to/dd-core/dd_reflex.py init     --repo-root .     --anchors ROADMAP.md VISION.md
```

That writes `reflex.config.json`, copies the two charter templates into
`reflex/` for you to customize, and appends the (backgrounded, fail-soft)
post-commit hook. It also:

- **auto-detects your repo's code dirs** and reports them, while leaving
  `substantive_prefixes` **empty** — the repo-agnostic default that fires on any
  layout. Narrow it later only if you mean to.
- **reuses your project's existing `.ddb`** if it finds one, so findings live
  alongside the rest of that project's memory instead of fragmenting into a
  second ledger. Override with `--gap-db`.

Then run **`doctor`** (below), and **edit the two charters** — they are where you
tell the reviewer/auditor what a "gap" and "drift" mean *for your project*
(invariants, boundaries, what "on course" looks like). The generic templates work
out of the box — they do find real gaps — but the value scales sharply with
project-specific rules.

### Check it will actually work — `doctor`

```bash
python dd_reflex.py doctor --config reflex.config.json
```

**Run this first.** The loop's worst failure is *silence*: a config that matches
nothing produces output identical to a clean codebase. `doctor` proves the
config would fire — it reports how many tracked files count as substantive,
whether the ledger exists, whether your AI CLI is on PATH, and whether the
charters are still `[CUSTOMIZE]` templates. It exits non-zero on `WOULD NOT
WORK`.

> This exists because of a real failure: `substantive_prefixes` used to default
> to `src/ tests/ lib/ app/`. A project laid out as `engine/ domains/ judge/`
> matched **nothing**, so the loop never fired and looked exactly like "no gaps
> found". The default is now empty (= repo-agnostic) and `doctor` makes a
> mismatch loud.

### What to tune (`reflex.config.json`)

| Field | Meaning | Default |
|---|---|---|
| `gap_db` | ledger to write findings to. **Point it at your project's EXISTING `.ddb`** so findings live with the rest of its memory — `init` auto-detects one. Keep it OUTSIDE the reviewed repo so recording gaps makes no commit (no self-trigger). | auto-detected |
| `substantive_prefixes` | which paths make a commit worth reviewing. **Empty = repo-agnostic** (any changed file that isn't obviously non-code) and works on ANY layout. Set it only to *narrow* scope — a wrong value silently disables the loop, so run `doctor` after. | `[]` |
| `ignored_prefixes` | never review these (e.g. the reflex dir itself → no self-trigger) | `["reflex/"]` |
| `provider` | which AI. `claude` preset, or `generic` + your own `cli`/`cmd_template` | `claude` |
| `cli` / `cmd_template` | the executable and its argv. Placeholders: `{cli} {model} {charter} {repo}`. The **prompt always goes over stdin**, so any CLI that reads stdin works. | preset |
| `review_model` / `audit_model` | Tier-1 (every commit → **cheap**) vs Tier-2 (rare, deep → mid). Defaults are deliberately *not* the expensive tier. | Haiku / Sonnet |
| `charter_mode` | `flag` (CLI takes a system-prompt file) or `prepend` (charter is prepended to the stdin prompt — universal fallback) | preset |
| `major_commit_regex` | what counts as a "major" commit for the Tier-2 cadence | conventional commits `feat\|fix\|refactor\|perf` |
| `audit_every` | run the whole-codebase audit every N major commits | `3` |
| `threshold` / `floor` | open gaps needed to escalate / min confidence that counts | `3` / `0.6` |
| `north_star_anchors` | the roadmap/vision files the Tier-2 auditor measures against | `[]` |

## Managed backlog, not a firehose

Left running, a naive review loop drowns you: every audit re-proposes still-open
issues, small stuff and serious stuff arrive with equal urgency, and closed items
linger in the ledger. Three deterministic mechanisms keep the loop a *help*:

- **Semantic dedup.** A later audit that re-words the same issue under a new slug
  is collapsed onto the original via `same_as` instead of spawning a duplicate
  (deterministic token+area+file match; every collapse is logged, never silent).
  So the loop stops arguing with itself.
- **Severity triage.** Findings split into **act-now** (`high`/`critical` --
  surfaced immediately, even one) and **backlog** (`medium`/`low` -- recorded,
  listed on demand, never nags unless the pile grows past a large threshold).
  You are interrupted for what matters and left alone for what doesn't.
- **Auto-close.** A commit whose message says `Closes <subject>` (or Fixes/
  Resolves) marks that finding `fixed` in the ledger automatically, cited to the
  SHA -- the same convention you already use for issues. The backlog self-drains
  as fixes ship; nobody has to remember to update it.

```bash
python dd_reflex.py backlog --config reflex.config.json   # triaged view: act-now vs parked
python dd_reflex.py status  --config reflex.config.json   # every finding + state
```

You do **not** have to clear findings the moment they appear. Fix the urgent ones;
let the rest sit as a tracked backlog and get to them when it makes sense. All of
this is deterministic -- no model decides what to hide, escalate, or close.

### Precision, detection, and metrics (deterministic)

Beyond the core loop, these run automatically and keep it trustworthy — all
without a model:

- **Evidence validation.** Before a finding is recorded, its cited `file:line`
  is checked against the real repo. A citation that doesn't resolve is likely
  hallucinated → the finding is kept but down-ranked below the action floor.
- **Structural probes** (`dd_ri.py probe`). Deterministic detectors for *facts*
  about the code — e.g. an optional constructor param that is defined but never
  passed anywhere ("built but not wired"). Recorded during a Tier-2 audit with
  source `reflex-probe`; no model involved.
- **Learning from history.** The Tier-2 auditor is primed with the defect
  *shapes* this loop has already caught + fixed, and cautioned about findings a
  human rejected as wrong. Precision compounds run over run.
- **Metrics** (`dd_ri.py metrics`). Precision, false-positive rate,
  mean-time-to-close, and open-by-severity — read straight from the ledger's
  dispositions. If precision drops, that's your signal to tighten the charter or
  raise the floor.

## Using a different AI (no vendor is assumed)

Nothing in the loop requires Claude. Pick the `generic` provider and describe
your CLI:

```json
{
  "provider": "generic",
  "cli": "llm",
  "cmd_template": ["{cli}", "-m", "{model}"],
  "review_model": "<your cheap model>",
  "audit_model": "<your mid model>",
  "charter_mode": "prepend"
}
```

`charter_mode: "prepend"` puts the charter at the top of the stdin prompt, so it
works with any CLI that has no system-prompt flag. Tier 1 runs on **every**
substantive commit — keep `review_model` cheap.

### Driving it by hand

```bash
python dd_reflex.py status  --config reflex.config.json   # list open findings
python dd_reflex.py gate    --config reflex.config.json   # deterministic escalation check
python dd_reflex.py run     --config reflex.config.json   # run the loop once (both tiers)
python dd_reflex.py audit   --config reflex.config.json   # force a Tier-2 audit now
```

Requires a headless `claude` CLI on PATH for the discovery step; everything else
is pure stdlib. If the CLI is absent the loop degrades gracefully (records
nothing, never errors). Disable entirely with `REFLEX_DISABLE=1`.

## Isolation & safety

- **Per-project isolation:** one `.ddb` per project via the path / `DD_DB`. No
  shared global state.
- **Append-only:** the store never overwrites or deletes; `retract` only flags a
  claim while keeping its history. Low blast radius.
- **Portable:** the `.ddb` is a single SQLite file — copy it, back it up, or
  commit it (or git-ignore it) as you prefer.
- **No network:** everything is local.

---

## Versioning note

dd-core is **v0.1.0**. Pin the version you copied into a project, and re-run
`python -m pytest tests/ -q` after any upgrade — the tests lock the primitive's
promises (accumulation, confidence resolution, time-travel, determined-mode
faults).
