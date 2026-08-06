"""
SMS OTP phone-verification endpoints (delivered via Bird).

    POST /api/quotes/phone-verification/start     — free prechecks, sends ONE SMS
    POST /api/quotes/phone-verification/complete  — validates the typed 6-digit code
    GET  /api/quotes/phone-verification/status    — safe status for the UI

The backend generates and sends the code (through Bird) and validates the
code the customer types back. Every free abuse check runs before any paid send.
"""
from flask import Blueprint, current_app, jsonify, request

import sms_verification as fv
import logger
from models import PhoneVerificationAttempt, SmsBudget, db
from security import rate_limited

bp = Blueprint("verification", __name__, url_prefix="/api/quotes/phone-verification")



# The public endpoint serves both the customer quote form and the partner
# application form. The purpose is derived from the draft id server-side
# rather than taken from the request body, so a caller cannot name its own
# trust level. Anything unrecognised is treated as an ordinary quote.
PURPOSE_BY_DRAFT = {"partner_apply": "partner_apply"}


def _purpose_for(quote_draft_id: str) -> str:
    return PURPOSE_BY_DRAFT.get((quote_draft_id or "").strip(), "quote")


@bp.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "")
    allowed = current_app.config["ALLOWED_ORIGINS"]
    if origin and (origin in allowed or "*" in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Request-ID")
    return resp


@bp.route("/start", methods=["OPTIONS"])
@bp.route("/complete", methods=["OPTIONS"])
@bp.route("/status", methods=["OPTIONS"])
def preflight():
    return ("", 204)


def _client_ip() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    return ip.split(",")[0].strip()


def _config_ready() -> bool:
    cfg = current_app.config
    return bool(cfg.get("PHONE_VERIFICATION_HMAC_SECRET"))


@bp.post("/start")
def start():
    cfg = current_app.config
    if not cfg["PHONE_VERIFICATION_ENABLED"]:
        logger.warn("verify.disabled", "PHONE_VERIFICATION_ENABLED is false")
        return jsonify(error="Phone verification is turned off right now.",
                       code="disabled"), 503
    if not _config_ready():
        logger.error("verify.not_configured",
                     "PHONE_VERIFICATION_HMAC_SECRET missing from .env")
        return jsonify(error="Phone verification isn't configured yet. (Site owner: "
                             "set PHONE_VERIFICATION_HMAC_SECRET in backend/.env.)",
                       code="config"), 503

    data = request.get_json(silent=True) or request.form
    ip = _client_ip()
    logger.debug("verify.request_received",
                 has_phone=bool(data.get("phone")),
                 has_quote_draft=bool(data.get("quote_draft_id")),
                 has_session=bool(data.get("session_id")),
                 origin=request.headers.get("Origin", "none"))

    raw_phone = (data.get("phone") or "")
    dev_test_number = (fv.is_fictional(raw_phone)
                       and cfg.get("APP_ENV") != "production")
    if not dev_test_number and rate_limited(
            ip, cfg["RATE_LIMIT_SUBMISSIONS"] * 3, cfg["RATE_LIMIT_WINDOW_SECONDS"]):
        return jsonify(error="Too many attempts. Please try again later.",
                       code="rate_limited"), 429
    if (data.get("company_website") or "").strip():
        logger.warn("verify.honeypot_triggered")
        SmsBudget.today().blocked = (SmsBudget.today().blocked or 0) + 1
        db.session.commit()
        return jsonify(success=True, verification_attempt_id="pva_blocked",
                       masked_phone="***-***-0000", expires_in=600,
                       resend_available_in=60), 200
    try:
        result = fv.start_verification(
            raw_phone=data.get("phone", ""),
            quote_draft_id=(data.get("quote_draft_id") or "")[:60],
            ip=ip,
            purpose=_purpose_for(data.get("quote_draft_id")),
            session_id=data.get("session_id", ""))
    except fv.VerificationError as e:
        logger.info("verify.start_rejected", reason=e.code,
                    retry_after=e.retry_after)
        SmsBudget.today().blocked = (SmsBudget.today().blocked or 0) + 1
        db.session.commit()
        body = {"error": e.message, "code": e.code}
        if e.retry_after:
            body["retry_after"] = e.retry_after
        return jsonify(body), 429 if e.retry_after else 400
    return jsonify(result), 200


@bp.post("/complete")
def complete():
    if not _config_ready():
        return jsonify(error="Phone verification isn't configured yet.",
                       code="config"), 503
    data = request.get_json(silent=True) or request.form
    try:
        result = fv.complete_verification(
            quote_draft_id=(data.get("quote_draft_id") or "")[:60],
            attempt_id=(data.get("verification_attempt_id") or "")[:50],
            code=(data.get("code") or ""),
            purpose=_purpose_for(data.get("quote_draft_id")),
            session_id=data.get("session_id", ""))
    except fv.VerificationError as e:
        logger.info("verify.complete_rejected", reason=e.code)
        return jsonify(error=e.message, code=e.code), 400
    return jsonify(result), 200


@bp.get("/status")
def status():
    attempt_id = request.args.get("verification_attempt_id", "")
    attempt = PhoneVerificationAttempt.query.filter_by(attempt_id=attempt_id).first()
    if not attempt:
        return jsonify(error="That verification session is no longer valid.",
                       code="not_found"), 404
    cfg = current_app.config
    status_value = attempt.status
    if status_value == "approved_to_send" and attempt.is_expired:
        status_value = "expired"
    import phone as phone_util
    return jsonify(
        status=status_value,
        masked_phone=phone_util.mask(attempt.phone_e164),
        sends_used=attempt.send_request_count or 0,
        sends_allowed=cfg["PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE"],
        resend_available_in=attempt.resend_available_in(
            cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"]),
    ), 200
