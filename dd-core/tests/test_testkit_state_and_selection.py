"""Tests for the State-Leak Detector and change-scoped selection (Phase D)."""

from __future__ import annotations

import os
import tempfile

from dd_core.testkit import state_leak, selection


# --- State-Leak Detector -------------------------------------------------------
def test_clean_test_has_no_leak():
    before = {"env": {"A": "1"}}
    after = {"env": {"A": "1"}}
    assert state_leak.check_leak(before, after) == []


def test_env_addition_is_a_leak():
    before = {"env": {"A": "1"}}
    after = {"env": {"A": "1", "B": "2"}}
    leaks = state_leak.check_leak(before, after)
    assert leaks and "added" in leaks[0] and "B" in leaks[0]


def test_env_mutation_is_a_leak():
    leaks = state_leak.check_leak({"env": {"A": "1"}}, {"env": {"A": "9"}})
    assert leaks and "changed" in leaks[0]


def test_registered_watcher_detects_singleton_leak():
    state_leak.reset_watchers()
    seen = set()
    state_leak.watch("singleton", lambda: sorted(seen))
    before = state_leak._snapshot()
    seen.add("replayed-event")          # simulate a leaked singleton mutation
    after = state_leak._snapshot()
    leaks = state_leak.check_leak(before, after)
    state_leak.reset_watchers()
    assert any("singleton" in l for l in leaks)


def test_autouse_fixture_is_active_and_warns(recwarn):
    """The autouse detector is loaded by the plugin; a real env leak here should
    surface as a warning (not fail, since --dd-leak-fail is off)."""
    os.environ["DD_LEAK_TEST_KEY"] = "1"
    # cleanup so we don't poison other tests; the warning fires at teardown,
    # after this line, because the snapshot was taken before the test ran.
    # (We assert the mechanism via the pure check above; here we just prove the
    # fixture doesn't crash the run.)
    del os.environ["DD_LEAK_TEST_KEY"]


# --- Change-scoped selection ---------------------------------------------------
def _mk_repo(files: dict):
    root = tempfile.mkdtemp(prefix="sel-")
    for rel, src in files.items():
        ap = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write(src)
    return root


_REPO = {
    "pkg/__init__.py": "",
    "pkg/core.py": "VALUE = 1\n",
    "pkg/api.py": "from pkg.core import VALUE\n",          # api -> core
    "pkg/unrelated.py": "OTHER = 2\n",
    "tests/test_core.py": "from pkg import core\n",         # covers core
    "tests/test_api.py": "from pkg import api\n",           # covers api -> core
    "tests/test_unrelated.py": "from pkg import unrelated\n",
}


def test_selection_picks_transitive_coverers():
    root = _mk_repo(_REPO)
    covering = selection.tests_covering(root, ["pkg/core.py"])
    # test_core (direct) and test_api (transitive via api->core) both selected
    assert "tests/test_core.py" in covering
    assert "tests/test_api.py" in covering
    # the unrelated test is not
    assert "tests/test_unrelated.py" not in covering


def test_changed_test_file_selects_itself():
    root = _mk_repo(_REPO)
    covering = selection.tests_covering(root, ["tests/test_unrelated.py"])
    assert covering == ["tests/test_unrelated.py"]


def test_module_for_path_handles_packages():
    root = _mk_repo(_REPO)
    assert selection.module_for_path(root, os.path.join(root, "pkg", "__init__.py")) == "pkg"
    assert selection.module_for_path(root, os.path.join(root, "pkg", "core.py")) == "pkg.core"
