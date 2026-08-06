"""
Admin: partner applications, and assigning a lead to a partner.

Kept in its own module so the existing admin blueprint stays readable. It
registers onto the same blueprint, under the same login, with the same CSRF.

The assignment flow is the important part. There are two ways to get it wrong:
block the admin from doing something they legitimately need to do, or let them
do it without noticing. So nothing here is ever silently refused *or* silently
allowed — an ineligible partner can be assigned, but only through an explicit
override that names every warning and is written to the audit trail.
"""
import json
from datetime import datetime, timezone

from flask import (abort, current_app, flash, g, redirect, render_template,
                   request, url_for)

import logger
import partner_eligibility
from logger import audit
from models import (Lead, LeadAssignment, Partner, PartnerAccount,
                    PartnerActivity, PartnerApplication, PartnerAvailability,
                    PartnerNotification, db)


def register(bp, login_required, check_csrf):
    """Attach these routes to the existing admin blueprint."""

    # ------------------------------------------------------ applications
    @bp.get("/partner-applications")
    @login_required
    def partner_applications():
        status = request.args.get("status", "")
        query = PartnerApplication.query
        if status:
            query = query.filter_by(status=status)
        applications = query.order_by(PartnerApplication.created_at.desc()).all()
        counts = {}
        for row in PartnerApplication.query.all():
            counts[row.status] = counts.get(row.status, 0) + 1
        return render_template("admin/partner_applications.html",
                               applications=applications, counts=counts,
                               status=status)

    @bp.get("/partner-applications/<int:application_id>")
    @login_required
    def partner_application_detail(application_id):
        application = PartnerApplication.query.get_or_404(application_id)
        schedule = []
        try:
            schedule = json.loads(application.availability_json or "[]")
        except ValueError:
            schedule = []
        return render_template("admin/partner_application_detail.html",
                               a=application, schedule=schedule)

    @bp.post("/partner-applications/<int:application_id>")
    @login_required
    def partner_application_action(application_id):
        check_csrf()
        application = PartnerApplication.query.get_or_404(application_id)
        action = request.form.get("action", "")
        message = (request.form.get("admin_message") or "").strip()[:2000]
        now = datetime.now(timezone.utc)

        if action == "approve":
            partner = _approve(application, request.form, now)
            flash(f"{application.business_name} approved. They can sign in now.", "ok")
            return redirect(url_for("admin.partners", edit_id=partner.id))

        if action in ("reject", "request_changes", "suspend", "reactivate"):
            previous = application.status
            application.status = {
                "reject": "rejected", "request_changes": "changes_requested",
                "suspend": "suspended", "reactivate": "approved",
            }[action]
            application.admin_message = message or application.admin_message
            application.reviewed_at = now
            # Suspending must actually cut off access, not just relabel it.
            if application.partner_id:
                account = PartnerAccount.query.filter_by(
                    partner_id=application.partner_id).first()
                if action == "suspend":
                    if account:
                        account.active = False
                    Partner.query.get(application.partner_id).active = False
                elif action == "reactivate":
                    if account:
                        account.active = True
                    Partner.query.get(application.partner_id).active = True
            db.session.add(PartnerActivity(
                partner_id=application.partner_id, event_type=f"application.{action}",
                old_value=previous, new_value=application.status))
            db.session.commit()
            logger.info("admin.application_action", action=action,
                        application_id=application.id, actor=g.admin_user)
            flash(f"Application marked {application.status.replace('_', ' ')}.", "ok")
            return redirect(url_for("admin.partner_application_detail",
                                    application_id=application.id))

        abort(400)

    def _approve(application, form, now):
        """Create or reuse the real Partner record and the login account.

        The application itself is preserved — it becomes the historical record
        of what the company told us when they applied.
        """
        partner = (Partner.query.get(application.partner_id)
                   if application.partner_id else None)
        if partner is None:
            partner = Partner(name=application.business_name)
            db.session.add(partner)
            db.session.flush()

        partner.contact_person = application.contact_person
        partner.email = application.email
        partner.notification_email = partner.notification_email or application.email
        partner.phone = application.phone
        partner.service_zips = application.zip_codes
        partner.services_accepted = application.services_accepted
        partner.crew_size = application.crew_size
        partner.truck_capacity = application.truck_capacity
        partner.heavy_item_capable = application.heavy_item_capable
        partner.commercial_capable = application.commercial_capable
        partner.minimum_job = (application.minimum_job_requirements or "")[:255]
        partner.jobs_not_accepted = application.jobs_not_accepted
        partner.minimum_notice_hours = application.minimum_notice_hours
        partner.same_day_ok = (application.minimum_notice_hours or 24) == 0
        partner.taking_leads = True
        partner.active = form.get("active", "on") == "on"
        partner.approved_at = now

        # Commercial terms are the admin's to set, never the applicant's.
        for field, caster in (("credit_balance", float), ("max_lead_price", float),
                              ("price_per_lead", float), ("daily_lead_limit", int)):
            raw = (form.get(field) or "").strip()
            if raw:
                try:
                    setattr(partner, field, caster(raw))
                except ValueError:
                    pass
        notes = (form.get("internal_notes") or "").strip()
        if notes:
            partner.notes = notes

        # Carry the weekly grid from the application into real rows.
        try:
            grid = json.loads(application.availability_json or "[]")
        except ValueError:
            grid = []
        existing = {r.day_of_week: r for r in (partner.availability or [])}
        for entry in grid:
            day = int(entry.get("day", 0))
            row = existing.get(day) or PartnerAvailability(
                partner_id=partner.id, day_of_week=day)
            row.available = bool(entry.get("available"))
            row.start_time = (entry.get("start") or "08:00")[:5]
            row.end_time = (entry.get("end") or "17:00")[:5]
            if row not in db.session:
                db.session.add(row)

        account = PartnerAccount.query.filter_by(phone=application.phone).first()
        if account is None:
            account = PartnerAccount(partner_id=partner.id, phone=application.phone,
                                     phone_verified=True, active=True)
            db.session.add(account)
        else:
            account.partner_id = partner.id
            account.active = True
        account.application_id = application.id

        application.partner_id = partner.id
        application.status = "approved"
        application.approved_at = now
        application.reviewed_at = now
        db.session.add(PartnerActivity(
            partner_id=partner.id, event_type="application.approved",
            new_value=partner.name))
        db.session.commit()
        logger.info("admin.partner_approved", partner_id=partner.id,
                    application_id=application.id, actor=g.admin_user)
        return partner

    # -------------------------------------------------------- assignment
    @bp.get("/leads/<int:lead_id>/assign")
    @login_required
    def assign_panel(lead_id):
        """Eligibility for every partner, sorted best-first."""
        lead = Lead.query.get_or_404(lead_id)
        price = float(lead.lead_charge or lead.lead_price or 0)
        results = partner_eligibility.evaluate_all(
            Partner.query.order_by(Partner.name).all(), lead, lead_price=price)
        return render_template("admin/assign_panel.html", lead=lead,
                               results=results, price=f"{price:.2f}")

    @bp.post("/leads/<int:lead_id>/assign")
    @login_required
    def assign_partner(lead_id):
        check_csrf()
        lead = Lead.query.get_or_404(lead_id)
        partner = Partner.query.get_or_404(int(request.form.get("partner_id") or 0))
        price = float(request.form.get("lead_price")
                      or lead.lead_charge or lead.lead_price or 0)

        result = partner_eligibility.evaluate(partner, lead, lead_price=price)
        warnings = result["failures"] + result["unknowns"]
        confirmed = request.form.get("confirm_override") == "yes"

        # An admin may assign anyone, but never by accident. If anything is
        # wrong or unknown, the override checkbox has to have been ticked.
        if warnings and not confirmed:
            audit("assignment.blocked", lead, status="warn", actor_type="admin",
                  actor_id=g.admin_user, contractor_id=partner.id,
                  reasons="; ".join(warnings)[:400])
            db.session.commit()
            return render_template("admin/assign_panel.html", lead=lead,
                                   results=[result], price=f"{price:.2f}",
                                   blocked=result), 400

        existing = LeadAssignment.query.filter_by(
            lead_id=lead.id, partner_id=partner.id).first()
        if existing:
            flash(f"{partner.name} already has this lead.", "warn")
            return redirect(url_for("admin.lead_detail", lead_id=lead.id))

        assignment = LeadAssignment(
            lead_id=lead.id, partner_id=partner.id, lead_price=price,
            status="assigned", assigned_by_admin=g.admin_user,
            assigned_with_override=bool(warnings),
            override_reasons="; ".join(warnings)[:2000] if warnings else None)
        db.session.add(assignment)
        lead.partner_id = partner.id
        if lead.status == "new":
            lead.status = "sent_to_partner"

        # The notification deliberately carries no customer detail — the
        # partner signs in to see the lead.
        db.session.add(PartnerNotification(
            partner_id=partner.id, lead_id=lead.id,
            title="New lead assigned",
            message=f"A new {(lead.service_type or '').replace('_', ' ')} lead in "
                    f"{lead.city or lead.zip_code} is waiting for you."))
        db.session.add(PartnerActivity(
            partner_id=partner.id, lead_id=lead.id, event_type="lead.assigned",
            new_value=f"by {g.admin_user}"))

        audit("assignment.created", lead, actor_type="admin", actor_id=g.admin_user,
              contractor_id=partner.id, new_value=partner.name,
              lead_price=price, override=bool(warnings),
              reasons="; ".join(warnings)[:400] if warnings else None)
        db.session.commit()

        _notify_partner_by_sms(partner, lead)
        logger.info("admin.lead_assigned", lead=lead.reference,
                    partner_id=partner.id, override=bool(warnings))
        if warnings:
            flash(f"Assigned to {partner.name} with an override. "
                  f"Warnings recorded: {'; '.join(warnings)}", "warn")
        else:
            flash(f"Assigned to {partner.name}. They've been notified.", "ok")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))

    @bp.post("/leads/<int:lead_id>/unassign")
    @login_required
    def unassign_partner(lead_id):
        check_csrf()
        lead = Lead.query.get_or_404(lead_id)
        assignment = LeadAssignment.query.filter_by(
            id=int(request.form.get("assignment_id") or 0), lead_id=lead.id).first()
        if not assignment:
            abort(404)
        partner_name = assignment.partner.name
        audit("assignment.removed", lead, actor_type="admin", actor_id=g.admin_user,
              contractor_id=assignment.partner_id, old_value=partner_name,
              was_accepted=assignment.customer_visible)
        db.session.add(PartnerActivity(
            partner_id=assignment.partner_id, lead_id=lead.id,
            event_type="lead.unassigned", old_value=assignment.status))
        db.session.delete(assignment)
        remaining = LeadAssignment.query.filter_by(lead_id=lead.id).count()
        if remaining <= 1:
            lead.partner_id = None
        db.session.commit()
        flash(f"Removed {partner_name} from this lead.", "ok")
        return redirect(url_for("admin.lead_detail", lead_id=lead.id))

    def _notify_partner_by_sms(partner, lead):
        """Short, no customer detail, best effort. A failed text must not
        undo an assignment that is already committed."""
        cfg = current_app.config
        if not cfg.get("BIRD_API_KEY") or not partner.phone:
            return
        import bird_client
        import phone as phone_util
        checked = phone_util.validate_us_mobile(partner.phone)
        if not (checked and checked.ok):
            return
        try:
            bird_client.send_notification(
                cfg, checked.e164,
                "A new HaulChime lead has been assigned to you. "
                f"Sign in to review it: {cfg['SITE_URL'].rstrip('/')}/partner/login")
            db.session.add(PartnerActivity(
                partner_id=partner.id, lead_id=lead.id,
                event_type="notification.sms_sent"))
            db.session.commit()
        except Exception:
            logger.warn("admin.assign_sms_failed", partner_id=partner.id)
