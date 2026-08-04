"""
Telnyx Messaging API v2 client — SMS delivery only.

We do NOT use Telnyx Verify. HaulChime generates, stores, expires and
validates its own OTP codes; Telnyx is a dumb pipe that carries the text.

Never retried automatically on timeout: Telnyx may have already accepted the
message, and a blind retry would send (and bill for) a duplicate.
"""
import base64
import json
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

import logger

API_URL = "https://api.telnyx.com/v2/messages"
TIMEOUT_SECONDS = 10


class TelnyxError(Exception):
    """Provider failure. `category` drives the safe user-facing message."""

    def __init__(self, category: str, detail: str = "", retry_after: int = 0):
        super().__init__(category)
        self.category = category      # auth | balance | rate_limited | blocked |
                                      # invalid_destination | timeout | unknown
        self.detail = detail          # safe summary, never raw provider payload
        self.retry_after = retry_after


def send_sms(config, to_e164: str, text: str) -> Tuple[str, Optional[float]]:
    """Send one SMS. Returns (message_id, cost_amount_or_None).

    Raises TelnyxError on any failure. Callers must treat a timeout as
    "possibly sent" and never auto-retry.
    """
    api_key = config.get("TELNYX_API_KEY")
    if not api_key:
        raise TelnyxError("auth", "TELNYX_API_KEY is not configured")

    payload = {
        "from": config["TELNYX_FROM_NUMBER"],
        "to": to_e164,
        "text": text,
        "messaging_profile_id": config["TELNYX_MESSAGING_PROFILE_ID"],
    }
    base = (config.get("APP_BASE_URL") or "").rstrip("/")
    if base.startswith("https://"):
        payload["webhook_url"] = f"{base}/api/webhooks/telnyx/messaging"

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with logger.external_call("telnyx", "send_message") as call:
        call["to"] = logger.mask_phone(to_e164)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                err_body = json.loads(e.read().decode())
                errors = err_body.get("errors", [{}])
                code = str(errors[0].get("code", ""))
                title = errors[0].get("title", "")[:120]
            except Exception:
                code, title = "", ""
            call["status"] = status
            call["provider_code"] = code
            if status == 401 or status == 403:
                raise TelnyxError("auth", f"{status} {title}")
            if status == 429:
                retry = int(e.headers.get("Retry-After", "60") or 60)
                raise TelnyxError("rate_limited", title, retry_after=retry)
            if status == 402 or "balance" in title.lower() or "spend" in title.lower():
                raise TelnyxError("balance", title)
            if status == 422:
                raise TelnyxError("invalid_destination", f"{code} {title}")
            raise TelnyxError("unknown", f"{status} {title}")
        except (urllib.error.URLError, TimeoutError) as e:
            # Do NOT retry: the message may already be on its way.
            raise TelnyxError("timeout", type(e).__name__)

        data = body.get("data", {})
        message_id = data.get("id", "")
        cost = None
        try:
            cost = float(data.get("cost", {}).get("amount"))
        except (TypeError, ValueError):
            pass
        call["provider_response_id"] = message_id
        return message_id, cost


def verify_webhook(config, raw_body: bytes, signature_b64: str,
                   timestamp: str, tolerance_seconds: int = 300) -> bool:
    """Verify a Telnyx Ed25519 webhook signature and reject replays."""
    public_key_b64 = config.get("TELNYX_PUBLIC_KEY")
    if not public_key_b64:
        logger.warn("telnyx.webhook_unverified", "TELNYX_PUBLIC_KEY not set")
        return False
    if not signature_b64 or not timestamp:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > tolerance_seconds:
        logger.warn("telnyx.webhook_replay", age_seconds=int(age))
        return False
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
    except ImportError:
        logger.error("telnyx.webhook_no_pynacl",
                     "PyNaCl is required to verify webhook signatures")
        return False
    try:
        key = VerifyKey(base64.b64decode(public_key_b64))
        key.verify(f"{timestamp}|".encode() + raw_body,
                   base64.b64decode(signature_b64))
        return True
    except (BadSignatureError, ValueError, TypeError):
        logger.warn("telnyx.webhook_bad_signature")
        return False


def otp_message(code: str) -> str:
    """One GSM-7 segment. No emoji, curly quotes, or accented characters."""
    return (f"HaulChime code: {code}. Expires in 5 minutes. "
            f"Do not share this code. Reply STOP to opt out.")
