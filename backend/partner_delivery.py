"""
Deliver a lead to a partner — by email, by text, or both.

Separate from mailer.py because handing a job to a partner is a business event
with its own rules, not just another template send:

  * Every attempt is written to the audit trail, success or failure, so there
    is always an answer to "did they actually get it?".
  * Email and SMS are independent. If the text fails, the email still counts
    as delivered, and the admin sees exactly which channel worked.
  * The SMS is deliberately short and carries no customer address. A text sits
    unencrypted on a phone that may be shared or lost; the full job detail
    lives behind the emailed link instead.
  * Nothing here mentions what the job might cost. The partner quotes the work.
"""
from flask import current_app

import bird_client
import labels
import logger
import phone as phone_util
from logger import audit
from mailer import send_templated
from models import db


class DeliveryError(Exception):
    """Every requested channel failed. Carries a safe message for the admin."""


def _summary_line(lead):
    """A one-line description of the job for the SMS body."""
    parts = [labels.service_label(lead.service_type or lead.pest_type)]
    if lead.job_size:
        parts.append(labels.job_size_label(lead.job_size))
    return " - ".join(parts)


def build_sms(lead, site_url):
    """Short, plain, GSM-7 friendly. No street address, no pricing."""
    when = labels.timing_label(lead.urgency)
    lines = [
        f"New {current_app.config['BRAND_NAME']} lead {lead.reference}",
        _summary_line(lead),
        f"Area: {lead.city or ''} {lead.zip_code}".strip(),
        f"Timing: {when}",
        f"Contact: {lead.first_name} {lead.phone}",
    ]
    if lead.phone_verified:
        lines.append("Phone verified")
    lines.append(f"Full details: {site_url.rstrip('/')}/admin/leads/{lead.id}")
    lines.append("Reply STOP to opt out.")
    return "\n".join(lines)


def _email_context(lead):
    cfg = current_app.config
    destination = lead.destination_zip or "-"
    return dict(
        brand=cfg["BRAND_NAME"], reference=lead.reference,
        first_name=lead.first_name, full_name=f"{lead.first_name} {lead.last_name}",
        phone=lead.phone, email=lead.email or "(not provided)",
        zip_code=lead.zip_code, destination_zip=destination,
        service_type=labels.service_label(lead.service_type or lead.pest_type),
        job_size=labels.job_size_label(lead.job_size),
        urgency=labels.timing_label(lead.urgency),
        property_type=labels.property_label(lead.property_type),
        contact_time=labels.preferred_time_label(lead.contact_time),
        contact_method=labels.contact_label(lead.preferred_contact),
        description=(lead.description or "")[:600],
        inventory=(lead.inventory or "")[:600],
        quality=lead.quality, score=lead.score,
        tier=(lead.lead_tier or "standard").replace("_", " ").title(),
        lead_price=float(lead.lead_charge or lead.lead_price or 0),
        photo_count=len(lead.photos),
        source=lead.utm_source or "direct", campaign=lead.utm_campaign or "-",
        landing_page=lead.landing_page or "-",
        duplicate_status=lead.duplicate_of or "No",
        contractor=lead.partner.name if lead.partner else "UNASSIGNED",
        site_url=cfg["SITE_URL"],
    )


def send_to_partner(lead, partner, *, channels=("email", "sms"), actor=None):
    """Deliver one lead. Returns a per-channel result dict.

    Raises DeliveryError only when every requested channel failed, so a
    partial success still counts as the lead having been handed over.
    """
    cfg = current_app.config
    results = {}
    delivery_id = logger.new_id("dlv")

    if "email" in channels:
        to = (partner.notification_email or partner.email or "").strip()
        if not to:
            results["email"] = {"ok": False, "detail": "No email address on file."}
        else:
            audit("delivery.attempted", lead, contractor_id=partner.id,
                  actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                  template="partner_notification", to_role="contractor", channel="email")
            try:
                with logger.external_call(cfg["MAIL_BACKEND"], "send_email") as call:
                    call["template"] = "partner_notification"
                    call["delivery_id"] = delivery_id
                    send_templated(cfg, "partner_notification", to, **_email_context(lead))
                results["email"] = {"ok": True, "detail": to}
                audit("delivery.sent", lead, contractor_id=partner.id,
                      actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                      channel="email", new_value=to)
            except Exception as exc:
                logger.error("delivery.email_failed", exc_info=True,
                             lead=lead.reference, partner_id=partner.id)
                results["email"] = {"ok": False, "detail": type(exc).__name__}
                audit("delivery.failed", lead, contractor_id=partner.id, status="failed",
                      actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                      channel="email")

    if "sms" in channels:
        raw = (partner.phone or "").strip()
        checked = phone_util.validate_us_mobile(raw) if raw else None
        if not raw:
            results["sms"] = {"ok": False, "detail": "No phone number on file."}
        elif not (checked and checked.ok):
            results["sms"] = {"ok": False,
                              "detail": "That number can't receive texts."}
        elif not cfg.get("BIRD_API_KEY"):
            results["sms"] = {"ok": False,
                              "detail": "SMS isn't configured (BIRD_API_KEY)."}
        else:
            audit("delivery.attempted", lead, contractor_id=partner.id,
                  actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                  to_role="contractor", channel="sms")
            try:
                message_id, _cost = bird_client.send_notification(
                    cfg, checked.e164, build_sms(lead, cfg["SITE_URL"]))
                results["sms"] = {"ok": True,
                                  "detail": phone_util.national_format(checked.e164)}
                audit("delivery.sent", lead, contractor_id=partner.id,
                      actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                      channel="sms", new_value=message_id or "sent")
            except bird_client.BirdError as exc:
                logger.error("delivery.sms_failed", category=exc.category,
                             lead=lead.reference, partner_id=partner.id)
                friendly = {
                    "auth": "SMS credentials were rejected.",
                    "balance": "The SMS account is out of balance.",
                    "rate_limited": "Too many texts right now — try again shortly.",
                    "invalid_destination": "The carrier rejected that number.",
                    "timeout": "The text may still arrive; it wasn't confirmed.",
                }.get(exc.category, "The text could not be sent.")
                results["sms"] = {"ok": False, "detail": friendly}
                audit("delivery.failed", lead, contractor_id=partner.id, status="failed",
                      actor_type="admin", actor_id=actor, delivery_id=delivery_id,
                      channel="sms")

    db.session.commit()
    if results and not any(r["ok"] for r in results.values()):
        raise DeliveryError(" ".join(
            f"{name.upper()}: {r['detail']}" for name, r in results.items()))
    return results
