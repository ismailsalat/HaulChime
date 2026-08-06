"""
Partner portal: application, passwordless SMS login, and assigned leads.

Three rules govern this whole file.

1. **A partner sees a lead only through a LeadAssignment row that names them.**
   Every query joins on `partner_id == g.partner.id`. Nothing here trusts a
   URL, a hidden field, or a template that "doesn't show the link" — guessing
   a reference must return a 404, not somebody else's customer.

2. **Customer contact details appear only after acceptance.** The gate is
   `LeadAssignment.customer_visible`, one property, consulted by both the
   route and the template so they cannot drift apart.

3. **The partner session is separate from the admin session.** Different
   session keys entirely, so a logged-in partner is never one missing check
   away from an admin page, and signing out of one does not touch the other.

SMS handling is delegated to the existing sms_verification module: codes are
HMAC-digested, expiring, attempt-limited and rate-limited. Nothing new is
invented here, because inventing a second OTP path is how one of them ends up
weaker than the other.
"""
from datetime import date, datetime, timezone
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

import labels
import logger
import phone as phone_util
import sms_verification
from models import (ASSIGNMENT_STATUSES, DECLINE_REASONS, LeadAssignment, Lead,
                    Partner, PartnerAccount, PartnerActivity, PartnerApplication,
                    PartnerAvailability, PartnerNotification, PartnerTimeOff, db)
from security import honeypot_triggered, rate_limited

bp = Blueprint("partner", __name__, url_prefix="/partner")

# Deliberately distinct from the admin's "admin" key.
SESSION_KEY = "partner_account_id"
CSRF_KEY = "partner_csrf_token"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]

SERVICE_CHOICES = [
    ("junk_removal", "Junk removal"),
    ("hauling", "Hauling / delivery"),
    ("local_move", "Local move"),
    ("long_distance_move", "Long-distance move"),
]

STATUS_MESSAGES = {
    "pending_review": "Your application is still under review. "
                      "We'll text you as soon as there's a decision.",
    "phone_verification_required": "Verify your mobile number to finish your application.",
    "incomplete": "Your application isn't finished yet.",
    "changes_requested": "We need a few changes before we can approve your application.",
    "rejected": "Your application wasn't approved.",
    "suspended": "This partner account is currently inactive. "
                 "Contact HaulChime for assistance.",
}


# --------------------------------------------------------------------- auth
def _client_ip():
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def check_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get(CSRF_KEY):
        abort(400, "Invalid CSRF token")


@bp.context_processor
def inject_context():
    if CSRF_KEY not in session:
        import secrets
        session[CSRF_KEY] = secrets.token_urlsafe(32)
    unread = 0
    partner = getattr(g, "partner", None)
    if partner is not None:
        unread = PartnerNotification.query.filter_by(
            partner_id=partner.id, read=False).count()
    from flask import current_app
    return {"csrf_token": session[CSRF_KEY], "partner": partner,
            "api_url": current_app.config.get("SITE_URL", ""),
            "unread_count": unread, "L": labels, "DAY_NAMES": DAY_NAMES,
            "SERVICE_CHOICES": SERVICE_CHOICES}


def current_account():
    account_id = session.get(SESSION_KEY)
    if not account_id:
        return None
    return PartnerAccount.query.get(account_id)


