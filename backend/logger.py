"""
HaulChime centralized logging.

- Structured JSON logs in production (LOG_FORMAT=json), readable lines in dev.
- Every log carries: timestamp, level, event, request_id, plus lead/contractor/
  actor context when available.
- PII is masked in logs (phone -> ***-***-1234, email -> i***@example.com).
  Full values live only in the protected database, never in log output.
- audit() writes append-only rows to the lead_activity table AND emits a log
  line with the same event name, so the database is the proof and the logs
  are the debugging trail.

Event names are stable, dot-separated: lead.created, routing.matched,
delivery.attempted, admin.lead_updated, ... (see LOGGING.md).
"""
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from flask import g, has_request_context, request

# ---------------------------------------------------------------- masking
_PHONE_RE = re.compile(r"(?<![\w.-])\+?\d[\d\s\-().]{6,18}\d(?![\w-])")
_EMAIL_RE = re.compile(r"([^@\s])([^@\s]*)(@[^@\s]+)")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]")
SENSITIVE_KEYS = {
    "phone", "email", "password", "token", "turnstile_token", "secret",
    "api_key", "cookie", "session", "authorization", "consent_text",
    "street", "address", "smtp_password",
}


def mask_phone(v):
    if not v:
        return v
    digits = re.sub(r"\D", "", str(v))
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"


def mask_email(v):
    if not v or "@" not in str(v):
        return "***"
    return _EMAIL_RE.sub(lambda m: m.group(1) + "***" + m.group(3), str(v), count=1)


def hash_ip(ip):
    """Store a salted hash, never the raw IP, in audit rows."""
    if not ip:
        return None
    salt = os.getenv("IP_HASH_SALT", "haulchime")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:24]


def sanitize(data):
    """Recursively mask sensitive values in a dict destined for logs."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            lk = str(k).lower()
            if isinstance(v, bool) or v is None:
                out[k] = v          # flags are never PII
            elif lk in ("phone",) or lk.endswith("_phone"):
                out[k] = mask_phone(v)
            elif lk in ("email",) or lk.endswith("_email"):
                out[k] = mask_email(v)
            elif any(s in lk for s in SENSITIVE_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(data, (list, tuple)):
        return [sanitize(v) for v in data]
    if isinstance(data, str):
        # Don't mangle timestamps, IDs or other structured values.
        if _TIMESTAMP_RE.match(data) or data.count("-") >= 2 and ":" in data:
            return data
        # Defense in depth: mask raw phones/emails that slip into strings.
        s = _EMAIL_RE.sub(lambda m: m.group(1) + "***" + m.group(3), data)
        return _PHONE_RE.sub(lambda m: mask_phone(m.group()), s)
    return data


# ---------------------------------------------------------------- formatter
class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        entry.update(getattr(record, "ctx", {}))
        if record.exc_info:
            entry["error"] = str(record.exc_info[1])
            if os.getenv("FLASK_ENV") == "development" or os.getenv("LOG_STACKTRACES") == "true":
                entry["stack"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class PrettyFormatter(logging.Formatter):
    def format(self, record):
        ctx = getattr(record, "ctx", {})
        bits = " ".join(f"{k}={v}" for k, v in ctx.items() if v is not None)
        base = f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:<8} {getattr(record, 'event', '-'):<28} {record.getMessage()} {bits}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


CRITICAL = logging.CRITICAL
_logger = logging.getLogger("haulchime")


def init_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = os.getenv("LOG_FORMAT", "json").lower()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else PrettyFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    # Quiet noisy libraries below WARNING regardless of app level.
    for noisy in ("werkzeug", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return _logger


def _context(extra):
    ctx = {}
    if has_request_context():
        ctx["request_id"] = getattr(g, "request_id", None)
        ctx["route"] = request.endpoint
    ctx.update({k: v for k, v in (extra or {}).items() if v is not None})
    return sanitize(ctx)


def _log(level, event, message="", exc_info=False, **ctx):
    _logger.log(level, message or event, exc_info=exc_info,
                extra={"event": event, "ctx": _context(ctx)})


def debug(event, message="", **ctx):
    _log(logging.DEBUG, event, message, **ctx)


def info(event, message="", **ctx):
    _log(logging.INFO, event, message, **ctx)


def warn(event, message="", **ctx):
    _log(logging.WARNING, event, message, **ctx)


def error(event, message="", exc_info=False, **ctx):
    _log(logging.ERROR, event, message, exc_info=exc_info, **ctx)


def critical(event, message="", exc_info=False, **ctx):
    _log(logging.CRITICAL, event, message, exc_info=exc_info, **ctx)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- audit
def audit(event_type, lead=None, *, status="ok", actor_type="system",
          actor_id=None, contractor_id=None, previous_value=None,
          new_value=None, **metadata):
    """Append-only audit record + matching log line.

    Never update or delete rows written here — corrections are new events.
    Returns the LeadActivity row (uncommitted; caller commits with the
    surrounding transaction, or we commit if there is none in flight).
    """
    from models import LeadActivity, db  # local import to avoid cycles

    row = LeadActivity(
        activity_id=new_id("act"),
        lead_id=getattr(lead, "id", None) if lead is not None else metadata.pop("lead_db_id", None),
        lead_reference=getattr(lead, "reference", None) or metadata.pop("lead_reference", None),
        contractor_id=contractor_id,
        request_id=getattr(g, "request_id", None) if has_request_context() else None,
        event_type=event_type,
        event_status=status,
        actor_type=actor_type,
        actor_id=actor_id,
        previous_value=str(previous_value)[:500] if previous_value is not None else None,
        new_value=str(new_value)[:500] if new_value is not None else None,
        metadata_json=json.dumps(sanitize(metadata), default=str) if metadata else None,
        ip_hash=hash_ip(request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()) if has_request_context() else None,
        user_agent=(request.headers.get("User-Agent", "")[:250] if has_request_context() else None),
    )
    db.session.add(row)
    info(event_type, status=status, lead=row.lead_reference,
         actor=f"{actor_type}:{actor_id}" if actor_id else actor_type,
         activity_id=row.activity_id, **metadata)
    return row


# ---------------------------------------------------------------- external calls
def external_call(provider, operation):
    """Context manager logging external-service calls with duration/outcome.

    with external_call("smtp", "send") as call:
        ...do the thing...
        call["provider_response_id"] = message_id
    """
    class _Call(dict):
        def __enter__(self):
            self["provider"] = provider
            self["operation"] = operation
            self["_start"] = time.monotonic()
            debug("external.call_started", provider=provider, operation=operation)
            return self

        def __exit__(self, exc_type, exc, tb):
            duration = round((time.monotonic() - self.pop("_start")) * 1000)
            if exc:
                error("external.call_failed", str(exc), exc_info=(exc_type, exc, tb),
                      duration_ms=duration, **{k: v for k, v in self.items()})
                return False  # propagate
            info("external.call_completed", duration_ms=duration,
                 **{k: v for k, v in self.items()})
            return True

    return _Call()
