"""Password-protected admin area (server-rendered, noindexed)."""
import csv
import io
import json
import secrets
from datetime import datetime
from decimal import Decimal
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for, Response)
from sqlalchemy import func
from werkzeug.security import check_password_hash

from flask import g

import labels
import logger
from logger import audit
from models import (db, Lead, LeadActivity, Partner, PhoneVerificationAttempt,
                    SmsBudget, LEAD_STATUSES, QUALIFICATION_STATUSES, utcnow)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.after_request
def noindex(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login", next=request.path))
        g.admin_user = session.get("admin_user", "admin")
        return fn(*args, **kwargs)
    return wrapper


def check_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        abort(400, "Invalid CSRF token")


@bp.context_processor
def inject_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"],
            "statuses": LEAD_STATUSES, "qualifications": QUALIFICATION_STATUSES,
            "L": labels}


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        cfg = current_app.config
        ok = (request.form.get("username") == cfg["ADMIN_USERNAME"]
              and cfg["ADMIN_PASSWORD_HASH"]
              and check_password_hash(cfg["ADMIN_PASSWORD_HASH"],
                                      request.form.get("password", "")))
        if ok:
            session["admin"] = True
            session["admin_user"] = request.form.get("username")
            session["csrf_token"] = secrets.token_urlsafe(32)
            audit("admin.login_success", actor_type="admin",
                  actor_id=request.form.get("username"))
            db.session.commit()
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        error = "Invalid credentials."
        audit("admin.login_failed", status="failed", actor_type="admin",
              actor_id=request.form.get("username", "")[:60])
        db.session.commit()
        logger.warn("admin.login_failed", username=request.form.get("username", "")[:60])
    return render_template("admin/login.html", error=error)


@bp.get("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


def _filtered_leads():
    q = Lead.query
    if s := request.args.get("q", "").strip():
        like = f"%{s}%"
        q = q.filter(db.or_(Lead.reference.ilike(like), Lead.first_name.ilike(like),
                            Lead.last_name.ilike(like), Lead.email.ilike(like),
                            Lead.phone.ilike(like), Lead.zip_code.ilike(like)))
    if v := request.args.get("status"):
        q = q.filter(Lead.status == v)
    if v := request.args.get("service"):
        q = q.filter(Lead.service_type == v)
    if v := request.args.get("source"):
        q = q.filter(Lead.utm_source == v)
    if v := request.args.get("campaign"):
        q = q.filter(Lead.utm_campaign == v)
    if v := request.args.get("from"):
        try:
            q = q.filter(Lead.created_at >= datetime.strptime(v, "%Y-%m-%d"))
        except ValueError:
            pass
    if v := request.args.get("to"):
        try:
            q = q.filter(Lead.created_at <= datetime.strptime(v, "%Y-%m-%d")
                         .replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    return q.order_by(Lead.created_at.desc())


@bp.get("/")
@login_required
def dashboard():
    from datetime import timedelta
    from models import utcnow
    rng = request.args.get("range", "7d")
    now = utcnow()
    if rng == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif rng == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif rng == "custom":
        since = None  # use from/to params via _filtered_leads
    else:
        rng = "7d"
        since = now - timedelta(days=7)

    q = Lead.query
    if since is not None:
        q = q.filter(Lead.created_at >= since)
    else:
        q = _filtered_leads()

    total = q.count()
    qualified = q.filter(Lead.qualification == "qualified").count()
    sent = q.filter(Lead.partner_id.isnot(None)).count()
    billable = q.filter(Lead.billable.is_(True)).count()
    revenue = db.session.query(func.coalesce(func.sum(Lead.lead_charge), 0)).filter(
        Lead.id.in_([l.id for l in q.with_entities(Lead.id)])).scalar() if total else 0

    # Needs attention
    attn = {
        "review": Lead.query.filter(Lead.status == "validation_needed").count(),
        "unassigned": Lead.query.filter(Lead.partner_id.is_(None),
                                        Lead.status.in_(["new", "validation_needed"])).count(),
        "failed_deliveries": LeadActivity.query.filter_by(
            event_type="delivery.failed").count(),
        "no_partner_area": Lead.query.filter(Lead.status == "outside_service_area").count(),
    }
    recent = Lead.query.order_by(Lead.created_at.desc()).limit(5).all()
    activity = (LeadActivity.query.order_by(LeadActivity.created_at.desc())
                .limit(8).all())
    by_service = db.session.query(Lead.service_type, func.count()).group_by(
        Lead.service_type).order_by(func.count().desc()).all()
    by_source = db.session.query(Lead.utm_source, func.count()).group_by(
        Lead.utm_source).order_by(func.count().desc()).all()
    partners_over = [p for p in Partner.query.filter_by(active=True).all()
                     if p.quota_state == "over"]
    return render_template("admin/dashboard.html", rng=rng,
                           total=total, qualified=qualified, sent=sent,
                           billable=billable, revenue=revenue, attn=attn,
                           partners_over=partners_over, recent=recent,
                           activity=activity, by_service=by_service,
                           by_source=by_source)


@bp.get("/leads")
@login_required
def leads():
    page = max(int(request.args.get("page", 1)), 1)
    pagination = _filtered_leads().paginate(page=page, per_page=25, error_out=False)
    services = [r[0] for r in db.session.query(Lead.service_type).distinct()]
    return render_template("admin/leads.html", pagination=pagination, services=services)


@bp.get("/leads/export.csv")
@login_required
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "created_at", "first_name", "last_name", "phone",
                "email", "pickup_address", "zip", "city", "destination_address", "destination_zip",
                "property_type", "service_type", "job_size", "inventory", "special_items",
                "urgency", "lead_tier", "lead_price", "score", "status", "qualification", "partner",
                "lead_charge"])
    for l in _filtered_leads().all():
        w.writerow([l.reference, l.created_at, l.first_name, l.last_name,
                    l.phone, l.email, l.pickup_address, l.zip_code, l.city, l.destination_address,
                    l.destination_zip, l.property_type, l.service_type, l.job_size, l.inventory,
                    l.special_items, l.urgency, l.lead_tier, l.lead_price, l.score, l.status,
                    l.qualification, l.partner.name if l.partner else "", l.lead_charge or ""])
    audit("admin.csv_exported", actor_type="admin", actor_id=g.admin_user,
          rows=buf.getvalue().count("\n") - 1, filters=dict(request.args))
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


