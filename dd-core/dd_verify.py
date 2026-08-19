#!/usr/bin/env python3
"""dd-verify — independently verify a Dynamic Data ledger's tamper-evidence.

This proves the audit trail yourself without trusting the store: it walks
the append-only hash chain and reports whether any
past claim was altered (integrity), and optionally checks Ed25519 signatures
against registered agent public keys (authenticity).

    python dd_verify.py --db project.ddb                # integrity
    python dd_verify.py --db project.ddb --signatures   # integrity + authenticity

Exit code 0 = intact, 1 = tampering detected.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core import DynamicDataStore  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Verify a Dynamic Data ledger")
    p.add_argument("--db", required=True, help="path to the .ddb ledger")
    p.add_argument("--signatures", action="store_true",
                   help="also verify Ed25519 signatures (needs cryptography)")
    args = p.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"error: no such ledger: {args.db}")
        return 1

    ddb = DynamicDataStore(args.db)
    res = ddb.verify_chain(check_signatures=args.signatures)
    ddb.close()

    print(f"ledger:   {args.db}")
    print(f"entries:  {res['entries']}")
    if res["ok"]:
        print(f"head:     {res.get('head')}")
        print(f"STATUS:   OK — {res['detail']}")
        return 0
    print(f"STATUS:   TAMPERED — {res['detail']}")
    print(f"broken at ledger seq: {res['broken_at']}")
    print("\nThe chain snapped: a past claim was altered, so its hash no longer")
    print("matches and every entry after it is invalidated.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
