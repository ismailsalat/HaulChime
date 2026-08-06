"""
Is this partner able to take this lead?

One function, one answer, used everywhere: the admin dropdown, the assignment
confirmation, and the server-side check when the assignment is actually saved.
Duplicating this logic across those three places is how a partner ends up
looking eligible in the UI and being rejected on save, or worse, the reverse.

Three outcomes, never two:

    eligible      every condition checked and satisfied
    needs_review  nothing is wrong, but something could not be checked
    not_eligible  at least one condition is definitively violated

The middle one matters. If a customer said "I'm flexible" rather than giving a
date, we cannot know whether the partner is free — and calling that "eligible"
would quietly assign jobs to people who aren't available. Unknown is not the
same as fine, so it gets its own state and a human decides.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

# 0 = Monday, matching Python's date.weekday().
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]

# Timing answers that pin down a real day, and roughly how soon.
TIMING_LEAD_HOURS = {
    "asap": 0, "today": 0,
    "2_3_days": 48, "48_hours": 48,
    "one_week": 168, "this_week": 168,
    "flexible": None,          # no committed date
    "specific_date": None,     # read from service_date instead
}

PREFERRED_TIME_WINDOWS = {
    "morning": (time(8, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(21, 0)),
}


class Check:
    """One condition, with a label the admin can actually read.

    `state` is "ok", "unknown" or "fail". Storing the reason next to the result
    means the confirmation dialog can list exactly what is wrong without
    re-deriving it.
    """

    __slots__ = ("key", "label", "state", "detail")

    def __init__(self, key, label, state, detail=""):
        self.key = key
        self.label = label
        self.state = state
        self.detail = detail

    @property
    def ok(self):
        return self.state == "ok"

    def as_dict(self):
        return {"key": self.key, "label": self.label,
                "state": self.state, "detail": self.detail}


def _parse_hhmm(value, fallback):
    try:
        hour, minute = str(value or "").split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return fallback


def _requested_date(lead):
    """The day the job would happen, or None if the customer didn't commit.

    A specific date wins. Otherwise a relative answer ("within 2-3 days") gives
    a window rather than a day, and we deliberately return None: a window is
    not a date, and pretending otherwise produces confident wrong answers.
    """
    raw = (getattr(lead, "service_date", "") or "").strip()
    if raw:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    urgency = (getattr(lead, "urgency", "") or "").strip()
    if urgency in ("today", "asap"):
        return date.today()
    return None


def _requested_window(lead):
    """(start, end) times, or None when the customer had no preference."""
    preferred = (getattr(lead, "preferred_time", "") or "").strip()
    return PREFERRED_TIME_WINDOWS.get(preferred)


def _notice_hours(lead, job_day):
    if job_day is None:
        return None
    delta = datetime.combine(job_day, time(8, 0)) - datetime.now()
    return delta.total_seconds() / 3600.0


def check_schedule(partner, lead):
    """Day, time, time-off and notice, as separate readable checks."""
    checks = []
    job_day = _requested_date(lead)

    if job_day is None:
        checks.append(Check("day", "Requested day", "unknown",
                            "Customer gave a timing range, not a date"))
        checks.append(Check("hours", "Within working hours", "unknown",
                            "No specific date or time was given"))
        checks.append(Check("notice", "Minimum notice", "unknown",
                            "No date to measure notice against"))
        return checks

    weekday = job_day.weekday()
    day_name = DAY_NAMES[weekday]
    rows = {row.day_of_week: row for row in (partner.availability or [])}
    row = rows.get(weekday)

    if not rows:
        checks.append(Check("day", "Requested day", "unknown",
                            "Partner has not set a weekly schedule"))
        checks.append(Check("hours", "Within working hours", "unknown",
                            "No schedule on file"))
    elif row is None or not row.available:
        checks.append(Check("day", "Requested day", "fail",
                            f"Not available on {day_name}"))
        checks.append(Check("hours", "Within working hours", "fail",
                            f"{day_name} is marked unavailable"))
    else:
        checks.append(Check("day", "Requested day", "ok",
                            f"Available {day_name} {row.start_time}-{row.end_time}"))
        window = _requested_window(lead)
        if window is None:
            checks.append(Check("hours", "Within working hours", "unknown",
                                "Customer gave no preferred time of day"))
        else:
            opens = _parse_hhmm(row.start_time, time(8, 0))
            closes = _parse_hhmm(row.end_time, time(17, 0))
            want_start, want_end = window
            if want_start >= opens and want_end <= closes:
                checks.append(Check("hours", "Within working hours", "ok",
                                    f"{want_start:%H:%M}-{want_end:%H:%M} fits "
                                    f"{row.start_time}-{row.end_time}"))
            else:
                checks.append(Check("hours", "Within working hours", "fail",
                                    f"Customer wants {want_start:%H:%M}-{want_end:%H:%M}, "
                                    f"partner works {row.start_time}-{row.end_time}"))

    clash = next((t for t in (partner.time_off or []) if t.covers(job_day)), None)
    if clash:
        checks.append(Check("time_off", "Time off", "fail",
                            f"On time off {clash.start_date} to {clash.end_date}"))
    else:
        checks.append(Check("time_off", "Time off", "ok", "No time off booked"))

    hours = _notice_hours(lead, job_day)
    required = partner.minimum_notice_hours if partner.minimum_notice_hours is not None else 24
    if hours is None:
        checks.append(Check("notice", "Minimum notice", "unknown", "No date given"))
    elif hours < 0:
        checks.append(Check("notice", "Minimum notice", "fail", "Requested date is in the past"))
    elif hours < 24 and not partner.same_day_ok and required > hours:
        checks.append(Check("notice", "Minimum notice", "fail",
                            f"Needs {required}h notice, job is in {hours:.0f}h"))
    elif hours < required:
        checks.append(Check("notice", "Minimum notice", "fail",
                            f"Needs {required}h notice, job is in {hours:.0f}h"))
    else:
        checks.append(Check("notice", "Minimum notice", "ok",
                            f"{hours:.0f}h notice, needs {required}h"))
    return checks


def evaluate(partner, lead, *, lead_price=None, leads_today=None, application=None):
    """Full eligibility picture for one partner and one lead.

    Returns a dict with `status`, the ordered `checks`, and flat lists of
    failure and unknown reasons ready to drop into a confirmation dialog.
    """
    from models import LeadAssignment, PartnerApplication

    checks = []

    # --- account standing ------------------------------------------------
    if application is None:
        application = (PartnerApplication.query
                       .filter_by(partner_id=partner.id)
                       .order_by(PartnerApplication.id.desc()).first())
    if application is None:
        # Partners created directly by the admin predate the application flow.
        # They are legitimate, so this is not a failure.
        checks.append(Check("application", "Application", "ok",
                            "Created directly by admin"))
    elif application.status == "approved":
        checks.append(Check("application", "Application", "ok", "Approved"))
    else:
        checks.append(Check("application", "Application", "fail",
                            f"Application is {application.status.replace('_', ' ')}"))

    checks.append(Check("active", "Partner active", "ok", "Active")
                  if partner.active else
                  Check("active", "Partner active", "fail", "Deactivated by admin"))

    taking = partner.taking_leads if partner.taking_leads is not None else True
    checks.append(Check("taking_leads", "Taking leads", "ok", "Accepting new leads")
                  if taking else
                  Check("taking_leads", "Taking leads", "fail", "Partner paused new leads"))

    # --- job fit ---------------------------------------------------------
    service = (lead.service_type or lead.pest_type or "").strip()
    accepted = {s.strip() for s in (partner.services_accepted or "").split(",") if s.strip()}
    if not accepted:
        checks.append(Check("service", "Service match", "unknown",
                            "Partner has no services listed"))
    elif service in accepted:
        checks.append(Check("service", "Service match", "ok",
                            service.replace("_", " ")))
    else:
        checks.append(Check("service", "Service match", "fail",
                            f"Does not accept {service.replace('_', ' ')}"))

    zips = {z.strip() for z in (partner.service_zips or "").split(",") if z.strip()}
    if not zips:
        checks.append(Check("zip", "ZIP coverage", "unknown", "No service ZIPs listed"))
    elif lead.zip_code in zips:
        checks.append(Check("zip", "ZIP coverage", "ok", f"Covers {lead.zip_code}"))
    else:
        checks.append(Check("zip", "ZIP coverage", "fail",
                            f"{lead.zip_code} not in service area"))

    # --- capability ------------------------------------------------------
    heavy_wanted = bool((lead.special_items or "").strip()) or \
        "heavy_specialty" in (lead.item_categories or "")
    if not heavy_wanted:
        checks.append(Check("heavy", "Heavy-item capability", "ok", "Not needed"))
    elif partner.heavy_item_capable:
        checks.append(Check("heavy", "Heavy-item capability", "ok", "Capable"))
    else:
        checks.append(Check("heavy", "Heavy-item capability", "fail",
                            "Job has heavy or special items"))

    commercial_wanted = (lead.property_type or "") in ("commercial", "office") or \
        (lead.job_size or "") in ("office", "commercial")
    if not commercial_wanted:
        checks.append(Check("commercial", "Commercial capability", "ok", "Not needed"))
    elif partner.commercial_capable:
        checks.append(Check("commercial", "Commercial capability", "ok", "Capable"))
    else:
        checks.append(Check("commercial", "Commercial capability", "fail",
                            "Job is commercial"))

    # --- crew capacity ----------------------------------------------------
    # A three-person job silently reduced to a two-person crew is how someone
    # ends up alone with a piano at the top of a staircase.
    import job_costing
    needed, reasons = job_costing.recommend_crew(
        service_type=service, job_size=lead.job_size or "",
        item_categories=lead.item_categories or "",
        special_item_types=getattr(lead, "special_items", "") or "",
        access_issues=lead.access_issues or "",
        stairs_flights=lead.stairs_flights or "")
    available = partner.available_crew_size or partner.crew_size
    if not available:
        checks.append(Check("crew", "Crew capacity", "unknown",
                            f"Job needs {needed}; partner crew size not recorded"))
    elif available >= needed:
        checks.append(Check("crew", "Crew capacity", "ok",
                            f"{available} available, job needs {needed}"))
    else:
        checks.append(Check("crew", "Crew capacity", "fail",
                            f"Partner has {available} available, this job needs "
                            f"{needed} ({'; '.join(reasons[1:]) or 'job size'})"))

    # --- schedule --------------------------------------------------------
    checks.extend(check_schedule(partner, lead))

    # --- commercial limits ----------------------------------------------
    if leads_today is None:
        today = date.today()
        leads_today = (LeadAssignment.query
                       .filter(LeadAssignment.partner_id == partner.id)
                       .filter(db_date(LeadAssignment.assigned_at) == today)
                       .count())
    limit = partner.daily_lead_limit or 0
    if not limit:
        checks.append(Check("daily_limit", "Daily lead limit", "ok", "No limit set"))
    elif leads_today < limit:
        checks.append(Check("daily_limit", "Daily lead limit", "ok",
                            f"{leads_today} of {limit} today"))
    else:
        checks.append(Check("daily_limit", "Daily lead limit", "fail",
                            f"Limit reached ({leads_today} of {limit})"))

    price = Decimal(str(lead_price if lead_price is not None
                        else (lead.lead_charge or lead.lead_price or 0)))
    maximum = Decimal(str(partner.max_lead_price or 0))
    if not maximum:
        checks.append(Check("max_price", "Maximum lead price", "ok", "No maximum set"))
    elif price <= maximum:
        checks.append(Check("max_price", "Maximum lead price", "ok",
                            f"${price:.2f} within ${maximum:.2f}"))
    else:
        checks.append(Check("max_price", "Maximum lead price", "fail",
                            f"${price:.2f} exceeds their ${maximum:.2f} maximum"))

    balance = Decimal(str(partner.credit_balance or 0))
    if partner.billing_type == "monthly":
        checks.append(Check("credit", "Credit", "ok", "Monthly plan"))
    elif balance >= price:
        checks.append(Check("credit", "Credit balance", "ok",
                            f"${balance:.2f} available"))
    else:
        checks.append(Check("credit", "Credit balance", "fail",
                            f"${balance:.2f} available, lead costs ${price:.2f}"))

    failures = [c for c in checks if c.state == "fail"]
    unknowns = [c for c in checks if c.state == "unknown"]
    status = "not_eligible" if failures else ("needs_review" if unknowns else "eligible")

    return {
        "partner_id": partner.id,
        "partner_name": partner.name,
        "status": status,
        "checks": [c.as_dict() for c in checks],
        "failures": [f"{c.label}: {c.detail}" for c in failures],
        "unknowns": [f"{c.label}: {c.detail}" for c in unknowns],
        "lead_price": f"{price:.2f}",
        "credit_balance": f"{balance:.2f}",
        "leads_today": leads_today,
        "daily_limit": limit,
    }


def db_date(column):
    """Portable DATE() for the daily-limit count. SQLite and Postgres disagree
    about how to truncate a timestamp, so go through SQLAlchemy's cast."""
    from sqlalchemy import Date, cast
    return cast(column, Date)


# Sort key for the admin dropdown: eligible first, then needs review, then not
# eligible — and alphabetical inside each group so the list is stable.
SORT_ORDER = {"eligible": 0, "needs_review": 1, "not_eligible": 2}


def rank(result):
    return (SORT_ORDER.get(result["status"], 3), result["partner_name"].lower())


def evaluate_all(partners, lead, **kwargs):
    return sorted((evaluate(p, lead, **kwargs) for p in partners), key=rank)
