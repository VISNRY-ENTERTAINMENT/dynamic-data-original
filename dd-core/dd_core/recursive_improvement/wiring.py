"""Wiring Prover -- the strong 'built but not wired' oracle. NO model.

The single most recurring structural defect: a capability is DEFINED (an injected
dependency param, or an Optional dataclass field) and USED (read/consumed
somewhere), but nothing ever PROVIDES it -- no caller passes it, no builder
assigns it. Each individual file looks correct; the gap only exists in the
cross-file wiring, which is exactly where a single-diff review (and the model)
is blind. Real WorldStak cases this catches: an identity-matching coordinator
accepted but never constructed; a `truth_mode_state` field read by every guard
but never assigned in a builder.

This upgrades the keyword-only `probes.unwired_optional_params` primitive (which
false-positives on positionally- or attribute-wired deps, hence its LOW/on-demand
status) into a real detector by computing three facts across the WHOLE tree and
intersecting them:

    DECLARED   -- a dep-suffixed optional param `x=None`, or an Optional field
                  `x: ... = None`, anywhere.
    CONSUMED   -- `something.x` is read (Load) somewhere -- the capability is
                  actually used, so being unprovided is a live bug, not dead code.
    PROVIDED   -- `x=<non-None>` passed as a keyword at any call site, OR
                  `something.x = <non-None>` assigned anywhere (excluding the
                  bare `self.x = x` plumbing forward).

    UNWIRED = DECLARED & CONSUMED & not PROVIDED.

Deterministic, conservative (a false positive costs trust), and honest about its
one blind spot: a dependency injected purely POSITIONALLY with no attribute
assignment can still read as unwired -- so a consumed-but-unprovided finding is
MEDIUM, not certain.
"""

from __future__ import annotations

from dd_core.codefacts import iter_facts
from dd_core.recursive_improvement.probes import _DEP_SUFFIXES, _looks_like_dependency

# Skip test dirs when scanning for unwired production capabilities (a fixture's
# unused optional is not a wiring bug). The codefacts walker already skips
# vendored/build dirs.
_SKIP_TESTS = frozenset({"tests", "test"})

# Fields (unlike injected params) carry data as often as capabilities, so the
# field path is gated to capability-ish names -- injected collaborators plus the
# control-plane suffixes -- to keep plain data fields (a `base_value`, a
# `semantic_mapping`) out. This is what lets it catch `truth_mode_state` (a gate)
# without flagging every optional result field.
_CAP_SUFFIXES = _DEP_SUFFIXES + (
    "_state", "_mode", "_gate", "_switches", "_controller", "_sensor",
    "_monitor", "_flag", "_policy", "_hook",
)


def _looks_like_capability_field(name: str) -> bool:
    return not name.startswith("_") and name.endswith(_CAP_SUFFIXES)


def unwired_capabilities(repo_root: str, min_len: int = 5) -> list[dict]:
    """Findings for capabilities declared + consumed but never provided.

    Reads language-neutral CodeFacts, so it works for every language with a
    registered adapter (Python via stdlib; others via the tree-sitter adapter).
    The declared/consumed/provided set algebra is identical across languages;
    only the naming heuristics below (dependency/capability suffixes) are shared
    string checks. No model in the discovery path.
    """
    declared: dict[str, tuple[str, int, str]] = {}   # name -> (rel, lineno, kind)
    provided: set[str] = set()
    consumed: set[str] = set()

    for facts in iter_facts(repo_root, extra_skip=_SKIP_TESTS):
        for fn in facts.functions:
            for name, lineno in fn.optional_none_params:
                if _looks_like_dependency(name) and len(name) >= min_len:
                    declared.setdefault(name, (facts.rel, lineno, f"param in {fn.name}()"))
        for name, lineno in facts.optional_none_fields:
            if len(name) >= min_len and _looks_like_capability_field(name):
                declared.setdefault(name, (facts.rel, lineno, "optional field"))
        provided |= facts.provided_keywords      # non-null keyword/named args
        provided |= facts.provided_attributes    # non-null, non-bare attr assigns
        consumed |= facts.consumed_attributes    # attribute reads

    findings = []
    for name, (rel, lineno, kind) in sorted(declared.items()):
        # Only the high-signal case: a capability that IS used but never provided.
        # (A declared-but-unused optional is a dead-code concern, not a wiring
        # bug, and is far noisier -- deliberately out of scope for this oracle.)
        if name in provided or name not in consumed:
            continue
        findings.append({
            "slug": f"unwired-{name.replace('_', '-')}",
            "title": (f"capability '{name}' ({kind}, {rel}) is read across the "
                      f"code but NOTHING provides it -- built but not wired"),
            "area": rel,
            "severity": "medium",
            "confidence": 0.72,
            "evidence": f"{rel}:{lineno}",
            "proposed_action": (
                f"wire '{name}': construct it and pass/assign a real value in the "
                f"production builder, or add a test proving it is set; if it is "
                f"genuinely unused, remove the declaration and its readers"),
        })
    return findings
