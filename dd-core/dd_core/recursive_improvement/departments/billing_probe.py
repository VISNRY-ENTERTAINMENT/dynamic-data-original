"""Billing department -- deterministic oracle. NO model.

Detects the mechanical half of billing-enforcement failures: patterns that
have caused real revenue leaks and are exactly regex-able. The judgment half
(is the pricing model right, which abuse vectors are worth fixing) belongs
in the Tier-1/Tier-2 charter lenses, not here.

Every oracle is PATH-FILTERED to billing-adjacent files (name or content
mentions billing/stripe/payment/subscription/invoice/meter) so these
patterns never fire on unrelated code -- deliberate precision-over-recall,
per the probe design bar: <10 findings on a clean mature codebase.

Pattern categories (all from real incidents, not hypotheticals):
  1. Calendar-month cycle key  -- `.slice(0, 7)` / `[:7]` on a date in a
     billing path. Bills against wall-clock months instead of the tenant's
     actual provider billing anchor; misaligns the enforcement window from
     the real invoice for every customer who didn't sign up on the 1st.
  2. Incomplete subscription-state handling -- a webhook handler that
     matches `subscription.deleted` / `payment_failed` but never mentions
     `past_due` or `unpaid`. The failed-but-not-canceled dunning states are
     where indefinite free access lives; `deleted` may never fire.
  3. Unidempotent usage recording -- a metering/usage-event call with no
     idempotency mechanism in the surrounding window. Retries and webhook
     redelivery double-count the bill.
  4. Silent billing no-op -- an early `return`/`return null` guarded only
     by a missing env/config value inside a billing-named function, with no
     logging in the guard. Deployed unconfigured, the feature is inert and
     invisible; revenue loss with zero signal.

All findings use slug prefix `billing-` so they live at arch.gap:billing-*.

Confidence calibration:
  0.75 -- pattern is specific and the file is billing-adjacent
  0.60 -- heuristic within a billing file; at the default floor
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", ".idea", ".vscode",
    "tests", "test", "spec", "specs", "__tests__", "reflex", ".reflex",
    "_shelved", "proofs", "e2e",
})

# Test/harness files by NAME (they often live outside test dirs). A billing
# assertion in a test exercising the missing-dunning case is coverage, not a
# gap -- flagging it would punish exactly the test we want to exist.
_TEST_FILE_RE = re.compile(
    r"(?:\.test\.|\.spec\.|_test\.|test_|selftest|harness|fixture)", re.IGNORECASE)

_CODE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".py", ".go", ".rb"}

# A file is billing-adjacent if its PATH or its CONTENT matches this.
_BILLING_PATH = re.compile(
    r"billing|stripe|payment|subscription|invoice|meter|checkout|pricing|tier",
    re.IGNORECASE)
_BILLING_CONTENT = re.compile(
    r"stripe|billing|subscription|invoice|meterEvent|usage_record|payment_intent",
    re.IGNORECASE)


def _walk(repo_root: str):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in _CODE_EXTS:
                yield os.path.join(root, f)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def _rel(repo_root: str, path: str) -> str:
    return os.path.relpath(path, repo_root).replace("\\", "/")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _slug(prefix: str, rel: str, lineno: int) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")
    return f"billing-{prefix}-{safe}-{lineno}"


def _billing_files(repo_root: str):
    """Yield (rel, src) for billing-adjacent NON-TEST files only."""
    for path in _walk(repo_root):
        rel = _rel(repo_root, path)
        if _TEST_FILE_RE.search(os.path.basename(rel)):
            continue
        by_path = bool(_BILLING_PATH.search(rel))
        src = _read(path)
        if src is None:
            continue
        if by_path or _BILLING_CONTENT.search(src):
            yield rel, src


# ---------------------------------------------------------------------------
# 1. Calendar-month cycle key in a billing path
# ---------------------------------------------------------------------------

# JS: something.slice(0, 7) / substring(0, 7); Py: [:7] -- near cycle/period terms
_CAL_MONTH_JS = re.compile(r"\.(?:slice|substring)\(\s*0\s*,\s*7\s*\)")
_CAL_MONTH_PY = re.compile(r"\[\s*:\s*7\s*\]")
# Deliberately narrow: "month"/"period" alone fire on ordinary DISPLAY date
# formatting (dashboards truncate dates for labels constantly). Only terms
# that indicate the value KEYS a billing computation count.
_CYCLE_TERMS = re.compile(r"cycle[_ ]?key|billing[_ ]?cycle|billed|anchor|overage",
                          re.IGNORECASE)
_CYCLE_WINDOW = 300


def calendar_month_cycle_key(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for rel, src in _billing_files(repo_root):
        pat = _CAL_MONTH_PY if rel.endswith(".py") else _CAL_MONTH_JS
        for m in pat.finditer(src):
            window = src[max(0, m.start() - _CYCLE_WINDOW): m.end() + _CYCLE_WINDOW]
            if not _CYCLE_TERMS.search(window):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("calendar-cycle-key", rel, lineno),
                "title": (f"calendar-month date truncation in billing path "
                          f"{rel}:{lineno} -- cycle key derived from wall-clock "
                          f"YYYY-MM instead of the tenant's provider billing anchor"),
                "area": rel,
                "severity": "high",
                "confidence": 0.75,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "key the billing period off the subscription's actual "
                    "current_period_start (provider-supplied anchor date), not a "
                    "calendar-month truncation -- wall-clock YYYY-MM misaligns the "
                    "enforcement window from the real invoice for every tenant "
                    "whose anchor isn't the 1st"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 2. Incomplete subscription-state handling
# ---------------------------------------------------------------------------

_HANDLES_TERMINAL = re.compile(
    r"subscription\.deleted|payment_failed|invoice\.payment", re.IGNORECASE)
_HANDLES_DUNNING = re.compile(r"past_due|pastDue|unpaid", re.IGNORECASE)


def incomplete_subscription_states(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for rel, src in _billing_files(repo_root):
        m = _HANDLES_TERMINAL.search(src)
        if not m:
            continue
        if _HANDLES_DUNNING.search(src):
            continue
        lineno = _line_of(src, m.start())
        findings.append({
            "slug": _slug("missing-dunning-states", rel, lineno),
            "title": (f"{rel} handles terminal subscription events "
                      f"(deleted/payment_failed) but never mentions "
                      f"past_due/unpaid -- the failed-but-not-canceled dunning "
                      f"states are unhandled"),
            "area": rel,
            "severity": "high",
            "confidence": 0.75,
            "evidence": f"{rel}:{lineno} handles terminal events only",
            "proposed_action": (
                "map past_due/unpaid subscription statuses to a defined access "
                "behavior (grace period -> soft-lock); relying only on "
                "subscription.deleted leaves indefinite free access when the "
                "provider's dunning is configured to end in `unpaid` rather than "
                "auto-cancel -- deleted may simply never fire"
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# 3. Unidempotent usage recording
# ---------------------------------------------------------------------------

_USAGE_CALL = re.compile(
    r"meterEvents\.create|usage_?records?\.create|record_?usage|createUsageRecord",
    re.IGNORECASE)
_IDEMPOTENCY = re.compile(r"idempoten", re.IGNORECASE)
_USAGE_WINDOW = 600


def unidempotent_usage_recording(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for rel, src in _billing_files(repo_root):
        for m in _USAGE_CALL.finditer(src):
            window = src[max(0, m.start() - _USAGE_WINDOW): m.end() + _USAGE_WINDOW]
            if _IDEMPOTENCY.search(window):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("no-idempotency-key", rel, lineno),
                "title": (f"usage-metering call at {rel}:{lineno} with no "
                          f"idempotency mechanism in the surrounding code -- "
                          f"retries/redelivery will double-count the bill"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.60,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "attach a deterministic idempotency key derived from the "
                    "actual event (not a random request ID) to every "
                    "bill-affecting write, using the provider's idempotency "
                    "mechanism -- retries and webhook redelivery are normal, "
                    "double-billing on them is not"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# 4. Silent billing no-op on missing config
# ---------------------------------------------------------------------------

# JS: if (!X) { return ... } or if (!X) return -- with env/config/key in the
# guard, inside a billing file, and no log call in the guard body.
_SILENT_GUARD_JS = re.compile(
    r"if\s*\(\s*!\s*([A-Za-z_$][\w$.]*(?:[Kk]ey|[Ii]d|[Cc]onfig|[Cc]lient|[Ss]ecret|env\.[A-Z_]+)[\w$.]*)\s*"
    r"(?:\|\|[^)]*)?\)\s*(?:\{\s*)?return\b[^;\n]*")
_SILENT_GUARD_PY = re.compile(
    r"if\s+not\s+([A-Za-z_][\w.]*(?:key|id|config|client|secret)[\w.]*)\s*:\s*\n\s+return\b",
    re.IGNORECASE)
_LOG_NEARBY = re.compile(r"\blog|\bwarn|\balert|\bconsole\.|logger\.", re.IGNORECASE)
_GUARD_LOG_WINDOW = 160


def silent_billing_noop(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for rel, src in _billing_files(repo_root):
        pat = _SILENT_GUARD_PY if rel.endswith(".py") else _SILENT_GUARD_JS
        for m in pat.finditer(src):
            # a log call adjacent to the guard means the no-op is at least visible
            window = src[m.start(): m.end() + _GUARD_LOG_WINDOW]
            if _LOG_NEARBY.search(window):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("silent-noop", rel, lineno),
                "title": (f"silent early-return on missing config/key at "
                          f"{rel}:{lineno} in a billing path -- deployed "
                          f"unconfigured, this feature no-ops invisibly"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.60,
                "evidence": f"{rel}:{lineno} guard on `{m.group(1)}`",
                "proposed_action": (
                    "a no-op path in a revenue-critical function must emit a "
                    "loud, monitored signal (warn-level log at minimum, alert "
                    "preferably) -- a silent return means the entire billing "
                    "feature can be inert in production with nothing to show for it"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

_ROLLUP_THRESHOLD = 3


def _rollup(findings: list[dict]) -> list[dict]:
    """Collapse >_ROLLUP_THRESHOLD same-pattern findings per file into one
    aggregate -- same precision discipline as the observability department:
    past the threshold the signal is 'this file has a systemic problem',
    which is one finding, not N."""
    by_group: dict[tuple, list[dict]] = {}
    for f in findings:
        label = "-".join(f.get("slug", "").split("-")[:3])
        by_group.setdefault((f.get("area", ""), label), []).append(f)
    out: list[dict] = []
    for (area, label), group in by_group.items():
        if len(group) <= _ROLLUP_THRESHOLD:
            out.extend(group)
            continue
        linenos = sorted(
            int(f["slug"].rsplit("-", 1)[-1]) for f in group
            if f["slug"].rsplit("-", 1)[-1].isdigit()
        )
        first = group[0]
        safe = re.sub(r"[^a-z0-9]+", "-", area.lower()).strip("-")
        out.append({
            "slug": f"{label}-{safe}-rollup",
            "title": (f"{len(group)}x {label.removeprefix('billing-')} in {area} "
                      f"(lines {', '.join(map(str, linenos[:8]))}"
                      f"{', ...' if len(linenos) > 8 else ''}) -- systemic "
                      f"pattern, rolled up into one finding"),
            "area": area,
            "severity": first.get("severity", "medium"),
            "confidence": first.get("confidence", 0.60),
            "evidence": f"{area}: {len(group)} occurrences",
            "proposed_action": (
                f"fix as ONE batch pass across this file, not {len(group)} "
                f"individual tickets: " + first.get("proposed_action", "")
            ),
        })
    return out


def run_billing_probes(repo_root: str) -> list[dict]:
    """All billing department findings (same-pattern-per-file rolled up)."""
    out: list[dict] = []
    for oracle in (
        calendar_month_cycle_key,
        incomplete_subscription_states,
        unidempotent_usage_recording,
        silent_billing_noop,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return _rollup(out)
