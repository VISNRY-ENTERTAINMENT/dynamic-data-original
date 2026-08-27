"""The single pytest plugin for the dd_core testkit.

Enable per-run with ``pytest -p dd_core.testkit.plugin`` or permanently by
adding ``pytest_plugins = ["dd_core.testkit.plugin"]`` to a top-level conftest.

It provides:
  * the ``differential`` fixture -- a thin handle onto ``run_differential`` so a
    test reads ``differential(scenario, {...})``.
  * the autouse State-Leak Detector -- warns (or, with ``--dd-leak-fail``, fails)
    on a test that mutates guarded global state without cleaning up.

Importing this module does not require pytest at collection time for the core
helpers; the fixture registration below is only reached when pytest loads the
plugin, so plain ``import dd_core.testkit`` stays dependency-free.
"""

from __future__ import annotations

import warnings

import pytest

from .differential import run_differential
from .state_leak import _snapshot, check_leak


def pytest_addoption(parser):
    group = parser.getgroup("dd_core testkit")
    group.addoption(
        "--dd-leak-fail", action="store_true", default=False,
        help="make State-Leak Detector findings hard test failures (default: warn)",
    )


@pytest.fixture(autouse=True)
def _dd_state_leak(request):
    """Snapshot guarded global state (env + registered watchers) around every
    test; report anything left changed. Non-breaking by default (warns)."""
    before = _snapshot()
    yield
    leaks = check_leak(before, _snapshot())
    if not leaks:
        return
    msg = (f"STATE LEAK in {request.node.nodeid}: "
           f"{'; '.join(leaks)} -- this test mutates shared global state without "
           f"restoring it, a source of order-dependent flakes")
    if request.config.getoption("--dd-leak-fail"):
        pytest.fail(msg, pytrace=False)
    else:
        warnings.warn(msg, stacklevel=2)


@pytest.fixture
def differential():
    """Return the differential runner. Usage inside a test::

        def test_matches(differential):
            differential(
                lambda backend: backend.query(...),
                {"postgres": make_pg, "memory": make_mem},
                normalize=lambda r: {k: v for k, v in r.items() if k != "id"},
            )

    A divergence fails the test with a per-backend diff.
    """
    return run_differential
