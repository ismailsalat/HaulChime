# HaulChime Logging & Audit System

## Architecture in one paragraph

Two layers. **Logs** (stdout, structured JSON in production) are the
debugging trail — Railway captures and retains them. **Audit records**
(`lead_activity` table, append-only) are the *proof* — who did what to which
lead, when, from where. `logger.audit()` writes both at once: a database row
and a matching log line, tied together by the same `request_id`. Application
code never calls `print()` or `logging` directly; everything goes through
`backend/logger.py`.

## Identifiers

| Prefix | Example | Meaning | Created |
|---|---|---|---|
| `req_` | req_6fbb50c9027a | One API request | app.py before_request (or honored from X-Request-ID header) |
| `HC-`  | HC-20260716-4831 | One lead | intake |
| `sub_` | sub_9653bc43eb89 | One form submission attempt | intake |
| `dlv_` | dlv_1ba43f19cbb3 | One email delivery attempt | notification loop |
| `act_` | act_91cf7a9598e6 | One audit row | logger.audit() |

The `request_id` follows a request through Turnstile → validation → dedupe →
scoring → routing → DB insert → every email → the response (returned in the
`X-Request-ID` header and in error responses).

## Event names (stable — never rename, only add)

- `intake.*` — turnstile_attempted/passed/failed, validation_passed/failed,
  duplicate_checked, honeypot_triggered, rate_limited, photos_stored, photo_rejected
- `lead.*` — created, scored, duplicate_detected, status_<newstatus>
- `routing.*` — matched, no_contractor
- `delivery.*` — attempted, sent, failed, skipped.
  **"sent" means our mail backend accepted it** — not that it reached an
  inbox. True delivered/bounced tracking requires your email provider's
  webhooks (add a webhook route that calls `audit("delivery.delivered", ...)`)
- `admin.*` — login_success, login_failed, lead_updated (one event per
  changed field, with old and new values), partner_saved, csv_exported
- `external.*` — call_started (debug), call_completed, call_failed
- `api.request` — every request: method, path, status, duration_ms, actor
- `app.startup`, `db.connected`, `db.connection_failed`, `retention.purged`

## Log levels

`LOG_LEVEL` env var: DEBUG < INFO < WARNING < ERROR < CRITICAL.
- **debug** — dev details (external.call_started, /api/config health noise)
- **info** — normal lifecycle events
- **warn** — suspicious/recoverable (honeypot, duplicate, no contractor, bad login)
- **error** — failed operations (delivery.failed, unhandled API errors)
- **critical** — data-integrity/system failures (db.connection_failed)

`LOG_FORMAT=json` (production) or `pretty` (development, human-readable
lines). `LOG_STACKTRACES=true` includes stacks (dev only).

## Sensitive data rules

Logs never contain raw phones, emails, tokens, secrets, or consent text:
- phone → `***-***-0142`, email → `i***@example.com`
- `turnstile_token`, `password`, `api_key`, cookies, sessions → `[REDACTED]`
- IPs are salted-hashed (`IP_HASH_SALT`) before storage in audit rows
- `logger.sanitize()` runs on every context dict AND string defensively

The **database** stores full contact details and the exact
`original_submission` JSON — that's the protected store, which is the point:
proof lives in Postgres, masked breadcrumbs live in logs.

## The audit table (`lead_activity`)

id, activity_id, lead_id, lead_reference, contractor_id, request_id,
event_type, event_status (ok|warn|failed), actor_type
(customer|admin|contractor|system), actor_id, previous_value, new_value,
metadata_json, ip_hash, user_agent, created_at.

**Append-only policy:** nothing in the app updates or deletes these rows.
Corrections are new events. A retry is a new `delivery.attempted` — the
earlier failure stays. Admin edits create one `admin.lead_updated` per field
with old→new preserved; the lead's `original_submission` column is never
touched after creation, so the as-submitted evidence survives any editing.

## How to investigate

**"What happened to lead HC-20260716-4831?"**
Admin → open the lead → Activity timeline shows the full story chronologically.
Or Admin → Logs → filter by Lead ID. In Railway logs: search `HC-20260716-4831`.

**"A customer says they submitted but nothing arrived."**
Railway logs → search their masked phone tail (`***-***-0142`) or the time
window → find `intake.validation_failed` (which fields), `honeypot`,
`rate_limited`, or `turnstile.failed`. If `lead.created` exists, follow its
`request_id` to the delivery events.

**"Was the lead actually delivered to the contractor?"**
Lead timeline → `delivery.attempted` + `delivery.sent` with template
partner_notification. Remember: sent = accepted by mail backend. For
inbox-level proof, wire provider webhooks (see event names above).

**"Which admin changed this price?"**
Logs page → event `admin.lead_updated` → actor_id column shows the username;
previous/new values are on the row.

## Adding a new event

```python
import logger
from logger import audit

logger.info("thing.happened", lead=ref, detail=x)      # log only
audit("thing.happened", lead_obj, actor_type="system") # log + proof row
```
Name it `domain.action`, add it to the list above, never log raw PII
(sanitize handles known keys — mask anything unusual yourself).

## Retention

- stdout logs: retained by Railway per your plan (no app action needed)
- audit rows: kept forever by default. `python retention.py` deletes
  non-protected rows older than `AUDIT_RETENTION_DAYS` *only if that env var
  is explicitly set*, and never deletes lead.created / lead.scored /
  delivery.* / routing.* proof events. Run it manually or as a Railway cron.

## Health & monitoring

Startup logs version (`RAILWAY_GIT_COMMIT_SHA`) and environment;
db.connected/failed on boot; every request timed. To watch for trouble in
Railway logs: `delivery.failed` (email provider issues),
`routing.no_contractor` spikes (coverage gaps), `intake.validation_failed`
spikes (form/ads mismatch), `honeypot`/`rate_limited` spikes (spam),
`duplicate_detected` spikes.

## Connecting a monitoring platform later

Everything is stdout JSON with stable event names — point Railway's log
drain at Datadog/Axiom/Better Stack and it ingests unchanged. No code
rewrite needed; that modularity is deliberate.
