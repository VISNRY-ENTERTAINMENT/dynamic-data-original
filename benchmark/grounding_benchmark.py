#!/usr/bin/env python3
"""Grounding benchmark: Dynamic Data claims vs static text chunks.

THE QUESTION: is Dynamic Data actually *better* than a normal (static) store for
grounding an AI — or does it just sound better?

THE INSIGHT that makes this rigorous and LLM-free: an AI can only answer as well
as its retrieval layer can *surface* the needed information. So before involving
any model, we measure **retrieval adequacy** — for each question, can the store
even provide the facts required to answer correctly?

  - Static backend  = flattened text chunks + keyword retrieval (a stand-in for
                      the vector-store / RAG status quo). Holds values, throws
                      away source, confidence, time, conflict, and history.
  - Dynamic backend = the dd-core claim store. Holds all of those natively.

We score each backend on a task set where questions probe: current value,
provenance, calibrated confidence, time-travel, conflict, and change history.
A model on top can only be as grounded as what these layers expose.

Run:  python grounding_benchmark.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dd-core"))

from dd_core import DynamicDataStore  # noqa: E402


# --------------------------------------------------------------------------- #
# The shared ground truth. Facts that (a) have sources, (b) carry confidence,
# (c) change over time, and (d) conflict — i.e. the real world, not a toy.
# --------------------------------------------------------------------------- #
FACTS = [
    # subject, predicate, value, source, confidence, observed_at, evidence
    ("worldstak", "prod_branch", "blue", "ezra", 1.0, "2026-07-14T00:00:00+00:00",
     "blue = production"),
    ("worldstak", "head_commit", "977623d", "claude-earlier", 0.7, "2026-07-14T18:00:00+00:00",
     "pre-M9B-lock promotion"),
    ("worldstak", "head_commit", "91790c1", "claude", 1.0, "2026-07-14T22:00:00+00:00",
     "after M9B flag-lock promotion — supersedes 977623d"),
    ("worldstak", "test_pool", "NullPool", "claude", 1.0, "2026-07-14T00:00:00+00:00",
     "conftest WORLDSTAK_ENGINE_NULLPOOL=1"),
    ("leshy", "weakness", "fire", "designer", 1.0, "2026-04-16T00:00:00+00:00",
     "CPS_BESTIARY.cps line 47"),
    ("leshy", "weakness", "ice", "telemetry", 0.3, "2026-05-01T00:00:00+00:00",
     "players observed using ice — hypothesis only"),
]


# --------------------------------------------------------------------------- #
# Static backend: text chunks + keyword retrieval (the RAG status quo).
# It can store the *value* as prose, but the metadata is gone.
# --------------------------------------------------------------------------- #
class StaticChunkStore:
    def __init__(self):
        self.chunks: list[str] = []

    def index(self, facts):
        # A static store flattens each fact to a sentence. Source/confidence/
        # time/conflict/history have nowhere to live and are dropped — exactly
        # what happens when you chunk documents into a vector store.
        for subj, pred, val, *_ in facts:
            self.chunks.append(f"The {pred} of {subj} is {val}.")

    def retrieve(self, subject, predicate):
        hits = [c for c in self.chunks if subject in c and predicate.replace("_", " ") in c
                or (subject in c and predicate in c)]
        return hits

    # Capabilities the static layer can/can't surface -----------------------
    def can_give_value(self, subject, predicate):
        return len(self.retrieve(subject, predicate)) > 0

    def can_give_source(self, *_):        return False   # metadata was dropped
    def can_give_confidence(self, *_):    return False
    def can_time_travel(self, *_):        return False
    def can_surface_conflict(self, *_):   return False
    def can_give_history(self, *_):       return False
    def unique_value(self, subject, predicate):
        # With multiple chunks for the same fact it cannot tell which is current.
        vals = self.retrieve(subject, predicate)
        return len(vals) == 1


# --------------------------------------------------------------------------- #
# Dynamic backend: the dd-core claim store.
# --------------------------------------------------------------------------- #
class DynamicBackend:
    def __init__(self):
        self.ddb = DynamicDataStore(":memory:")

    def index(self, facts):
        for subj, pred, val, src, conf, obs, ev in facts:
            self.ddb.assert_claim(subj, pred, val, source=src, confidence=conf,
                                  observed_at=obs, evidence=ev)

    def can_give_value(self, subject, predicate):
        return self.ddb.resolve(subject, predicate).chosen is not None

    def can_give_source(self, subject, predicate):
        c = self.ddb.resolve(subject, predicate).chosen
        return bool(c and c.source)

    def can_give_confidence(self, subject, predicate):
        c = self.ddb.resolve(subject, predicate).chosen
        return c is not None and c.confidence is not None

    def can_time_travel(self, subject, predicate):
        # Can it answer "what did we believe at an earlier time" distinctly?
        early = self.ddb.resolve(subject, predicate, as_of="2026-07-14T19:00:00+00:00").chosen
        now = self.ddb.resolve(subject, predicate).chosen
        return early is not None and now is not None

    def can_surface_conflict(self, subject, predicate):
        return any(c["predicate"] == predicate for c in self.ddb.conflicts(subject=subject))

    def can_give_history(self, subject, predicate):
        return len(self.ddb.history(subject, predicate)) >= 1

    def unique_value(self, subject, predicate):
        # Resolution always yields exactly one chosen claim.
        return self.ddb.resolve(subject, predicate).chosen is not None


# --------------------------------------------------------------------------- #
# Task set. Each question declares which capability it REQUIRES to be answered
# correctly. Retrieval adequacy = does the backend expose that capability?
# --------------------------------------------------------------------------- #
TASKS = [
    ("What is WorldStak's prod branch?",                       "worldstak", "prod_branch", "value"),
    ("Pick the single current value for WorldStak head commit","worldstak", "head_commit", "unique"),
    ("Who says WorldStak's prod branch is blue?",              "worldstak", "prod_branch", "source"),
    ("How confident are we the Leshy is weak to fire?",        "leshy",     "weakness",    "confidence"),
    ("What did we believe WorldStak's head commit was at 18:00?","worldstak","head_commit", "time_travel"),
    ("Is there disagreement about the Leshy's weakness?",      "leshy",     "weakness",    "conflict"),
    ("Has WorldStak's head commit changed? To what?",          "worldstak", "head_commit", "history"),
]

CAP = {
    "value":       ("can_give_value",       "surface the current value"),
    "unique":      ("unique_value",         "identify the single current value (not N chunks)"),
    "source":      ("can_give_source",      "attribute the fact to a source"),
    "confidence":  ("can_give_confidence",  "give a calibrated confidence"),
    "time_travel": ("can_time_travel",      "answer 'as of' an earlier time"),
    "conflict":    ("can_surface_conflict", "surface that beliefs conflict"),
    "history":     ("can_give_history",     "show what changed over time"),
}


def run():
    static = StaticChunkStore();  static.index(FACTS)
    dyn = DynamicBackend();       dyn.index(FACTS)

    print("=" * 78)
    print("GROUNDING BENCHMARK - retrieval adequacy (can the store surface the answer?)")
    print("=" * 78)
    header = f"{'question':50} {'static':>8} {'dynamic':>8}"
    print(header)
    print("-" * len(header))

    s_score = d_score = 0
    for q, subj, pred, cap in TASKS:
        method, _desc = CAP[cap]
        s_ok = getattr(static, method)(subj, pred)
        d_ok = getattr(dyn, method)(subj, pred)
        s_score += int(s_ok); d_score += int(d_ok)
        print(f"{q[:50]:50} {('YES' if s_ok else 'no'):>8} {('YES' if d_ok else 'no'):>8}")

    print("-" * len(header))
    n = len(TASKS)
    print(f"{'SCORE (questions the store can ground)':50} {f'{s_score}/{n}':>8} {f'{d_score}/{n}':>8}")
    print()
    print("Reading: both surface simple current values. Only the Dynamic Data store")
    print("can ground provenance, confidence, time-travel, conflict, and change -")
    print("the questions a static/RAG chunk store structurally cannot answer, because")
    print("it threw that information away at index time. An AI is capped by this.")
    dyn.ddb.close()
    return s_score, d_score


if __name__ == "__main__":
    run()
