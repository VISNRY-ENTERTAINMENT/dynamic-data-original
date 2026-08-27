"""Install Dynamic Data git hooks into the current repo.

Installs two hooks idempotently (chains with any existing content):
  post-commit        -- runs department probes + Tier-1 reflex after each commit
  prepare-commit-msg -- injects REFLEX WARNINGS block when MAJOR findings are open

Run from the root of the repo you want to govern:
    python /path/to/dd-core/../install_hooks.py
"""
from __future__ import annotations

import os
import stat
import sys

_HOOKS_DIR = os.path.join(os.getcwd(), ".git", "hooks")

# Absolute path to this file's directory (repo root of dynamic-data)
_DD_ROOT = os.path.dirname(os.path.abspath(__file__))
_DD_CORE = os.path.join(_DD_ROOT, "dd-core")

# The reflex runner entry point
_POST_COMMIT_CMD = (
    f'{sys.executable} -c '
    f'"import sys; sys.path.insert(0, r\'{_DD_CORE}\'); '
    f'from dd_core.recursive_improvement.runner import run_post_commit; '
    f'sys.exit(run_post_commit())"'
)

_PREPARE_MSG_CMD = (
    f'{sys.executable} '
    f'"{os.path.join(_DD_CORE, "dd_core", "recursive_improvement", "prepare_commit_msg.py")}" '
    f'"$1" "$2" "$3"'
)


def _make_hook(hook_path: str, cmd: str, hook_name: str):
    """Write or chain the hook. Idempotent: won't double-add the same command."""
    if os.path.exists(hook_path):
        with open(hook_path, encoding="utf-8") as fh:
            existing = fh.read()
        if cmd in existing:
            print(f"  {hook_name}: already installed (no change)")
            return
        # Chain: append after existing content
        with open(hook_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n# --- dynamic-data {hook_name} ---\n{cmd}\n")
        print(f"  {hook_name}: chained into existing hook")
    else:
        with open(hook_path, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\n# dynamic-data {hook_name}\n{cmd}\n")
        print(f"  {hook_name}: installed")

    # Ensure executable
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def main():
    if not os.path.isdir(_HOOKS_DIR):
        print(f"ERROR: no .git/hooks directory found at {_HOOKS_DIR}")
        print("Run this script from the root of a git repository.")
        sys.exit(1)

    print(f"Installing hooks into {_HOOKS_DIR}")
    _make_hook(os.path.join(_HOOKS_DIR, "post-commit"), _POST_COMMIT_CMD, "post-commit")
    _make_hook(os.path.join(_HOOKS_DIR, "prepare-commit-msg"), _PREPARE_MSG_CMD, "prepare-commit-msg")
    print("Done.")


if __name__ == "__main__":
    main()
