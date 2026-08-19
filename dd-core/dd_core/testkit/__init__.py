"""dd_core.testkit -- the runtime face of the deterministic oracles.

Where the analyzers in ``dd_core.recursive_improvement`` compute facts about
code at rest, the testkit catches failure classes that only appear when code
RUNS: a backend that passes in-memory but breaks against the real store
(Differential Oracle), a test that only passes because of state left by the
test before it (State-Leak Detector), a change verified against the whole slow
suite instead of the tests that actually cover it (change-scoped selection).

Everything here is one pytest plugin. Enable it with::

    pytest -p dd_core.testkit.plugin

or add ``"dd_core.testkit.plugin"`` to ``pytest_plugins`` in a conftest. The
core helpers are also importable directly, with no pytest dependency, so they
can run inside plain scripts or other harnesses.

    from dd_core.testkit import run_differential, DifferentialMismatch
"""

from __future__ import annotations

from .differential import DifferentialMismatch, run_differential

__all__ = ["run_differential", "DifferentialMismatch"]
