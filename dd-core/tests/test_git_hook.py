"""Test dd_git_hook.py: automatic, model-agnostic commit logging.

No AI is involved in this path -- the point is that it can't be skipped by a
model choosing not to call a tool. Runs the hook script against a real throwaway
git repo and checks the resulting claim.
"""

import os
import subprocess
import sys
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOOK = os.path.join(_HERE, "..", "dd_git_hook.py")
sys.path.insert(0, os.path.join(_HERE, ".."))

from dd_core import DynamicDataStore  # noqa: E402


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
def test_git_hook_logs_a_commit(tmp_path):
    repo = tmp_path / "throwaway_repo"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "t@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "f.txt").write_text("hello")
    _run(["git", "add", "f.txt"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "first commit"], cwd=repo)

    db = str(tmp_path / "hooktest.ddb")
    r = _run([sys.executable, _HOOK, "--db", db, "--repo", "throwaway"], cwd=repo)
    assert r.returncode == 0, r.stderr

    ddb = DynamicDataStore(db)
    res = ddb.resolve("throwaway", "head_commit")
    assert res.chosen is not None
    assert res.chosen.source == "git"
    assert res.chosen.author_kind == "system"
    assert "first commit" in res.chosen.evidence
    ddb.close()


def test_hook_never_blocks_outside_a_git_repo(tmp_path):
    """No .git dir -> the hook exits 0 (fail-soft), never blocks a commit."""
    empty = tmp_path / "not_a_repo"
    empty.mkdir()
    db = str(tmp_path / "x.ddb")
    r = _run([sys.executable, _HOOK, "--db", db, "--repo", "x"], cwd=empty)
    assert r.returncode == 0
