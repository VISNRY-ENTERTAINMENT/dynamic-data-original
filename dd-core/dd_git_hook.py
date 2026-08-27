#!/usr/bin/env python3
"""dd-git-hook — auto-assert a claim for every git commit. Zero AI involvement.

This is the answer to "force automatic logging without depending on any AI
model": don't rely on a model's discretion for facts a script can capture
directly. Wired into .git/hooks/post-commit, this fires on EVERY commit --
interactive, scripted, or made by any AI assistant (Claude, GPT, Cursor,
whatever) -- and cannot be skipped by a model "deciding" not to log, because no
model is involved in the write path at all. Pure git + stdlib, no third-party
service.

Records: repo, branch, commit sha, author, message, files changed. Source is
"git" (not an AI agent), author_kind "system", confidence 1.0 -- ground truth
straight from git itself.

Usage (called by the git post-commit hook, not run directly):
    python dd_git_hook.py --db <path-to-.ddb> --repo <repo-name>
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core import DynamicDataStore  # noqa: E402


def _git(args: list[str]) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=False).stdout.strip()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--repo", required=True)
    args = p.parse_args(argv)

    sha = _git(["rev-parse", "HEAD"])
    if not sha:
        return 0  # not in a git repo / no commits yet -- never block the commit
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    author = _git(["log", "-1", "--format=%an <%ae>"])
    message = _git(["log", "-1", "--format=%s"])
    files = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", sha]).splitlines()

    try:
        ddb = DynamicDataStore(args.db)
        ddb.assert_claim(
            args.repo, "head_commit", sha[:12],
            source="git", confidence=1.0, author_kind="system",
            evidence=f"branch={branch} author={author} msg={message!r} "
                     f"files_changed={len(files)}",
            dims={"full_sha": sha, "branch": branch, "author": author,
                  "message": message, "files_changed": files[:50]},
        )
        ddb.close()
    except Exception as e:
        # Never block a commit because logging failed.
        sys.stderr.write(f"dd-git-hook: warning: could not log commit ({e})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
