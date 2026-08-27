#!/usr/bin/env python3
"""dd-reflex-hook — post-commit entry point for the reflex loop.

Wire this (backgrounded, fail-soft) into .git/hooks/post-commit, right after
dd_git_hook.py. It loads the project's reflex.config.json and runs the two-tier
loop (Tier 1 per commit, Tier 2 every N major commits). Never blocks a commit.

    # in .git/hooks/post-commit, after the dd_git_hook.py line:
    if [ "$REFLEX_DISABLE" != "1" ]; then
      ( python "<path>/dd_reflex_hook.py" --config "<repo>/reflex.config.json" \
          >/dev/null 2>&1 & ) || true
    fi

Zero-arg form uses $REFLEX_CONFIG or ./reflex.config.json.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core.recursive_improvement.config import ReflexConfig  # noqa: E402
from dd_core.recursive_improvement.runner import run_post_commit  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    p.add_argument("--sha", default=None)
    args = p.parse_args(argv)
    try:
        cfg = ReflexConfig.load(args.config)
        return run_post_commit(cfg, args.sha)
    except Exception as e:  # never surface to the commit
        print(f"[reflex] hook error (ignored): {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