# ------------------------- Backup & restore -------------------------
def _row_to_dict(obj):
    from sqlalchemy import inspect as sa_inspect
    out = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        v = getattr(obj, attr.key)
        if isinstance(v, Decimal):
            v = float(v)
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        out[attr.key] = v
    return out


def _assign_columns(obj, data, skip=()):
    """Copy JSON values onto a model, coercing dates/decimals back to types."""
    import datetime as _dt
    from sqlalchemy import inspect as sa_inspect
    for col in sa_inspect(type(obj)).mapper.column_attrs:
        key = col.key
        if key in skip or key not in data:
            continue
        v = data[key]
        if v is not None:
            try:
                pytype = col.columns[0].type.python_type
            except Exception:
                pytype = None
            try:
                if pytype is _dt.datetime and isinstance(v, str):
                    v = _dt.datetime.fromisoformat(v)
                elif pytype is _dt.date and isinstance(v, str):
                    v = _dt.date.fromisoformat(v)
                elif pytype is Decimal and not isinstance(v, Decimal):
                    v = Decimal(str(v))
            except Exception:
                pass
        setattr(obj, key, v)


@bp.get("/backup.json")
@login_required
def backup_json():
    """Full JSON snapshot — leads, partners and activity history — for
    off-site backup. Restore it with Import on the Settings page."""
    data = {
        "_meta": {"app": "haulchime", "format": 1,
                  "exported_at": utcnow().isoformat()},
        "partners": [_row_to_dict(p) for p in Partner.query.order_by(Partner.id).all()],
        "leads": [_row_to_dict(l) for l in Lead.query.order_by(Lead.id).all()],
        "activities": [_row_to_dict(a) for a in LeadActivity.query.order_by(LeadActivity.id).all()],
    }
    data["_meta"]["counts"] = {k: len(data[k]) for k in ("partners", "leads", "activities")}
    audit("admin.backup_exported", actor_type="admin", actor_id=g.admin_user,
          **data["_meta"]["counts"])
    db.session.commit()
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return Response(json.dumps(data, indent=2, default=str),
                    mimetype="application/json",
                    headers={"Content-Disposition":
                             f"attachment; filename=haulchime-backup-{stamp}.json"})


