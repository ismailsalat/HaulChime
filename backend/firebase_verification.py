"""
Firebase phone-verification service.

Division of responsibility:
  Firebase  — generates the code, sends the SMS, confirms the code, issues an
              ID token. HaulChime never sees or stores the code.
  HaulChime — decides *whether* an SMS may be requested (all free checks and
              rate limits), then cryptographically verifies the resulting
              Firebase ID token and binds it to the right quote and session.

Never trust the frontend's claim that verification happened: the ID token is
the only proof, and it is verified server-side with check_revoked=True.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import current_app

import logger
import phone as phone_util
from models import Lead, PhoneVerificationAttempt, SmsBudget, db, utcnow

_firebase_app = None


class VerificationError(Exception):
    """Safe, user-facing failure with a machine-readable category."""

    def __init__(self, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


# ---------------------------------------------------------------- init
def get_firebase_app():
    """Initialize the Admin SDK once, from GOOGLE_APPLICATION_CREDENTIALS."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        raise VerificationError("config", "Phone verification isn't available.")

    project_id = current_app.config.get("FIREBASE_PROJECT_ID")
    cred_path = current_app.config.get("GOOGLE_APPLICATION_CREDENTIALS")
    try:
        if cred_path:
            cred = credentials.Certificate(cred_path)
            _firebase_app = firebase_admin.initialize_app(
                cred, {"projectId": project_id}, name="haulchime")
        else:
            # Application Default Credentials (e.g. GCP / Railway secret file)
            _firebase_app = firebase_admin.initialize_app(
                options={"projectId": project_id}, name="haulchime")
    except ValueError:
        _firebase_app = firebase_admin.get_app("haulchime")
    except Exception as exc:
        logger.error("firebase.init_failed", str(exc)[:120], exc_info=True)
        raise VerificationError(
            "config",
            "Phone verification isn't configured yet. (Site owner: set "
            "GOOGLE_APPLICATION_CREDENTIALS — see SMS_PHONE_VERIFICATION_SETUP.md.)")
    return _firebase_app


# ---------------------------------------------------------------- hashing
def phone_hash(e164: str) -> str:
    """HMAC (not a bare hash) — phone numbers have a tiny search space."""
    secret = current_app.config.get("PHONE_VERIFICATION_HMAC_SECRET") or ""
    if not secret:
        raise VerificationError(
            "config",
            "Phone verification isn't configured yet. (Site owner: set "
            "PHONE_VERIFICATION_HMAC_SECRET in backend/.env.)")
    return hmac.new(secret.encode(), e164.encode(), hashlib.sha256).hexdigest()


def session_hash(session_id: str) -> str:
    return hashlib.sha256((session_id or "").encode()).hexdigest()[:48]


# ---------------------------------------------------------------- limits
def _sends_since(column, value, minutes: int) -> int:
    since = utcnow() - timedelta(minutes=minutes)
    return (db.session.query(
        db.func.coalesce(db.func.sum(PhoneVerificationAttempt.send_request_count), 0))
        .filter(column == value,
                PhoneVerificationAttempt.last_send_requested_at >= since)
        .scalar() or 0)


def check_rate_limits(cfg, ph: str, ip_h: str, sess_h: str) -> None:
    """Every limit fails closed: if it trips, Firebase is never called."""
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


def find_reusable(cfg, ph: str, sess_h: str) -> Optional[PhoneVerificationAttempt]:
    """Reuse only within the SAME browser session — never trust a phone number
    globally because a different visitor once verified it."""
    if not sess_h:
        return None
    cutoff = utcnow() - timedelta(days=cfg["PHONE_VERIFICATION_REUSE_DAYS"])
    return (PhoneVerificationAttempt.query
            .filter(PhoneVerificationAttempt.phone_hash == ph,
                    PhoneVerificationAttempt.session_hash == sess_h,
                    PhoneVerificationAttempt.status == "verified",
                    PhoneVerificationAttempt.verified_at >= cutoff)
            .order_by(PhoneVerificationAttempt.verified_at.desc())
            .first())


# ---------------------------------------------------------------- start
# Reserved fictional US numbers (555-01xx / 555-3434 style) never receive a
# real SMS — Firebase accepts a preconfigured code instead. They must not
# consume production rate limits during development.
def is_fictional(e164: str) -> bool:
    digits = "".join(ch for ch in (e164 or "") if ch.isdigit())[-10:]
    return len(digits) == 10 and digits[3:6] == "555"


