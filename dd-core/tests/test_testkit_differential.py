"""Tests for the Differential Oracle (dd_core.testkit.differential)."""

from __future__ import annotations

import pytest

from dd_core.testkit import DifferentialMismatch, run_differential


# --- two toy "backends": a reference store and one with a real drift bug. ------
class ReferenceStore:
    """The correct backend: keeps confidence at full scale, retains all fields."""

    def upsert_and_read(self, name, confidence):
        return {"name": name, "confidence": round(confidence, 6), "merged": True}


class DriftyStore:
    """A backend with the exact ExampleProject-style drift: it silently drops a
    field (`merged`) and rounds confidence to 2dp on persist."""

    def upsert_and_read(self, name, confidence):
        return {"name": name, "confidence": round(confidence, 2)}


def _scenario(backend):
    return backend.upsert_and_read("acme", 0.123456)


def test_agreeing_backends_pass():
    results = run_differential(
        _scenario,
        {"reference": ReferenceStore, "clone": ReferenceStore},
        name="upsert_and_read",
    )
    assert results["reference"] == results["clone"]


def test_drift_is_caught_with_a_diff():
    with pytest.raises(DifferentialMismatch) as exc:
        run_differential(
            _scenario,
            {"reference": ReferenceStore, "drifty": DriftyStore},
            name="upsert_and_read",
        )
    msg = str(exc.value)
    assert "drifty" in msg and "reference" in msg
    assert "upsert_and_read" in msg


def test_normalize_strips_incidental_fields():
    """If the only divergence is a field the caller declares irrelevant,
    normalize removes it and the backends agree."""

    def normalize(r):
        return {k: v for k, v in r.items() if k in ("name",)}

    results = run_differential(
        _scenario,
        {"reference": ReferenceStore, "drifty": DriftyStore},
        normalize=normalize,
        name="upsert_and_read",
    )
    assert results["reference"] == {"name": "acme"}


def test_async_scenario_is_driven_to_completion():
    class AsyncStore:
        async def read(self):
            return {"ok": True}

    async def scenario(backend):
        return await backend.read()

    results = run_differential(
        scenario, {"a": AsyncStore, "b": AsyncStore}, name="async"
    )
    assert results["a"] == {"ok": True}


def test_factory_gives_each_backend_a_fresh_instance():
    made = []

    def factory():
        obj = ReferenceStore()
        made.append(obj)
        return obj

    run_differential(_scenario, {"one": factory, "two": factory})
    assert len(made) == 2 and made[0] is not made[1]


def test_fewer_than_two_backends_is_an_error():
    with pytest.raises(ValueError):
        run_differential(_scenario, {"only": ReferenceStore})


def test_differential_fixture_is_available(differential):
    differential(_scenario, {"a": ReferenceStore, "b": ReferenceStore})
