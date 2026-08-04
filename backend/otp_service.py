"""
OTP challenge lifecycle for phone verification.

Design rules (from the security spec):
- HaulChime generates the code with `secrets`, never Telnyx.
- Plaintext codes are never stored or logged. We store an HMAC digest keyed
  with OTP_HMAC_SECRET over (code, challenge_id, phone) so the small 6-digit
  search space can't be brute-forced from a database dump.
- Every free check runs BEFORE the paid Telnyx call.
- Limits are database-backed so they hold across multiple workers.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from flask import current_app

import logger
import phone as phone_util
import telnyx_client
from models import OtpChallenge, PhoneOptOut, SmsBudget, db, utcnow


class OtpError(Exception):
    """Safe, user-facing failure with a machine-readable code."""

    def __init__(self, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _secret(name: str) -> bytes:
    value = current_app.config.get(name) or ""
    if not value:
        logger.error("otp.missing_secret",
                     f"{name} is not set in the environment — phone "
                     f"verification cannot run. See backend/SMS_SETUP.md.",
                     secret_name=name)
        raise OtpError("config",
                       "Phone verification isn't configured yet. "
                       "(Site owner: set OTP_HMAC_SECRET and PHONE_HASH_SECRET "
                       "in backend/.env — see SMS_SETUP.md.)")
    return value.encode()


def phone_hash(e164: str) -> str:
    """Deterministic hash for duplicate/rate-limit lookups without storing
    the raw number repeatedly."""
    return hmac.new(_secret("PHONE_HASH_SECRET"), e164.encode(),
                    hashlib.sha256).hexdigest()


def _digest(code: str, challenge_id: str, e164: str) -> str:
    msg = f"{code}|{challenge_id}|{e164}".encode()
    return hmac.new(_secret("OTP_HMAC_SECRET"), msg, hashlib.sha256).hexdigest()


def _generate_code() -> str:
    """Six digits, leading zeros preserved."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _count_since(column, value, minutes: int) -> int:
    since = utcnow() - timedelta(minutes=minutes)
    return (OtpChallenge.query
            .filter(column == value, OtpChallenge.last_sent_at >= since)
            .with_entities(db.func.coalesce(db.func.sum(OtpChallenge.send_count), 0))
            .scalar() or 0)


def check_rate_limits(cfg, ph: str, ip_hash: str, session_hash: str) -> None:
    """All free abuse checks. Raises OtpError (fails closed) before any spend."""
    if _count_since(OtpChallenge.phone_hash, ph, 60) >= cfg["SMS_MAX_PER_PHONE_HOUR"]:
        raise OtpError("phone_hour", "Too many codes requested for this number. "
                                     "Please try again in an hour.", 3600)
    if _count_since(OtpChallenge.phone_hash, ph, 1440) >= cfg["SMS_MAX_PER_PHONE_DAY"]:
        raise OtpError("phone_day", "Too many codes requested for this number today.")
    if _count_since(OtpChallenge.request_ip_hash, ip_hash, 60) >= cfg["SMS_MAX_PER_IP_HOUR"]:
        raise OtpError("ip_hour", "Too many verification attempts. "
                                  "Please try again later.", 3600)
    if _count_since(OtpChallenge.request_ip_hash, ip_hash, 1440) >= cfg["SMS_MAX_PER_IP_DAY"]:
        raise OtpError("ip_day", "Too many verification attempts today.")
    if session_hash and _count_since(OtpChallenge.session_hash, session_hash, 60) >= 5:
        raise OtpError("session_hour", "Too many verification attempts. "
                                       "Please try again later.", 3600)

    # Unique destination numbers per IP per hour — catches phone-number farming.
    since = utcnow() - timedelta(hours=1)
    unique_phones = (db.session.query(OtpChallenge.phone_hash)
                     .filter(OtpChallenge.request_ip_hash == ip_hash,
                             OtpChallenge.created_at >= since)
                     .distinct().count())
    if unique_phones >= cfg["SMS_MAX_UNIQUE_PHONES_PER_IP_HOUR"]:
        raise OtpError("ip_unique_phones", "Too many verification attempts. "
                                           "Please try again later.", 3600)

    # Global minute + daily caps protect the account from runaway spend.
    minute_ago = utcnow() - timedelta(minutes=1)
    recent = (OtpChallenge.query
              .filter(OtpChallenge.last_sent_at >= minute_ago)
              .with_entities(db.func.coalesce(db.func.sum(OtpChallenge.send_count), 0))
              .scalar() or 0)
    if recent >= cfg["SMS_GLOBAL_MAX_PER_MINUTE"]:
        raise OtpError("global_minute", "We're experiencing high volume. "
                                        "Please try again in a minute.", 60)

    budget = SmsBudget.today()
    if budget.sent >= cfg["SMS_GLOBAL_DAILY_LIMIT"]:
        logger.critical("sms.daily_budget_exhausted", sent=budget.sent,
                        limit=cfg["SMS_GLOBAL_DAILY_LIMIT"])
        raise OtpError("global_day", "Phone verification is temporarily "
                                     "unavailable. Please try again tomorrow.")