def start_verification(*, raw_phone: str, quote_draft_id: str, ip: str,
                       session_id: str) -> dict:
    """Run every free check, then grant permission for ONE Firebase SMS."""
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
    reusable = find_reusable(cfg, ph, sess_h)
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
               .filter_by(quote_draft_id=quote_draft_id, phone_hash=ph)
               .order_by(PhoneVerificationAttempt.created_at.desc())
               .first())
    logger.debug("verify.existing_attempt",
                 found=bool(attempt),
                 status=attempt.status if attempt else None,
                 sends=(attempt.send_request_count if attempt else 0),
                 expired=(attempt.is_expired if attempt else None))
    if attempt and attempt.status == "verified":
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
            quote_draft_id=quote_draft_id, phone_e164=e164, phone_hash=ph,
            session_hash=sess_h, ip_hash=ip_h,
            risk_flags=result.risk_flag or None,
        )
        db.session.add(attempt)

    testing_number = is_fictional(e164)
    logger.debug("verify.limit_stage", fictional=testing_number,
                 app_env=cfg.get("APP_ENV"),
                 limits_enforced=not (testing_number and cfg.get("APP_ENV") != "production"))
    if testing_number and cfg.get("APP_ENV") != "production":
        logger.info("verify.testing_number", masked=result.masked,
                    note="fictional number - rate limits and budget skipped")
    else:
        check_rate_limits(cfg, ph, ip_h, sess_h)

    # Count the send BEFORE approving — conservative by design, so a
    # double-click or a browser failure can't buy a free extra message.
    attempt.send_request_count = (attempt.send_request_count or 0) + 1
    attempt.last_send_requested_at = utcnow()
    attempt.expires_at = utcnow() + timedelta(
        seconds=cfg["PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS"])
    attempt.status = "approved_to_send"
    if not (testing_number and cfg.get("APP_ENV") != "production"):
        budget = SmsBudget.today()
        budget.attempted = (budget.attempted or 0) + 1
        budget.sent = (budget.sent or 0) + 1
    db.session.commit()

    logger.debug("verify.attempt_saved", attempt_id=attempt.attempt_id,
                 expires_at=str(attempt.expires_at))
    logger.info("verify.send_approved", attempt_id=attempt.attempt_id,
                masked=result.masked, send_count=attempt.send_request_count,
                risk=result.risk_flag)
    return {
        "success": True,
        "verification_attempt_id": attempt.attempt_id,
        "phone_e164": e164,
        "masked_phone": result.masked,
        "expires_in": cfg["PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS"],
        "resend_available_in": cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"],
    }


# ---------------------------------------------------------------- complete
def verify_app_check(token: Optional[str]) -> bool:
    """Verify a Firebase App Check token. Bypass is only ever allowed outside
    production, and only when enforcement is off."""
    cfg = current_app.config
    if not cfg["FIREBASE_APP_CHECK_ENFORCED"]:
        if cfg["APP_ENV"] == "production" and not token:
            logger.warn("appcheck.missing_in_production")
        return True
    if not token:
        logger.warn("appcheck.missing")
        return False
    try:
        from firebase_admin import app_check
        app_check.verify_token(token, app=get_firebase_app())
        return True
    except Exception as exc:
        logger.warn("appcheck.invalid", type(exc).__name__)
        return False


