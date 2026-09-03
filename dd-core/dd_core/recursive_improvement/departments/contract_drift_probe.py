"""Contract-drift department -- deterministic oracle. NO model.

Finds the conditions under which an EXTERNAL contract can drift beneath a
correct implementation without anything noticing: fixture-only integration
testing, silent missing-field fallbacks around counterparty payloads, and
unpinned/inconsistently-pinned API versions. This is the one defect class
in the incident record that NO internal trace can catch -- the code is
right about a world that no longer exists -- so the probe targets the
*preconditions* of silent drift rather than the drift itself.

Origin (real incident): a webhook read `current_period_start` at the
subscription top level. A newer Stripe API version moved the field onto
subscription items; the read returned null inside a best-effort try/catch,
and the entire downstream anchor-persist mechanism was silently inert on
every live event while 142/142 unit tests stayed green against fixtures
frozen in the old payload shape.

Doctrine source: AGENT_SYSTEM_THINK/EXTERNAL_CONTRACTS_AND_DRIFT.md.

Precision bar (same constraint as the other departments): high-signal,
low-volume. Oracles restrict themselves to files that demonstrably touch a
known external provider, and each oracle caps per-file findings.

Oracles:
  1. silent-optional-external-read -- webhook/event-handler code reading a
     provider payload field via an optional chain / .get() whose null path
     produces NO log or raise within the surrounding block
  2. stale-fixture-risk            -- fixture/mock payload files for a
     provider present while no test references live/recorded-refresh
     markers (heuristic: fixtures exist, zero live-mode test markers)
  3. unpinned-api-version          -- provider client construction with no
     explicit apiVersion/api_version pin in the same file
  4. version-pin-disagreement      -- two different explicit version pins
     for the same provider across the repo

All findings use slug prefix `drift-` so they live at arch.gap:drift-* in
the claim store.

Confidence calibration:
  0.75 -- silent optional read in a webhook path: the exact incident shape
  0.65 -- version pins that disagree across the repo
  0.55 -- unpinned client / stale-fixture risk: often deliberate, but the
          question deserves an owner
"""
from __future__ import annotations

import os
import re

_SKIP = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "vendor", "site-packages", ".idea", ".vscode", "reflex",
    ".reflex", "coverage", "_shelved",
})

_TEST_DIRS = frozenset({
    "tests", "test", "spec", "specs", "__tests__", "proofs", "e2e",
})
_TEST_FILE_RE = re.compile(
    r"(?:\.test\.|\.spec\.|_test\.|test_|selftest)", re.IGNORECASE)
_FIXTURE_DIR_RE = re.compile(r"(?:^|/)(?:fixtures?|mocks?|__mocks__|samples?)(?:/|$)")

_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}

