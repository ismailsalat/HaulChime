"""
SMS phone-verification service (Bird).

Division of responsibility:
  HaulChime — generates the 6-digit code with `secrets`, decides *whether* an
              SMS may be sent (all free checks and rate limits), stores only an
              HMAC digest of the code, sends the code through Bird, then
              validates the code the customer types back.
  Bird      — a dumb pipe that carries the text. It never generates or checks
              the code.

Design rules carried over from the security spec:
  - Plaintext codes are never stored or logged. We store an HMAC digest keyed
    with PHONE_VERIFICATION_HMAC_SECRET over (code, attempt_id, phone) so the
    small 6-digit search space can't be brute-forced from a database dump.
  - Every free check runs BEFORE the paid Bird call (fails closed).
  - Limits are database-backed so they hold across multiple workers.

All rate-limit / reuse / budget logic is unchanged from the previous provider;
only the send + confirm mechanism changed.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional

from flask import current_app

import bird_client
import logger
import phone as phone_util
from models import PhoneVerificationAttempt, SmsBudget, db, utcnow


class VerificationError(Exception):
    """Safe, user-facing failure with a machine-readable category."""

    def __init__(self, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


# ---------------------------------------------------------------- hashing
def _hmac_secret() -> bytes:
    secret = current_app.config.get("PHONE_VERIFICATION_HMAC_SECRET") or ""
    if not secret:
        raise VerificationError(
            "config",
            "Phone verification isn't configured yet. (Site owner: set "
            "PHONE_VERIFICATION_HMAC_SECRET in backend/.env.)")
    return secret.encode()


def phone_hash(e164: str) -> str:
    """HMAC (not a bare hash) — phone numbers have a tiny search space."""
    return hmac.new(_hmac_secret(), e164.encode(), hashlib.sha256).hexdigest()


def session_hash(session_id: str) -> str:
    return hashlib.sha256((session_id or "").encode()).hexdigest()[:48]


def _code_digest(code: str, attempt_id: str, e164: str) -> str:
    msg = f"{code}|{attempt_id}|{e164}".encode()
    return hmac.new(_hmac_secret(), msg, hashlib.sha256).hexdigest()


def _generate_code() -> str:
    """Six digits, leading zeros preserved."""
    return f"{secrets.randbelow(1_000_000):06d}"


# ---------------------------------------------------------------- limits
def _sends_since(column, value, minutes: int) -> int:
    since = utcnow() - timedelta(minutes=minutes)
    return (db.session.query(
        db.func.coalesce(db.func.sum(PhoneVerificationAttempt.send_request_count), 0))
        .filter(column == value,
                PhoneVerificationAttempt.last_send_requested_at >= since)
        .scalar() or 0)


def check_rate_limits(cfg, ph: str, ip_h: str, sess_h: str) -> None:
    """Every limit fails closed: if it trips, Bird is never called."""
    logger.debug("verify.limits_checking",
                 phone_hour=_sends_since(PhoneVerificationAttempt.phone_hash, ph, 60),
                 phone_day=_sends_since(PhoneVerificationAttempt.phone_hash, ph, 1440),
                 ip_hour=_sends_since(PhoneVerificationAttempt.ip_hash, ip_h, 60),
                 session_hour=_sends_since(PhoneVerificationAttempt.session_hash, sess_h, 60),
                 today_sent=(SmsBudget.today().sent or 0),
                 daily_limit=cfg["PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT"])
    if _sends_since(PhoneVerificationAttempt.phone_hash, ph, 60) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_HOUR"]:
        raise VerificationError("phone_hour", "Too many verification attempts for this "
                                              "number. Please try again in an hour.", 3600)
    if _sends_since(PhoneVerificationAttempt.phone_hash, ph, 1440) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_DAY"]:
        raise VerificationError("phone_day", "Too many verification attempts for this "
                                             "number today.")
    if _sends_since(PhoneVerificationAttempt.ip_hash, ip_h, 60) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_IP_HOUR"]:
        raise VerificationError("ip_hour", "Too many verification attempts. "
                                           "Please try again later.", 3600)
    if _sends_since(PhoneVerificationAttempt.ip_hash, ip_h, 1440) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_IP_DAY"]:
        raise VerificationError("ip_day", "Too many verification attempts today.")
    if sess_h and _sends_since(PhoneVerificationAttempt.session_hash, sess_h, 60) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_SESSION_HOUR"]:
        raise VerificationError(
            "session_hour",
            "You've requested several codes recently. Please wait a few minutes, "
            "or call us and we'll take your details over the phone.", 900)

    since = utcnow() - timedelta(hours=1)
    unique_phones = (db.session.query(PhoneVerificationAttempt.phone_hash)
                     .filter(PhoneVerificationAttempt.ip_hash == ip_h,
                             PhoneVerificationAttempt.created_at >= since)
                     .distinct().count())
    if unique_phones >= cfg["PHONE_VERIFICATION_MAX_UNIQUE_PHONES_PER_IP_HOUR"]:
        raise VerificationError("ip_unique_phones", "Too many verification attempts. "
                                                    "Please try again later.", 3600)

    budget = SmsBudget.today()
    limit = cfg["PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT"]
    if (budget.sent or 0) >= limit:
        logger.critical("sms.daily_limit_reached", sent=budget.sent, limit=limit)
        raise VerificationError("global_day", "Phone verification is temporarily "
                                              "unavailable. Please try again tomorrow.")
    pct = (budget.sent or 0) / limit * 100 if limit else 0
    for threshold in (100, 90, 75, 50):
        if pct >= threshold:
            logger.warn("sms.budget_threshold", percent=threshold,
                        sent=budget.sent, limit=limit)
            break


def find_reusable(cfg, ph: str, sess_h: str,
                  purpose: str = "quote") -> Optional[PhoneVerificationAttempt]:
    """Reuse only within the SAME browser session AND the same purpose.

    Session scoping stops us trusting a number because some other visitor once
    verified it. Purpose scoping stops a customer quote verification being
    reused to sign in to the partner portal — the number is the same but the
    thing it unlocks is not.
    """
    if not sess_h:
        return None
    cutoff = utcnow() - timedelta(days=cfg["PHONE_VERIFICATION_REUSE_DAYS"])
    return (PhoneVerificationAttempt.query
            .filter(PhoneVerificationAttempt.phone_hash == ph,
                    PhoneVerificationAttempt.session_hash == sess_h,
                    PhoneVerificationAttempt.status == "verified",
                    PhoneVerificationAttempt.purpose == purpose,
                    PhoneVerificationAttempt.verified_at >= cutoff)
            .order_by(PhoneVerificationAttempt.verified_at.desc())
            .first())


# ---------------------------------------------------------------- start
# Reserved fictional US numbers (555-01xx / 555-3434 style) never receive a
# real SMS during development — we skip the Bird call and the budget, and use a
# fixed dev code so the flow can be completed locally. They must not consume
# production rate limits or spend.
def is_fictional(e164: str) -> bool:
    digits = "".join(ch for ch in (e164 or "") if ch.isdigit())[-10:]
    return len(digits) == 10 and digits[3:6] == "555"


def start_verification(*, raw_phone: str, quote_draft_id: str, ip: str,
                       session_id: str, purpose: str = "quote",
                       allow_reuse: bool = True) -> dict:
    """Run every free check, then send at most ONE Bird SMS."""
    cfg = current_app.config

    logger.debug("verify.start_requested", quote_draft_id=quote_draft_id,
                 raw_length=len(raw_phone or ""))

    result = phone_util.validate_us_mobile(raw_phone)
    logger.debug("verify.phone_parsed", ok=result.ok, reason=result.reason,
                 number_type=result.number_type, risk=result.risk_flag)
    if not result.ok:
        logger.info("verify.phone_rejected", reason=result.reason)
        raise VerificationError(
            "invalid_phone",
            "Enter a valid U.S. phone number that can receive text messages.")

    e164 = result.e164
    ph = phone_hash(e164)
    ip_h = logger.hash_ip(ip) or ""
    sess_h = session_hash(session_id)

    # Reuse a verification from this same session — no SMS needed.
    logger.debug("verify.checking_reuse", reuse_days=cfg["PHONE_VERIFICATION_REUSE_DAYS"])
    # Signing in must always cost a fresh code — reusing an earlier
    # verification would let anyone holding the session skip the SMS entirely.
    reusable = find_reusable(cfg, ph, sess_h, purpose) if allow_reuse else None
    logger.debug("verify.reuse_result", found=bool(reusable))
    if reusable:
        logger.info("verify.reused", attempt_id=reusable.attempt_id,
                    masked=result.masked)
        SmsBudget.today().reused = (SmsBudget.today().reused or 0) + 1
        db.session.commit()
        return {"success": True, "already_verified": True,
                "verification_attempt_id": reusable.attempt_id,
                "phone_e164": e164, "masked_phone": result.masked,
                "expires_in": 0, "resend_available_in": 0}

    # Existing attempt for this quote+phone: enforce cooldown and the 2-send cap.
    attempt = (PhoneVerificationAttempt.query
               .filter_by(quote_draft_id=quote_draft_id, phone_hash=ph,
                          purpose=purpose)
               .order_by(PhoneVerificationAttempt.created_at.desc())
               .first())
    logger.debug("verify.existing_attempt",
                 found=bool(attempt),
                 status=attempt.status if attempt else None,
                 sends=(attempt.send_request_count if attempt else 0),
                 expired=(attempt.is_expired if attempt else None))
    if attempt and attempt.status == "verified" and allow_reuse:
        return {"success": True, "already_verified": True,
                "verification_attempt_id": attempt.attempt_id,
                "phone_e164": e164, "masked_phone": result.masked,
                "expires_in": 0, "resend_available_in": 0}
    if attempt and not attempt.is_expired:
        cooldown = attempt.resend_available_in(cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"])
        if cooldown > 0:
            raise VerificationError("cooldown",
                                    f"A code was just sent. You can request another "
                                    f"in {cooldown} seconds.", cooldown)
        if (attempt.send_request_count or 0) >= cfg["PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE"]:
            raise VerificationError("max_sends",
                                    "You've reached the limit for verification codes "
                                    "on this request. Please call us instead.")
    else:
        attempt = PhoneVerificationAttempt(
            attempt_id="pva_" + secrets.token_urlsafe(24),
            quote_draft_id=quote_draft_id, purpose=purpose,
            phone_e164=e164, phone_hash=ph,
            session_hash=sess_h, ip_hash=ip_h,
            risk_flags=result.risk_flag or None,
        )
        db.session.add(attempt)

    testing_number = is_fictional(e164)
    dev_test = testing_number and cfg.get("APP_ENV") != "production"
    logger.debug("verify.limit_stage", fictional=testing_number,
                 app_env=cfg.get("APP_ENV"), limits_enforced=not dev_test)
    if dev_test:
        logger.info("verify.testing_number", masked=result.masked,
                    note="fictional number - rate limits, budget and send skipped")
    else:
        check_rate_limits(cfg, ph, ip_h, sess_h)

    # Generate the code and store only its digest. A new code invalidates any
    # previous one and resets the wrong-guess counter.
    code = _generate_code() if not dev_test else (cfg.get("DEV_OTP_CODE") or "123456")
    attempt.otp_digest = _code_digest(code, attempt.attempt_id, e164)
    attempt.attempt_count = 0
    # Issuing a new code means this attempt is no longer proven. Leaving a
    # reused row marked "verified" would let complete_verification short-
    # circuit and accept ANY code — the login flow reuses the row by design,
    # so this reset is what keeps that safe.
    attempt.status = "pending"
    attempt.verified_at = None

    # Count the send BEFORE the paid call — conservative by design, so a
    # double-click or a browser failure can't buy a free extra message.
    attempt.send_request_count = (attempt.send_request_count or 0) + 1
    attempt.last_send_requested_at = utcnow()
    attempt.expires_at = utcnow() + timedelta(
        seconds=cfg["PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS"])
    attempt.status = "approved_to_send"
    db.session.flush()

    if dev_test:
        logger.info("verify.dev_code", masked=result.masked,
                    note="fictional number - use the dev code to complete",
                    dev_code=code)
        db.session.commit()
        return {
            "success": True,
            "verification_attempt_id": attempt.attempt_id,
            "phone_e164": e164,
            "masked_phone": result.masked,
            "expires_in": cfg["PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS"],
            "resend_available_in": cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"],
        }

    budget = SmsBudget.today()
    budget.attempted = (budget.attempted or 0) + 1
    try:
        message_id, cost = bird_client.send_otp(cfg, e164, code)
    except bird_client.BirdError as e:
        budget.failed = (budget.failed or 0) + 1
        attempt.status = "failed"
        attempt.failure_category = e.category
        db.session.commit()
        logger.error("verify.send_failed", category=e.category,
                     masked=result.masked, attempt_id=attempt.attempt_id)
        friendly = {
            "rate_limited": "We're sending a lot of codes right now. Please try again shortly.",
            "invalid_destination": phone_util.USER_ERROR,
            "timeout": "The code may still arrive. Wait a moment before requesting another.",
            "balance": "Phone verification is temporarily unavailable. Please try again later.",
        }.get(e.category, "We couldn't send the code right now. Please try again later.")
        raise VerificationError("send_failed", friendly, e.retry_after)

    attempt.provider_message_id = message_id
    attempt.provider_cost_amount = cost
    budget.sent = (budget.sent or 0) + 1
    if cost:
        from decimal import Decimal
        budget.cost_amount = (Decimal(str(budget.cost_amount or 0))
                              + Decimal(str(cost)))
    db.session.commit()

    logger.info("verify.send_approved", attempt_id=attempt.attempt_id,
                masked=result.masked, send_count=attempt.send_request_count,
                message_id=message_id, risk=result.risk_flag)
    return {
        "success": True,
        "verification_attempt_id": attempt.attempt_id,
        "phone_e164": e164,
        "masked_phone": result.masked,
        "expires_in": cfg["PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS"],
        "resend_available_in": cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"],
    }


# ---------------------------------------------------------------- complete
def complete_verification(*, quote_draft_id: str, attempt_id: str,
                          code: str, session_id: str,
                          purpose: str = "quote") -> dict:
    """Validate the 6-digit code the customer typed and bind it to this
    quote + session. Success consumes the attempt so it can't be replayed."""
    cfg = current_app.config

    logger.debug("verify.complete_requested", attempt_id=attempt_id,
                 quote_draft_id=quote_draft_id, code_present=bool(code))

    attempt = PhoneVerificationAttempt.query.filter_by(attempt_id=attempt_id).first()
    logger.debug("verify.attempt_lookup", found=bool(attempt),
                 status=attempt.status if attempt else None,
                 consumed=(attempt.is_consumed if attempt else None),
                 expired=(attempt.is_expired if attempt else None))
    # A code proved for one purpose must not complete another. Without this a
    # quote verification would sign someone in to the partner portal.
    if attempt and (attempt.purpose or "quote") != purpose:
        logger.warn("verify.purpose_mismatch", attempt_id=attempt_id,
                    expected=purpose, actual=attempt.purpose)
        raise VerificationError(
            "attempt_not_found",
            "That code isn't valid here. Request a new one.")
    if not attempt:
        raise VerificationError("attempt_not_found",
                                "That verification session is no longer valid. "
                                "Please request a new code.")
    if attempt.status == "verified":
        return {"success": True, "phone_verification_status": "verified",
                "masked_phone": phone_util.mask(attempt.phone_e164),
                "verification_attempt_id": attempt_id, "already_verified": True}
    if attempt.status == "locked":
        raise VerificationError("locked", "Too many incorrect attempts. "
                                          "Please request a new code.")
    if attempt.is_consumed:
        raise VerificationError("attempt_consumed",
                                "That verification was already used. "
                                "Please request a new code.")
    if attempt.is_expired:
        attempt.status = "expired"
        db.session.commit()
        raise VerificationError("attempt_expired",
                                "That verification expired. Please request a new code.")
    if attempt.quote_draft_id != quote_draft_id:
        logger.warn("verify.quote_mismatch", attempt_id=attempt_id)
        raise VerificationError("quote_mismatch",
                                "That verification doesn't match this request.")
    if attempt.session_hash != session_hash(session_id):
        logger.warn("verify.session_mismatch", attempt_id=attempt_id)
        raise VerificationError("session_mismatch",
                                "That verification doesn't match this browser session. "
                                "Please request a new code.")

    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        raise VerificationError("bad_format",
                                "Enter the 6-digit code from your text message.")

    attempt.attempt_count = (attempt.attempt_count or 0) + 1
    expected = _code_digest(code, attempt.attempt_id, attempt.phone_e164)
    if not hmac.compare_digest(expected, attempt.otp_digest or ""):
        remaining = max(0, cfg["PHONE_VERIFICATION_MAX_ATTEMPTS"] - attempt.attempt_count)
        if remaining == 0:
            attempt.status = "locked"
            attempt.failure_category = "too_many_attempts"
            db.session.commit()
            logger.warn("verify.locked", attempt_id=attempt.attempt_id)
            raise VerificationError("locked", "Too many incorrect attempts. "
                                              "Please request a new code.")
        db.session.commit()
        logger.info("verify.incorrect", attempt_id=attempt.attempt_id,
                    attempts=attempt.attempt_count)
        raise VerificationError("incorrect",
                                f"That code isn't right. {remaining} attempt"
                                f"{'s' if remaining != 1 else ''} remaining.")

    # Atomically consume the attempt so a code can't be replayed.
    updated = (PhoneVerificationAttempt.query
               .filter_by(id=attempt.id, consumed_at=None)
               .update({"status": "verified", "verified_at": utcnow(),
                        "consumed_at": utcnow(), "otp_digest": None},
                       synchronize_session=False))
    if not updated:
        raise VerificationError("attempt_consumed",
                                "That verification was already used.")
    budget = SmsBudget.today()
    budget.verified = (budget.verified or 0) + 1
    db.session.commit()

    logger.info("verify.completed", attempt_id=attempt_id,
                masked=phone_util.mask(attempt.phone_e164), method="sms_otp")
    return {"success": True, "phone_verification_status": "verified",
            "masked_phone": phone_util.mask(attempt.phone_e164),
            "verification_attempt_id": attempt_id}


def attempt_for_quote(quote_draft_id: str, attempt_id: str,
                      phone_e164: str,
                      purpose: str = "quote") -> Optional[PhoneVerificationAttempt]:
    """Used at lead submission: confirm this quote really was verified."""
    if not attempt_id:
        return None
    attempt = PhoneVerificationAttempt.query.filter_by(
        attempt_id=attempt_id, status="verified").first()
    if not attempt:
        return None
    if attempt.quote_draft_id != quote_draft_id:
        return None
    if (attempt.purpose or "quote") != purpose:
        return None
    try:
        if not hmac.compare_digest(phone_hash(phone_e164), attempt.phone_hash):
            return None
    except VerificationError:
        return None
    return attempt
