"""Deterministic attack-pattern probe. NO model at all.

Same doctrine as the other structural probes (probes.py, wiring.py,
gate_calibration.py): compute a FACT about the code with a regex/text scan,
not a judgment call, and record it with source `reflex-probe` so a model is
never in this discovery path either.

Origin: built from a real static security review of a production governance server (an internal security review) cross-checked against a hand-built attack
technique corpus (an attack-technique corpus, MITRE ATT&CK-mapped). Every
pattern below is something that was actually found in real, shipped code
during that review -- not a hypothetical vulnerability class. The value of
this probe is turning a one-time audit into a standing, automatic check: the
same four patterns get flagged the next time they're written, in this project
or any other project using dd-core.

Detection strategy: plain regex over source text, deliberately NOT an AST
parse. dd-core is pure-stdlib with zero dependencies (see
SETUP_FOR_ANOTHER_PROJECT.md); a real JS/TS parser would break that. Regex
scanning is conservative by construction here -- every pattern requires a
fairly specific textual shape, so false negatives are expected and accepted
(same tradeoff the other probes make), but a match is a real, citable
instance of the pattern, not a guess.

Confidence calibration (matters for whether a finding auto-escalates through
the existing floor/severity gate in gate.py -- see record_attack_probe_findings
in runner.py):
  * obfuscated_dynamic_require   -- HIGH confidence (0.85). There is no
    legitimate reason to base64-decode a bare module-name string immediately
    before require()/import -- this is either intentional evasion of naive
    string-matching review tooling (ATT&CK T1036, and the exact pattern found
    in a production governance server's auth/file/session/
    secure-server.js) or, generously, a strange stylistic choice with the
    identical shape as an attack technique. Either way it earns a look.
  * presence_only_auth_check     -- capped at 0.5 (below the 0.6 default
    floor -- see config.py -- so it does NOT auto-escalate by default). This
    is the real, previously-confirmed pattern behind a production governance server's
    `/api/bootstrap` auth-bypass finding (checks a credential-shaped value is
    NON-EMPTY, never checks it against a key store), but proving "never
    validated ANYWHERE in this function" from a regex is inherently
    heuristic -- a real validation call slightly outside the scanned window
    is a plausible false positive. Stays on-demand (`dd_ri.py probe`) unless
    a project explicitly lowers its floor.
  * unbounded_regexp_from_request -- 0.5, same reasoning: `new RegExp(x)`
    where `x` is traced to req.body/req.query is the real shape of the ReDoS
    finding in a production policy-enforcement module, but whether `x` is actually
    attacker-reachable at that specific call site needs a human's read.
  * unescaped_html_concat        -- 0.5, same reasoning: string concatenation
    feeding innerHTML with a request-derived field is the real shape of
    a real stored-XSS finding, but distinguishing "session.id" (server-
    trusted) from "req.body.sessionId" (attacker-controlled) reliably needs
    more context than a regex window reliably has.
"""

from __future__ import annotations

import os
import re

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
         "build", "vendor", "site-packages", ".idea", ".vscode", "tests",
         "test", "reflex", ".reflex"}
_JS_LIKE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}

# --- pattern 1: obfuscated dynamic require/import ---------------------------
# require(Buffer.from('...', 'base64').toString(...))  -- the exact shape
# found in a production governance server's auth/file/session/
# secure-server.js. Also matches the atob() browser-side equivalent.
_OBFUSCATED_REQUIRE = re.compile(
    r"require\s*\(\s*Buffer\.from\([^)]*['\"]base64['\"]"
    r"|require\s*\(\s*atob\("
)

# --- pattern 2: presence-only auth check ------------------------------------
# `if (!x && !y) { ...401/unauthorized... }` where x/y come from a body/header
# credential field, with NOTHING that looks like a validity check (compare/
# verify/validate/hash/lookup/find against a store) within the same nearby
# window. This mirrors a production bootstrap endpoint: presence of `body.apiKey` /
# `x-api-key` header is checked, but the value is never looked up anywhere.
_CRED_PRESENCE_CHECK = re.compile(
    r"if\s*\(\s*!\s*[\w.\-\[\]'\"]*(?:apiKey|api_key|token|secret|credential)"
    r"[\w.\-\[\]'\"]*\s*(?:&&\s*!\s*[\w.\-\[\]'\"]*\s*)?\)",
    re.IGNORECASE,
)
_VALIDATION_HINT = re.compile(
    r"\b(?:verify|validate|compare|timingSafeEqual|hash(?:Token|Digest)?|"
    r"lookup|findByHash|getByHash|keyStore|checkKeyAccess)\b",
    re.IGNORECASE,
)
_WINDOW_CHARS = 600  # forward-scan window after the presence check

# --- pattern 3: unbounded RegExp built from request-derived input ----------
_NEW_REGEXP = re.compile(r"new\s+RegExp\s*\(\s*([A-Za-z_$][\w.$]*)\s*[,)]")
_REQUEST_DERIVED_HINT = re.compile(
    r"\breq\.(?:body|query|params|headers)\b|\bpolicy\.match\b", re.IGNORECASE
)

# --- pattern 4: unescaped concatenation into innerHTML ----------------------
# The assignment and the actual "+" concatenation are often separated by a
# callback's own braces (e.g. `.map(function(s) { return '<td>' + s.x + ... })`),
# so this is an anchor + forward-scan-window match, same shape as pattern 2,
# not a single contiguous regex.
_INNERHTML_ASSIGN = re.compile(r"\.innerHTML\s*=")
_CONCAT_FIELD = re.compile(
    r"\+\s*[\w.\[\]'\"]*\b\w+\.\w*(?:[Ii]d|[Nn]ame|[Ee]mail|[Vv]alue)\b"
)
_HTML_WINDOW_CHARS = 400


