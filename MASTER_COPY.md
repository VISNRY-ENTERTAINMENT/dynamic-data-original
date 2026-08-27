# This is the canonical master copy of Dynamic Data (dd-core)

**Do not delete this folder.** Every project that uses Dynamic Data — Ovyero
(`aios-v4.1.5_Creation`), UVE, the Hivemind v6 external store
(`hivemind-v6-dynamic-data`), and any future project — either references this
folder directly as its `dd-core` driver path, or copies `dd-core/` out of it
per `dd-core/SETUP_FOR_ANOTHER_PROJECT.md`. This is the source of truth those
copies/references sync from. It is not itself a per-project instance — no
project's own `.ddb` data file lives in here; each project keeps its own data
file elsewhere (see `SETUP_FOR_ANOTHER_PROJECT.md`, step 2).

Renamed from `dynamic data` (with a space) to `dynamic-data-MASTER` specifically
so it reads unambiguously in a file listing as "the one, never delete, copy or
update from this" — not just another project's working folder.

See `VERSION` for the current dd-core version and `dd-core/README.md` /
`00_DYNAMIC_DATA_CONCEPT.md` for what the library actually does.

## Known references to this folder (update if you ever move/rename it again)

- `hivemind-v6-dynamic-data/store.config.json` — `driver.path`
- `hivemind-v6-dynamic-data/README.md` — Quickstart `cd` + prose references
- `hivemind-v6-dynamic-data/bindings/hivemind_dd_binding.py` — docstring
- `aios-v4.1.5_Creation/reflex.config.json` — `dd_core_path`
- `aios-v4.1.5_Creation/.git/hooks/post-commit` — hardcoded path in the reflex hook line
- `aios-v5.1_Creation/reflex.config.json` — `dd_core_path` (gitignored — see its
  note there, hardcodes an absolute Windows path, cannot ship as-is)
- `aios-v5.1-dynamic-data/store.config.json` — `driver.path`
- `UVE/UVE_ARCHITECTURE.md` — prose reference (domain #38)
- `UVE/UVE_BUILD_ROADMAP.md` — prose reference (dev-tool section)

Search for `dynamic-data-MASTER` or `dynamic data` (pre-rename form, should
find nothing left) across `Downloads/` to catch anything missed.

## GitHub mirrors — private vs public, and the sync gap

This folder is also the source for two GitHub repos under
`github.com/VISNRY-ENTERTAINMENT`, and they are **not currently kept in sync
automatically** — that's a manual step today, not a running process. Recording
the intended policy here so it isn't lost between sessions:

- **`dynamic-data-master`** (private) — should mirror this folder's full
  content whenever it changes. Proprietary license, everything included
  (VISNRY internal references, WorldStak examples, `seed_worldstak.py`,
  private product integrations like `ovyero_calibration.py`, all of it).
- **`dynamic-data-original`** (public) — should mirror a **filtered** subset:
  Apache 2.0 licensed, VISNRY-internal references stripped or genericized,
  WorldStak mentions genericized (e.g. → "ExampleApp"), and anything tightly
  coupled to a specific private product (file/route names, `.ddb` seed data,
  product-specific integration modules) excluded rather than partially
  redacted. First built 2026-08-19 — see that repo's history for the exact
  exclude/genericize list; the same list applies to future updates.

**What "automatically" should mean, when this gets built:** a local update to
this folder should trigger (a) a straight push to `dynamic-data-master`, and
(b) a filtered re-export + push to `dynamic-data-original` that re-applies the
same exclude/genericize rules rather than a one-time hand-pass. Until that
exists, treat every local change as needing a manual decision — "does this
touch anything on the exclude list?" — before it's safe to push to the public
mirror. Do not assume the public repo is current with this folder.
