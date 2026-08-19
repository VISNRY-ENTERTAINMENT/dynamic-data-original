"""Deterministic recorder: reviewer/auditor JSON -> deduped, append-only gap
claims. NO model in this path -- the model already judged upstream.

Two layers of dedup, both deterministic:

1. EXACT slug: subject is "<prefix><slug>". Same slug already open -> refresh
   severity, don't duplicate. Previously closed and seen again -> re-open (a
   regression signal).

2. SEMANTIC: a later audit re-describes the SAME issue with a DIFFERENT slug
   (the real bug this closes). Before creating a genuinely-new subject, its
   token signature is compared against every existing finding. A strong,
   same-area match is treated as a re-sighting of the canonical one via
   `same_as` -- NOT a new open finding. Every suppression is returned so the
   caller can log it: dedup must never SILENTLY hide a real finding, or the
   loop would start missing things to avoid nagging.
"""

from __future__ import annotations

import json
import re

_OPEN_STATES = {"open", "escalated"}
_CLOSED_STATES = {"fixed", "wontfix", "accepted"}
_VALID_SEV = {"low", "medium", "high", "critical"}

# Similarity thresholds (deterministic). Conservative on purpose: a false merge
# hides a real finding, which is worse than an extra dup. Requires BOTH a token
# overlap and an area/evidence signal.
_SIM_THRESHOLD = 0.55
# A SECOND, stricter path: two findings whose TITLES alone overlap this much are
# almost certainly the same root cause (the title is the sharpest signal; combining
# it with divergent evidence tokens dilutes it below _SIM_THRESHOLD and lets near-
# identical-title dups slip through -- e.g. the kill-switch pair). Kept high so it
# stays a low-false-merge signal on its own.
_TITLE_SIM_THRESHOLD = 0.72
_STOP = frozenset("""
a an the of to in on for and or is are was were be been being this that these
those it its as at by with from into via not no than then so such only also
still yet but if when where which who whom whose how why what into onto over
""".split())


def extract_json_array(raw: str) -> list:
    """Tolerant: reviewers should emit only a JSON array, but strip stray prose
    or a code fence. Returns [] on any failure -- a malformed review never
    crashes the loop."""
    raw = (raw or "").strip()
    if not raw:
        return []
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        s, e = raw.find("["), raw.rfind("]")
        if s != -1 and e != -1 and e > s:
            raw = raw[s:e + 1]
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _latest_status(ddb, subject):
    hist = ddb.history(subject, "status")
    if not hist:
        return None
    ordered = sorted(hist, key=lambda c: (getattr(c, "seq", 0),
                                          getattr(c, "recorded_at", "")))
    return getattr(ordered[-1], "value", None)


def _clean(gap: dict) -> dict | None:
    slug = re.sub(r"[^a-z0-9\-]+", "-", str(gap.get("slug", "")).strip().lower()).strip("-")
    if not slug:
        return None
    sev = str(gap.get("severity", "medium")).strip().lower()
    if sev not in _VALID_SEV:
        sev = "medium"
    try:
        conf = max(0.0, min(1.0, float(gap.get("confidence", 0.5))))
    except Exception:
        conf = 0.5
    return {
        "slug": slug, "severity": sev, "confidence": conf,
        "title": str(gap.get("title", slug)).strip()[:280],
        "area": str(gap.get("area", "")).strip()[:160],
        "evidence": str(gap.get("evidence", "")).strip()[:400],
        "proposed_action": str(gap.get("proposed_action", "")).strip()[:400],
    }


def _tokens(*parts: str) -> set:
    text = " ".join(p or "" for p in parts).lower()
    return {w for w in re.split(r"[^a-z0-9]+", text)
            if len(w) > 2 and w not in _STOP}


def _file_tokens(*parts: str) -> set:
    """Filenames/paths mentioned -- a shared one is a strong same-issue signal."""
    text = " ".join(p or "" for p in parts).lower()
    return set(re.findall(r"[a-z0-9_]+\.[a-z0-9]+", text))


def _similarity(a_tokens: set, b_tokens: set) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _index_existing(ddb, prefixes) -> list[dict]:
    """Signature index of every existing finding (open or closed), so a re-worded
    re-sighting collapses onto it instead of spawning a new subject."""
    out = []
    for subject in ddb.subjects():
        if not subject.startswith(tuple(prefixes)):
            continue
        opens = [c for c in ddb.history(subject, "status")]
        if not opens:
            continue
        dims = {}
        for c in sorted(opens, key=lambda c: getattr(c, "seq", 0)):
            if (c.dims or {}):
                dims = c.dims
        title, area, ev = dims.get("title", subject), dims.get("area", ""), dims.get("evidence", "")
        out.append({
            "subject": subject,
            "status": _latest_status(ddb, subject),
            "tokens": _tokens(title, area, ev),
            "title_tokens": _tokens(title),
            "files": _file_tokens(ev, title),
            "area": (area or "").lower(),
        })
    return out


