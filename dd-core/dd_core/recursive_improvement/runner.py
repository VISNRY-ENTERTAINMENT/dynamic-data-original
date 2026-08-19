"""The reflex runner -- ties config + gate + record + a headless model into the
two-tier loop. Called by the post-commit hook (run_post_commit) and by the CLI.

Doctrine (see 04_RECURSIVE_IMPROVEMENT.md): only the review/audit step uses a
model, and only to PROPOSE. Every write is deterministic. Fail-soft everywhere
-- nothing here can block or slow a commit. The findings ledger is expected to
live OUTSIDE the reviewed repo, so recording gaps creates no commit and the loop
cannot re-trigger itself.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from .config import ReflexConfig
from . import gate as _gate
from . import record as _record


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _git(repo_root, args):
    return subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=True, check=False).stdout


def _load_store(cfg: ReflexConfig):
    if cfg.dd_core_path and cfg.dd_core_path not in sys.path:
        sys.path.insert(0, cfg.dd_core_path)
    from dd_core import DynamicDataStore  # noqa: E402
    return DynamicDataStore(cfg.abspath(cfg.gap_db))


def _run_model(cfg: ReflexConfig, prompt: str, model: str, charter_path: str,
               timeout: int) -> str | None:
    """Headless model call -- AI/vendor agnostic.

    The prompt ALWAYS goes over stdin (never argv: a large diff would blow the
    OS arg-length limit, ~32k on Windows). The command itself comes from the
    provider preset or the project's cmd_template, so any CLI that reads a
    prompt from stdin works -- nothing here assumes Claude.

    charter_mode:
      "flag"    -> {charter} is substituted into the command (CLI has a
                   system-prompt-file option).
      "prepend" -> the charter text is prepended to the prompt. Universal
                   fallback for CLIs with no such flag.
    """
    cli = cfg.resolved_cli()
    template = cfg.resolved_cmd_template()
    if not cli or not template:
        print("[reflex] no CLI/cmd_template configured for provider "
              f"{cfg.provider!r}; skipping. Set 'cli' + 'cmd_template' "
              "(see SETUP_FOR_ANOTHER_PROJECT.md).", file=sys.stderr)
        return None

    # Resolve the executable robustly: on Windows a CLI is often a .CMD shim
    # that a bare argv cannot find (WinError 2).
    exe = shutil.which(cli) or cli

    if cfg.resolved_charter_mode() == "prepend":
        try:
            charter_text = open(charter_path, encoding="utf-8").read()
            prompt = charter_text + "\n\n---\n\n" + prompt
        except Exception:
            pass

    subs = {"cli": exe, "model": model, "charter": charter_path,
            "repo": cfg.repo_root}
    try:
        cmd = [part.format(**subs) for part in template]
    except Exception as e:
        print(f"[reflex] bad cmd_template {template!r}: {e}", file=sys.stderr)
        return None

    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              cwd=cfg.repo_root, timeout=timeout, check=False)
    except FileNotFoundError:
        print(f"[reflex] CLI {cli!r} not found on PATH; skipping.",
              file=sys.stderr)
        return None
    except Exception as e:
        print(f"[reflex] model call failed: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"[reflex] {cli} exit {proc.returncode}: {proc.stderr[:300]}",
              file=sys.stderr)
    return proc.stdout


def _changed_files(repo_root, sha):
    out = _git(repo_root, ["diff-tree", "--no-commit-id", "--name-only", "-r", sha])
    return [f for f in out.splitlines() if f.strip()]


def _is_substantive(cfg: ReflexConfig, files) -> bool:
    """Repo-agnostic: see ReflexConfig.is_substantive_path."""
    return any(cfg.is_substantive_path(f) for f in files)


def _fmt_suppression(s: dict) -> str:
    if s.get("reason") == "unverified-evidence":
        return (f"\n  [unverified] {s['incoming']} down-ranked: "
                f"{s.get('detail','')}")
    return (f"\n  [dedup] {s['incoming']} == {s['matched']} "
            f"({s.get('matched_status')})")


def _known_findings_context(cfg: ReflexConfig, limit: int = 250) -> str:
    """Phase 1: tell the reviewer what is ALREADY tracked (every status), so it
    doesn't re-propose them. Cheaper and cleaner than post-hoc dedup -- the model
    just skips what it's told is known. Deterministic assembly.

    IMPORTANT: the CLOSED set (fixed/accepted/wontfix) is exactly what the auditor
    must not re-raise, so it is listed FIRST and never truncated away. A too-small
    limit that dropped closed findings was the direct cause of the loop re-flagging
    already-fixed work (a real-but-stale "finding" that is pure noise). The limit
    is high enough to carry a mature ledger; if truncation is ever needed it drops
    OPEN items last, since re-raising a still-open item is less harmful than
    re-raising something already resolved.
    """
    try:
        ddb = _load_store(cfg)
    except Exception:
        return ""
    _CLOSED = ("fixed", "accepted", "wontfix")
    closed_lines, open_lines = [], []
    try:
        for subject in ddb.subjects():
            if not subject.startswith((cfg.gap_prefix, cfg.audit_prefix)):
                continue
            hist = sorted(ddb.history(subject, "status"),
                          key=lambda c: (getattr(c, "seq", 0),
                                         getattr(c, "recorded_at", "")))
            if not hist:
                continue
            st = hist[-1].value
            dims = next((c.dims for c in reversed(hist) if c.dims), {}) or {}
            line = f"- [{st}] {subject} :: {dims.get('title','')[:90]}"
            (closed_lines if st in _CLOSED else open_lines).append(line)
    finally:
        ddb.close()
    closed_lines.sort()
    open_lines.sort()
    ordered = closed_lines + open_lines  # closed first: never truncate the "done" set
    if not ordered:
        return ""
    shown = ordered[:limit]
    more = (f"\n(...{len(ordered) - limit} more OPEN items)"
            if len(ordered) > limit else "")
    return ("\n\nALREADY-TRACKED FINDINGS -- do NOT re-report ANY of these (all "
            "statuses, including fixed/accepted); only raise something genuinely "
            "new or a clear regression of a closed one:\n"
            + "\n".join(shown) + more + "\n")


# --------------------------------------------------------------------------
# Tier 1 -- per-commit diff review
# --------------------------------------------------------------------------
def run_tier1(cfg: ReflexConfig, sha: str) -> str | None:
    diff = _git(cfg.repo_root, ["show", "--stat", "--patch", "--no-color", "-M", sha])
    if len(diff) > cfg.max_diff_chars:
        diff = diff[:cfg.max_diff_chars] + "\n...[diff truncated]..."
    msg = _git(cfg.repo_root, ["log", "-1", "--format=%B", sha]).strip()
    prompt = (f"A commit just shipped. Review it per your charter and output "
              f"ONLY the JSON array of gaps (or []).\n\nCOMMIT {sha}\n"
              f"MESSAGE:\n{msg}\n\nDIFF:\n{diff}\n"
              + _known_findings_context(cfg))
    raw = _run_model(cfg, prompt, cfg.resolved_review_model(),
                     cfg.reviewer_charter_path(), cfg.review_timeout)
    if raw is None:
        return None
    gaps = _record.extract_json_array(raw)
    ddb = _load_store(cfg)
    try:
        new, dup, reop, supp = _record.record_gaps(
            ddb, gaps, sha, cfg.review_source, cfg.gap_prefix,
            repo_root=cfg.repo_root)
        counted, escalation = _gate.run_gate(ddb, cfg.threshold, cfg.floor,
                                             cfg.gap_prefix)
    finally:
        ddb.close()
    out = (f"[reflex] tier1 recorded={new} dup={dup} reopened={reop} "
           f"suppressed={len(supp)} open={counted}")
    for s in supp:
        out += _fmt_suppression(s)
    if escalation:
        _write(cfg, "ESCALATION.md", f"# Reflex escalation (commit {sha})\n\n"
               "```\n" + escalation + "\n```\n")
        out += " -> ESCALATION.md"
    return out


# --------------------------------------------------------------------------
# Tier 2 -- whole-codebase audit against roadmap + north star
# --------------------------------------------------------------------------
def _major_commits_since(cfg, since_sha):
    rng = f"{since_sha}..HEAD" if since_sha else "HEAD"
    log = _git(cfg.repo_root, ["log", rng, "--format=%s", "--no-merges"])
    rx = re.compile(cfg.major_commit_regex, re.IGNORECASE)
    return [m for m in log.splitlines() if rx.match(m.strip())]


def _get_last_audit_sha(ddb):
    hist = ddb.history("reflex.audit", "last_deep_audit_sha")
    if not hist:
        return None
    latest = sorted(hist, key=lambda c: (getattr(c, "seq", 0),
                                         getattr(c, "recorded_at", "")))[-1]
    return getattr(latest, "value", None)


def run_tier2(cfg: ReflexConfig, sha: str) -> str | None:
    anchors = "\n".join(
        f"- {a}" + (" (present)" if os.path.exists(cfg.abspath(a))
                    else " (MISSING -- flag this)")
        for a in cfg.north_star_anchors
    ) or "- (none configured -- audit against the codebase's own stated goals)"
    recent = _git(cfg.repo_root, ["log", "-15", "--oneline", "--no-color"])
    # Phase 2: prime the auditor with anti-patterns this loop has actually
    # caught + fixed, and with what is already tracked (so it doesn't re-raise).
    from . import learn as _learn
    prompt = (f"Run a Tier-2 whole-codebase audit per your charter, as of "
              f"{sha}. Read the anchors, then read across the codebase (you have "
              f"file tools + --add-dir) and audit the architecture against its "
              f"destination. Output ONLY the JSON array (or []).\n\n"
              f"NORTH-STAR ANCHORS:\n{anchors}\n\nRECENT COMMITS:\n{recent}\n"
              + _learn.antipattern_hints(cfg)
              + _learn.false_positive_hints(cfg)
              + _known_findings_context(cfg))
    raw = _run_model(cfg, prompt, cfg.resolved_audit_model(),
                     cfg.auditor_charter_path(), cfg.audit_timeout)
    if raw is None:
        return None
    gaps = _record.extract_json_array(raw)

    # NOTE: the deterministic structural probes (probes.py) are intentionally
    # NOT auto-recorded here. They are a HEURISTIC hint tool (keyword-passing
    # only -> positionally-injected dependencies look unwired), so recording
    # them would put low-signal candidates in the escalation ledger. They stay
    # on-demand via `dd_ri.py probe`. Only the model's findings are recorded.

    ddb = _load_store(cfg)
    try:
        new, dup, reop, supp = _record.record_gaps(
            ddb, gaps, sha, cfg.audit_source, cfg.audit_prefix,
            repo_root=cfg.repo_root)
    finally:
        ddb.close()
    _write(cfg, "AUDIT_REPORT.md",
           f"# Reflex Tier-2 codebase audit (as of {sha})\n\n"
           f"Findings recorded under `{cfg.audit_prefix}` in the ledger. "
           f"Review and, per finding, act or dismiss.\n\n"
           f"recorded={new} dup={dup} reopened={reop}\n\n"
           "## Raw auditor output\n\n```json\n" + (raw or "[]") + "\n```\n")
    return f"[reflex] tier2 recorded={new} dup={dup} reopened={reop} -> AUDIT_REPORT.md"


def maybe_run_tier2(cfg: ReflexConfig, sha: str) -> str | None:
    ddb = _load_store(cfg)
    try:
        last = _get_last_audit_sha(ddb)
    finally:
        ddb.close()
    if last is None:
        _set_last_audit_sha(cfg, sha)  # begin counting from here
        return None
    majors = _major_commits_since(cfg, last)
    if len(majors) < cfg.audit_every:
        return None
    result = run_tier2(cfg, sha)
    _set_last_audit_sha(cfg, sha)
    return result


def _set_last_audit_sha(cfg, sha):
    try:
        ddb = _load_store(cfg)
        ddb.assert_claim("reflex.audit", "last_deep_audit_sha", sha,
                         source="reflex", confidence=1.0, author_kind="system")
        ddb.close()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Attack-pattern probe recording -- opt-in, deterministic, NO model.
#
# Unlike the other structural probes (see the comment in run_tier2 explaining
# why they stay on-demand-only), this one is recorded through the SAME
# record_gaps + gate.run_gate path Tier-1/Tier-2 use, IF a project opts in via
# cfg.record_attack_probe_findings. The reason for the different treatment:
# those probes are heuristic hints with a known false-positive shape
# (unwired_optional_params flags positionally-injected deps); this probe's
# highest-confidence pattern (obfuscated dynamic require, 0.85) has no
# plausible legitimate false-positive shape and is exactly the kind of
# "should surface immediately" finding the escalation gate exists for. The
# lower-confidence patterns (0.5, below the 0.6 default floor) are recorded
# alongside it but stay silent unless a project's floor is lowered -- the
# existing floor mechanism does the confidence-based gating, not new logic
# here. Opt-in (default False) so no existing dd-core consumer's behavior
# changes on upgrade.
# --------------------------------------------------------------------------
def run_attack_probe_recording(cfg: ReflexConfig, sha: str) -> str | None:
    if not cfg.record_attack_probe_findings:
        return None
    from . import attack_pattern_probe as _app
    findings = _app.run_attack_probes(cfg.repo_root)
    if not findings:
        return None
    ddb = _load_store(cfg)
    try:
        new, dup, reop, supp = _record.record_gaps(
            ddb, findings, sha, cfg.attack_probe_source, cfg.gap_prefix,
            repo_root=cfg.repo_root)
        counted, escalation = _gate.run_gate(ddb, cfg.threshold, cfg.floor,
                                             cfg.gap_prefix)
    finally:
        ddb.close()
    out = (f"[reflex] attack-probe recorded={new} dup={dup} reopened={reop} "
           f"suppressed={len(supp)} open={counted}")
    if escalation:
        _write(cfg, "ESCALATION.md", f"# Reflex escalation (commit {sha})\n\n"
               "```\n" + escalation + "\n```\n")
        out += " -> ESCALATION.md"
    return out


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def _write(cfg, name, text):
    """Write a runtime artifact (ESCALATION.md / AUDIT_REPORT.md) into a
    contained `.reflex/` dir rather than dumping it in the repo root -- a bare
    repo_root drop clutters the project and risks accidental commits. Prefer the
    charter dir when configured, else <repo_root>/.reflex/."""
    try:
        if cfg.reviewer_charter:
            d = os.path.dirname(cfg.abspath(cfg.reviewer_charter))
        else:
            d = os.path.join(cfg.repo_root, ".reflex")
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass


def run_autoclose(cfg: ReflexConfig, sha: str) -> list[str]:
    """Close findings a commit's message says it closed. Deterministic; runs on
    EVERY commit (a fix that closes a finding needn't itself be 'substantive')."""
    from . import autoclose as _ac
    msg = _git(cfg.repo_root, ["log", "-1", "--format=%B", sha])
    ddb = _load_store(cfg)
    try:
        return _ac.autoclose_from_commit(ddb, msg, sha)
    finally:
        ddb.close()


def run_post_commit(cfg: ReflexConfig, sha: str | None = None) -> int:
    """Full post-ship flow. Always returns 0 (observer, never a gatekeeper)."""
    try:
        sha = sha or _git(cfg.repo_root, ["rev-parse", "HEAD"]).strip()
        if not sha:
            return 0
        # Auto-close first, on EVERY commit: "Closes arch.gap:X" in the message
        # marks X fixed in the ledger, so the backlog self-drains as fixes ship.
        try:
            closed = run_autoclose(cfg, sha)
            if closed:
                print(f"[reflex] auto-closed {len(closed)} finding(s): "
                      + ", ".join(closed))
        except Exception as e:
            print(f"[reflex] autoclose skipped: {e}", file=sys.stderr)

        if not _is_substantive(cfg, _changed_files(cfg.repo_root, sha)):
            return 0
        r1 = run_tier1(cfg, sha)
        if r1:
            print(r1)
        rap = run_attack_probe_recording(cfg, sha)  # opt-in, see comment above
        if rap:
            print(rap)
        r2 = maybe_run_tier2(cfg, sha)  # runs regardless of tier-1 outcome
        if r2:
            print(r2)
    except Exception as e:
        print(f"[reflex] fatal (ignored): {e}", file=sys.stderr)
    return 0
