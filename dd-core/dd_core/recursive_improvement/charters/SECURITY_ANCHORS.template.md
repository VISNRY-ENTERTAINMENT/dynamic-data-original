# Security Anchors — TEMPLATE

> Copy to your project as `SECURITY_ANCHORS.md` (or the path you set in
> `reflex.config.json`). Fill in your project's specific threat model.
> This file is the Tier-2 auditor's measuring stick for the security department.

## Threat Model Summary

[CUSTOMIZE: one paragraph describing what this system does, who its users are,
and what an attacker would want to achieve. Example:]

This system accepts requests from external clients over HTTP/WebSocket. It stores
user data in a relational database and emits events to downstream consumers.
A successful attack could expose user data, modify records without authorization,
or cause denial-of-service.

## Authentication and Authorization

**Required guard on every external-facing endpoint:**
[CUSTOMIZE: name the function/decorator/middleware that must be present, e.g.
`@require_auth`, `assert_authenticated(request)`, `authMiddleware`]

**Privileged operations** (require elevated permissions):
[CUSTOMIZE: list operations that require admin/elevated roles]

**Public endpoints** (explicitly no auth required):
[CUSTOMIZE: list endpoints that are intentionally unauthenticated]

## STRIDE Threat Map (AI/Agent systems)

> This section applies when the system is built by or integrates an AI agent.

### Spoofing
- All inter-agent messages must be signed or carry a verifiable source claim.
- An agent must not be able to impersonate another agent or a human actor.

### Tampering
- The claims ledger is append-only; no agent may retract or overwrite another
  agent's claims without an explicit `retract` call with a recorded reason.
- Prompt injection from external content (web pages, documents, user input) must
  not be able to override agent instructions or invoke tool calls.

### Repudiation
- Every agent action that modifies state must produce an auditable claim with
  `source`, `author_kind`, and `recorded_at` stamped by the store, not the agent.

### Information Disclosure
- Secrets (API keys, tokens, credentials) must never appear in claims, logs, or
  diff output that the reflex loop reads.
- Error responses must not expose internal stack traces, database schemas, or
  file paths to unauthenticated callers.

### Denial of Service
- Any endpoint or agent capability that triggers expensive computation or
  external API calls must be rate-limited.
- Background tasks must have timeouts and circuit breakers.

### Elevation of Privilege
- An agent operating at a given trust level must not be able to invoke
  capabilities reserved for a higher trust level, even indirectly via tool chains.

## Never-Violate Rules

[CUSTOMIZE: list hard rules specific to your project. Examples:]
- No credentials in source code (detected deterministically by security_probe.py)
- No `eval()` / `exec()` on user-supplied input
- No SQL string concatenation (use parameterized queries)
- No pickle deserialization of untrusted data
- All file-path inputs from users must be resolved and validated against a base dir