def _semantic_match(g: dict, index: list[dict]) -> dict | None:
    g_tok = _tokens(g["title"], g["area"], g["evidence"])
    g_title = _tokens(g["title"])
    g_files = _file_tokens(g["evidence"], g["title"])
    g_area = (g["area"] or "").lower()
    best, best_sim = None, 0.0
    best_title, best_title_sim = None, 0.0
    for cand in index:
        sim = _similarity(g_tok, cand["tokens"])
        # a shared file path + same area is a strong signal on its own
        shared_file = bool(g_files & cand["files"])
        area_ok = bool(g_area) and bool(cand["area"]) and (
            g_area in cand["area"] or cand["area"] in g_area
            or _similarity(_tokens(g_area), _tokens(cand["area"])) >= 0.5)
        score = sim + (0.2 if shared_file and area_ok else 0.0)
        if score > best_sim:
            best, best_sim = cand, score
        # stricter TITLE-only path: near-identical titles are a dup even when the
        # combined tokens (diluted by different evidence) fall below the threshold.
        t_sim = _similarity(g_title, cand.get("title_tokens") or set())
        if t_sim > best_title_sim:
            best_title, best_title_sim = cand, t_sim
    if best_title is not None and best_title_sim >= _TITLE_SIM_THRESHOLD:
        return best_title
    if best is not None and best_sim >= _SIM_THRESHOLD:
        return best
    return None


def record_gaps(ddb, gaps: list, sha: str, source: str,
                prefix: str = "arch.gap:", dedup_prefixes=None,
                repo_root: str | None = None):
    """Record findings with exact + semantic dedup + evidence validation.

    Returns (recorded_new, refreshed_dup, reopened, suppressions). suppressions
    surfaces BOTH semantic duplicates collapsed via `same_as` and findings
    down-ranked for unverifiable evidence -- nothing is hidden silently.
    """
    dedup_prefixes = dedup_prefixes or ("arch.gap:", "arch.audit:")
    index = _index_existing(ddb, dedup_prefixes)

    # Deterministic evidence index (Phase 1), built once per batch.
    ev_index = None
    if repo_root:
        try:
            from . import evidence as _ev
            ev_index = _ev._index_repo(repo_root)
        except Exception:
            ev_index = None

    new = dup = reop = 0
    suppressions = []
    for raw in gaps:
        g = _clean(raw)
        if g is None:
            continue

        # A finding whose cited file/line does not exist is likely hallucinated.
        # Don't silently drop it -- record it at REDUCED confidence + mark it
        # unverified, so the gate's floor keeps it out of "act now" until a human
        # or a real re-detection confirms it.
        # Only validate when we actually indexed the repo. An EMPTY index means
        # we couldn't read the repo (bad repo_root, empty tree) -- that's a
        # config error, not a hallucination, so we must NOT down-rank every
        # finding. `if ev_index:` (truthy = non-empty) skips validation cleanly.
        ev_verdict = "SKIPPED"
        if ev_index:
            try:
                from . import evidence as _ev
                chk = _ev.validate_evidence(repo_root, g["evidence"], ev_index)
                ev_verdict = chk.verdict
                if chk.verdict == "UNVERIFIED":
                    g["confidence"] = min(g["confidence"], 0.3)
                    suppressions.append({
                        "incoming": f"{prefix}{g['slug']}", "matched": None,
                        "reason": "unverified-evidence", "detail": chk.reason,
                    })
            except Exception:
                pass

        subject = f"{prefix}{g['slug']}"
        current = _latest_status(ddb, subject)
        dims = {"title": g["title"], "area": g["area"], "evidence": g["evidence"],
                "proposed_action": g["proposed_action"], "first_seen_sha": sha,
                "evidence_verdict": ev_verdict}

        # 1. exact slug already open -> refresh, done
        if current in _OPEN_STATES:
            ddb.assert_claim(subject, "severity", g["severity"], source=source,
                             confidence=g["confidence"], author_kind="ai")
            dup += 1
            continue

        # 2. semantic match to an EXISTING finding (only when this slug is brand
        #    new -- never override an explicit re-open of the same slug).
        if current is None:
            match = _semantic_match(g, index)
            if match is not None:
                # record the equivalence (auditable) and do NOT spawn a new
                # open finding. If the canonical is already closed, this is a
                # re-sighting of a resolved issue -> stays closed.
                ddb.same_as(subject, match["subject"], source=source,
                            confidence=g["confidence"])
                suppressions.append({
                    "incoming": subject, "matched": match["subject"],
                    "matched_status": match["status"],
                })
                dup += 1
                continue

        # 3. genuinely new, or an explicit re-open of a previously-closed slug
        ddb.assert_claim(subject, "status", "open", source=source,
                         confidence=g["confidence"], author_kind="ai",
                         evidence=g["evidence"], dims=dims)
        ddb.assert_claim(subject, "severity", g["severity"], source=source,
                         confidence=g["confidence"], author_kind="ai")
        # keep the index fresh so two incoming dups also collapse onto each other
        index.append({"subject": subject, "status": "open",
                      "tokens": _tokens(g["title"], g["area"], g["evidence"]),
                      "title_tokens": _tokens(g["title"]),
                      "files": _file_tokens(g["evidence"], g["title"]),
                      "area": (g["area"] or "").lower()})
        if current is None:
            new += 1
        else:
            reop += 1
    return new, dup, reop, suppressions
