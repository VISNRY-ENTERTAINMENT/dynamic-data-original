# Observability Anchors — TEMPLATE

> Copy to your project as `OBSERVABILITY.md`. This file is the Tier-2 auditor's
> measuring stick for the observability department. The deterministic
> `observability_probe.py` also uses the standards described here.

## What "Observable Enough" Means for This Project

[CUSTOMIZE: one paragraph. Example:]

Every failure that can affect a user must produce a log line at WARNING or higher
with enough context to reproduce the failure. Every background job must report
completion (success or failure) via a structured event. On-call should be able to
answer "what failed, when, and for whom" from logs alone, without reproducing the
issue.

## Required Observability Per Code Pattern

### HTTP Handlers
- All 5xx responses must log the exception with stack trace and request context.
- 4xx responses on auth/permission paths must log the reason (not the credential).
- Slow responses (> [CUSTOMIZE: e.g. 2s]) must log a warning with the endpoint
  and elapsed time.

### Background Jobs / Async Workers
- Every job must log on start and on completion (success or failure).
- Every job must have a timeout. Timeout must produce a log at ERROR level.
- Unhandled exceptions in background tasks must NOT be silently swallowed.

### Database / External Service Calls
- Failed calls must be logged with the operation name and error.
- Retry logic must log each retry attempt at DEBUG and the final failure at ERROR.
- Circuit-breaker state transitions must be logged.

### Event/Message Processing
- Every message that fails processing must be logged with enough context to
  re-process it (message id, payload summary, error).
- Dead-letter queue events must generate alerts.

## Banned Patterns

These patterns are flagged deterministically by `observability_probe.py`; they
should never appear in production code:

- `except: pass` — silent swallow of all exceptions
- `except Exception: pass` — silent swallow of all standard exceptions
- `catch(e) {}` — empty catch block in JS/TS
- Goroutines / threads with no error boundary and no done callback

[CUSTOMIZE: add project-specific banned patterns.]

## Metrics Contract

[CUSTOMIZE: list the metrics your system must emit. Example:]
- `http_requests_total{endpoint, method, status}` — every HTTP handler
- `job_duration_seconds{job_name}` — every background job
- `db_query_duration_seconds{table, operation}` — every DB call
- `external_call_duration_seconds{service}` — every external API call
