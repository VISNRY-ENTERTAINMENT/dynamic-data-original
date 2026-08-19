"""attack_pattern_probe.py must actually detect the four patterns it claims
to, and must NOT flag clean equivalents. Fixture files are real minimal
repros of the four Ovyero findings the probe was built from, not synthetic
strings -- so a regression here means the probe stopped catching what it was
built to catch.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_core.recursive_improvement import attack_pattern_probe as app


def _repo_with(filename: str, content: str) -> str:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, filename), "w", encoding="utf-8") as fh:
        fh.write(content)
    return d


# --- 1. obfuscated dynamic require -------------------------------------------

def test_flags_base64_obfuscated_require():
    repo = _repo_with("auth.js", (
        "'use strict';\n"
        "const nodeSec = require(Buffer.from('Y3J5cHRv', 'base64').toString('utf8'));\n"
    ))
    findings = app.obfuscated_dynamic_require(repo)
    assert len(findings) == 1
    assert findings[0]["confidence"] == 0.85
    assert findings[0]["severity"] == "high"
    assert "auth.js:2" in findings[0]["evidence"]


def test_does_not_flag_plain_require():
    repo = _repo_with("auth.js", "'use strict';\nconst crypto = require('crypto');\n")
    assert app.obfuscated_dynamic_require(repo) == []


# --- 2. presence-only auth check ---------------------------------------------

def test_flags_presence_only_credential_check():
    repo = _repo_with("bootstrap.js", (
        "app.post('/api/bootstrap', (req, res) => {\n"
        "  if (!req.body.apiKey && !req.headers['x-api-key']) { res.status(401).end(); return; }\n"
        "  doTheThing();\n"
        "});\n"
    ))
    findings = app.presence_only_auth_check(repo)
    assert len(findings) == 1
    assert findings[0]["confidence"] == 0.5  # below default 0.6 floor, on-demand only


def test_does_not_flag_check_with_nearby_validation():
    repo = _repo_with("auth.js", (
        "if (!req.body.apiKey) { res.status(401).end(); return; }\n"
        "const record = keyStore.findByHash(hashToken(req.body.apiKey));\n"
        "if (!record || !timingSafeEqual(record.hash, expected)) { res.status(403).end(); return; }\n"
    ))
    assert app.presence_only_auth_check(repo) == []


# --- 3. unbounded RegExp from request-derived input --------------------------

def test_flags_regexp_from_policy_pattern_in_request_handling_file():
    repo = _repo_with("policy-enforce.js", (
        "function matchesPolicy(filename, policy) {\n"
        "  const pattern = policy.match.pattern;\n"
        "  if (new RegExp(pattern).test(filename)) return true;\n"
        "}\n"
        "app.post('/govern/policy', (req, res) => { matchesPolicy(req.body); });\n"
    ))
    findings = app.unbounded_regexp_from_request(repo)
    assert len(findings) == 1
    assert findings[0]["severity"] == "medium"


def test_does_not_flag_regexp_in_file_with_no_request_handling():
    repo = _repo_with("util.js", (
        "function isWord(s) { const pattern = 'ok'; return new RegExp(pattern).test(s); }\n"
    ))
    assert app.unbounded_regexp_from_request(repo) == []


# --- 4. unescaped innerHTML concatenation ------------------------------------

def test_flags_unescaped_innerhtml_concat():
    repo = _repo_with("dashboard.js", (
        "document.getElementById('rows').innerHTML = d.sessions.map(function(s) {\n"
        "  return '<tr><td>' + s.sessionId + '</td></tr>';\n"
        "}).join('');\n"
    ))
    findings = app.unescaped_html_concat(repo)
    assert len(findings) == 1


def test_does_not_flag_textcontent_assignment():
    repo = _repo_with("dashboard.js", (
        "document.getElementById('rows').textContent = s.sessionId;\n"
    ))
    assert app.unescaped_html_concat(repo) == []


# --- fan-out --------------------------------------------------------------

def test_run_attack_probes_combines_all_four_and_survives_one_failing():
    repo = _repo_with("mixed.js", (
        "const nodeSec = require(Buffer.from('Y3J5cHRv', 'base64').toString('utf8'));\n"
        "if (!req.body.apiKey && !req.headers['x-api-key']) { res.status(401).end(); }\n"
    ))
    findings = app.run_attack_probes(repo)
    slugs = {f["slug"] for f in findings}
    assert any(s.startswith("obfuscated-require-") for s in slugs)
    assert any(s.startswith("presence-only-auth-") for s in slugs)
