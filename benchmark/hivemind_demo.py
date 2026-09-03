#!/usr/bin/env python3
"""Hivemind demo: two AI agents sharing one Dynamic Data memory.

Shows the whole trust story end to end:
  1. Two agents (different trust) write to ONE store, each signing its claims.
  2. They disagree -> the conflict SURFACES (no overwrite, no race).
  3. An arbiter resolves by trust: the higher-trust agent's claim wins even
     though the low-trust agent claimed full confidence.
  4. Provenance traces a belief back to the agent that authored it.
  5. The append-only ledger is tamper-evident: verify passes, then a tamper is
     injected and the chain snaps visibly.

Run:  python hivemind_demo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dd-core"))

from dd_core import DynamicDataStore, signing  # noqa: E402


def line(title): print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main():
    ddb = DynamicDataStore(":memory:")

    line("1. Register two agents with Ed25519 identities and different trust")
    planner_key = signing.AgentKey.generate() if signing.available() else None
    scout_key = signing.AgentKey.generate() if signing.available() else None
    ddb.register_agent("planner", kind="ai", trust_ceiling=0.95,
                       public_key=planner_key.public_hex if planner_key else None)
    ddb.register_agent("scout", kind="ai", trust_ceiling=0.30,
                       public_key=scout_key.public_hex if scout_key else None)
    print("planner: trust 0.95   scout: trust 0.30   (signed:",
          signing.available(), ")")

    line("2. Both agents write to the SAME memory - and they DISAGREE")
    ddb.assert_claim("example_project", "head_commit", "abc1234", source="scout",
                     confidence=1.0, author_kind="ai", signer=scout_key,
                     evidence="scout skimmed the log")
    p_claim = ddb.assert_claim("example_project", "head_commit", "91790c1", source="planner",
                               confidence=0.8, author_kind="ai", signer=planner_key,
                               evidence="planner ran git log on blue")
    for c in ddb.conflicts():
        print(f"CONFLICT on {c['subject']}.{c['predicate']}: "
              f"chosen={c['chosen']['value']} (by {c['chosen']['source']}), "
              f"alt={[a['value']+' by '+a['source'] for a in c['alternatives']]}")

    line("3. Arbiter resolves by TRUST (not by raw confidence)")
    res = ddb.resolve("example_project", "head_commit")
    print(f"resolved -> {res.chosen.value}  (source: {res.chosen.source})")
    print(f"why: {res.reason}")
    print("note: scout claimed confidence 1.0 but is trusted only to 0.30, so")
    print("      planner's 0.80 wins. Low-trust agents can't shout over trusted ones.")

    line("4. Provenance: trace a belief back to its author")
    prov = ddb.provenance(p_claim.claim_id)
    print(f"claim {prov['claim_id'][:12]}... value={prov['value']} "
          f"authored by '{prov['source']}' - evidence: {prov['evidence']}")

    line("5. Tamper-evidence: the ledger is a hash chain")
    v = ddb.verify_chain(check_signatures=True)
    print(f"verify (before tamper): OK={v['ok']}  {v['detail']}  entries={v['entries']}")
    # Inject a tamper: rewrite scout's claim value directly in the DB.
    ddb._conn.execute("UPDATE claims SET value_json = ? WHERE source = 'scout' AND predicate='head_commit'",
                      ('"MALICIOUS"',))
    ddb._conn.commit()
    v2 = ddb.verify_chain()
    print(f"verify (after tamper):  OK={v2['ok']}  {v2['detail']}")
    print("\nThe chain snapped visibly - you can detect that history was rewritten,")
    print("without trusting the store. Integrity (chain) + authenticity (signatures)")
    print("= a hivemind memory you can audit.")
    ddb.close()


if __name__ == "__main__":
    main()
