"""Differential Oracle -- retire 'backend-only drift'.

The failure class: a scenario passes against the fast in-memory fake but breaks
against the real backend (Postgres, an HTTP peer, a file store), because the two
implementations silently diverge on ordering, rounding, null handling, or a
dropped field. The in-memory suite is green, so nobody looks -- until it breaks
in production. This was ExampleApp's second-costliest class (EntityRow.to_domain
dropping merge state; confidence losing scale on persist): every instance was a
place where 'passes in memory' and 'is actually correct' came apart.

The oracle makes the two agree BY CONSTRUCTION: run the SAME scenario against
every backend and assert the results are equivalent. A divergence is a hard,
deterministic failure with a diff -- not a judgement. It is the runtime twin of
the Wiring Prover: the analyzer proves a capability is provided; this proves the
provided implementation matches the reference one.

No model. No network unless the caller's backend factory opens one. The
comparison is normal ``==`` after an optional caller-supplied ``normalize`` that
strips incidental fields (auto-ids, timestamps) the caller declares irrelevant.
"""

from __future__ import annotations

import asyncio


class DifferentialMismatch(AssertionError):
    """Raised when a scenario produces different results across backends.

    Subclasses AssertionError so pytest reports it as a normal test failure.
    """


def _run_maybe_async(fn, backend):
    result = fn(backend)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _resolve_backend(backend):
    """A backend entry is a zero-arg factory (preferred -- gives each run a
    fresh, isolated instance) or a ready instance. Callables are treated as
    factories; anything else is used as-is."""
    if callable(backend):
        return backend()
    return backend


def _format(name, ref_name, ref, mismatches):
    lines = [
        f"differential mismatch in scenario {name!r}:",
        f"  [{ref_name}] (reference) -> {ref!r}",
    ]
    for bname, val in mismatches:
        lines.append(f"  [{bname}] diverged        -> {val!r}")
    lines.append(
        "  the backends do not agree -- one implementation is wrong, or a "
        "field that differs incidentally needs a `normalize` to strip it.")
    return "\n".join(lines)


def run_differential(scenario, backends, *, normalize=None, name="scenario"):
    """Run ``scenario`` against every backend and assert equivalence.

    scenario:  callable(backend) -> result. May be sync or ``async``; an async
               scenario is driven to completion with ``asyncio.run``.
    backends:  dict[str, factory-or-instance] with 2+ entries. A factory (any
               zero-arg callable) is called once per run so each backend gets a
               fresh instance; the first key is the reference all others are
               compared against, so make it the one you trust (usually the real
               backend, or a hand-verified oracle).
    normalize: optional callable(result) -> comparable, applied to every result
               before comparison. Use it to drop incidental differences
               (auto-generated ids, timestamps) so only meaningful divergence
               fails.

    Returns the dict of (normalized) results on agreement. Raises
    ``DifferentialMismatch`` with a per-backend diff on any divergence.
    """
    if len(backends) < 2:
        raise ValueError("run_differential needs at least two backends to compare")

    results = {}
    for bname, backend in backends.items():
        raw = _run_maybe_async(scenario, _resolve_backend(backend))
        results[bname] = normalize(raw) if normalize is not None else raw

    items = list(results.items())
    ref_name, ref = items[0]
    mismatches = [(bname, val) for bname, val in items[1:] if val != ref]
    if mismatches:
        raise DifferentialMismatch(_format(name, ref_name, ref, mismatches))
    return results