@bp.post("/import")
@login_required
def import_data():
    """Restore from a backup file. Additive and safe to re-run: existing
    partners (by name) and leads (by reference) are kept, not overwritten."""
    check_csrf()
    f = request.files.get("backup")
    if not f or not f.filename:
        flash("Choose a backup .json file to import.", "error")
        return redirect(url_for("admin.settings"))
    try:
        data = json.loads(f.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        flash("That file isn't valid JSON. Use a file downloaded from Backup.", "error")
        return redirect(url_for("admin.settings"))
    if not isinstance(data, dict) or "leads" not in data:
        flash("That JSON doesn't look like a HaulChime backup.", "error")
        return redirect(url_for("admin.settings"))

    added = {"partners": 0, "leads": 0, "activities": 0}
    skipped = {"partners": 0, "leads": 0, "activities": 0}
    try:
        pmap = {}
        for pd in data.get("partners", []):
            existing = Partner.query.filter_by(name=pd.get("name")).first() if pd.get("name") else None
            if existing:
                pmap[pd.get("id")] = existing
                skipped["partners"] += 1
                continue
            np = Partner()
            _assign_columns(np, pd, skip={"id"})
            db.session.add(np)
            db.session.flush()
            pmap[pd.get("id")] = np
            added["partners"] += 1

        lmap = {}
        for ld in data.get("leads", []):
            existing = Lead.query.filter_by(reference=ld.get("reference")).first() if ld.get("reference") else None
            if existing:
                lmap[ld.get("id")] = existing
                skipped["leads"] += 1
                continue
            nl = Lead()
            _assign_columns(nl, ld, skip={"id", "partner_id"})
            old_pid = ld.get("partner_id")
            nl.partner_id = pmap[old_pid].id if old_pid in pmap else None
            db.session.add(nl)
            db.session.flush()
            lmap[ld.get("id")] = nl
            added["leads"] += 1

        for ad in data.get("activities", []):
            target = lmap.get(ad.get("lead_id"))
            if not target:
                skipped["activities"] += 1
                continue
            aid = ad.get("activity_id")
            if aid and LeadActivity.query.filter_by(activity_id=aid).first():
                skipped["activities"] += 1
                continue
            na = LeadActivity()
            _assign_columns(na, ad, skip={"id", "lead_id"})
            na.lead_id = target.id
            db.session.add(na)
            added["activities"] += 1

        audit("admin.backup_imported", actor_type="admin", actor_id=g.admin_user, **added)
        db.session.commit()
    except Exception as e:  # noqa: BLE001 — surface a friendly message, never a 500
        db.session.rollback()
        logger.error("admin.import_failed", error=type(e).__name__)
        flash("Import failed — nothing was changed. Check the file and try again.", "error")
        return redirect(url_for("admin.settings"))

    flash(f"Import complete. Added {added['leads']} leads, {added['partners']} partners, "
          f"{added['activities']} activity records. Kept {skipped['leads']} existing leads "
          f"and {skipped['partners']} existing partners.", "ok")
    return redirect(url_for("admin.settings"))


@bp.route("/leads/<int:lead_id>", methods=["GET", "POST"])
@login_required
def lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    attempt = (PhoneVerificationAttempt.query
               .filter_by(attempt_id=lead.phone_verification_attempt_id).first()
               if lead.phone_verification_attempt_id else None)
    if request.method == "POST":
        check_csrf()
        f = request.form
        before = {field: getattr(lead, field) for field in (
            "status", "qualification", "lead_charge", "partner_id", "admin_notes")}
        new_status = f.get("status") if f.get("status") in LEAD_STATUSES else lead.status
        new_qual = f.get("qualification") if f.get("qualification") in QUALIFICATION_STATUSES else lead.qualification
        pid_raw = f.get("partner_id")
        new_pid = int(pid_raw) if pid_raw and pid_raw.isdigit() else None
        new_partner = Partner.query.get(new_pid) if new_pid else None
        raw_charge = (f.get("lead_charge") or "").strip()
        try:
            new_charge = Decimal(raw_charge or str(lead.lead_price or lead.lead_charge or 40))
        except Exception:
            new_charge = Decimal(str(lead.lead_price or lead.lead_charge or 40))

        was_counted = bool(lead.status == "sent_to_partner" and lead.partner_id)
        old_partner = Partner.query.get(lead.partner_id) if was_counted else None
        old_charge = Decimal(str(lead.lead_charge or lead.lead_price or 0))
        now_counted = bool(new_status == "sent_to_partner" and new_pid)

        # ---- Consistency rules for the prepaid lead model ----
        problems = []
        if (new_status == "sent_to_partner"
                and current_app.config["REQUIRE_PHONE_VERIFICATION"]
                and not lead.phone_verified):
            problems.append("This lead's phone number was never verified, so it "
                            "can't be sent to a partner.")
        if new_status == "sent_to_partner" and not new_pid:
            problems.append("A lead can't be marked Sent/Accepted/Declined without an "
                            "assigned partner. Choose a partner below.")
        if new_status in ("invalid", "duplicate") and new_qual == "qualified":
            problems.append("An Invalid or Duplicate lead can't also be Qualified. "
                            "Change one of the two.")
        if now_counted and new_partner:
            max_price = Decimal(str(new_partner.max_lead_price or 70))
            available = Decimal(str(new_partner.credit_balance or 0))
            if was_counted and lead.partner_id == new_pid:
                available += old_charge
            if new_charge > max_price:
                problems.append(f"This lead costs ${new_charge:.2f}, above {new_partner.name}'s "
                                f"maximum lead price of ${max_price:.2f}.")
            if available < new_charge:
                problems.append(f"{new_partner.name} has ${available:.2f} available credit, "
                                f"but this lead costs ${new_charge:.2f}. Add credit first.")
        if problems:
            for p in problems:
                flash(p, "error")
            partners = Partner.query.order_by(Partner.name).all()
            timeline = (LeadActivity.query.filter_by(lead_id=lead.id)
                        .order_by(LeadActivity.created_at.asc()).all())
            return render_template("admin/lead_detail.html", lead=lead,
                                   partners=partners, timeline=timeline,
                                   attempt=attempt), 400

        # Assigning outside a partner's service area is a real mistake most of
        # the time, so it stops the save and asks for an explicit confirmation
        # rather than quietly going through with a note afterwards.
        zip_override = f.get("confirm_zip_override") == "yes"
        if new_partner and not new_partner.serves_zip(lead.zip_code) and not zip_override:
            covered = ", ".join(sorted(
                z.strip() for z in (new_partner.service_zips or "").split(",") if z.strip()
            )) or "none listed"
            partners = Partner.query.order_by(Partner.name).all()
            timeline = (LeadActivity.query.filter_by(lead_id=lead.id)
                        .order_by(LeadActivity.created_at.asc()).all())
            audit("partner.zip_mismatch", lead, status="warn", actor_type="admin",
                  actor_id=g.admin_user, contractor_id=new_partner.id,
                  zip=lead.zip_code, partner=new_partner.name, confirmed=False)
            # Commit the audit row even though the save is being refused —
            # a blocked attempt is exactly the kind of thing worth a record.
            db.session.commit()
            return render_template(
                "admin/lead_detail.html", lead=lead, partners=partners,
                timeline=timeline, attempt=attempt,
                zip_warning={
                    "partner": new_partner.name,
                    "partner_id": new_partner.id,
                    "zip": lead.zip_code,
                    "covered": covered,
                    "status": new_status,
                    "qualification": new_qual,
                    "lead_charge": str(new_charge),
                    "admin_notes": (f.get("admin_notes") or ""),
                }), 400
        if new_partner and not new_partner.serves_zip(lead.zip_code):
            flash(f"Assigned outside the service area: {new_partner.name} does not "
                  f"list ZIP {lead.zip_code}. You confirmed this.", "warn")
            audit("partner.zip_mismatch", lead, status="warn", actor_type="admin",
                  actor_id=g.admin_user, contractor_id=new_partner.id,
                  zip=lead.zip_code, partner=new_partner.name, confirmed=True)
        if new_partner and new_partner.quota_state == "over":
            flash(f"Heads up: {new_partner.name} is over their monthly quota "
                  f"({new_partner.quota_display}).", "warn")
            audit("partner.quota_exceeded", lead, status="warn", actor_type="admin",
                  actor_id=g.admin_user, contractor_id=new_partner.id,
                  quota=new_partner.quota_display)

        # Credit accounting is transactional: refund the old purchase first,
        # then debit the new purchase. Reassigning and price edits stay balanced.
        if was_counted and old_partner:
            old_balance = Decimal(str(old_partner.credit_balance or 0))
            old_partner.credit_balance = old_balance + old_charge
            audit("partner.credit_refunded", lead, contractor_id=old_partner.id,
                  actor_type="admin", actor_id=g.admin_user,
                  previous_value=float(old_balance), new_value=float(old_partner.credit_balance),
                  amount=float(old_charge))
            if not now_counted or lead.partner_id != new_pid:
                if (old_partner.leads_this_period or 0) > 0:
                    old_partner.leads_this_period -= 1

        if now_counted and new_partner:
            before_balance = Decimal(str(new_partner.credit_balance or 0))
            new_partner.credit_balance = before_balance - new_charge
            audit("partner.credit_debited", lead, contractor_id=new_partner.id,
                  actor_type="admin", actor_id=g.admin_user,
                  previous_value=float(before_balance), new_value=float(new_partner.credit_balance),
                  amount=float(new_charge))
            if not was_counted or lead.partner_id != new_pid:
                new_partner.reset_period_if_needed()
                new_partner.leads_this_period = (new_partner.leads_this_period or 0) + 1

        lead.status = new_status
        lead.qualification = new_qual
        lead.partner_id = new_pid
        lead.lead_charge = new_charge if now_counted else (new_charge if raw_charge else lead.lead_charge)
        lead.admin_notes = (f.get("admin_notes") or "").strip() or None

        for field, old in before.items():
            new = getattr(lead, field)
            if new != old:
                audit("admin.lead_updated", lead, actor_type="admin",
                      actor_id=g.admin_user, previous_value=old, new_value=new,
                      field=field)
                if field == "status":
                    audit(f"lead.status_{new}", lead, actor_type="admin",
                          actor_id=g.admin_user)
        db.session.commit()
        flash("Lead updated.", "ok")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))
    partners = Partner.query.order_by(Partner.name).all()
    timeline = (LeadActivity.query.filter_by(lead_id=lead.id)
                .order_by(LeadActivity.created_at.asc()).all())
    return render_template("admin/lead_detail.html", lead=lead,
                           partners=partners, timeline=timeline, attempt=attempt)


