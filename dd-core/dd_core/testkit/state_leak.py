"""State-Leak Detector -- retire 'order-dependent / nondeterministic tests'.

The failure class: a test passes only because of state a PRIOR test left behind
(a mutated env var, a module-level singleton primed once), or leaves state that
makes a LATER test flaky. It passes in the full suite and fails in isolation, or
vice-versa -- the 'harness pollution' that cost this project real triage time
(the Stripe webhook replay guard singleton leaking across tests is the textbook
case). The bug is invisible per-test; it only exists in the ORDER.

This autouse detector snapshots a bounded, deterministic slice of global state
around every test and reports any test that leaves it changed without cleanup.
The default slice is ``os.environ``; projects register more watchers (a module
singleton, a class registry) via ``watch()``.

Non-breaking by default: a leak is a WARNING, so adopting the plugin never turns
a green suite red overnight. Flip ``--dd-leak-fail`` (or ``dd_leak_fail=true`` in
config) once the suite is clean to make new leaks hard failures.
"""

from __future__ import annotations

import copy
import os
import warnings

# Env keys the test harness itself owns and rewrites every test -- comparing
# them would flag pytest's own bookkeeping as a leak. Excluded from the snapshot.
_ENV_IGNORE = {"PYTEST_CURRENT_TEST"}

# Project-registered watchers: (label, snapshot_callable) where the callable
# returns a *comparable, copyable* value representing the watched state now.
_WATCHERS: list[tuple[str, object]] = []


def watch(label: str, snapshot):
    """Register an extra piece of global state to guard around every test.

    snapshot: a zero-arg callable returning the current value (it will be
    deep-copied and compared with ==). Example -- guard a module singleton::

        from dd_core.testkit.state_leak import watch
        watch("stripe_replay_guard", lambda: sorted(mymod.REPLAY_GUARD.seen))
    """
    _WATCHERS.append((label, snapshot))


def reset_watchers():
    """Drop all registered watchers (env is always watched). For tests of the
    detector itself and for clean re-registration."""
    _WATCHERS.clear()


def _snapshot():
    snap = {"env": {k: v for k, v in os.environ.items() if k not in _ENV_IGNORE}}
    for label, fn in _WATCHERS:
        try:
            snap[label] = copy.deepcopy(fn())
        except Exception:
            snap[label] = ("<unsnapshotable>",)
    return snap


def _diff(before: dict, after: dict) -> list[str]:
    leaks = []
    for key in before:
        if before[key] != after.get(key):
            if key == "env":
                b, a = before["env"], after["env"]
                added = sorted(set(a) - set(b))
                removed = sorted(set(b) - set(a))
                changed = sorted(k for k in set(b) & set(a) if b[k] != a[k])
                parts = []
                if added:
                    parts.append(f"env added {added}")
                if removed:
                    parts.append(f"env removed {removed}")
                if changed:
                    parts.append(f"env changed {changed}")
                if parts:
                    leaks.append("; ".join(parts))
            else:
                leaks.append(f"watched state {key!r} changed")
    return leaks


def check_leak(before: dict, after: dict):
    """Pure comparison used by the fixture and by the detector's own tests.
    Returns a list of human-readable leak descriptions (empty if clean)."""
    return _diff(before, after)