def partner_required(fn):
    """Gate every portal page: signed in, account active, partner active, and
    the application actually approved. All four, every request — a partner
    suspended mid-session must lose access on their next click."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        account = current_account()
        if not account:
            return redirect(url_for("partner.login", next=request.path))
        partner = Partner.query.get(account.partner_id)
        if not account.active or not partner or not partner.active:
            session.pop(SESSION_KEY, None)
            flash(STATUS_MESSAGES["suspended"], "error")
            return redirect(url_for("partner.login"))
        application = (PartnerApplication.query
                       .filter_by(partner_id=partner.id)
                       .order_by(PartnerApplication.id.desc()).first())
        if application and application.status != "approved":
            session.pop(SESSION_KEY, None)
            flash(STATUS_MESSAGES.get(application.status,
                                      "Your account isn't active yet."), "error")
            return redirect(url_for("partner.application_status",
                                    ref=application.phone or ""))
        g.account = account
        g.partner = partner
        account.last_activity_at = datetime.now(timezone.utc)
        db.session.commit()
        return fn(*args, **kwargs)
    return wrapper


def activity(event_type, *, lead_id=None, assignment_id=None,
             old_value=None, new_value=None):
    db.session.add(PartnerActivity(
        partner_id=getattr(g, "partner", None).id if getattr(g, "partner", None) else None,
        lead_id=lead_id, assignment_id=assignment_id, event_type=event_type,
        old_value=str(old_value)[:255] if old_value is not None else None,
        new_value=str(new_value)[:255] if new_value is not None else None,
        ip_address_hash=logger.hash_ip(_client_ip())))


# ------------------------------------------------------------------- assign
def assignment_or_404(reference):
    """The ownership check. Anything that fetches a partner's lead goes
    through here — a reference belonging to another partner is a 404, not a
    403, so URL guessing cannot even confirm the lead exists."""
    assignment = (LeadAssignment.query
                  .join(Lead, LeadAssignment.lead_id == Lead.id)
                  .filter(Lead.reference == reference)
                  .filter(LeadAssignment.partner_id == g.partner.id)
                  .first())
    if not assignment:
        logger.warn("partner.lead_access_denied", partner_id=g.partner.id,
                    reference=reference[:24])
        abort(404)
    return assignment


def _tel_link(raw):
    """A tel:/sms: target. Falls back to the raw number rather than producing
    a dead link if parsing fails."""
    checked = phone_util.validate_us_mobile(raw or "")
    return checked.e164 if checked and checked.ok else (raw or "")


def safe_lead_view(assignment):
    """What this partner is allowed to know right now.

    Built server-side rather than filtered in a template, so a future template
    edit cannot accidentally surface a phone number. Before acceptance the
    customer's identity and exact addresses simply are not in the dict.
    """
    lead = assignment.lead
    view = {
        "reference": lead.reference,
        "service": labels.service_label(lead.service_type or lead.pest_type),
        "job_type": labels.job_type_label(lead.job_type) if lead.job_type else "",
        "job_size": labels.job_size_label(lead.job_size),
        "city": lead.city or "",
        "zip_code": lead.zip_code,
        "timing": labels.timing_label(lead.urgency),
        "service_date": lead.service_date or "",
        "preferred_time": labels.preferred_time_label(lead.preferred_time)
                          if lead.preferred_time else "",
        "inventory": lead.inventory or "",
        "item_categories": labels.item_list(lead.item_categories),
        "special_items": lead.special_items or "",
        "extra_services": labels.extra_service_list(lead.extra_services),
        "property_type": labels.property_label(lead.property_type),
        "access": labels.access_list(lead.access_issues) or
                  (lead.pickup_access or "").replace("_", " "),
        "stairs": labels.flight_label(lead.stairs_flights) if lead.stairs_flights else "",
        "destination_access": labels.access_list(lead.destination_access_issues),
        "notes": lead.description or "",
        "photo_count": len(lead.photos),
        "photos": list(lead.photos),
        "lead_price": f"{float(assignment.lead_price or lead.lead_charge or 0):.2f}",
        "status": assignment.status,
        "customer_visible": assignment.customer_visible,
    }
    if assignment.customer_visible:
        view.update({
            "customer_name": f"{lead.first_name} {lead.last_name}".strip(),
            "customer_phone": lead.phone,
            "customer_phone_link": _tel_link(lead.phone),
            "customer_email": lead.email or "",
            "pickup_address": " ".join(x for x in (
                lead.pickup_address, lead.pickup_unit) if x),
            "pickup_line2": f"{lead.city or ''} {lead.pickup_state or ''} {lead.zip_code}".strip(),
            "destination_address": " ".join(x for x in (
                lead.destination_address, lead.destination_unit) if x),
            "destination_line2": f"{lead.destination_city or ''} "
                                 f"{lead.destination_zip or ''}".strip(),
            "contact_preference": labels.contact_label(lead.preferred_contact),
            "contact_time": labels.preferred_time_label(lead.contact_time),
        })
    return view


# -------------------------------------------------------------------- login
@bp.get("/login")
def login():
    if current_account():
        return redirect(url_for("partner.home"))
    return render_template("partner/login.html", step="phone")


@bp.post("/login")
def login_post():
    check_csrf()
    step = request.form.get("step", "phone")
    ip = _client_ip()

    if step == "phone":
        raw = (request.form.get("phone") or "").strip()
        if rate_limited("plogin:" + ip, 8, 3600):
            flash("Too many sign-in attempts. Try again in a little while.", "error")
            return render_template("partner/login.html", step="phone", phone=raw)
        checked = phone_util.validate_us_mobile(raw)
        if not checked.ok:
            flash("Enter the mobile number on your HaulChime partner account.", "error")
            return render_template("partner/login.html", step="phone", phone=raw)

        account = PartnerAccount.query.filter_by(phone=checked.e164).first()
        if not account:
            # Same message whether or not the number exists: confirming which
            # numbers are partners would leak the partner list.
            flash("If that number is on a HaulChime partner account, "
                  "a code is on its way.", "ok")
            return render_template("partner/login.html", step="code",
                                   phone=raw, masked=checked.masked)
        try:
            result = sms_verification.start_verification(
                raw_phone=raw, quote_draft_id="partner_login",
                ip=ip, session_id=session.get(CSRF_KEY, ""))
        except sms_verification.VerificationError as exc:
            flash(exc.message if hasattr(exc, "message") else str(exc), "error")
            return render_template("partner/login.html", step="phone", phone=raw)
        session["partner_login_attempt"] = result.get("verification_attempt_id", "")
        session["partner_login_phone"] = checked.e164
        return render_template("partner/login.html", step="code",
                               phone=raw, masked=checked.masked)

    # step == "code"
    code = (request.form.get("code") or "").strip()
    attempt_id = session.get("partner_login_attempt", "")
    e164 = session.get("partner_login_phone", "")
    if rate_limited("pcode:" + ip, 12, 3600):
        flash("Too many code attempts. Try again later.", "error")
        return render_template("partner/login.html", step="phone")
    if not attempt_id or not e164:
        flash("That sign-in expired. Start again.", "error")
        return render_template("partner/login.html", step="phone")
    try:
        sms_verification.complete_verification(
            quote_draft_id="partner_login", attempt_id=attempt_id,
            code=code, session_id=session.get(CSRF_KEY, ""))
    except sms_verification.VerificationError as exc:
        flash(getattr(exc, "message", str(exc)), "error")
        return render_template("partner/login.html", step="code",
                               phone=request.form.get("phone", ""))

    account = PartnerAccount.query.filter_by(phone=e164).first()
    if not account:
        flash("No partner account uses that number.", "error")
        return render_template("partner/login.html", step="phone")

    application = (PartnerApplication.query.filter_by(partner_id=account.partner_id)
                   .order_by(PartnerApplication.id.desc()).first())
    if application and application.status != "approved":
        flash(STATUS_MESSAGES.get(application.status, "Your account isn't active yet."),
              "warn")
        return redirect(url_for("partner.application_status", ref=e164))
    partner = Partner.query.get(account.partner_id)
    if not account.active or not partner or not partner.active:
        flash(STATUS_MESSAGES["suspended"], "error")
        return render_template("partner/login.html", step="phone")

    session.pop("partner_login_attempt", None)
    session.pop("partner_login_phone", None)
    session[SESSION_KEY] = account.id
    account.last_login_at = datetime.now(timezone.utc)
    g.partner = partner
    activity("partner.login")
    db.session.commit()
    logger.info("partner.login", partner_id=partner.id)
    nxt = request.args.get("next") or url_for("partner.home")
    return redirect(nxt if nxt.startswith("/partner") else url_for("partner.home"))


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop(SESSION_KEY, None)
    flash("Signed out.", "ok")
    return redirect(url_for("partner.login"))


# ---------------------------------------------------------------- dashboard
@bp.get("/")
@partner_required
def home():
    assignments = (LeadAssignment.query.filter_by(partner_id=g.partner.id)
                   .order_by(LeadAssignment.assigned_at.desc()).all())
    new = [a for a in assignments if a.status in ("assigned", "viewed")]
    active = [a for a in assignments if a.status in
              ("accepted", "customer_contacted", "estimate_scheduled")]
    booked = [a for a in assignments if a.status in ("job_booked", "job_completed")]
    notifications = (PartnerNotification.query
                     .filter_by(partner_id=g.partner.id, read=False)
                     .order_by(PartnerNotification.created_at.desc()).limit(5).all())
    return render_template("partner/home.html", new=new, active=active,
                           booked=booked, notifications=notifications,
                           views={a.id: safe_lead_view(a) for a in new[:5]})


@bp.post("/toggle-leads")
@partner_required
def toggle_leads():
    check_csrf()
    was = g.partner.taking_leads if g.partner.taking_leads is not None else True
    g.partner.taking_leads = not was
    activity("partner.taking_leads_changed", old_value=was, new_value=not was)
    db.session.commit()
    flash("You're paused — we won't send new leads until you resume." if was
          else "You're back on. New leads can be assigned to you.", "ok")
    return redirect(url_for("partner.home"))


# -------------------------------------------------------------------- leads
@bp.get("/leads")
@partner_required
def leads():
    assignments = (LeadAssignment.query.filter_by(partner_id=g.partner.id)
                   .order_by(LeadAssignment.assigned_at.desc()).all())
    groups = {
        "new": [a for a in assignments if a.status in ("assigned", "viewed")],
        "active": [a for a in assignments if a.status in
                   ("accepted", "customer_contacted", "estimate_scheduled", "job_booked")],
        "closed": [a for a in assignments if a.status in
                   ("declined", "job_completed", "closed", "customer_no_response",
                    "customer_chose_another_provider", "not_a_good_fit")],
    }
    return render_template("partner/leads.html", groups=groups,
                           views={a.id: safe_lead_view(a) for a in assignments})


@bp.get("/leads/<reference>")
@partner_required
def lead_detail(reference):
    assignment = assignment_or_404(reference)
    if assignment.status == "assigned":
        assignment.status = "viewed"
        assignment.viewed_at = datetime.now(timezone.utc)
        activity("lead.viewed", lead_id=assignment.lead_id,
                 assignment_id=assignment.id)
    PartnerNotification.query.filter_by(
        partner_id=g.partner.id, lead_id=assignment.lead_id).update({"read": True})
    db.session.commit()
    return render_template("partner/lead_detail.html", a=assignment,
                           view=safe_lead_view(assignment),
                           decline_reasons=DECLINE_REASONS,
                           statuses=ASSIGNMENT_STATUSES)


@bp.post("/leads/<reference>/accept")
@partner_required
def accept_lead(reference):
    check_csrf()
    assignment = assignment_or_404(reference)
    if rate_limited("paccept:" + _client_ip(), 30, 3600):
        flash("Too many actions right now. Try again shortly.", "error")
        return redirect(url_for("partner.lead_detail", reference=reference))
    if assignment.status in ("declined", "closed"):
        flash("That lead is already closed.", "error")
        return redirect(url_for("partner.lead_detail", reference=reference))
    if not assignment.customer_visible:
        now = datetime.now(timezone.utc)
        assignment.status = "accepted"
        assignment.accepted_at = now
        assignment.customer_details_revealed_at = now
        activity("lead.accepted", lead_id=assignment.lead_id,
                 assignment_id=assignment.id, new_value="accepted")
        db.session.commit()
        logger.info("partner.lead_accepted", partner_id=g.partner.id,
                    lead=assignment.lead.reference)
        flash("Accepted. The customer's contact details are below.", "ok")
    return redirect(url_for("partner.lead_detail", reference=reference))


@bp.post("/leads/<reference>/decline")
@partner_required
def decline_lead(reference):
    check_csrf()
    assignment = assignment_or_404(reference)
    reason = (request.form.get("reason") or "").strip()
    if reason not in DECLINE_REASONS:
        flash("Choose a reason so we can send this to someone else quickly.", "error")
        return redirect(url_for("partner.lead_detail", reference=reference))
    assignment.status = "declined"
    assignment.declined_at = datetime.now(timezone.utc)
    assignment.decline_reason = reason
    assignment.decline_note = (request.form.get("note") or "")[:500]
    activity("lead.declined", lead_id=assignment.lead_id,
             assignment_id=assignment.id, new_value=reason)
    db.session.commit()
    logger.info("partner.lead_declined", partner_id=g.partner.id,
                lead=assignment.lead.reference, reason=reason)
    flash("Declined. Thanks for letting us know quickly.", "ok")
    return redirect(url_for("partner.leads"))


@bp.post("/leads/<reference>/status")
@partner_required
def update_status(reference):
    check_csrf()
    assignment = assignment_or_404(reference)
    new_status = (request.form.get("status") or "").strip()
    # A partner may move a lead forward, but cannot un-accept it or reach back
    # into the pre-acceptance states.
    allowed = {"customer_contacted", "estimate_scheduled", "job_booked",
               "job_completed", "customer_no_response",
               "customer_chose_another_provider", "not_a_good_fit", "closed"}
    if new_status not in allowed:
        abort(400)
    if not assignment.customer_visible:
        flash("Accept the lead before updating its outcome.", "error")
        return redirect(url_for("partner.lead_detail", reference=reference))
    old = assignment.status
    assignment.status = new_status
    if new_status in ("job_completed", "closed", "customer_no_response",
                      "customer_chose_another_provider", "not_a_good_fit"):
        assignment.closed_at = datetime.now(timezone.utc)
    activity("lead.status_changed", lead_id=assignment.lead_id,
             assignment_id=assignment.id, old_value=old, new_value=new_status)
    db.session.commit()
    flash("Updated. Thanks — this helps us send you better leads.", "ok")
    return redirect(url_for("partner.lead_detail", reference=reference))


@bp.get("/leads/<reference>/photos/<path:key>")
@partner_required
def lead_photo(reference, key):
    """Serve one photo from a lead assigned to this partner.

    Ownership is re-checked here rather than trusting the URL, and the key is
    matched against the photos actually attached to that lead - otherwise a
    partner with one valid assignment could read every photo in the bucket by
    swapping the key.
    """
    import os
    from flask import redirect, send_from_directory
    from storage import get_storage
    assignment = assignment_or_404(reference)
    safe_key = os.path.basename(key)
    if safe_key not in set(assignment.lead.photos):
        abort(404)
    cfg = current_app.config
    if cfg["STORAGE_BACKEND"] == "local":
        path = os.path.join(cfg["UPLOAD_DIR"], safe_key)
        if not os.path.exists(path):
            # The row says there is a photo but the file is gone. Almost always
            # an ephemeral filesystem: photos written before a volume was
            # attached do not survive a redeploy. A labelled tile is far more
            # use to the partner than a broken image icon.
            logger.warn("partner.photo_missing", key=safe_key[:40],
                        upload_dir=cfg["UPLOAD_DIR"])
            return _photo_placeholder()
        return send_from_directory(cfg["UPLOAD_DIR"], safe_key)
    try:
        return redirect(get_storage(cfg).url_for(safe_key), code=302)
    except Exception:
        logger.error("partner.photo_failed", exc_info=True)
        return _photo_placeholder()


def _photo_placeholder():
    """A grey tile that says what happened, served with 200 so the browser
    renders it instead of showing its own broken-image glyph."""
    from flask import Response
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 160' "
           "width='160' height='160'><rect width='160' height='160' fill='#eef1f4'/>"
           "<text x='80' y='74' text-anchor='middle' font-family='sans-serif' "
           "font-size='13' fill='#6b7885'>Photo</text>"
           "<text x='80' y='92' text-anchor='middle' font-family='sans-serif' "
           "font-size='13' fill='#6b7885'>unavailable</text></svg>")
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------- availability
def _save_weekly(partner, form):
    rows = {r.day_of_week: r for r in (partner.availability or [])}
    for day in range(7):
        row = rows.get(day)
        if row is None:
            row = PartnerAvailability(partner_id=partner.id, day_of_week=day)
            db.session.add(row)
        row.available = form.get(f"available_{day}") == "on"
        row.start_time = (form.get(f"start_{day}") or "08:00")[:5]
        row.end_time = (form.get(f"end_{day}") or "17:00")[:5]
        # An end before the start would silently make the day unbookable.
        if row.end_time <= row.start_time:
            row.end_time = "23:59"


@bp.route("/availability", methods=["GET", "POST"])
@partner_required
def availability():
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action", "weekly")
        if action == "weekly":
            _save_weekly(g.partner, request.form)
            notice = request.form.get("minimum_notice_hours")
            if notice and notice.isdigit():
                g.partner.minimum_notice_hours = min(int(notice), 336)
            g.partner.same_day_ok = request.form.get("same_day_ok") == "on"
            activity("availability.updated")
            flash("Schedule saved.", "ok")
        elif action == "time_off":
            try:
                start = date.fromisoformat(request.form.get("start_date", ""))
                end = date.fromisoformat(request.form.get("end_date", ""))
            except ValueError:
                flash("Enter both dates.", "error")
                return redirect(url_for("partner.availability"))
            if end < start:
                flash("The end date can't be before the start date.", "error")
                return redirect(url_for("partner.availability"))
            db.session.add(PartnerTimeOff(
                partner_id=g.partner.id, start_date=start, end_date=end,
                note=(request.form.get("note") or "")[:255]))
            activity("time_off.added", new_value=f"{start} to {end}")
            flash("Time off added. We won't assign jobs in that window.", "ok")
        elif action == "delete_time_off":
            row = PartnerTimeOff.query.filter_by(
                id=request.form.get("time_off_id"), partner_id=g.partner.id).first()
            if row:
                db.session.delete(row)
                activity("time_off.removed", old_value=f"{row.start_date} to {row.end_date}")
                flash("Time off removed.", "ok")
        db.session.commit()
        return redirect(url_for("partner.availability"))

    rows = {r.day_of_week: r for r in (g.partner.availability or [])}
    schedule = [rows.get(d) or PartnerAvailability(
        day_of_week=d, available=False, start_time="08:00", end_time="17:00")
        for d in range(7)]
    time_off = sorted(g.partner.time_off or [], key=lambda t: t.start_date)
    return render_template("partner/availability.html", schedule=schedule,
                           time_off=time_off, today=date.today().isoformat())


# ------------------------------------------------------------------ profile
@bp.route("/profile", methods=["GET", "POST"])
@partner_required
def profile():
    if request.method == "POST":
        check_csrf()
        form = request.form
        partner = g.partner
        # Only fields a partner owns. Credit, lead price, limits, active flag
        # and internal notes are absent by design — not hidden in a template.
        partner.contact_person = (form.get("contact_person") or "")[:120]
        partner.email = (form.get("email") or "")[:255]
        partner.service_zips = ",".join(
            z.strip() for z in (form.get("service_zips") or "").replace("\n", ",").split(",")
            if z.strip().isdigit())
        partner.services_accepted = ",".join(
            s for s, _ in SERVICE_CHOICES if form.get(f"service_{s}") == "on")
        crew = form.get("crew_size", "")
        partner.crew_size = int(crew) if crew.isdigit() else partner.crew_size
        partner.truck_capacity = (form.get("truck_capacity") or "")[:60]
        partner.heavy_item_capable = form.get("heavy_item_capable") == "on"
        partner.commercial_capable = form.get("commercial_capable") == "on"
        partner.minimum_job = (form.get("minimum_job") or "")[:255]
        partner.jobs_not_accepted = (form.get("jobs_not_accepted") or "")[:2000]
        activity("profile.updated")
        db.session.commit()
        flash("Profile saved.", "ok")
        return redirect(url_for("partner.profile"))

    accepted = {s.strip() for s in (g.partner.services_accepted or "").split(",") if s.strip()}
    return render_template("partner/profile.html", accepted=accepted,
                           account=g.account)


# ------------------------------------------------------------- application
@bp.get("/apply")
def apply():
    """Public. The application is one page with clear sections rather than a
    wizard: companies fill this in once, often on a phone at a job site, and
    a multi-step flow they can lose progress in is worse than a longer page."""
    return render_template("partner/apply.html", form={}, application=None)


@bp.post("/apply")
def apply_post():
    check_csrf()
    form = request.form
    if honeypot_triggered(form):
        # Bot. Look like success so it doesn't retry with a different shape.
        return redirect(url_for("partner.application_status"))
    if rate_limited("papply:" + _client_ip(), 5, 3600):
        flash("Too many applications from this connection. Try again later.", "error")
        return render_template("partner/apply.html", form=form, application=None)

    errors = {}
    business = (form.get("business_name") or "").strip()
    raw_phone = (form.get("phone") or "").strip()
    attempt_id = (form.get("verification_attempt_id") or "").strip()
    if len(business) < 2:
        errors["business_name"] = "Enter your business name."
    checked = phone_util.validate_us_mobile(raw_phone)
    if not checked.ok:
        errors["phone"] = "Enter a mobile number that can receive texts."

    zips = [z.strip() for z in (form.get("zip_codes") or "").replace("\n", ",").split(",")
            if z.strip().isdigit() and len(z.strip()) == 5]
    if not zips:
        errors["zip_codes"] = "Add at least one 5-digit ZIP code you serve."
    services = [s for s, _ in SERVICE_CHOICES if form.get(f"service_{s}") == "on"]
    if not services:
        errors["services"] = "Choose at least one service."

    # The phone must be verified before the application can be submitted —
    # this is checked server-side, because a hidden field is not proof.
    verified = False
    if checked.ok and attempt_id:
        try:
            # Third argument is the phone in E.164 — the digest is compared
            # against it, so the verified attempt has to belong to THIS number.
            attempt = sms_verification.attempt_for_quote(
                "partner_apply", attempt_id, checked.e164)
            verified = bool(attempt and attempt.status == "verified")
        except Exception:
            verified = False
    if not verified:
        errors["verification"] = "Verify your mobile number before submitting."

    if errors:
        for message in errors.values():
            flash(message, "error")
        return render_template("partner/apply.html", form=form,
                               application=None, errors=errors)

    existing = PartnerApplication.query.filter_by(phone=checked.e164).first()
    application = existing or PartnerApplication(phone=checked.e164)
    if existing and existing.status == "approved":
        flash("That number already belongs to an approved partner. Sign in instead.", "warn")
        return redirect(url_for("partner.login"))

    application.business_name = business[:160]
    application.contact_person = (form.get("contact_person") or "")[:120]
    application.email = (form.get("email") or "")[:255]
    application.phone_verified = True
    application.zip_codes = ",".join(sorted(set(zips)))
    application.services_accepted = ",".join(services)
    crew = form.get("crew_size", "")
    application.crew_size = int(crew) if crew.isdigit() else None
    application.truck_capacity = (form.get("truck_capacity") or "")[:60]
    application.heavy_item_capable = form.get("heavy_item_capable") == "on"
    application.commercial_capable = form.get("commercial_capable") == "on"
    application.minimum_job_requirements = (form.get("minimum_job_requirements") or "")[:2000]
    application.jobs_not_accepted = (form.get("jobs_not_accepted") or "")[:2000]
    notice = form.get("minimum_notice_hours", "24")
    application.minimum_notice_hours = int(notice) if notice.isdigit() else 24
    import json as _json
    application.availability_json = _json.dumps([
        {"day": d,
         "available": form.get(f"available_{d}") == "on",
         "start": (form.get(f"start_{d}") or "08:00")[:5],
         "end": (form.get(f"end_{d}") or "17:00")[:5]}
        for d in range(7)])
    application.status = "pending_review"
    application.submitted_at = datetime.now(timezone.utc)
    if not existing:
        db.session.add(application)
    db.session.commit()

    logger.info("partner.application_submitted", application_id=application.id,
                masked=checked.masked)
    _notify_admin_of_application(application)
    session["partner_application_phone"] = checked.e164
    return redirect(url_for("partner.application_status"))


def _notify_admin_of_application(application):
    """Tell HaulChime a company applied. Best effort — a mail outage must not
    lose the application, which is already committed by this point."""
    from flask import current_app
    from mailer import send_email
    cfg = current_app.config
    try:
        send_email(
            cfg, cfg["ADMIN_NOTIFY_EMAIL"],
            f"New partner application - {application.business_name}",
            f"{application.business_name} applied to become a HaulChime partner.\n\n"
            f"  Contact:  {application.contact_person or '-'}\n"
            f"  Email:    {application.email or '-'}\n"
            f"  Phone:    verified\n"
            f"  ZIPs:     {application.zip_codes}\n"
            f"  Services: {application.services_accepted}\n\n"
            f"Review it: {cfg['SITE_URL']}/admin/partner-applications/{application.id}\n")
    except Exception:
        logger.warn("partner.application_email_failed", application_id=application.id)


@bp.get("/application-status")
def application_status():
    phone = session.get("partner_application_phone") or request.args.get("ref", "")
    application = (PartnerApplication.query.filter_by(phone=phone).first()
                   if phone else None)
    return render_template("partner/application_status.html",
                           application=application,
                           message=STATUS_MESSAGES.get(
                               application.status if application else "", ""))
