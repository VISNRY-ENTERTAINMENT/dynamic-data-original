#!/usr/bin/env python3
"""Seed a Dynamic Data memory with the key WorldStak facts established so far.

Run:
    python seed_worldstak.py                 # writes ../worldstak.ddb
    python seed_worldstak.py --db /path.ddb

Then, e.g.:
    python dd_cli.py --db ../worldstak.ddb resolve worldstak prod_branch
    python dd_cli.py --db ../worldstak.ddb conflicts
    python dd_cli.py --db ../worldstak.ddb history worldstak full_suite_baseline

Idempotent: re-running does not duplicate claims (content-addressed ids).
Sources: 'ezra' = human-stated; 'claude-2026-07-14' = established/verified this
session. Confidence: 1.0 only for human-stated or directly-verified facts.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core import DynamicDataStore, Profile  # noqa: E402

EZRA = "ezra"
CLAUDE = "claude-2026-07-14"
T = "2026-07-14T00:00:00+00:00"   # session date for observed_at


def seed(ddb: DynamicDataStore) -> int:
    n = 0

    def claim(subject, predicate, value, source, confidence, evidence, obj=None):
        nonlocal n
        ddb.assert_claim(subject, predicate, value, obj=obj, source=source,
                         confidence=confidence, observed_at=T, evidence=evidence)
        n += 1

    # --- Identity / philosophy -------------------------------------------
    claim("worldstak", "is", "domain-agnostic deterministic claim-native truth engine",
          EZRA, 1.0, "Rule 11; no AI in the truth path — resolution is rule-based")
    claim("worldstak", "domain_agnostic", "true", EZRA, 1.0,
          "Rule 11 — hard stop; WorldStak owns relationships/confidence/ledger only")
    claim("archive", "is", "downstream fictional-world consumer of WorldStak",
          EZRA, 1.0, "Archive ingests FROM WorldStak; NOT part of the industry-agnostic core")

    # --- Blue/green workflow ---------------------------------------------
    claim("worldstak", "prod_branch", "blue", EZRA, 1.0, "blue = production, what we ship")
    claim("worldstak", "staging_branch", "green", EZRA, 1.0,
          "green = staging; new/risky work here, promote to blue when validated")
    claim("worldstak", "head_commit", "91790c1", CLAUDE, 1.0,
          "blue == green == 91790c1 after M9B flag-lock promotion")

    # --- Test-suite state -------------------------------------------------
    claim("worldstak", "full_suite_baseline", "3227 passed / 6 failed", CLAUDE, 1.0,
          "the 6 are product-correct harness pollution; all pass in isolation")
    claim("worldstak", "remaining_failures", "6 test-harness state-sharing (not product defects)",
          CLAUDE, 0.9, "step2_api x3, step2b_part1, step2b_part2, verification_api fingerprint")
    claim("worldstak", "test_pool", "NullPool", CLAUDE, 1.0,
          "conftest sets WORLDSTAK_ENGINE_NULLPOOL=1 to avoid cross-loop 'Event loop is closed'")

    # --- Gotchas / rules --------------------------------------------------
    claim("worldstak", "import_rule", "never use 'from src.X import' — always bare imports",
          CLAUDE, 1.0, "mixed src./bare loads a file twice as two modules, breaks dependency_overrides (fixed 977623d)")
    claim("worldstak", "rule_se6", "with MagicMock engines set optional async components to None",
          CLAUDE, 0.9, "e.g. engine.claim_repointer=None to avoid await-on-MagicMock errors")
    claim("worldstak", "rule_se7", "in sync test helpers use asyncio.run(coro), not run_until_complete",
          CLAUDE, 0.9, "prior async tests may have closed the loop")
    claim("worldstak", "stripe_replay_guard", "module-level singleton — pin fresh in-memory per test",
          CLAUDE, 1.0, "a prior module's lifespan can leak a DB-backed guard (fixed this session)")

    # --- M9B / schema -----------------------------------------------------
    claim("worldstak", "schema_mode_prod", "migrations", CLAUDE, 1.0,
          "blue-green compose pins WORLDSTAK_SCHEMA_MODE=migrations; startup does NO DDL, only validates head")
    claim("worldstak", "legacy_startup_ddl", "~100 ALTERs run only in create_all (dev/test) mode",
          CLAUDE, 1.0, "gated behind the flag; migrations mode fail-closed validates via assert_schema_current")

    # --- Governance -------------------------------------------------------
    claim("ovyero", "is", "governance gate — governs ACTIONS at commit/tool time",
          CLAUDE, 1.0, "pre-commit runs pytest + critics (e.g. crypto); distinct from Dynamic Data (which stores beliefs)")
    claim("ovyero", "vs_dynamic_data", "governance = referee (actions); dynamic data = memory (beliefs)",
          EZRA, 0.9, "different layers per AI_CODE_WORLDVIEW.md; complementary")

    # --- Bugs fixed this session -----------------------------------------
    claim("bug", "bridge_reason", "fixed — propagate from bridge claim through resolution to CanonicalRelationship",
          CLAUDE, 1.0, "archive canonization test; Claim gained optional bridge_reason")
    claim("bug", "relationship_matcher", "fixed — CanonicalEntity has no entity_ref; use entity_id",
          CLAUDE, 1.0, "matcher.py crashed external-adapter pilot reruns")

    # --- Relationships (graph edges) -------------------------------------
    claim("archive", "consumes", None, CLAUDE, 1.0, "Archive is a downstream consumer", obj="worldstak")
    claim("green", "promotes_to", None, EZRA, 1.0, "validated green promotes to blue", obj="blue")

    # --- A deliberate conflict to demonstrate resolution ------------------
    # Determined-style fact would reject this; as 'believed' it surfaces.
    ddb.assert_claim("worldstak", "head_commit", "977623d", source="claude-earlier",
                     confidence=0.7, observed_at="2026-07-14T18:00:00+00:00",
                     evidence="pre-M9B-lock promotion (superseded by 91790c1)")
    n += 1

    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Seed WorldStak facts into a Dynamic Data store")
    default_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worldstak.ddb")
    p.add_argument("--db", default=os.path.normpath(default_db))
    args = p.parse_args(argv)

    ddb = DynamicDataStore(args.db)
    count = seed(ddb)
    stats = ddb.stats()
    print(f"Seeded {count} claims into {args.db}")
    print(f"Store now holds: {stats}")
    print("\nTry:")
    print(f'  python dd_cli.py --db "{args.db}" resolve worldstak head_commit')
    print(f'  python dd_cli.py --db "{args.db}" history worldstak head_commit')
    print(f'  python dd_cli.py --db "{args.db}" conflicts')
    ddb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
