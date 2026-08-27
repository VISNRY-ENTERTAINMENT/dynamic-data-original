"""ReflexConfig -- per-project configuration for the reflex loop.

Everything project-specific lives here so the loop code stays generic. A project
drops a `reflex.config.json` at its repo root (or points $REFLEX_CONFIG at one);
every field has a default that works on ANY repo with ANY AI CLI.

TWO HARD DESIGN RULES, both learned from real failures:

1. THE DEFAULT MUST WORK ON ANY REPO LAYOUT.
   `substantive_prefixes` used to default to ("src/","tests/","lib/","app/").
   A project with none of those dirs (engine/, domains/, judge/, ...) got a loop
   that matched nothing, reviewed nothing, and looked EXACTLY like "no gaps
   found." A silent no-op is the worst possible failure for a tool whose job is
   finding what you missed. So the default is now EMPTY = "every changed file
   that isn't obviously non-code", which fires everywhere, and `dd_reflex.py
   doctor` exists to prove the config would actually fire.

2. NO AI OR VENDOR IS ASSUMED.
   The runner used to hardcode the `claude` CLI and Claude model names. Pick a
   `provider` preset or supply your own `cmd_template`; the prompt always goes
   over stdin, so any CLI that reads a prompt from stdin works. Defaults are the
   CHEAP tier of whatever provider you choose -- Tier 1 runs on every commit and
   must not be expensive.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields


def _default_charters_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "charters")


# ---------------------------------------------------------------------------
# Provider presets. `cmd_template` placeholders: {cli} {model} {charter} {repo}
#
# charter_mode:
#   "flag"    -- the CLI takes a system-prompt file argument ({charter} is
#                substituted into cmd_template).
#   "prepend" -- the CLI has no such flag, so the charter text is prepended to
#                the stdin prompt. Universal fallback: works with ANY CLI.
#
# No preset ships a concrete model name. Every AI vendor renames and retires
# models on its own schedule, and this file is not the place to track that --
# a hardcoded version string here is a slow-motion bug (it silently goes stale
# the day the vendor deprecates it) and it makes the tool read as locked to one
# vendor, which it deliberately is not. Set `review_model` / `audit_model`
# yourself in reflex.config.json, or leave them empty for the CLI's own
# default model. The only rule that matters, independent of vendor:
#
#   Tier 1 (`review_model`) runs on EVERY substantive commit -> pick the
#   CHEAPEST / LOWEST tier model your provider offers that can still follow
#   the reviewer charter. This runs constantly; cost and latency compound.
#
#   Tier 2 (`audit_model`) runs rarely (every N major commits, see
#   `audit_every`) -> a mid-or-higher tier model is fine here, since the low
#   frequency absorbs the extra cost. Raise it deliberately for deeper audits.
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict] = {
    "claude": {
        "cli": "claude",
        "review_model": "",   # cheapest/lowest-tier model available -- set yours
        "audit_model": "",    # mid-or-higher tier is fine here -- set yours
        "cmd_template": (
            "{cli}", "-p", "--model", "{model}",
            "--append-system-prompt-file", "{charter}", "--add-dir", "{repo}",
        ),
        "charter_mode": "flag",
    },
    # Bring-your-own CLI. Example (llm):
    #   "provider": "generic", "cli": "llm",
    #   "cmd_template": ["{cli}", "-m", "{model}"],
    #   "review_model": "<your lowest-tier model>", "audit_model": "<your mid-tier model>"
    "generic": {
        "cli": "",
        "review_model": "",
        "audit_model": "",
        "cmd_template": ("{cli}", "-m", "{model}"),
        "charter_mode": "prepend",
    },
}

# Never treated as a substantive code change (used when substantive_prefixes is
# empty, i.e. the repo-agnostic default). Extensions + path fragments.
_NON_CODE_SUFFIXES = (
    ".md", ".rst", ".txt", ".ddb", ".lock", ".log", ".csv", ".svg", ".png",
    ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".min.js", ".map",
)
_NON_CODE_FRAGMENTS = (
    ".git/", ".github/", "node_modules/", "__pycache__/", "dist/", "build/",
    "vendor/", ".venv/", "venv/", "docs/", "site-packages/", ".idea/", ".vscode/",
)


@dataclass
class ReflexConfig:
    # --- where ---
    repo_root: str = "."
    gap_db: str = "reflex.ddb"   # prefer an EXISTING project .ddb -- see below

    # --- which commits are worth a Tier-1 review ---
    # EMPTY = repo-agnostic: any changed file that isn't obviously non-code.
    # Set it only to NARROW the scope; a wrong value silently disables the loop,
    # so `dd_reflex.py doctor` checks it against the real repo.
    substantive_prefixes: tuple = ()
    ignored_prefixes: tuple = ()   # e.g. ("reflex/",) -> no self-trigger

    # --- Tier-2 cadence ---
    major_commit_regex: str = r"^(feat|fix|refactor|perf)(\(|:|!)"
    audit_every: int = 3

    # --- escalation gate ---
    threshold: int = 3
    floor: float = 0.6

    # --- which AI (see PROVIDER_PRESETS) ---
    provider: str = "claude"
    cli: str = ""                    # "" -> preset
    review_model: str = ""           # "" -> preset (Tier 1: cheap)
    audit_model: str = ""            # "" -> preset (Tier 2: mid)
    cmd_template: tuple = ()         # "" -> preset; {cli} {model} {charter} {repo}
    charter_mode: str = ""           # "" -> preset; "flag" | "prepend"

    # --- the auditor's measuring sticks (roadmap + north star), repo-relative ---
    north_star_anchors: tuple = ()

    # --- charters (what a gap / a drift IS, per project) ---
    reviewer_charter: str = ""
    auditor_charter: str = ""

    # --- charter lenses (doctrine injection, per tier) ---
    # Paths (repo-relative or absolute) to doctrine/reference documents whose
    # content is appended to the charter text at prompt-build time. This turns
    # "read the doc and forget it" knowledge into standing review doctrine the
    # model reasons with on every run -- WITHOUT converting judgment-based
    # guidance into brittle regex probes.
    #
    # Deliberately split per tier because cost scales differently:
    #   tier1_lenses -- Tier-1 runs per-commit on the cheap/fast model; keep
    #                   this list short and high-signal (1-2 docs max).
    #   tier2_lenses -- Tier-2 runs every N commits on the stronger model;
    #                   it can afford the full doctrine sweep, including the
    #                   expensive judgment lenses (scope/decision boundaries,
    #                   verification discipline, billing-model reasoning).
    # A lens file that doesn't exist is skipped silently (a missing doc must
    # never break governance runs).
    tier1_lenses: tuple = ()
    tier2_lenses: tuple = ()

    # --- claim namespaces + provenance ---
    review_source: str = "reflex-reviewer"
    audit_source: str = "reflex-auditor"
    gap_prefix: str = "arch.gap:"
    audit_prefix: str = "arch.audit:"

    # --- misc ---
    dd_core_path: str = ""
    max_diff_chars: int = 120_000
    review_timeout: int = 600
    audit_timeout: int = 1800

    # --- attack-pattern probe (attack_pattern_probe.py) ---
    # False by default so existing projects on dd-core see zero behavior change
    # on upgrade. When True, run_post_commit also runs the deterministic
    # attack-pattern oracles (obfuscated require, presence-only auth check,
    # unbounded RegExp, unescaped innerHTML concat) and records their findings
    # through the SAME record_gaps + gate.run_gate path as everything else --
    # so a HIGH-severity, HIGH-confidence match (e.g. the obfuscated-require
    # pattern, 0.85 confidence) clears the default 0.6 floor and escalates
    # immediately, while the lower-confidence heuristic patterns (0.5, below
    # floor) stay recorded but silent unless a project explicitly lowers its
    # floor -- no new escalation mechanism, just feeding the existing one.
    record_attack_probe_findings: bool = False
    attack_probe_source: str = "reflex-attack-probe"

    # --- department probes (departments/ package) ---
    # Controls which of the seven governance departments are active.
    # Empty tuple (the default) = ALL departments enabled -- backwards-compatible
    # because no existing project has this key, so upgrading silently turns all
    # departments on. To disable specific departments, list only the ones you
    # want: ("security", "debt") enables only those two.
    #
    # Valid department names:
    #   "security"      -- hardcoded secrets, injection, deserialization, etc.
    #   "debt"          -- TODO/FIXME/stub/NotImplementedError markers
    #   "observability" -- bare exception swallows, unguarded background tasks
    #   "architecture"  -- architecture_rules.json manifest enforcement
    #   "dependency"    -- manifest parsing, unpinned versions, missing lockfiles
    #   "contract"      -- invariants.json + contracts.json manifest enforcement
    #   "reachability"  -- exported/__all__ names never referenced outside their
    #                      own file + tests: "correct, tested, never executed"
    #   "billing"       -- billing-path-filtered enforcement patterns (calendar-
    #                      month cycle keys, missing dunning states, unidempotent
    #                      usage recording, silent config no-ops)
    #   "goal_alignment"-- no deterministic oracle; handled by Tier-1 model lens
    departments: tuple = ()  # empty = all enabled

    # Path to the architecture rules manifest (repo-relative).
    # Only used when "architecture" department is enabled.
    architecture_rules: str = "architecture_rules.json"

    # Intent predicate used by the goal_alignment department.
    # The Tier-1 reviewer resolves this claim from the store and injects it into
    # the prompt when present. Authors assert it before starting a build via:
    #   ddb.assert_claim("session.intent", cfg.intent_predicate, "...", source="ai")
    intent_predicate: str = "planned_change"

    # Whether to run department probes even on non-substantive commits (e.g.
    # docs-only changes). False by default: department probes run only when
    # the commit is already substantive (same gate as Tier-1).
    departments_on_all_commits: bool = False

    # ---------------- derived helpers ----------------
    def _preset(self) -> dict:
        return PROVIDER_PRESETS.get(self.provider, PROVIDER_PRESETS["generic"])

    def resolved_cli(self) -> str:
        return self.cli or self._preset().get("cli", "")

    def resolved_review_model(self) -> str:
        return self.review_model or self._preset().get("review_model", "")

    def resolved_audit_model(self) -> str:
        return self.audit_model or self._preset().get("audit_model", "")

    def resolved_cmd_template(self) -> tuple:
        return tuple(self.cmd_template or self._preset().get("cmd_template", ()))

    def resolved_charter_mode(self) -> str:
        return self.charter_mode or self._preset().get("charter_mode", "prepend")

    def abspath(self, p: str) -> str:
        return p if os.path.isabs(p) else os.path.normpath(
            os.path.join(self.repo_root, p))

    def reviewer_charter_path(self) -> str:
        return (self.abspath(self.reviewer_charter) if self.reviewer_charter
                else os.path.join(_default_charters_dir(),
                                  "reviewer_charter.template.md"))

    def auditor_charter_path(self) -> str:
        return (self.abspath(self.auditor_charter) if self.auditor_charter
                else os.path.join(_default_charters_dir(),
                                  "auditor_charter.template.md"))

    def is_substantive_path(self, path: str) -> bool:
        """Repo-agnostic by default.

        With substantive_prefixes set -> allow-list (narrowing).
        Empty (the default) -> anything that isn't obviously non-code. This is
        what makes the loop fire on a repo whose dirs are engine/ domains/
        judge/ rather than src/ tests/.
        """
        p = path.replace("\\", "/")
        if self.ignored_prefixes and p.startswith(tuple(self.ignored_prefixes)):
            return False
        if self.substantive_prefixes:
            return p.startswith(tuple(self.substantive_prefixes))
        low = p.lower()
        if any(frag in low for frag in _NON_CODE_FRAGMENTS):
            return False
        if low.endswith(_NON_CODE_SUFFIXES):
            return False
        return True

    # ---------------- io ----------------
    @classmethod
    def load(cls, path: str | None = None) -> "ReflexConfig":
        path = path or os.environ.get("REFLEX_CONFIG") or "reflex.config.json"
        data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            # A relative repo_root means "relative to the config file", NOT
            # "wherever the process is cwd'd". So "." = the config's dir, ".." =
            # its parent (e.g. a config in dynamic-data/ pointing at the repo
            # root one level up). Resolve any relative value against the config
            # file so `doctor`/`run` work from any working directory.
            rr = data.get("repo_root", ".")
            if not os.path.isabs(rr):
                cfg_dir = os.path.dirname(os.path.abspath(path))
                data["repo_root"] = os.path.normpath(os.path.join(cfg_dir, rr))
        known = {f.name for f in fields(cls)}
        tu = {"substantive_prefixes", "ignored_prefixes", "north_star_anchors",
              "cmd_template", "departments", "tier1_lenses", "tier2_lenses"}
        clean = {k: (tuple(v) if k in tu and v is not None else v)
                 for k, v in data.items() if k in known}
        return cls(**clean)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("substantive_prefixes", "ignored_prefixes",
                  "north_star_anchors", "cmd_template"):
            d[k] = list(d[k])
        return d