@bp.route("/partners", defaults={"edit_id": None}, methods=["GET", "POST"])
@bp.route("/partners/<int:edit_id>", methods=["GET", "POST"])
@login_required
def partners(edit_id):
    if request.method == "POST":
        check_csrf()
        f = request.form
        pid = f.get("id")
        p = Partner.query.get(int(pid)) if pid and pid.isdigit() else Partner()
        p.name = f.get("name", "").strip() or "Unnamed partner"
        p.contact_person = f.get("contact_person", "").strip()
        p.email = f.get("email", "").strip()
        p.phone = f.get("phone", "").strip()
        import re as _re
        raw_zips = _re.split(r"[\s,;]+", f.get("service_zips", "").strip())
        zips, bad = [], []
        for z in raw_zips:
            if not z:
                continue
            if _re.fullmatch(r"\d{5}", z):
                if z not in zips:
                    zips.append(z)
            else:
                bad.append(z)
        if bad:
            flash("These ZIP codes look invalid and were skipped: " + ", ".join(bad[:8]), "error")
        p.service_zips = ",".join(zips)
        p.services_accepted = ",".join(request.form.getlist("services_accepted"))
        p.minimum_job = f.get("minimum_job", "").strip()
        for field in ("credit_balance", "max_lead_price"):
            raw_v = (f.get(field) or "").strip()
            try:
                setattr(p, field, float(raw_v) if raw_v else None)
            except ValueError:
                pass
        raw_limit = (f.get("daily_lead_limit") or "").strip()
        p.daily_lead_limit = int(raw_limit) if raw_limit.isdigit() else 10
        raw_crew = (f.get("crew_size") or "").strip()
        p.crew_size = int(raw_crew) if raw_crew.isdigit() else None
        p.truck_capacity = f.get("truck_capacity", "").strip()
        p.heavy_item_capable = f.get("heavy_item_capable") == "on"
        p.commercial_capable = f.get("commercial_capable") == "on"
        p.billing_type = f.get("billing_type") if f.get("billing_type") in ("per_lead", "monthly") else "per_lead"
        for field in ("monthly_price", "overage_price_per_lead"):
            raw_v = (f.get(field) or "").strip()
            try:
                setattr(p, field, float(raw_v) if raw_v else None)
            except ValueError:
                pass
        raw_q = (f.get("monthly_lead_quota") or "").strip()
        p.monthly_lead_quota = int(raw_q) if raw_q.isdigit() else None
        p.reset_period_if_needed()
        p.notification_email = f.get("notification_email", "").strip()
        p.active = f.get("active") == "on"
        raw = (f.get("price_per_lead") or "").strip()
        try:
            p.price_per_lead = float(raw) if raw else None
        except ValueError:
            pass
        p.notes = f.get("notes", "").strip()
        db.session.add(p)
        db.session.flush()
        audit("admin.partner_saved", actor_type="admin", actor_id=g.admin_user,
              contractor_id=p.id, new_value=p.name,
              active=p.active, zips=p.service_zips)
        db.session.commit()
        flash("Partner saved.", "ok")
        return redirect(url_for("admin.partners"))
    editing = Partner.query.get(edit_id) if edit_id else None
    if edit_id and not editing:
        flash("That partner no longer exists.", "error")
        return redirect(url_for("admin.partners"))
    return render_template("admin/partners.html",
                           partners=Partner.query.order_by(Partner.name).all(),
                           editing=editing)