def find_reusable_verification(cfg, ph: str, session_hash: str) -> Optional[OtpChallenge]:
    """Reuse a recent verification only for the SAME browser session — never
    globally trust a number just because someone once verified it."""
    if not session_hash:
        return None
    cutoff = utcnow() - timedelta(days=cfg["PHONE_VERIFICATION_REUSE_DAYS"])
    return (OtpChallenge.query
            .filter(OtpChallenge.phone_hash == ph,
                    OtpChallenge.session_hash == session_hash,
                    OtpChallenge.status == "verified",
                    OtpChallenge.verified_at >= cutoff)
            .order_by(OtpChallenge.verified_at.desc())
            .first())


def request_code(*, raw_phone: str, quote_draft_id: str, ip: str,
                 session_id: str, user_agent: str) -> dict:
    """Run every free check, then send at most one SMS. Returns safe status."""
    cfg = current_app.config

    result = phone_util.validate_us_mobile(raw_phone)
    if not result.ok:
        logger.info("otp.phone_rejected", reason=result.reason,
                    masked=result.masked)
        raise OtpError("invalid_phone", phone_util.USER_ERROR)

    e164 = result.e164
    ph = phone_hash(e164)
    ip_h = logger.hash_ip(ip) or ""
    sess_h = hashlib.sha256((session_id or "").encode()).hexdigest()[:32]
    ua_h = hashlib.sha256((user_agent or "").encode()).hexdigest()[:32]

    if PhoneOptOut.is_opted_out(ph):
        raise OtpError("opted_out",
                       "This number has opted out of messages from HaulChime. "
                       "Reply START to our number to opt back in, or use a "
                       "different mobile number.")

    # Reuse an existing verification for this session — saves a paid message.
    reusable = find_reusable_verification(cfg, ph, sess_h)
    if reusable:
        logger.info("otp.verification_reused", masked=result.masked,
                    challenge_id=reusable.challenge_id)
        return {"success": True, "already_verified": True,
                "challenge_id": reusable.challenge_id,
                "masked_phone": result.masked, "expires_in": 0,
                "resend_available_in": 0}

    # An unexpired challenge already exists: return it, don't send again.
    existing = (OtpChallenge.query
                .filter(OtpChallenge.quote_draft_id == quote_draft_id,
                        OtpChallenge.phone_hash == ph,
                        OtpChallenge.status.in_(["pending", "sent"]))
                .order_by(OtpChallenge.created_at.desc())
                .first())
    if existing and not existing.is_expired:
        cooldown = existing.resend_available_in(cfg["OTP_RESEND_DELAY_SECONDS"])
        if cooldown > 0:
            raise OtpError("cooldown",
                           f"A code was just sent. You can request another in "
                           f"{cooldown} seconds.", cooldown)
        if existing.send_count >= cfg["OTP_MAX_SENDS_PER_QUOTE"]:
            raise OtpError("max_sends",
                           "You've reached the limit for verification codes on "
                           "this request. Please call us instead.")
        challenge = existing
    else:
        challenge = OtpChallenge(
            challenge_id="chl_" + secrets.token_hex(12),
            quote_draft_id=quote_draft_id,
            phone_e164=e164, phone_hash=ph,
            request_ip_hash=ip_h, session_hash=sess_h, user_agent_hash=ua_h,
            risk_flag=result.risk_flag,
        )
        db.session.add(challenge)

    check_rate_limits(cfg, ph, ip_h, sess_h)

    # Generate and store the digest. A new code invalidates the previous one.
    code = _generate_code()
    challenge.otp_digest = _digest(code, challenge.challenge_id, e164)
    challenge.expires_at = utcnow() + timedelta(seconds=cfg["OTP_EXPIRATION_SECONDS"])
    challenge.attempt_count = 0
    challenge.status = "pending"
    db.session.flush()

    budget = SmsBudget.today()
    budget.attempted += 1
    try:
        message_id, cost = telnyx_client.send_sms(
            cfg, e164, telnyx_client.otp_message(code))
    except telnyx_client.TelnyxError as e:
        budget.failed += 1
        challenge.status = "send_failed"
        challenge.provider_error_code = e.category
        challenge.provider_error_message_safe = e.detail[:200]
        db.session.commit()
        logger.error("otp.send_failed", category=e.category,
                     masked=result.masked, challenge_id=challenge.challenge_id)
        friendly = {
            "rate_limited": "We're sending a lot of codes right now. Please try again shortly.",
            "invalid_destination": phone_util.USER_ERROR,
            "timeout": "The code may still arrive. Wait a moment before requesting another.",
        }.get(e.category, "We couldn't send the code right now. Please try again later.")
        raise OtpError("send_failed", friendly, e.retry_after)

    challenge.telnyx_message_id = message_id
    challenge.provider_cost_amount = cost
    challenge.status = "sent"
    challenge.send_count = (challenge.send_count or 0) + 1
    challenge.last_sent_at = utcnow()
    budget.sent += 1
    if cost:
        from decimal import Decimal
        budget.cost_amount = (Decimal(str(budget.cost_amount or 0))
                              + Decimal(str(cost)))
    db.session.commit()

    _budget_warning(cfg, budget)
    logger.info("otp.sent", masked=result.masked,
                challenge_id=challenge.challenge_id,
                message_id=message_id, send_count=challenge.send_count)
    return {
        "success": True,
        "challenge_id": challenge.challenge_id,
        "expires_in": cfg["OTP_EXPIRATION_SECONDS"],
        "resend_available_in": cfg["OTP_RESEND_DELAY_SECONDS"],
        "masked_phone": result.masked,
    }


