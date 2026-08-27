# Architecture Anchors — TEMPLATE

> Copy to your project as `ARCHITECTURE.md`. This file is the Tier-2 auditor's
> measuring stick for the architecture department, and optionally drives the
> deterministic `architecture_probe.py` via `architecture_rules.json`.

## Layers

[CUSTOMIZE: describe your layer model. Example:]

```
Presentation  (routes/, handlers/, cli/)
     |
     v  (only through ports/interfaces)
Domain        (domain/, engine/, core/)
     |
     v  (only through repository interfaces)
Infrastructure (db/, storage/, http_client/, queue/)
```

**Rules:**
- Presentation may call Domain. Presentation must NEVER import Infrastructure directly.
- Domain must NEVER import Presentation or Infrastructure. Domain depends only on
  its own interfaces; Infrastructure implements them.
- Infrastructure may import Domain interfaces. Infrastructure must NEVER import
  Presentation.

## Module Boundaries

[CUSTOMIZE: list your top-level modules and what they own. Example:]

| Module | Owns | Must not import from |
|--------|------|----------------------|
| `engine/` | core authoring logic | `scripts/`, `domains/` |
| `domains/` | domain-specific operators | `engine/pipeline_runtime` (only public API) |
| `judge/` | quality assessment | `engine/` internals |

## Circular Dependency Policy

Circular imports are forbidden. The import graph must be a DAG.
[CUSTOMIZE: list any known intentional exceptions, e.g. TYPE_CHECKING-only imports.]

## Naming Conventions

[CUSTOMIZE: your file naming rules. Example:]
- All files in `handlers/` must end with `_handler.py`
- All files in `adapters/` must end with `_adapter.py`
- Test files must be named `test_*.py` (not `*_test.py`)

## Required Files

The following files must always exist:
[CUSTOMIZE: e.g.]
- `README.md`
- `ROADMAP.md` or `VISION.md`
- `reflex.config.json` (if the reflex loop is configured for this repo)

## Invariants That Must Never Drift

[CUSTOMIZE: your hard architecture rules. Examples:]
- No direct SQL in route handlers; all DB access goes through repository classes
- No business logic in migration files
- All public API endpoints must have a corresponding integration test
- Background jobs must be idempotent (safe to retry)
