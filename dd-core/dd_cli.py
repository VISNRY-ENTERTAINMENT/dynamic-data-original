#!/usr/bin/env python3
"""dd — command-line access to a Dynamic Data claim store.

Examples:
    python dd_cli.py --db project.ddb assert myproject db_pool NullPool \\
        --source ezra --confidence 1.0 --evidence "conftest NullPool=1"
    python dd_cli.py --db project.ddb resolve exampleapp test_pool
    python dd_cli.py --db project.ddb history bob role
    python dd_cli.py --db project.ddb conflicts
    python dd_cli.py --db project.ddb search --text NullPool
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core import DynamicDataStore, Profile  # noqa: E402


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dd", description="Dynamic Data claim store")
    p.add_argument("--db", default="dynamic_data.ddb", help="path to the .ddb file")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("assert", help="record a claim")
    a.add_argument("subject")
    a.add_argument("predicate")
    a.add_argument("value", nargs="?", default=None)
    a.add_argument("--obj", default=None, help="target entity (makes it a relationship)")
    a.add_argument("--source", default="cli")
    a.add_argument("--confidence", type=float, default=1.0)
    a.add_argument("--observed-at", default=None)
    a.add_argument("--evidence", default="")
    a.add_argument("--profile", choices=[p.value for p in Profile], default="believed")

    r = sub.add_parser("resolve", help="current truth for subject+predicate")
    r.add_argument("subject")
    r.add_argument("predicate")
    r.add_argument("--as-of", default=None)

    h = sub.add_parser("history", help="full timeline for subject+predicate")
    h.add_argument("subject")
    h.add_argument("predicate")

    c = sub.add_parser("conflicts", help="list surfaced conflicts")
    c.add_argument("--subject", default=None)

    s = sub.add_parser("search", help="find claims")
    s.add_argument("--subject", default=None)
    s.add_argument("--predicate", default=None)
    s.add_argument("--source", default=None)
    s.add_argument("--text", default=None)

    sub.add_parser("subjects", help="list all subjects")
    sub.add_parser("stats", help="store statistics")

    rel = sub.add_parser("relationships", help="resolved relationships from a subject")
    rel.add_argument("subject")

    args = p.parse_args(argv)
    ddb = DynamicDataStore(args.db)

    if args.cmd == "assert":
        claim = ddb.assert_claim(
            args.subject, args.predicate, args.value, obj=args.obj,
            source=args.source, confidence=args.confidence,
            observed_at=args.observed_at, evidence=args.evidence,
            profile=Profile(args.profile),
        )
        _print(claim.to_dict())
    elif args.cmd == "resolve":
        _print(ddb.resolve(args.subject, args.predicate, as_of=args.as_of).to_dict())
    elif args.cmd == "history":
        _print([c.to_dict() for c in ddb.history(args.subject, args.predicate)])
    elif args.cmd == "conflicts":
        _print(ddb.conflicts(subject=args.subject))
    elif args.cmd == "search":
        _print([c.to_dict() for c in ddb.search(
            subject=args.subject, predicate=args.predicate,
            source=args.source, text=args.text)])
    elif args.cmd == "subjects":
        _print(ddb.subjects())
    elif args.cmd == "stats":
        _print(ddb.stats())
    elif args.cmd == "relationships":
        _print([c.to_dict() for c in ddb.relationships(args.subject)])

    ddb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