def _budget_warning(cfg, budget) -> None:
    limit = cfg["SMS_GLOBAL_DAILY_LIMIT"]
    if not limit:
        return
    pct = budget.sent / limit * 100
    for threshold in (100, 90, 75, 50):
        if pct >= threshold:
            logger.warn("sms.budget_threshold", percent=threshold,
                        sent=budget.sent, limit=limit)
            break


def verify_code(*, challenge_id: str, code: str) -> dict:
    """Constant-time check. Success invalidates the code immediately."""
    cfg = current_app.config
    challenge = OtpChallenge.query.filter_by(challenge_id=challenge_id).first()
    if not challenge:
        raise OtpError("not_found", "That verification request is no longer valid. "
                                    "Please request a new code.")
    if challenge.status == "verified":
        return {"success": True, "already_verified": True}
    if challenge.status == "locked":
        raise OtpError("locked", "Too many incorrect attempts. "
                                 "Please request a new code.")
    if challenge.is_expired:
        challenge.status = "expired"
        db.session.commit()
        raise OtpError("expired", "That code has expired. Please request a new one.")

    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        raise OtpError("bad_format", "Enter the 6-digit code from your text message.")

    challenge.attempt_count = (challenge.attempt_count or 0) + 1
    expected = _digest(code, challenge.challenge_id, challenge.phone_e164)
    if not hmac.compare_digest(expected, challenge.otp_digest or ""):
        remaining = max(0, cfg["OTP_MAX_ATTEMPTS"] - challenge.attempt_count)
        if remaining == 0:
            challenge.status = "locked"
            challenge.locked_at = utcnow()
            db.session.commit()
            logger.warn("otp.locked", challenge_id=challenge.challenge_id)
            raise OtpError("locked", "Too many incorrect attempts. "
                                     "Please request a new code.")
        db.session.commit()
        logger.info("otp.incorrect", challenge_id=challenge.challenge_id,
                    attempts=challenge.attempt_count)
        raise OtpError("incorrect",
                       f"That code isn't right. {remaining} attempt"
                       f"{'s' if remaining != 1 else ''} remaining.")

    challenge.status = "verified"
    challenge.verified_at = utcnow()
    challenge.otp_digest = None          # invalidate immediately
    SmsBudget.today().verified += 1
    db.session.commit()
    logger.info("otp.verified", challenge_id=challenge.challenge_id,
                masked=phone_util.mask(challenge.phone_e164))
    return {"success": True, "verified_at": challenge.verified_at.isoformat()}


def status_for(challenge_id: str) -> dict:
    cfg = current_app.config
    challenge = OtpChallenge.query.filter_by(challenge_id=challenge_id).first()
    if not challenge:
        raise OtpError("not_found", "That verification request is no longer valid.")
    status = challenge.status
    if status in ("pending", "sent") and challenge.is_expired:
        status = "expired"
    return {
        "status": status,
        "masked_phone": phone_util.mask(challenge.phone_e164),
        "expires_in": challenge.seconds_remaining,
        "resend_available_in": challenge.resend_available_in(
            cfg["OTP_RESEND_DELAY_SECONDS"]),
        "sends_used": challenge.send_count or 0,
        "sends_allowed": cfg["OTP_MAX_SENDS_PER_QUOTE"],
    }