@bp.post("/partners/<int:partner_id>/toggle")
@login_required
def toggle_partner(partner_id):
    """Pause or resume a partner (pausing stops new lead assignments)."""
    check_csrf()
    p = Partner.query.get_or_404(partner_id)
    p.active = not p.active
    audit("admin.partner_saved", actor_type="admin", actor_id=g.admin_user,
          contractor_id=p.id, previous_value="paused" if p.active else "active",
          new_value="active" if p.active else "paused", partner=p.name)
    db.session.commit()
    flash(f"{p.name} is now {'active' if p.active else 'paused'}.", "ok")
    return redirect(url_for("admin.partners"))


@bp.post("/partners/<int:partner_id>/reset-quota")
@login_required
def reset_partner_quota(partner_id):
    """Manually reset this month's lead counter to zero."""
    check_csrf()
    p = Partner.query.get_or_404(partner_id)
    before = p.leads_this_period or 0
    p.leads_this_period = 0
    audit("admin.partner_quota_reset", actor_type="admin", actor_id=g.admin_user,
          contractor_id=p.id, previous_value=before, new_value=0, partner=p.name)
    db.session.commit()
    flash(f"{p.name}'s monthly counter was reset to 0.", "ok")
    return redirect(url_for("admin.partners"))


@bp.post("/partners/<int:partner_id>/delete")
@login_required
def delete_partner(partner_id):
    """Delete a partner. Their leads are unassigned and returned to New —
    lead records and history are never destroyed by this action."""
    check_csrf()
    p = Partner.query.get_or_404(partner_id)
    name = p.name
    affected = Lead.query.filter_by(partner_id=p.id).all()
    for lead in affected:
        lead.partner_id = None
        if lead.status == "sent_to_partner":
            lead.status = "new"
    # Activity/audit rows may also reference this partner (contractor_id).
    # Postgres enforces this foreign key strictly, so clear it before deleting
    # or the delete is rejected. History is preserved; only the link is removed.
    LeadActivity.query.filter_by(contractor_id=p.id).update(
        {"contractor_id": None}, synchronize_session=False)
    audit("admin.partner_deleted", actor_type="admin", actor_id=g.admin_user,
          previous_value=name, leads_unassigned=len(affected))
    try:
        db.session.delete(p)
        db.session.commit()
    except Exception as e:  # noqa: BLE001 — show a friendly message, never a 500
        db.session.rollback()
        logger.error("admin.partner_delete_failed", error=type(e).__name__)
        flash("Couldn't delete that partner. Reassign or remove its leads first, "
              "then try again.", "error")
        return redirect(url_for("admin.partners"))
    flash(f"{name} was deleted. {len(affected)} lead(s) returned to New.", "ok")
    return redirect(url_for("admin.partners"))