# Known external providers whose payloads/webhooks commonly drift. A file
# "touches" a provider when one of these tokens appears in it.
_PROVIDERS: tuple[tuple[str, re.Pattern], ...] = (
    ("stripe", re.compile(r"\bstripe\b", re.IGNORECASE)),
    ("github", re.compile(r"\bgithub\b|\boctokit\b", re.IGNORECASE)),
    ("twilio", re.compile(r"\btwilio\b", re.IGNORECASE)),
    ("sendgrid", re.compile(r"\bsendgrid\b", re.IGNORECASE)),
    ("slack", re.compile(r"\bslack\b", re.IGNORECASE)),
    ("openai", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("anthropic", re.compile(r"\banthropic\b", re.IGNORECASE)),
    ("aws", re.compile(r"\bboto3\b|\baws-sdk\b", re.IGNORECASE)),
    ("paypal", re.compile(r"\bpaypal\b", re.IGNORECASE)),
)

# Markers that a file processes external EVENTS (the highest-drift surface).
_WEBHOOK_MARK = re.compile(
    r"webhook|event\.type|constructEvent|payload\[|event\[\"|event\['",
    re.IGNORECASE)

# Optional-read shapes whose null path is the danger:
#   js/ts:  x?.a?.b        (optional chaining)
#   py:     d.get("k")     (dict get with default None)
_OPT_CHAIN = re.compile(r"\w+\?\.\w+(?:\?\.\w+)+")
_PY_GET = re.compile(r"\.get\(\s*['\"][A-Za-z_][\w.]*['\"]\s*\)")

# Loudness markers: if the surrounding window contains any of these, the
# null path is presumed handled loudly enough.
_LOUD = re.compile(
    r"raise\b|throw\b|logger\.|logging\.|console\.(?:error|warn)|"
    r"log\.(?:error|warn|warning)|alert", )

# Explicit API version pins (per provider ecosystems).
_PIN = re.compile(
    r"""(?:apiVersion|api_version|API_VERSION|stripe_version|Stripe-Version)
        \s*[:=]\s*['"]([^'"]{4,40})['"]""",
    re.VERBOSE)

# Markers that some test exercises a live / freshly-recorded payload.
_LIVE_TEST_MARK = re.compile(
    r"live[_-]?mode|record[_-]?fresh|@live|LIVE_TEST|smoke.*live|"
    r"real[_-]?payload|sandbox[_-]?e2e", re.IGNORECASE)

_MAX_PER_FILE = 3


def _walk(repo_root: str, exts: set):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
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


def _slug(kind: str, rel: str, extra: str = "") -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", f"{rel}-{extra}".lower()).strip("-")
    return f"drift-{kind}-{safe}"


def _is_test_path(rel: str) -> bool:
    parts = rel.lower().split("/")
    if any(p in _TEST_DIRS for p in parts):
        return True
    # Probe/oracle sources embed the very patterns they hunt (provider
    # tokens, optional-read shapes) as regex literals and doc examples --
    # scanning them is guaranteed self-noise.
    if parts[-1].endswith("_probe.py") or "recursive_improvement" in parts:
        return True
    return bool(_TEST_FILE_RE.search(parts[-1]))


def _provider_of(src: str) -> str | None:
    for name, pat in _PROVIDERS:
        if pat.search(src):
            return name
    return None


# ---------------------------------------------------------------------------
# Oracle 1: silent optional reads in webhook/event-handler code
# ---------------------------------------------------------------------------

def silent_optional_external_read(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _CODE_EXTS):
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None:
            continue
        provider = _provider_of(src)
        if provider is None or not _WEBHOOK_MARK.search(src):
            continue
        per_file = 0
        for m in list(_OPT_CHAIN.finditer(src)) + list(_PY_GET.finditer(src)):
            if per_file >= _MAX_PER_FILE:
                break
            # Loudness window: +/- 300 chars around the read.
            lo, hi = max(0, m.start() - 300), min(len(src), m.end() + 300)
            if _LOUD.search(src[lo:hi]):
                continue
            lineno = _line_of(src, m.start())
            per_file += 1
            findings.append({
                "slug": _slug("silent-read", rel, str(lineno)),
                "title": (f"{rel}:{lineno} reads a {provider} payload field "
                          f"optionally with no loud null path nearby -- if the "
                          f"provider moves/renames the field, this becomes a "
                          f"silent no-op (the exact billingAnchorDay incident "
                          f"shape)"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.75,
                "evidence": f"{rel}:{lineno} `{m.group(0)[:70]}`",
                "proposed_action": (
                    "treat a missing external field as an EVENT: log it with "
                    "the payload's api-version identifier (or raise) on the "
                    "null path, and add a documented fallback chain for the "
                    "field's known locations"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# Oracle 2: fixtures exist for a provider, no live-payload test marker
# ---------------------------------------------------------------------------

def stale_fixture_risk(repo_root: str) -> list[dict]:
    provider_fixtures: dict[str, str] = {}   # provider -> example fixture rel
    live_marked: set[str] = set()            # providers with live-test markers

    for path in _walk(repo_root, _CODE_EXTS | {".json"}):
        rel = _rel(repo_root, path)
        src = _read(path)
        if src is None:
            continue
        provider = _provider_of(src)
        if provider is None:
            continue
        if _FIXTURE_DIR_RE.search(rel.lower()) or (
                _is_test_path(rel) and rel.endswith(".json")):
            provider_fixtures.setdefault(provider, rel)
        if _is_test_path(rel) and _LIVE_TEST_MARK.search(src):
            live_marked.add(provider)

    findings: list[dict] = []
    for provider, example in sorted(provider_fixtures.items()):
        if provider in live_marked:
            continue
        findings.append({
            "slug": _slug("stale-fixtures", provider),
            "title": (f"integration tests for '{provider}' appear to be "
                      f"fixture-only (e.g. {example}) with no live/recorded-"
                      f"fresh test marker anywhere -- fixtures prove the "
                      f"logic against the past; only a current payload "
                      f"proves it against the present"),
            "area": example,
            "severity": "medium",
            "confidence": 0.55,
            "evidence": f"fixtures present ({example}); no live-test marker",
            "proposed_action": (
                f"add one test that consumes a REAL current {provider} "
                f"payload (sandbox/live probe or freshly recorded), tag it "
                f"with a live-mode marker, and run it on every SDK upgrade "
                f"or api-version pin change; if fixture-only is deliberate, "
                f"record the owner and refresh cadence to close as wontfix"
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# Oracles 3+4: version pinning -- absent, or inconsistent across the repo
# ---------------------------------------------------------------------------

_CLIENT_CTOR = re.compile(
    r"""(?:new\s+Stripe\s*\(|stripe\.Stripe\s*\(|require\(['"]stripe['"]\)\s*\(
        |Stripe\s*\(\s*process\.env)""",
    re.VERBOSE)


def api_version_pins(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    pins: dict[str, list[tuple[str, str, int]]] = {}  # provider -> [(pin, rel, line)]
    unpinned: list[tuple[str, str, int]] = []

    for path in _walk(repo_root, _CODE_EXTS):
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None:
            continue
        provider = _provider_of(src)
        if provider is None:
            continue
        file_pins = _PIN.findall(src)
        for p in file_pins:
            m = _PIN.search(src)
            pins.setdefault(provider, []).append(
                (p, rel, _line_of(src, m.start()) if m else 0))
        if provider == "stripe" and _CLIENT_CTOR.search(src) and not file_pins:
            m = _CLIENT_CTOR.search(src)
            unpinned.append((rel, provider, _line_of(src, m.start())))

    # Oracle 3: unpinned client construction
    for rel, provider, lineno in unpinned[:5]:
        findings.append({
            "slug": _slug("unpinned", rel, str(lineno)),
            "title": (f"{rel}:{lineno} constructs a {provider} client with no "
                      f"explicit apiVersion pin in the file -- the effective "
                      f"contract version is whatever the SDK/account default "
                      f"is today, and it changes without a code change"),
            "area": rel,
            "severity": "medium",
            "confidence": 0.55,
            "evidence": f"{rel}:{lineno} client constructed, no pin in file",
            "proposed_action": (
                "pin the API version explicitly at client construction (one "
                "shared constant), and treat changing the pin as a change "
                "that requires the live-payload test to run"
            ),
        })

    # Oracle 4: pins that disagree for the same provider
    for provider, entries in pins.items():
        distinct = {p for p, _, _ in entries}
        if len(distinct) > 1:
            locs = "; ".join(f"{r}:{l}='{p}'" for p, r, l in entries[:4])
            findings.append({
                "slug": _slug("pin-disagree", provider),
                "title": (f"{provider} API version pinned to {len(distinct)} "
                          f"different values across the repo -- different "
                          f"code paths are speaking to different versions of "
                          f"the same counterparty"),
                "area": entries[0][1],
                "severity": "high",
                "confidence": 0.65,
                "evidence": locs,
                "proposed_action": (
                    "consolidate to one shared version constant; if the "
                    "split is a deliberate migration, record which paths are "
                    "on which version and the completion criterion"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_contract_drift_probes(repo_root: str) -> list[dict]:
    """All contract-drift department findings."""
    out: list[dict] = []
    for oracle in (
        silent_optional_external_read,
        stale_fixture_risk,
        api_version_pins,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
