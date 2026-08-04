"""
Phone verification API + Telnyx webhook receiver.

Routes:
    POST /api/otp/request   — run free checks, send at most one SMS
    POST /api/otp/verify    — validate the submitted 6-digit code
    GET  /api/otp/status    — safe status for the frontend timer
    POST /api/webhooks/telnyx/messaging — delivery + STOP/START events
"""
import hashlib
import json
import secrets

from flask import Blueprint, current_app, g, jsonify, request

import logger
import otp_service
import phone as phone_util
from logger import audit
from models import OtpChallenge, PhoneOptOut, SmsBudget, WebhookEvent, db, utcnow
from routes.public import verify_turnstile
from security import rate_limited

bp = Blueprint("otp", __name__, url_prefix="/api")


@bp.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "")
    allowed = current_app.config["ALLOWED_ORIGINS"]
    if origin and (origin in allowed or "*" in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Request-ID"
    return resp


@bp.route("/otp/request", methods=["OPTIONS"])
@bp.route("/otp/verify", methods=["OPTIONS"])
@bp.route("/otp/status", methods=["OPTIONS"])
def preflight():
    return ("", 204)


def _client_ip():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    return ip.split(",")[0].strip()


@bp.post("/otp/request")
def request_code():
    cfg = current_app.config
    if not cfg["SMS_ENABLED"]:
        logger.warn("otp.disabled", "SMS_ENABLED is false")
        return jsonify(error="Phone verification is turned off right now.",
                       code="disabled"), 503
    if not cfg.get("OTP_HMAC_SECRET") or not cfg.get("PHONE_HASH_SECRET"):
        logger.error("otp.not_configured",
                     "OTP_HMAC_SECRET / PHONE_HASH_SECRET missing from .env")
        return jsonify(error="Phone verification isn't configured yet. "
                             "(Site owner: set OTP_HMAC_SECRET and "
                             "PHONE_HASH_SECRET in backend/.env.)",
                       code="config"), 503
    if not cfg.get("TELNYX_API_KEY"):
        logger.error("otp.no_telnyx_key", "TELNYX_API_KEY missing from .env")
        return jsonify(error="Text messaging isn't connected yet. "
                             "(Site owner: set TELNYX_API_KEY in backend/.env.)",
                       code="config"), 503

    data = request.get_json(silent=True) or request.form
    ip = _client_ip()

    # Cheap guards first — never spend money on an obvious bot.
    if rate_limited(ip, cfg["RATE_LIMIT_SUBMISSIONS"] * 3,
                    cfg["RATE_LIMIT_WINDOW_SECONDS"]):
        return jsonify(error="Too many attempts. Please try again later."), 429
    if (data.get("company_website") or "").strip():
        logger.warn("otp.honeypot_triggered")
        return jsonify(success=True, challenge_id="chl_000000000000",
                       expires_in=300, resend_available_in=60,
                       masked_phone="***-***-0000"), 200
    if not verify_turnstile(data.get("turnstile_token"), ip):
        return jsonify(error="Verification failed. Please reload and try again."), 400

    try:
        result = otp_service.request_code(
            raw_phone=data.get("phone", ""),
            quote_draft_id=(data.get("quote_draft_id") or "")[:60] or secrets.token_hex(8),
            ip=ip,
            session_id=data.get("session_id", ""),
            user_agent=request.headers.get("User-Agent", ""),
        )
    except otp_service.OtpError as e:
        body = {"error": e.message, "code": e.code}
        if e.retry_after:
            body["retry_after"] = e.retry_after
        return jsonify(body), 429 if e.retry_after else 400
    return jsonify(result), 200


@bp.post("/otp/verify")
def verify_code():
    data = request.get_json(silent=True) or request.form
    try:
        result = otp_service.verify_code(
            challenge_id=data.get("challenge_id", ""),
            code=data.get("code", ""))
    except otp_service.OtpError as e:
        return jsonify(error=e.message, code=e.code), 400
    return jsonify(result), 200


@bp.get("/otp/status")
def status():
    try:
        return jsonify(otp_service.status_for(request.args.get("challenge_id", "")))
    except otp_service.OtpError as e:
        return jsonify(error=e.message, code=e.code), 404


@bp.post("/webhooks/telnyx/messaging")
def telnyx_webhook():
    """Delivery receipts and inbound keywords. Signature-verified, idempotent."""
    cfg = current_app.config
    raw = request.get_data()
    if not __import__("telnyx_client").verify_webhook(
            cfg, raw,
            request.headers.get("telnyx-signature-ed25519", ""),
            request.headers.get("telnyx-timestamp", "")):
        logger.warn("telnyx.webhook_rejected")
        return jsonify(error="invalid signature"), 403

    try:
        payload = json.loads(raw.decode())
        event = payload.get("data", {})
        event_id = event.get("id", "")
        event_type = event.get("event_type", "")
        p = event.get("payload", {})
    except Exception:
        return jsonify(error="bad payload"), 400

    if not event_id:
        return jsonify(ok=True), 200
    if WebhookEvent.query.filter_by(event_id=event_id).first():
        return jsonify(ok=True, duplicate=True), 200   # idempotent
    db.session.add(WebhookEvent(event_id=event_id, event_type=event_type))

    if event_type in ("message.sent", "message.finalized"):
        message_id = p.get("id", "")
        challenge = OtpChallenge.query.filter_by(telnyx_message_id=message_id).first()
        if challenge:
            to = (p.get("to") or [{}])[0]
            status = to.get("status", p.get("status", ""))
            challenge.delivery_status = status[:30]
            errors = p.get("errors") or []
            if errors:
                challenge.provider_error_code = str(errors[0].get("code", ""))[:40]
                challenge.provider_error_message_safe = str(errors[0].get("title", ""))[:200]
            cost = p.get("cost") or {}
            try:
                challenge.provider_cost_amount = float(cost.get("amount"))
                challenge.provider_cost_currency = cost.get("currency", "USD")[:6]
            except (TypeError, ValueError):
                pass
            budget = SmsBudget.today()
            if status == "delivered":
                budget.delivered += 1
            elif status in ("delivery_failed", "sending_failed"):
                budget.failed += 1
            logger.info("telnyx.delivery_update", status=status,
                        challenge_id=challenge.challenge_id)
            # Never auto-resend on failure — the user requests the one resend.

    elif event_type == "message.received":
        text = (p.get("text") or "").strip().upper()
        from_num = (p.get("from") or {}).get("phone_number", "")
        if from_num and text:
            result = phone_util.validate_us_mobile(from_num)
            if result.ok:
                ph = otp_service.phone_hash(result.e164)
                row = PhoneOptOut.query.filter_by(phone_hash=ph).first()
                if text in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
                    if not row:
                        row = PhoneOptOut(phone_hash=ph, phone_e164=result.e164)
                        db.session.add(row)
                    row.opted_out, row.keyword = True, text[:20]
                    logger.info("sms.opt_out", masked=result.masked)
                elif text in ("START", "UNSTOP", "YES"):
                    if row:
                        row.opted_out, row.keyword = False, text[:20]
                    logger.info("sms.opt_in", masked=result.masked)

    db.session.commit()
    return jsonify(ok=True), 200