@bp.post("/leads/<int:lead_id>/send")
@login_required
def send_lead(lead_id):
    """Deliver a lead to its assigned partner by email, text, or both.

    Deliberately a separate action from saving the lead: assigning a partner
    and telling that partner are different decisions, and conflating them
    means every accidental save fires a text.
    """
    check_csrf()
    import partner_delivery
    lead = Lead.query.get_or_404(lead_id)
    partner = Partner.query.get(lead.partner_id) if lead.partner_id else None
    if not partner:
        flash("Assign a partner before sending this lead.", "error")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))

    channels = [c for c in ("email", "sms") if request.form.get(c) == "on"]
    if not channels:
        flash("Choose at least one way to send it — email or text.", "error")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))

    try:
        results = partner_delivery.send_to_partner(
            lead, partner, channels=channels, actor=g.admin_user)
    except partner_delivery.DeliveryError as exc:
        flash(f"Nothing was sent to {partner.name}. {exc}", "error")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))

    sent = [name for name, r in results.items() if r["ok"]]
    failed = [f"{name} ({r['detail']})" for name, r in results.items() if not r["ok"]]
    if sent:
        flash(f"Sent to {partner.name} by {' and '.join(sent)}.", "ok")
    if failed:
        flash(f"Couldn't send by {', '.join(failed)}.", "warn")
    return redirect(url_for("admin.lead_detail", lead_id=lead.id))


