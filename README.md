# Dynamic Data

An AI-agnostic software governance system. Sits outside any project, fires on commit via git hooks, runs a separate AI through structured review "departments", and logs every finding in a claim-based reflex ledger.

## How it works

1. A git hook fires after each commit in the governed repo.
2. **Deterministic department probes** scan the diff immediately — no model, no cost, instant.
3. **Tier-1 model review** reads the diff + any open findings and records new gaps.
4. Every finding lands in `reflex.ddb` — an append-only SQLite claim store. Truth is computed on read; nothing is ever overwritten.
5. When MAJOR findings (high/critical) are open, the **prepare-commit-msg hook** injects a `REFLEX WARNINGS` block into the building AI's next commit message — informational, never blocking.
6. When a finding's pattern is no longer detected, **probe-based auto-close** marks it fixed automatically. When the AI commits with `Closes arch.gap:<slug>`, the ledger closes it too.

## Departments

| Department | What it catches | Oracle |
|---|---|---|
| **security** | Hardcoded secrets, shell injection, unsafe deserialization, path traversal | Deterministic regex, multi-language |
| **debt** | TODO/FIXME/HACK markers, `raise NotImplementedError` stubs, empty functions | Deterministic regex |
| **observability** | Bare `except: pass`, unguarded background tasks, discarded Go errors | Deterministic regex |
| **architecture** | Layer violations, forbidden imports, required files (`architecture_rules.json`) | Deterministic manifest |
| **dependency** | Unpinned versions (`==*`, `latest`, `>=0.0`), missing lockfiles | Deterministic manifest parser |
| **contract** | Invariant and payload contract violations (`invariants.json`, `contracts.json`) | Deterministic manifest |
| **goal_alignment** | Intent drift, commit message mismatch | Tier-1 model lens (no deterministic oracle) |

## Setup

### 1. Clone this repo alongside the project you want to govern

```
your-project/
dynamic-data-master/   ← this repo
```

### 2. Drop a `reflex.config.json` in your project root

```json
{
  "repo_root": ".",
  "gap_db": "reflex.ddb",
  "provider": "claude",
  "review_model": "",
  "audit_model": "",
  "dd_core_path": "../dynamic-data-master/dd-core",
  "departments": [],
  "architecture_rules": "architecture_rules.json",
  "departments_on_all_commits": false
}
```

`"departments": []` enables all departments. To restrict: `["security", "debt"]`.

### 3. Install the hooks

Run from your project root:

```
python ../dynamic-data-master/install_hooks.py
```

Installs two hooks idempotently (chains with any existing hook content):
- **`post-commit`** — runs department probes + Tier-1 model review
- **`prepare-commit-msg`** — injects `REFLEX WARNINGS` when MAJOR findings are open

### 4. Optionally add `architecture_rules.json`

```json
{
  "rules": [
    {
      "id": "no-route-to-db-direct",
      "type": "no_import",
      "from_glob": "app/routes.py",
      "forbidden_import": "db.",
      "severity": "high"
    },
    {
      "id": "must-have-readme",
      "type": "required_file",
      "path": "README.md",
      "severity": "low"
    }
  ]
}
```

Rule types: `no_import`, `no_pattern`, `naming_rule`, `required_file`.

## Claim lifecycle

```
open  →  escalated  →  fixed
                    →  wontfix
```

- **Auto-closed by probe**: pattern absent from scan → `fixed` asserted automatically
- **Auto-closed by commit message**: `Closes arch.gap:<slug>` in message → `fixed`
- **Manual**: `ddb.assert_claim("arch.gap:<slug>", "status", "wontfix", ...)`

## Viewing the backlog

```bash
python dd-core/dd_reflex.py show --db reflex.ddb
```

## Design rules

- **No model in the write/escalate path.** Models suggest; deterministic code decides status.
- **Append-only.** Every claim is timestamped and sourced. Nothing is deleted or overwritten.
- **AI-model-agnostic.** Any CLI that reads a prompt from stdin works. No SDK names in engine code.
- **Non-blocking.** The commit message warning is informational. Commits always go through.
