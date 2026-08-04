"""
Bird SMS client — OTP delivery only.

Bird is a "dumb pipe": HaulChime generates, stores, expires and validates its
own OTP codes (see sms_verification.py). Bird only carries the text. We never
ask Bird to generate or check a code.

Auth: the Bird SDK reads BIRD_API_KEY from the environment and infers the
region (us1 / eu1) from the key prefix. We pass the key explicitly from config
when present so the same value drives both. The key must NEVER be hardcoded.

Never retried automatically on timeout: Bird may already have accepted the
message, and a blind retry would send (and bill for) a duplicate.
"""
from typing import Optional, Tuple

import logger

# The SDK's import package is `bird` (installed via `pip install messagebird-sdk`).
try:
    from bird import (
        Bird,
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        ValidationError,
        APIStatusError,
        APIError,
    )
    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - surfaced as a config error at call time
    _SDK_AVAILABLE = False


class BirdError(Exception):
    """Provider failure. `category` drives the safe user-facing message.

    Mirrors the old TelnyxError categories so callers don't care which
    provider is behind the pipe:
        auth | balance | rate_limited | blocked | invalid_destination |
        timeout | unknown
    """

    def __init__(self, category: str, detail: str = "", retry_after: int = 0):
        super().__init__(category)
        self.category = category
        self.detail = detail            # safe summary, never raw provider payload
        self.retry_after = retry_after


_client = None


def _get_client(config):
    """Build (once) and reuse a Bird client. Safe to share across threads."""
    global _client
    if not _SDK_AVAILABLE:
        raise BirdError("auth", "messagebird-sdk is not installed")
    if _client is not None:
        return _client
    api_key = config.get("BIRD_API_KEY")
    if not api_key:
        raise BirdError("auth", "BIRD_API_KEY is not configured")
    kwargs = {"api_key": api_key}
    region = config.get("BIRD_REGION")
    if region:
        kwargs["region"] = region
    _client = Bird(**kwargs)
    return _client


def otp_message(code: str) -> str:
    """Plain-text fallback used only when no Bird template is configured.
    One GSM-7 segment: no emoji, curly quotes, or accented characters."""
    return (f"HaulChime code: {code}. Expires in 5 minutes. "
            f"Do not share this code. Reply STOP to opt out.")


def _status_code(exc) -> Optional[int]:
    for attr in ("status_code", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def send_notification(config, to_e164: str, text: str) -> Tuple[str, Optional[float]]:
    """Send one plain notification SMS (partner lead alerts).

    Category is "message", not "authentication": OTP traffic and business
    notifications are different classes of traffic to carriers, and mislabeling
    them is a fast route to filtering.
    """
    client = _get_client(config)
    kwargs = {"to": to_e164, "text": text[:1500], "category": "message"}
    sender = (config.get("BIRD_SMS_FROM") or "").strip()
    if sender:
        kwargs["from_"] = sender
    return _dispatch(client, kwargs, to_e164)


def send_otp(config, to_e164: str, code: str) -> Tuple[str, Optional[float]]:
    """Send one OTP SMS through Bird. Returns (message_id, cost_or_None).

    Prefers the registered Bird template (recommended for OTP / A2P
    deliverability); falls back to plain text if BIRD_OTP_TEMPLATE is blank.
    The `code` we pass is always the one HaulChime generated.

    Raises BirdError on any failure. Callers must treat a timeout as
    "possibly sent" and never auto-retry.
    """
    client = _get_client(config)
    template = (config.get("BIRD_OTP_TEMPLATE") or "").strip()
    sender = (config.get("BIRD_SMS_FROM") or "").strip()

    kwargs = {"to": to_e164}
    if sender:
        kwargs["from_"] = sender
    if template:
        # A template send derives its category from the template itself —
        # Bird rejects the request (422) if we also pass `category`.
        kwargs["template"] = template
        kwargs["parameters"] = {"code": code}
    else:
        kwargs["text"] = otp_message(code)
        kwargs["category"] = "authentication"

    return _dispatch(client, kwargs, to_e164)


def _dispatch(client, kwargs, to_e164):
    """Shared Bird send path: one attempt, never auto-retried."""
    with logger.external_call("bird", "send_sms") as call:
        call["to"] = logger.mask_phone(to_e164)
        try:
            message = client.sms.send(**kwargs)
        except RateLimitError as e:
            retry = int(getattr(e, "retry_after", 0) or 60)
            call["status"] = 429
            raise BirdError("rate_limited", str(e)[:120], retry_after=retry)
        except ValidationError as e:
            call["status"] = 422
            raise BirdError("invalid_destination", str(e)[:120])
        except (APITimeoutError, APIConnectionError) as e:
            # Do NOT retry: the message may already be on its way.
            call["status"] = "timeout"
            raise BirdError("timeout", type(e).__name__)
        except APIStatusError as e:
            status = _status_code(e)
            call["status"] = status or "error"
            detail = str(e)[:120]
            if status in (401, 403):
                raise BirdError("auth", detail)
            if status == 402 or "balance" in detail.lower() or "wallet" in detail.lower():
                raise BirdError("balance", detail)
            if status == 422:
                raise BirdError("invalid_destination", detail)
            raise BirdError("unknown", f"{status} {detail}")
        except APIError as e:
            call["status"] = "error"
            raise BirdError("unknown", str(e)[:120])

        message_id = getattr(message, "id", "") or ""
        cost = None
        raw_cost = getattr(message, "cost", None)
        try:
            # cost may be a number or an object with an .amount attribute.
            cost = float(getattr(raw_cost, "amount", raw_cost))
        except (TypeError, ValueError):
            cost = None
        call["provider_response_id"] = message_id
        return message_id, cost
