"""prepare-commit-msg hook script.

Git calls this with the commit message file as argv[1] immediately after the
message is drafted but before the commit is finalised. We read the gap ledger
and, when there are MAJOR (high/critical) open findings, append a REFLEX
WARNINGS block so the committing AI (or human) sees it in context.

Non-blocking: exits 0 always. The warning is informational.

Usage (git hook):
    #!/usr/bin/env python
    import sys, subprocess
    subprocess.run([sys.executable, "<path>/prepare_commit_msg.py"] + sys.argv[1:])
"""
from __future__ import annotations

import os
import sys


def _load_cfg():
    """Walk up from CWD to find reflex.config.json and load it."""
    here = os.path.abspath(os.getcwd())
    for _ in range(8):
        candidate = os.path.join(here, "reflex.config.json")
        if os.path.exists(candidate):
            # Ensure dd_core is importable
            import json
            with open(candidate, encoding="utf-8") as fh:
                raw = json.load(fh)
            dd_core_path = raw.get("dd_core_path", "dd-core")
            if not os.path.isabs(dd_core_path):
                dd_core_path = os.path.join(here, dd_core_path)
            if dd_core_path not in sys.path:
                sys.path.insert(0, dd_core_path)
            try:
                from dd_core.recursive_improvement.config import ReflexConfig
                return ReflexConfig.load(candidate)
            except Exception:
                return None
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    msg_file = sys.argv[1]
    if not os.path.exists(msg_file):
        sys.exit(0)

    cfg = _load_cfg()
    if cfg is None:
        sys.exit(0)

    try:
        from dd_core import DynamicDataStore
        from dd_core.recursive_improvement.gate import triage, render_escalation

        ddb = DynamicDataStore(cfg.abspath(cfg.gap_db))
        try:
            t = triage(ddb, cfg.floor, "arch.gap:")
        finally:
            try:
                ddb.close()
            except Exception:
                pass

        act_now = t["act_now"]
        backlog = t["backlog"]
        recommended = t["recommended"]

        if not act_now and not backlog:
            sys.exit(0)

        warning = render_escalation(act_now, backlog, recommended)
        block = (
            "\n\n# --- REFLEX WARNINGS (auto-injected, informational) ---\n"
            "# The gap ledger has open findings. Address or 'Closes arch.gap:<slug>'.\n"
            + "\n".join(f"# {line}" for line in warning.splitlines())
            + "\n# --- end REFLEX WARNINGS ---\n"
        )

        with open(msg_file, "a", encoding="utf-8") as fh:
            fh.write(block)

    except Exception:
        pass  # never block a commit

    sys.exit(0)


if __name__ == "__main__":
    main()