def complete_verification(*, quote_draft_id: str, attempt_id: str,
                          id_token: str, session_id: str,
                          app_check_token: Optional[str] = None) -> dict:
    """Verify the Firebase ID token and bind it to this quote + session."""
    cfg = current_app.config

    logger.debug("verify.complete_requested", attempt_id=attempt_id,
                 quote_draft_id=quote_draft_id,
                 token_present=bool(id_token),
                 app_check_present=bool(app_check_token))

    if not verify_app_check(app_check_token):
        raise VerificationError("app_check", "We couldn't verify this request. "
                                             "Please reload the page and try again.")

    attempt = PhoneVerificationAttempt.query.filter_by(attempt_id=attempt_id).first()
    logger.debug("verify.attempt_lookup", found=bool(attempt),
                 status=attempt.status if attempt else None,
                 consumed=(attempt.is_consumed if attempt else None),
                 expired=(attempt.is_expired if attempt else None))
    if not attempt:
        raise VerificationError("attempt_not_found",
                                "That verification session is no longer valid. "
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

    # --- Verify the Firebase ID token (the only real proof) ---
    try:
        from firebase_admin import auth as fb_auth
    except ImportError:
        logger.critical("firebase.sdk_missing",
                        "firebase-admin is not installed in this environment")
        raise VerificationError(
            "config",
            "Phone verification isn't installed on the server. (Site owner: run "
            "pip install -r requirements.txt in the backend folder, then restart.)")

    try:
        # clock_skew_seconds tolerates small clock differences between this
        # server and Google. Without it, a machine a few seconds fast rejects
        # its own valid tokens with "Token used too early".
        try:
            decoded = fb_auth.verify_id_token(
                id_token, app=get_firebase_app(), check_revoked=True,
                clock_skew_seconds=cfg["FIREBASE_CLOCK_SKEW_SECONDS"])
        except TypeError:
            # Older firebase-admin without clock_skew_seconds support.
            decoded = fb_auth.verify_id_token(id_token, app=get_firebase_app(),
                                              check_revoked=True)
    except VerificationError:
        raise
    except ModuleNotFoundError as exc:
        # A missing dependency is a server problem, not a bad code.
        logger.critical("firebase.dependency_missing", str(exc)[:120])
        raise VerificationError(
            "config",
            "Phone verification isn't fully installed on the server. (Site owner: "
            "run pip install -r requirements.txt, then restart the backend.)")
    except Exception as exc:
        name = type(exc).__name__
        detail = str(exc)[:200]
        # Credential/setup failures must not masquerade as an invalid code.
        if any(k in detail.lower() for k in
               ("credential", "default credentials", "service account",
                "project id", "not initialized")):
            logger.critical("firebase.credentials_problem", detail)
            raise VerificationError(
                "config",
                "Phone verification isn't configured on the server. (Site owner: "
                "check GOOGLE_APPLICATION_CREDENTIALS — see "
                "SMS_PHONE_VERIFICATION_SETUP.md.)")
        if "too early" in detail.lower() or "clock" in detail.lower():
            logger.critical("firebase.clock_skew", detail)
            raise VerificationError(
                "clock_skew",
                "This device's clock is out of sync, so the code couldn't be "
                "confirmed. (Site owner: sync the server clock — Windows: "
                "Settings > Time & language > Date & time > Sync now.)")
        logger.warn("verify.token_invalid", error_type=name, detail=detail[:120])
        attempt.failure_category = "invalid_token"
        db.session.commit()
        raise VerificationError("invalid_token",
                                "We couldn't confirm that code. Please request a new one.")

    logger.debug("verify.token_decoded",
                 project_ok=decoded.get("aud") == cfg["FIREBASE_PROJECT_ID"],
                 provider=(decoded.get("firebase") or {}).get("sign_in_provider"),
                 has_phone=bool(decoded.get("phone_number")),
                 uid_present=bool(decoded.get("uid")))

    if decoded.get("aud") != cfg["FIREBASE_PROJECT_ID"]:
        logger.warn("verify.wrong_project", aud=str(decoded.get("aud"))[:40])
        raise VerificationError("wrong_project", "We couldn't confirm that code.")

    provider = (decoded.get("firebase") or {}).get("sign_in_provider")
    if provider != "phone":
        logger.warn("verify.wrong_provider", provider=str(provider)[:20])
        raise VerificationError("wrong_provider", "We couldn't confirm that code.")

    auth_time = decoded.get("auth_time", 0)
    age = datetime.now(timezone.utc).timestamp() - auth_time
    if age > cfg["PHONE_VERIFICATION_AUTH_MAX_AGE_SECONDS"]:
        logger.warn("verify.auth_too_old", age_seconds=int(age))
        raise VerificationError("auth_too_old",
                                "That verification took too long. Please request a new code.")

    # Read the phone from the trusted token/user record — never from the client.
    verified_phone = decoded.get("phone_number")
    if not verified_phone:
        try:
            from firebase_admin import auth as fb_auth2
            user = fb_auth2.get_user(decoded["uid"], app=get_firebase_app())
            verified_phone = user.phone_number
        except Exception:
            verified_phone = None
    if not verified_phone:
        raise VerificationError("no_phone", "We couldn't confirm that code.")

    logger.debug("verify.phone_from_firebase",
                 masked=phone_util.mask(verified_phone))
    checked = phone_util.validate_us_mobile(verified_phone)
    normalized = checked.e164 if checked.ok else verified_phone
    if not hmac.compare_digest(phone_hash(normalized), attempt.phone_hash):
        logger.warn("verify.phone_mismatch", attempt_id=attempt_id)
        attempt.failure_category = "phone_mismatch"
        db.session.commit()
        raise VerificationError("phone_mismatch",
                                "The verified number doesn't match the one on your request.")

    # Atomically consume the attempt so a token can't be replayed.
    updated = (PhoneVerificationAttempt.query
               .filter_by(id=attempt.id, consumed_at=None)
               .update({"status": "verified", "verified_at": utcnow(),
                        "consumed_at": utcnow(), "firebase_uid": decoded["uid"],
                        "firebase_auth_time": datetime.fromtimestamp(
                            auth_time, tz=timezone.utc)},
                       synchronize_session=False))
    if not updated:
        raise VerificationError("attempt_consumed",
                                "That verification was already used.")
    budget = SmsBudget.today()
    budget.verified = (budget.verified or 0) + 1
    db.session.commit()

    logger.info("verify.completed", attempt_id=attempt_id,
                masked=phone_util.mask(normalized), method="firebase_phone")
    return {"success": True, "phone_verification_status": "verified",
            "masked_phone": phone_util.mask(normalized),
            "verification_attempt_id": attempt_id}


def attempt_for_quote(quote_draft_id: str, attempt_id: str,
                      phone_e164: str) -> Optional[PhoneVerificationAttempt]:
    """Used at lead submission: confirm this quote really was verified."""
    if not attempt_id:
        return None
    attempt = PhoneVerificationAttempt.query.filter_by(
        attempt_id=attempt_id, status="verified").first()
    if not attempt:
        return None
    if attempt.quote_draft_id != quote_draft_id:
        return None
    try:
        if not hmac.compare_digest(phone_hash(phone_e164), attempt.phone_hash):
            return None
    except VerificationError:
        return None
    return attempt