@bp.post("/partners/<int:partner_id>/test-message")
@login_required
def test_partner_message(partner_id):
    """Prove the partner's email and phone actually work before a real lead
    depends on them."""
    check_csrf()
    import partner_delivery
    partner = Partner.query.get_or_404(partner_id)
    lead = (Lead.query.order_by(Lead.created_at.desc()).first())
    if not lead:
        flash("Create or seed one lead first — the test uses a real example.", "error")
        return redirect(url_for("admin.partners"))
    try:
        results = partner_delivery.send_to_partner(
            lead, partner, channels=("email", "sms"), actor=g.admin_user)
    except partner_delivery.DeliveryError as exc:
        flash(f"Test failed for {partner.name}. {exc}", "error")
        return redirect(url_for("admin.partners"))
    detail = "; ".join(
        f"{name}: {'OK ' + r['detail'] if r['ok'] else 'FAILED ' + r['detail']}"
        for name, r in results.items())
    flash(f"Test message to {partner.name} — {detail}", "ok")
    return redirect(url_for("admin.partners"))


@bp.post("/leads/<int:lead_id>/delete")
@login_required
def delete_lead(lead_id):
    """Permanently delete a lead, its photos and its activity history.
    One tombstone audit row (with no personal data) records that the
    deletion happened, who did it and when."""
    check_csrf()
    lead = Lead.query.get_or_404(lead_id)
    ref = lead.reference
    # Remove stored photos
    import os as _os
    for key in (lead.photo_keys or "").split(","):
        if not key:
            continue
        try:
            _os.remove(_os.path.join(current_app.config["UPLOAD_DIR"], key))
        except OSError:
            pass
    activity_count = LeadActivity.query.filter_by(lead_id=lead.id).delete()
    db.session.delete(lead)
    db.session.flush()
    audit("admin.lead_deleted", None, actor_type="admin", actor_id=g.admin_user,
          lead_reference=ref, previous_value=ref,
          activity_rows_removed=activity_count)
    db.session.commit()
    flash(f"Lead {ref} and its history were permanently deleted.", "ok")
    return redirect(url_for("admin.leads"))


@bp.get("/logs")
@login_required
def logs():
    """Protected audit/system log viewer with filters, pagination, CSV."""
    q = LeadActivity.query
    if v := request.args.get("event"):
        q = q.filter(LeadActivity.event_type.ilike(f"%{v}%"))
    if v := request.args.get("status"):
        q = q.filter(LeadActivity.event_status == v)
    if v := request.args.get("lead"):
        q = q.filter(LeadActivity.lead_reference.ilike(f"%{v}%"))
    if v := request.args.get("request_id"):
        q = q.filter(LeadActivity.request_id == v)
    if v := request.args.get("actor"):
        q = q.filter(LeadActivity.actor_type == v)
    if request.args.get("errors_only"):
        q = q.filter(LeadActivity.event_status == "failed")
    if v := request.args.get("from"):
        try:
            q = q.filter(LeadActivity.created_at >= datetime.strptime(v, "%Y-%m-%d"))
        except ValueError:
            pass
    if v := request.args.get("to"):
        try:
            q = q.filter(LeadActivity.created_at <= datetime.strptime(v, "%Y-%m-%d")
                         .replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    q = q.order_by(LeadActivity.created_at.desc())
    if request.args.get("export") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["created_at", "activity_id", "event_type", "status",
                    "lead", "actor", "request_id", "previous", "new", "metadata"])
        for a in q.limit(10000).all():
            w.writerow([a.created_at, a.activity_id, a.event_type,
                        a.event_status, a.lead_reference,
                        f"{a.actor_type}:{a.actor_id or ''}", a.request_id,
                        a.previous_value, a.new_value, a.metadata_json])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"})
    page = max(int(request.args.get("page", 1)), 1)
    pagination = q.paginate(page=page, per_page=50, error_out=False)
    event_types = [r[0] for r in db.session.query(LeadActivity.event_type).distinct()]
    tab = request.args.get("tab", "activity")
    return render_template("admin/logs.html", pagination=pagination,
                           event_types=sorted(event_types), tab=tab)