def _source_files(repo_root: str):
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in _JS_LIKE_EXTS:
                yield os.path.join(root, f)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def obfuscated_dynamic_require(repo_root: str) -> list[dict]:
    """ATT&CK T1036 (Masquerading) shape: a routine import disguised via
    base64/atob decoding immediately before require()/import, evading naive
    string-match review of what a file references."""
    findings = []
    for path in _source_files(repo_root):
        src = _read(path)
        if not src:
            continue
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        for m in _OBFUSCATED_REQUIRE.finditer(src):
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": f"obfuscated-require-{rel.replace('/', '-').replace('.', '-')}-{lineno}",
                "title": (f"obfuscated dynamic require/import in {rel}:{lineno} -- "
                          f"a routine module reference decoded from base64/atob "
                          f"immediately before require(), the same shape as "
                          f"ATT&CK T1036 (Masquerading)"),
                "area": rel,
                "severity": "high",
                "confidence": 0.85,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "replace with a plain require()/import of the literal module "
                    "name unless there is a specific, documented reason to "
                    "obfuscate it (e.g. evading a specific naive-scanner false "
                    "positive) -- if so, comment why at the call site"),
            })
    return findings


def presence_only_auth_check(repo_root: str) -> list[dict]:
    """The /api/bootstrap shape: a credential-shaped value is checked for
    presence (non-empty) but never checked for validity against any store
    within the surrounding code."""
    findings = []
    for path in _source_files(repo_root):
        src = _read(path)
        if not src:
            continue
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        for m in _CRED_PRESENCE_CHECK.finditer(src):
            window = src[m.end():m.end() + _WINDOW_CHARS]
            if _VALIDATION_HINT.search(window):
                continue  # a validation-shaped call appears nearby -- likely fine
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": f"presence-only-auth-{rel.replace('/', '-').replace('.', '-')}-{lineno}",
                "title": (f"credential checked for presence only, not validity, "
                          f"in {rel}:{lineno} -- no verify/validate/compare/hash/"
                          f"lookup call within {_WINDOW_CHARS} chars afterward"),
                "area": rel,
                "severity": "high",
                "confidence": 0.5,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "confirm the credential value is actually checked against a "
                    "key store somewhere in this handler; if not, this is a real "
                    "auth bypass (the confirmed /api/bootstrap pattern) -- add a "
                    "validity check, don't just gate on non-empty"),
            })
    return findings


def unbounded_regexp_from_request(repo_root: str) -> list[dict]:
    """The policy-enforcement ReDoS shape: new RegExp() built from a variable,
    in a file that also references request-derived input, with no visible
    length/complexity guard on the pattern before compilation."""
    findings = []
    for path in _source_files(repo_root):
        src = _read(path)
        if not src:
            continue
        if not _REQUEST_DERIVED_HINT.search(src):
            continue  # file never touches request input -- not attacker-reachable
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        for m in _NEW_REGEXP.finditer(src):
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": f"unbounded-regexp-{rel.replace('/', '-').replace('.', '-')}-{lineno}",
                "title": (f"new RegExp() built from a variable in {rel}:{lineno}, "
                          f"in a file that also handles request-derived input -- "
                          f"confirm the pattern source isn't attacker-controlled "
                          f"(ReDoS if it is)"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.5,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "trace whether the RegExp source string can reach req.body/"
                    "req.query/policy config from a client; if so, bound its "
                    "length and/or validate it against a safe-pattern allowlist "
                    "before compiling, or use a non-regex matcher (glob) instead"),
            })
    return findings


def unescaped_html_concat(repo_root: str) -> list[dict]:
    """The dashboard stored-XSS shape: innerHTML assignment via string
    concatenation that includes a per-record field (s.sessionId, item.name,
    etc.) instead of using textContent or an escaping helper."""
    findings = []
    for path in _source_files(repo_root):
        src = _read(path)
        if not src:
            continue
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        for m in _INNERHTML_ASSIGN.finditer(src):
            window = src[m.end():m.end() + _HTML_WINDOW_CHARS]
            if not _CONCAT_FIELD.search(window):
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": f"unescaped-innerhtml-{rel.replace('/', '-').replace('.', '-')}-{lineno}",
                "title": (f"innerHTML built via string concatenation of a "
                          f"per-record field in {rel}:{lineno} -- unescaped, "
                          f"the shape of a stored-XSS sink if that field is ever "
                          f"client-supplied"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.5,
                "evidence": f"{rel}:{lineno}",
                "proposed_action": (
                    "confirm the concatenated field's origin; if it is or could "
                    "ever be request-supplied (not a server-computed enum), "
                    "switch to textContent, a templating helper with escaping, "
                    "or explicit HTML-entity encoding before concatenation"),
            })
    return findings


def run_attack_probes(repo_root: str) -> list[dict]:
    """Every attack-pattern oracle's findings, combined. Same fan-out shape as
    probes.run_all_probes -- each oracle isolated so one failing never
    suppresses the others."""
    out = []
    for oracle in (
        obfuscated_dynamic_require,
        presence_only_auth_check,
        unbounded_regexp_from_request,
        unescaped_html_concat,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
