"""SQLAlchemy models. Portable between SQLite (dev) and PostgreSQL (prod)."""
import secrets
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

LEAD_STATUSES = [
    "new",                  # just arrived, needs your review
    "validation_needed",    # questionable, check before sending
    "sent_to_partner",      # delivered and billable — the job is done
    "duplicate",
    "invalid",              # spam, fake, or unusable
    "outside_service_area", # no partner covers this ZIP
]
QUALIFICATION_STATUSES = ["pending", "qualified", "unqualified"]


def utcnow():
    return datetime.now(timezone.utc)


def make_reference():
    """Lead ID in the format HC-YYYYMMDD-####."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"HC-{day}-{secrets.randbelow(10000):04d}"


class Partner(db.Model):
    __tablename__ = "partners"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact_person = db.Column(db.String(120))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(40))
    service_zips = db.Column(db.Text, default="")       # comma-separated
    services_accepted = db.Column(db.Text, default="")  # comma-separated slugs
    minimum_job = db.Column(db.String(255))
    notification_email = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True)
    price_per_lead = db.Column(db.Numeric(10, 2))
    # Prepaid lead marketplace controls (payment processor integration is separate).
    credit_balance = db.Column(db.Numeric(10, 2), default=0)
    max_lead_price = db.Column(db.Numeric(10, 2), default=70)
    daily_lead_limit = db.Column(db.Integer, default=10)
    crew_size = db.Column(db.Integer)
    truck_capacity = db.Column(db.String(40))
    heavy_item_capable = db.Column(db.Boolean, default=False)
    commercial_capable = db.Column(db.Boolean, default=False)
    # Billing: one-off per-lead, or a recurring monthly plan.
    billing_type = db.Column(db.String(20), default="per_lead")  # per_lead | monthly
    monthly_price = db.Column(db.Numeric(10, 2))    # used when billing_type=monthly
    monthly_lead_quota = db.Column(db.Integer)      # leads included per month
    quota_period_start = db.Column(db.Date)         # first day of current cycle
    leads_this_period = db.Column(db.Integer, default=0)
    overage_price_per_lead = db.Column(db.Numeric(10, 2))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def reset_period_if_needed(self):
        """Roll the counter over on the 1st of each month."""
        from datetime import date
        today = date.today()
        start = today.replace(day=1)
        if self.quota_period_start != start:
            self.quota_period_start = start
            self.leads_this_period = 0

    @property
    def quota_display(self):
        """'12 / 50' for monthly plans, or a plain count for per-lead."""
        if self.billing_type == "monthly" and self.monthly_lead_quota:
            return f"{self.leads_this_period or 0} / {self.monthly_lead_quota}"
        return str(self.leads_this_period or 0)

    @property
    def quota_state(self):
        """ok | near | over — drives the badge color in the admin."""
        if self.billing_type != "monthly" or not self.monthly_lead_quota:
            return "ok"
        used, quota = (self.leads_this_period or 0), self.monthly_lead_quota
        if used > quota:
            return "over"
        if used >= quota * 0.8:
            return "near"
        return "ok"

    def serves_zip(self, zip_code):
        zips = {z.strip() for z in (self.service_zips or "").split(",") if z.strip()}
        return zip_code in zips


class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True, default=make_reference, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)

    # Contact
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(255))  # optional per lead-quality plan
    preferred_contact = db.Column(db.String(20), default="phone")
    contact_time = db.Column(db.String(20))  # morning|afternoon|evening|anytime
    comments = db.Column(db.Text)

    # Location
    zip_code = db.Column(db.String(10), nullable=False, index=True)
    city = db.Column(db.String(80))
    property_type = db.Column(db.String(20))
    pickup_address = db.Column(db.String(300))
    pickup_unit = db.Column(db.String(40))
    pickup_state = db.Column(db.String(10))
    destination_address = db.Column(db.String(300))
    destination_unit = db.Column(db.String(40))
    destination_city = db.Column(db.String(80))
    destination_state = db.Column(db.String(10))
    destination_zip = db.Column(db.String(10), index=True)
    destination_known = db.Column(db.Boolean, default=True)
    # Smarty (smarty.com) verification result for the pickup address.
    address_verified = db.Column(db.Boolean, default=False)
    address_verification = db.Column(db.Text)  # JSON snapshot from Smarty

    # Haul / moving job
    service_type = db.Column(db.String(60), index=True)
    # pest_type remains as a compatibility mirror used by a few older admin/report paths.
    pest_type = db.Column(db.String(60), index=True)
    # The customer's answer to "what best describes the job?" — the sub-type
    # inside the chosen service (e.g. apartment_move, garage_cleanout).
    job_type = db.Column(db.String(60))
    job_size = db.Column(db.String(60))
    inventory = db.Column(db.Text)
    # Multi-select answers, stored as comma-separated slugs.
    item_categories = db.Column(db.Text)
    extra_services = db.Column(db.Text)
    special_items = db.Column(db.Text)
    special_items_note = db.Column(db.Text)
    access_issues = db.Column(db.Text)
    destination_access_issues = db.Column(db.Text)
    stairs_flights = db.Column(db.String(10))
    destination_stairs_flights = db.Column(db.String(10))
    pickup_access = db.Column(db.String(50))
    destination_access = db.Column(db.String(50))
    parking_access = db.Column(db.String(50))
    service_date = db.Column(db.String(30))
    preferred_time = db.Column(db.String(20))
    description = db.Column(db.Text)
    location_seen = db.Column(db.String(30))  # legacy compatibility
    urgency = db.Column(db.String(30))
    # INTERNAL ONLY. What the job plausibly costs and is worth, so the admin
    # can price the lead. Never returned by the public API, never emailed to a
    # customer, never rendered on a customer-facing page.
    estimated_job_value = db.Column(db.Numeric(10, 2))
    cost_breakdown = db.Column(db.Text)          # JSON from job_costing.py
    cost_confidence = db.Column(db.String(10))   # high | medium | low
    difficulty_score = db.Column(db.Integer, default=0)
    information_score = db.Column(db.Integer, default=0)
    lead_tier = db.Column(db.String(30), default="standard", index=True)
    lead_price = db.Column(db.Numeric(10, 2), default=40)
    job_details = db.Column(db.Text)  # JSON snapshot of structured questionnaire answers

    # Qualification scoring (computed at intake; see routes/public.py)
    score = db.Column(db.Integer, default=0)
    quality = db.Column(db.String(20), default="needs_review")  # A|B|C|D|rejected
    score_breakdown = db.Column(db.Text)   # JSON: fit/intent/contactability/...
    billable = db.Column(db.Boolean, default=False)
    phone_verified = db.Column(db.Boolean, default=False)   # set by SMS OTP
    phone_verification_status = db.Column(db.String(20), default="not_started")
    phone_verified_at = db.Column(db.DateTime(timezone=True))
    phone_verification_method = db.Column(db.String(20))     # sms_otp | reused
    phone_verification_reused = db.Column(db.Boolean, default=False)
    phone_verification_attempt_id = db.Column(db.String(50))
    firebase_uid = db.Column(db.String(128))
    phone_risk_flags = db.Column(db.String(120))
    phone_hash = db.Column(db.String(64), index=True)
    email_verified = db.Column(db.Boolean, default=False)  # hot|qualified|needs_review|rejected
    duplicate_of = db.Column(db.String(30))  # reference of earlier lead w/ same phone

    # Photos: relative storage keys, comma-separated (JSON column in Postgres later)
    photo_keys = db.Column(db.Text, default="")

    # Compliance
    consent_text = db.Column(db.Text)
    consent_timestamp = db.Column(db.DateTime(timezone=True))

    # Attribution
    referrer_url = db.Column(db.String(500))
    utm_source = db.Column(db.String(120))
    utm_medium = db.Column(db.String(120))
    utm_campaign = db.Column(db.String(120))
    gclid = db.Column(db.String(255))
    fbclid = db.Column(db.String(255))
    landing_page = db.Column(db.String(500))
    # Proof of submission: the exact payload as received (sensitive values
    # included — this is the protected DB, not a log). Never edited later.
    original_submission = db.Column(db.Text)
    form_version = db.Column(db.String(20), default="v2")

    # Pipeline
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"))
    partner = db.relationship("Partner")
    status = db.Column(db.String(30), default="new", index=True)
    qualification = db.Column(db.String(20), default="pending")
    # Referral model: we hand off the lead; the partner does the work.
    # No job-outcome tracking lives here.
    lead_charge = db.Column(db.Numeric(10, 2))
    admin_notes = db.Column(db.Text)

    @property
    def photos(self):
        return [k for k in (self.photo_keys or "").split(",") if k]

    def to_admin_dict(self):
        return {
            "id": self.id, "reference": self.reference,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "name": f"{self.first_name} {self.last_name}",
            "phone": self.phone, "email": self.email,
            "zip_code": self.zip_code, "city": self.city,
            "service_type": self.service_type or self.pest_type,
            "status": self.status, "qualification": self.qualification,
        }


class LeadActivity(db.Model):
    """Append-only audit trail. Application code must never UPDATE or DELETE
    rows here — corrections are recorded as new events (see LOGGING.md)."""
    __tablename__ = "lead_activity"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.String(30), unique=True, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), index=True)
    lead_reference = db.Column(db.String(30), index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey("partners.id"))
    request_id = db.Column(db.String(30), index=True)
    event_type = db.Column(db.String(60), index=True)   # e.g. lead.created
    event_status = db.Column(db.String(20), default="ok", index=True)  # ok|failed|warn
    actor_type = db.Column(db.String(20), default="system")  # customer|admin|contractor|system
    actor_id = db.Column(db.String(60))
    previous_value = db.Column(db.String(500))
    new_value = db.Column(db.String(500))
    metadata_json = db.Column(db.Text)
    ip_hash = db.Column(db.String(30))
    user_agent = db.Column(db.String(250))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)


class SmsBudget(db.Model):
    """Daily verification counters — the application-level spend guard."""
    __tablename__ = "sms_budget"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, unique=True, index=True)
    attempted = db.Column(db.Integer, default=0)
    sent = db.Column(db.Integer, default=0)
    delivered = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    verified = db.Column(db.Integer, default=0)
    reused = db.Column(db.Integer, default=0)
    blocked = db.Column(db.Integer, default=0)
    cost_amount = db.Column(db.Numeric(10, 5), default=0)

    @staticmethod
    def today():
        from datetime import date
        row = SmsBudget.query.filter_by(day=date.today()).first()
        if not row:
            row = SmsBudget(day=date.today(), attempted=0, sent=0, delivered=0,
                            failed=0, verified=0, cost_amount=0)
            db.session.add(row)
            db.session.flush()
        return row




class PhoneVerificationAttempt(db.Model):
    """An SMS OTP phone-verification attempt (delivered via Bird).

    HaulChime generates the 6-digit code, stores only an HMAC digest of it,
    sends it through Bird, and validates the code the customer types back. We
    record permission-to-send (for rate limiting and cost control) and the
    wrong-guess counter that locks the attempt after too many bad codes.
    """
    __tablename__ = "phone_verification_attempts"
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.String(50), unique=True, index=True)
    quote_draft_id = db.Column(db.String(60), index=True)
    phone_e164 = db.Column(db.String(20))
    phone_hash = db.Column(db.String(64), index=True)
    session_hash = db.Column(db.String(64), index=True)
    ip_hash = db.Column(db.String(40), index=True)
    status = db.Column(db.String(24), default="approved_to_send", index=True)
    # approved_to_send | verified | expired | consumed | locked | failed
    send_request_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    last_send_requested_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True), index=True)
    verified_at = db.Column(db.DateTime(timezone=True))
    consumed_at = db.Column(db.DateTime(timezone=True))
    # --- SMS OTP (Bird) ---
    otp_digest = db.Column(db.String(64))          # HMAC(code, attempt_id, phone)
    attempt_count = db.Column(db.Integer, default=0)  # wrong-code guesses
    provider_message_id = db.Column(db.String(64))    # Bird message id
    provider_cost_amount = db.Column(db.Numeric(10, 5))
    failure_category = db.Column(db.String(40))
    risk_flags = db.Column(db.String(120))

    @staticmethod
    def _aware(dt):
        """SQLite returns naive datetimes; treat stored values as UTC."""
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    @property
    def is_expired(self):
        exp = self._aware(self.expires_at)
        return bool(exp and utcnow() >= exp)

    @property
    def is_consumed(self):
        return self.consumed_at is not None

    def resend_available_in(self, delay_seconds):
        last = self._aware(self.last_send_requested_at)
        if not last:
            return 0
        return max(0, int(delay_seconds - (utcnow() - last).total_seconds()))