@bp.get("/settings")
@login_required
def settings():
    """Read-only settings overview. Values are managed via environment
    variables (Railway service Variables) — secrets are never displayed."""
    cfg = current_app.config
    import os
    integrations = [
        ("SMS OTP verification (Bird)",
         bool(cfg["PHONE_VERIFICATION_HMAC_SECRET"])
         and bool(cfg.get("BIRD_API_KEY"))),
        ("SMTP email sending", cfg["MAIL_BACKEND"] == "smtp" and bool(cfg["SMTP_HOST"])),
        ("PostgreSQL database", cfg["SQLALCHEMY_DATABASE_URI"].startswith("postgresql")),
        ("Google Analytics / Tag Manager", False),
    ]
    general = [
        ("Business name", cfg["BRAND_NAME"]),
        ("Public phone", cfg["PUBLIC_PHONE"] if "TRACKING_NUMBER" not in cfg["PUBLIC_PHONE"] else "Not configured"),
        ("Site URL", cfg["SITE_URL"]),
        ("Admin notification email", cfg["ADMIN_NOTIFY_EMAIL"]),
        ("Duplicate-detection window", "30 days"),
        ("Rate limit", f"{cfg['RATE_LIMIT_SUBMISSIONS']} submissions / {cfg['RATE_LIMIT_WINDOW_SECONDS'] // 60} min"),
        ("Log level", os.getenv("LOG_LEVEL", "INFO")),
        ("Audit retention", os.getenv("AUDIT_RETENTION_DAYS", "Keep forever (default)")),
    ]
    from models import PhoneVerificationAttempt, SmsBudget
    from datetime import date, timedelta
    since = date.today() - timedelta(days=30)
    rows = SmsBudget.query.filter(SmsBudget.day >= since).all()
    totals = {k: sum(getattr(r, k) or 0 for r in rows)
              for k in ("attempted", "sent", "delivered", "failed", "verified")}
    cost = float(sum(r.cost_amount or 0 for r in rows))
    sms = {
        **totals,
        "cost": cost,
        "completion_rate": round(totals["verified"] / totals["sent"] * 100, 1) if totals["sent"] else None,
        "sms_per_verified": round(totals["sent"] / totals["verified"], 2) if totals["verified"] else None,
        "cost_per_verified": round(cost / totals["verified"], 4) if totals["verified"] else None,
        "today_sent": (SmsBudget.query.filter_by(day=date.today()).first().sent
                       if SmsBudget.query.filter_by(day=date.today()).first() else 0),
        "daily_limit": cfg["PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT"],
        "blocked": SmsBudget.query.with_entities(db.func.coalesce(db.func.sum(SmsBudget.blocked), 0)).scalar() or 0,
        "expired": PhoneVerificationAttempt.query.filter_by(status="expired").count(),
    }
    db_uri = cfg["SQLALCHEMY_DATABASE_URI"]
    is_pg = db_uri.startswith("postgresql")
    photos_backend = cfg.get("STORAGE_BACKEND", "local")
    photos_cloud = photos_backend != "local"
    storage = {
        "db_label": "PostgreSQL — persistent" if is_pg else "SQLite file — resets on redeploy",
        "db_persistent": is_pg,
        "photos_label": ("Cloud object storage — persistent" if photos_cloud
                         else "Local disk — resets on redeploy"),
        "photos_persistent": photos_cloud,
        "lead_count": Lead.query.count(),
    }
    return render_template("admin/settings.html", general=general,
                           integrations=integrations, sms=sms, storage=storage)
