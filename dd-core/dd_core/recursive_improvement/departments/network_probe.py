"""Network-exposure department -- deterministic oracle. NO model.

Finds code and configuration whose NETWORK REACHABILITY is broader than the
pattern's context justifies: bind-to-all-interfaces in application code,
sensitive container ports published to every host interface, debug/inspect
listeners reachable off-box, and infrastructure rules open to the entire
internet. This is the reachability question asked at the network layer:
"who can connect to this?" -- and, like unreachable code, the answer is
invisible in tests because loopback and all-interfaces behave identically
on the machine running the test suite.

Doctrine source: AGENT_SYSTEM_THINK/NETWORK_EXPOSURE_AND_PORTS.md.
Key facts the oracles encode:
  - A port number is convention, not a control; the BIND ADDRESS is the
    control (127.0.0.1 = same machine only; 0.0.0.0/:: = every interface).
  - 0.0.0.0 is REQUIRED inside a container and dangerous outside one, so a
    static scan cannot be certain -- confidences are calibrated for that.
  - Docker `ports: "8080:80"` publishes on ALL host interfaces (and
    historically bypasses host INPUT firewall rules); the restricted form
    is "127.0.0.1:8080:80".
  - Debug interfaces (node --inspect, JDWP, docker daemon 2375) are
    arbitrary-code-execution by design; binding them beyond loopback is an
    invitation, not a bug.
  - Datastores (Redis/Mongo/Elasticsearch/...) assume a trusted network and
    ship with weak-or-no auth; publishing their ports is the standing cause
    of mass breaches.

Precision bar (same design constraint as reachability_probe): a probe that
fires 200 times is a lint rule, not a governance finding. Every oracle here
restricts itself to explicit high-signal patterns and skips test/example
trees; the bind-all oracle additionally caps per-file findings.

Oracles:
  1. code-bind-all-interfaces  -- 0.0.0.0/:: passed to listen/run/bind in
     app source (py/js/ts), outside tests
  2. compose-sensitive-publish -- docker-compose ports: entries publishing a
     SENSITIVE container port without a 127.0.0.1: host prefix
  3. debug-listener-exposed    -- --inspect/--inspect-brk on 0.0.0.0, JDWP
     address=*/0.0.0.0, dockerd -H tcp:// without TLS, EXPOSE 9229/5005/2375
  4. iac-open-to-world         -- 0.0.0.0/0 (or ::/0) ingress in
     Terraform/CloudFormation for ports other than 80/443

All findings use slug prefix `net-` so they live at arch.gap:net-* in the
claim store.

Confidence calibration:
  0.85 -- debug listener / docker daemon reachable beyond loopback, or a
          sensitive datastore port published unrestricted: no legitimate
          steady-state reading exists
  0.70 -- IaC ingress from the whole internet on a non-web port: usually
          wrong, occasionally a deliberate public service
  0.55 -- bind-all in app code: correct inside a container, wrong on a
          host; static analysis cannot see the deployment context
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
    "examples", "example", "fixtures",
})
_TEST_FILE_RE = re.compile(
    r"(?:\.test\.|\.spec\.|_test\.|test_|selftest)", re.IGNORECASE)

_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
_IAC_EXTS = {".tf", ".json", ".yml", ".yaml"}

# Ports whose exposure beyond loopback/private networks has no legitimate
# steady state: datastores with weak-default auth, remote desktop/file
# sharing, cluster control planes, and code-execution debug interfaces.
_SENSITIVE_PORTS = frozenset({
    "1433",   # SQL Server
    "2375",   # Docker daemon (no TLS) -- root on host
    "2379",   # etcd
    "3306",   # MySQL/MariaDB
    "3389",   # RDP
    "5005",   # Java JDWP debug
    "5432",   # PostgreSQL
    "5672",   # RabbitMQ
    "5900",   # VNC
    "6379",   # Redis
    "6443",   # Kubernetes API
    "8500",   # Consul
    "9092",   # Kafka
    "9200",   # Elasticsearch
    "9229",   # Node.js inspector
    "10250",  # kubelet
    "11211",  # Memcached
    "27017",  # MongoDB
})

# Web ports where internet-wide ingress is the normal, intended state.
_PUBLIC_OK_PORTS = frozenset({"80", "443"})

_MAX_PER_FILE_BINDALL = 2  # cap oracle-1 noise per file


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
    return f"net-{kind}-{safe}"


def _is_test_path(rel: str) -> bool:
    parts = rel.lower().split("/")
    if any(p in _TEST_DIRS for p in parts):
        return True
    return bool(_TEST_FILE_RE.search(parts[-1]))


# ---------------------------------------------------------------------------
# Oracle 1: bind-to-all-interfaces in application code
# ---------------------------------------------------------------------------
# Matches 0.0.0.0 (or "::" as a bind host) appearing inside a call that
# plausibly opens a listener. Restricting to call-adjacent occurrences keeps
# comments and docs from firing.

_BIND_ALL = re.compile(
    r"""(?:
          \.listen\s*\( [^)]{0,120}? ['"](?:0\.0\.0\.0|::)['"]
        | \.run\s*\(    [^)]{0,120}? host\s*=\s*['"]0\.0\.0\.0['"]
        | host\s*[=:]\s*['"](?:0\.0\.0\.0|::)['"]
        | ['"](?:0\.0\.0\.0|::)['"]\s*,\s*\d{2,5}
        | --host[=\s]+(?:0\.0\.0\.0|::)\b
        | HOST\s*=\s*['"]?0\.0\.0\.0
        )""",
    re.VERBOSE,
)


def code_bind_all_interfaces(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _CODE_EXTS):
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None or ("0.0.0.0" not in src and '"::"' not in src
                           and "'::'" not in src):
            continue
        per_file = 0
        for m in _BIND_ALL.finditer(src):
            if per_file >= _MAX_PER_FILE_BINDALL:
                break
            lineno = _line_of(src, m.start())
            per_file += 1
            findings.append({
                "slug": _slug("bind-all", rel, str(lineno)),
                "title": (f"{rel}:{lineno} binds a listener to all interfaces "
                          f"(0.0.0.0/::) -- correct inside a container, exposes "
                          f"the service to every network the host touches "
                          f"otherwise"),
                "area": rel,
                "severity": "medium",
                "confidence": 0.55,
                "evidence": f"{rel}:{lineno} `{m.group(0).strip()[:80]}`",
                "proposed_action": (
                    "decide the exposure explicitly: if this process runs "
                    "directly on a host, bind 127.0.0.1 (or the private "
                    "interface) and front it with a proxy; if it only ever "
                    "runs in a container, record that context here so the "
                    "claim can be closed as wontfix with the reason named"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# Oracle 2: docker-compose publishing a sensitive port to all interfaces
# ---------------------------------------------------------------------------
# ports: entries of the form "HOST:CONTAINER" (optionally with protocol)
# publish on ALL host interfaces unless prefixed with an IP. Publishing a
# datastore/debug port that way is the highest-signal misconfiguration in
# generated compose files.

_COMPOSE_NAME = re.compile(
    r"(?:^|/)(?:docker-)?compose[^/]*\.ya?ml$", re.IGNORECASE)
_PORT_ENTRY = re.compile(
    r"""^\s*-\s*['"]?
        (?P<hostip>\d{1,3}(?:\.\d{1,3}){3}:)?
        (?P<host>\d{2,5}):
        (?P<container>\d{2,5})
        (?:/(?:tcp|udp))?['"]?\s*$""",
    re.VERBOSE,
)


def compose_sensitive_publish(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, {".yml", ".yaml"}):
        rel = _rel(repo_root, path)
        if not _COMPOSE_NAME.search(rel) or _is_test_path(rel):
            continue
        src = _read(path)
        if src is None:
            continue
        for i, line in enumerate(src.splitlines(), start=1):
            m = _PORT_ENTRY.match(line)
            if not m:
                continue
            container = m.group("container")
            hostip = m.group("hostip")
            if container not in _SENSITIVE_PORTS:
                continue
            if hostip and hostip.startswith("127."):
                continue  # loopback-restricted: the correct form
            findings.append({
                "slug": _slug("compose-publish", rel, f"{i}-{container}"),
                "title": (f"{rel}:{i} publishes sensitive container port "
                          f"{container} on all host interfaces -- Docker "
                          f"published ports also bypass host INPUT firewall "
                          f"rules, so 'the firewall blocks it' may be false"),
                "area": rel,
                "severity": "high",
                "confidence": 0.85,
                "evidence": f"{rel}:{i} `{line.strip()[:80]}`",
                "proposed_action": (
                    f"restrict to loopback ('127.0.0.1:{m.group('host')}:"
                    f"{container}') or remove the mapping and use the compose "
                    f"network for inter-container access; if this host port "
                    f"must be LAN-reachable, bind the specific interface IP "
                    f"and record why"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# Oracle 3: debug / management listeners reachable beyond loopback
# ---------------------------------------------------------------------------
# These interfaces execute arbitrary code by design. Any non-loopback bind
# is a finding; there is no legitimate steady-state exception.

_DEBUG_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("node-inspect",
     re.compile(r"--inspect(?:-brk)?[=\s](?:0\.0\.0\.0|::|\*)")),
    ("jdwp",
     re.compile(r"jdwp[^\n]{0,80}address=(?:\*|0\.0\.0\.0)[:,]")),
    ("docker-daemon-tcp",
     re.compile(r"-H\s+tcp://(?:0\.0\.0\.0|::|\*)?:2375\b")),
    ("expose-debug-port",
     re.compile(r"^\s*EXPOSE\s+(?:.*\s)?(?:9229|5005|2375)\b",
                re.IGNORECASE | re.MULTILINE)),
)

_DEBUG_SCAN_EXTS = _CODE_EXTS | {".sh", ".yml", ".yaml", ".json", ""}


def _walk_debug(repo_root: str):
    """Code + shell + yaml + Dockerfiles (extensionless)."""
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in _DEBUG_SCAN_EXTS or f.lower().startswith("dockerfile"):
                yield os.path.join(root, f)


def debug_listener_exposed(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk_debug(repo_root):
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None:
            continue
        for kind, pat in _DEBUG_PATTERNS:
            m = pat.search(src)
            if not m:
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug(f"debug-{kind}", rel, str(lineno)),
                "title": (f"{rel}:{lineno} exposes a debug/management "
                          f"listener ({kind}) beyond loopback -- these "
                          f"interfaces execute arbitrary code by design; "
                          f"reachable means owned"),
                "area": rel,
                "severity": "high",
                "confidence": 0.85,
                "evidence": f"{rel}:{lineno} `{m.group(0).strip()[:80]}`",
                "proposed_action": (
                    "bind the debugger to 127.0.0.1 (or remove the flag from "
                    "the shipped invocation entirely); if remote debugging is "
                    "genuinely required, tunnel over SSH instead of widening "
                    "the bind"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# Oracle 4: infrastructure-as-code ingress open to the whole internet
# ---------------------------------------------------------------------------
# Terraform / CloudFormation rules admitting 0.0.0.0/0 (or ::/0). 80/443
# are exempt (public web is public by design); everything else fires.

_CIDR_WORLD = re.compile(r"(?:0\.0\.0\.0/0|::/0)")
_TF_PORT = re.compile(r"(?:from_port|to_port)\s*=\s*(\d{1,5})")
_CFN_PORT = re.compile(r"(?:FromPort|ToPort)['\"]?\s*[:=]\s*['\"]?(\d{1,5})")


def iac_open_to_world(repo_root: str) -> list[dict]:
    findings: list[dict] = []
    for path in _walk(repo_root, _IAC_EXTS):
        rel = _rel(repo_root, path)
        if _is_test_path(rel):
            continue
        src = _read(path)
        if src is None or not _CIDR_WORLD.search(src):
            continue
        # Windowed check: for each world-CIDR occurrence, look for port
        # declarations within +/- 400 chars (same rule block, heuristically).
        for m in _CIDR_WORLD.finditer(src):
            lo, hi = max(0, m.start() - 400), min(len(src), m.end() + 400)
            window = src[lo:hi]
            ports = set(_TF_PORT.findall(window)) | set(_CFN_PORT.findall(window))
            if not ports:
                continue  # no port context in this window: skip, stay precise
            bad = sorted(p for p in ports if p not in _PUBLIC_OK_PORTS and p != "0")
            if not bad:
                continue
            lineno = _line_of(src, m.start())
            findings.append({
                "slug": _slug("iac-world", rel, f"{lineno}-{'-'.join(bad[:3])}"),
                "title": (f"{rel}:{lineno} allows ingress from the entire "
                          f"internet (0.0.0.0/0) on port(s) {', '.join(bad)} "
                          f"-- non-web ports open to the world are scanned "
                          f"within minutes of creation"),
                "area": rel,
                "severity": "high",
                "confidence": 0.70,
                "evidence": f"{rel}:{lineno} world CIDR with ports {bad}",
                "proposed_action": (
                    "scope the source CIDR to the networks that actually need "
                    "access (VPN range, bastion, peer VPC); if this service "
                    "is deliberately public on a non-web port, record that "
                    "decision so the claim can close as wontfix with an owner"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run_network_probes(repo_root: str) -> list[dict]:
    """All network-exposure department findings."""
    out: list[dict] = []
    for oracle in (
        code_bind_all_interfaces,
        compose_sensitive_publish,
        debug_listener_exposed,
        iac_open_to_world,
    ):
        try:
            out += oracle(repo_root)
        except Exception:
            pass
    return out
