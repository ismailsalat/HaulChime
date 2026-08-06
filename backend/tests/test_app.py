import re
from werkzeug.security import generate_password_hash

from app import create_app
from models import db, Lead, LeadActivity, Partner


class TestConfig:
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BRAND_NAME = "HaulChime"
    TARGET_CITY = "Kent"
    TARGET_STATE = "WA"
    PUBLIC_PHONE = ""
    SITE_URL = "http://localhost:8080"
    CONSENT_TEXT = "I agree that {brand} may share this request."
    MAIL_BACKEND = "console"
    SMTP_HOST = ""
    SMTP_PORT = 587
    SMTP_USER = ""
    SMTP_PASSWORD = ""
    RESEND_API_KEY = ""
    MAIL_FROM = "test@example.com"
    ADMIN_NOTIFY_EMAIL = "owner@example.com"
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD_HASH = generate_password_hash("testpass")
    AUTO_ROUTE_LEADS = False
    PHONE_VERIFICATION_ENABLED = False
    REQUIRE_PHONE_VERIFICATION = False
    APP_ENV = "test"
    BIRD_API_KEY = ""
    BIRD_REGION = ""
    BIRD_OTP_TEMPLATE = ""
    BIRD_SMS_FROM = ""
    DEV_OTP_CODE = "123456"
    PHONE_VERIFICATION_HMAC_SECRET = "test-secret"
    PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS = 600
    PHONE_VERIFICATION_MAX_ATTEMPTS = 5
    PHONE_VERIFICATION_RESEND_DELAY_SECONDS = 60
    PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE = 2
    PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_HOUR = 3
    PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_DAY = 5
    PHONE_VERIFICATION_MAX_SENDS_PER_IP_HOUR = 8
    PHONE_VERIFICATION_MAX_SENDS_PER_IP_DAY = 20
    PHONE_VERIFICATION_MAX_UNIQUE_PHONES_PER_IP_HOUR = 4
    PHONE_VERIFICATION_MAX_SENDS_PER_SESSION_HOUR = 5
    PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT = 200
    PHONE_VERIFICATION_REUSE_DAYS = 30
    ALLOWED_ORIGINS = ["http://localhost:8080"]
    STORAGE_BACKEND = "local"
    UPLOAD_DIR = "/tmp/haulchime-test-uploads"
    MAX_CONTENT_LENGTH = 40 * 1024 * 1024
    MAX_PHOTO_MB = 8
    MAX_PHOTOS = 10
    ALLOWED_PHOTO_EXT = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png", "image/webp"}
    PHOTO_MAX_DIMENSION = 1920
    RATE_LIMIT_SUBMISSIONS = 100
    RATE_LIMIT_WINDOW_SECONDS = 3600
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False


def make_app():
    app = create_app(TestConfig)
    with app.app_context():
        db.drop_all(); db.create_all()
        db.session.add(Partner(name="Demo", service_zips="98030",
                               services_accepted="local_move,junk_removal,hauling,long_distance_move",
                               active=True, max_lead_price=70, credit_balance=100,
                               heavy_item_capable=True, commercial_capable=True))
        db.session.commit()
    return app


def valid_payload():
    return {
        "full_name": "Ismail Test", "phone": "2069440030", "email": "test@example.com",
        "service_type": "local_move", "pickup_address": "123 Main St", "pickup_city": "Kent",
        "zip_code": "98030", "destination_address": "456 Lake Ave",
        "destination_city": "Renton", "destination_zip": "98055",
        "property_type": "apartment", "job_size": "2br",
        "inventory": "Sectional, queen bed, desk, dining table and 25 boxes",
        "special_items": "Large sectional", "pickup_access": "one_flight",
        "destination_access": "ground_level", "parking_access": "close",
        "service_date": "2026-08-08", "urgency": "this_week",
        "description": "Need help moving furniture and boxes on Saturday morning.",
        "preferred_contact": "text", "contact_time": "anytime",
        "consent": "true", "company_website": "",
    }


def test_create_haul_lead_and_admin_pages():
    app = make_app(); client = app.test_client()
    response = client.post("/api/leads", data=valid_payload())
    assert response.status_code == 201
    body = response.get_json()
    assert body["reference"].startswith("HC-")
    assert set(body) == {"ok", "reference"}
    with app.app_context():
        lead = Lead.query.one()
        lead_price = float(lead.lead_price)
        assert lead_price in (40, 55, 70)
        assert lead.service_type == "local_move"
        assert lead.pickup_address == "123 Main St"
        assert lead.destination_zip == "98055"

    login = client.post("/admin/login", data={"username": "admin", "password": "testpass"})
    assert login.status_code == 302
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/leads").status_code == 200
    assert client.get("/admin/leads/1").status_code == 200

    with client.session_transaction() as sess:
        csrf = sess["csrf_token"]
    sold = client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "sent_to_partner", "qualification": "qualified",
        "partner_id": "1", "lead_charge": str(lead_price), "admin_notes": "sold",
    })
    assert sold.status_code == 302
    with app.app_context():
        partner = Partner.query.get(1)
        assert float(partner.credit_balance) == 100 - lead_price

    returned = client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "new", "qualification": "pending",
        "partner_id": "", "lead_charge": str(lead_price), "admin_notes": "returned",
    })
    assert returned.status_code == 302
    with app.app_context():
        partner = Partner.query.get(1)
        assert float(partner.credit_balance) == 100


def test_auto_route_debits_prepaid_credit():
    app = make_app()
    app.config["AUTO_ROUTE_LEADS"] = True
    client = app.test_client()
    response = client.post("/api/leads", data=valid_payload())
    assert response.status_code == 201
    assert set(response.get_json()) == {"ok", "reference"}
    with app.app_context():
        lead = Lead.query.one()
        partner = Partner.query.one()
        price = float(lead.lead_price)
        assert lead.status == "sent_to_partner"
        assert lead.partner_id == partner.id
        assert float(partner.credit_balance) == 100 - price


def test_optional_fields_can_be_left_blank():
    app = make_app(); client = app.test_client()
    payload = valid_payload()
    for key in ("email", "property_type", "special_items", "service_date", "description"):
        payload[key] = ""
    payload["pickup_access"] = "unknown"
    payload["destination_access"] = "unknown"
    payload["parking_access"] = "unknown"
    response = client.post("/api/leads", data=payload)
    assert response.status_code == 201
    with app.app_context():
        lead = Lead.query.one()
        assert lead.description == ""
        assert lead.property_type == "other"
        assert lead.email is None


def test_phone_must_be_verified_when_required():
    app = make_app()
    app.config.update(
        PHONE_VERIFICATION_ENABLED=True,
        REQUIRE_PHONE_VERIFICATION=True,
        APP_ENV="development",
        DEV_OTP_CODE="123456",
    )
    client = app.test_client()
    payload = valid_payload()
    payload["phone"] = "2065550142"
    payload["quote_draft_id"] = "qd_test_flow"
    payload["session_id"] = "sess_test_flow"

    blocked = client.post("/api/leads", data=payload)
    assert blocked.status_code == 400
    assert "phone_verification" in blocked.get_json()["errors"]

    started = client.post("/api/quotes/phone-verification/start", json={
        "phone": payload["phone"],
        "quote_draft_id": payload["quote_draft_id"],
        "session_id": payload["session_id"],
        "company_website": "",
    })
    assert started.status_code == 200
    attempt_id = started.get_json()["verification_attempt_id"]

    completed = client.post("/api/quotes/phone-verification/complete", json={
        "quote_draft_id": payload["quote_draft_id"],
        "verification_attempt_id": attempt_id,
        "session_id": payload["session_id"],
        "code": "123456",
    })
    assert completed.status_code == 200

    payload["verification_attempt_id"] = attempt_id
    created = client.post("/api/leads", data=payload)
    assert created.status_code == 201
    with app.app_context():
        assert Lead.query.one().phone_verified is True


def new_form_payload():
    """The shape the redesigned quote form actually posts: tapped choices and
    multi-select slugs instead of free text."""
    return {
        "full_name": "Dana Rivers", "phone": "2069440030",
        "service_type": "junk_removal", "job_type": "garage_basement_cleanout",
        "pickup_address": "123 Main St", "pickup_city": "Kent",
        "pickup_state": "WA", "zip_code": "98030", "pickup_unit": "Apt 4",
        "property_type": "house", "job_size": "half_truck",
        "item_categories": "furniture,mattresses,garage_storage",
        "special_item_types": "large_appliance",
        "special_items_note": "Old chest freezer in the basement",
        "access_issues": "stairs,limited_parking", "stairs_flights": "2",
        "timing": "2_3_days", "preferred_time": "morning",
        "preferred_contact": "either", "contact_time": "anytime",
        "consent": "true", "company_website": "",
    }


def test_new_questionnaire_shape_is_accepted():
    app = make_app(); client = app.test_client()
    response = client.post("/api/leads", data=new_form_payload())
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        lead = Lead.query.one()
        assert lead.job_type == "garage_basement_cleanout"
        assert lead.item_categories == "furniture,mattresses,garage_storage"
        # Timing buttons map onto the urgency the scoring engine speaks.
        assert lead.urgency == "48_hours"
        # Access checkboxes collapse into the legacy enum for scoring/admin.
        assert lead.pickup_access == "two_plus_flights"
        assert lead.parking_access == "difficult"
        # Inventory is written from the category checkboxes when the customer
        # types nothing, so the partner still gets a readable sentence.
        assert "furniture" in lead.inventory.lower()
        assert "freezer" in (lead.special_items or "").lower()


def test_move_allows_unknown_destination_street():
    app = make_app(); client = app.test_client()
    payload = new_form_payload()
    payload.update({"service_type": "local_move", "job_type": "apartment_move",
                    "job_size": "1br", "destination_known": "false",
                    "destination_city": "Renton", "destination_zip": "98055",
                    "item_categories": "boxes,furniture"})
    response = client.post("/api/leads", data=payload)
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        lead = Lead.query.one()
        assert lead.destination_known is False
        assert lead.destination_zip == "98055"
        assert lead.destination_address is None

    # A move that claims to know the destination still has to supply it.
    payload["destination_known"] = "true"
    payload["destination_address"] = ""
    assert client.post("/api/leads", data=payload).status_code == 400


def test_not_sure_answers_are_allowed_everywhere():
    app = make_app(); client = app.test_client()
    payload = new_form_payload()
    payload.update({"job_type": "not_sure", "job_size": "not_sure",
                    "property_type": "not_sure", "item_categories": "not_sure",
                    "special_item_types": "not_sure", "access_issues": "not_sure",
                    "timing": "flexible"})
    assert client.post("/api/leads", data=payload).status_code == 201


def test_address_lookup_degrades_without_smarty_keys():
    """No keys configured must never break the form — it just falls back to
    manual typing, and the endpoint says so with a 200."""
    app = make_app()
    app.config.update(SMARTY_AUTH_ID="", SMARTY_AUTH_TOKEN="",
                      ADDRESS_LOOKUP_LIMIT_PER_HOUR=300)
    client = app.test_client()
    response = client.get("/api/address/suggest?q=123+main")
    assert response.status_code == 200
    assert response.get_json()["available"] is False
    assert client.get("/api/config").get_json()["addressLookupEnabled"] is False


def test_cost_model_runs_but_never_reaches_the_customer():
    """The internal economics exist for the admin only. If a price for the JOB
    ever appears in the public response or the customer email, that is a bug
    with legal teeth — HaulChime does not quote work."""
    import json as _json
    app = make_app(); client = app.test_client()
    payload = new_form_payload()
    payload.update({"job_size": "full_truck",
                    "item_categories": "furniture,appliances,construction_debris",
                    "special_item_types": "piano",
                    "special_items_note": "Upright piano, about 500 lb"})
    response = client.post("/api/leads", data=payload)
    assert response.status_code == 201

    # The public response carries nothing but an acknowledgement.
    assert set(response.get_json()) == {"ok", "reference"}
    body = response.get_data(as_text=True).lower()
    for leak in ("price", "cost", "estimate", "quote", "$"):
        assert leak not in body

    with app.app_context():
        lead = Lead.query.one()
        economics = _json.loads(lead.cost_breakdown)
        # Junk removal hits the landfill, so disposal must be a real number.
        assert economics["involves_disposal"] is True
        assert economics["costs"]["disposal"] > 0
        assert economics["costs"]["fuel"] > 0
        assert economics["costs"]["labor"] > 0
        # A piano adds specialist equipment cost.
        # Consumables plus a hire, because this partner isn't known to own
        # piano gear — but nowhere near the old flat $250-per-piano charge.
        assert 0 < economics["costs"]["special_equipment"] <= 200
        # The job has to be worth more than it costs to run.
        assert economics["estimated_job_value"] > economics["total_cost"]
        assert economics["estimated_range_low"] < economics["estimated_job_value"]
        assert economics["internal_only"] is True
        assert float(lead.estimated_job_value) == economics["estimated_job_value"]


def test_cost_model_scales_with_the_size_of_the_job():
    """Sanity: a mattress pickup must not model the same as a 4-bed move."""
    import job_costing
    small = job_costing.calculate(service_type="junk_removal", job_size="single_item",
                                  item_categories="mattresses", distance_miles=6)
    big = job_costing.calculate(service_type="local_move", job_size="4br_plus",
                                item_categories="furniture,boxes,appliances",
                                access_issues="stairs", stairs_flights="3_plus",
                                special_item_types="piano",
                                special_items_note="Upright, ~500 lb", distance_miles=22)
    assert big["estimated_job_value"] > small["estimated_job_value"] * 4
    assert big["crew_size"] > small["crew_size"]
    assert big["estimated_weight_lbs"] > small["estimated_weight_lbs"]
    # A move doesn't visit the landfill; junk removal does.
    assert big["involves_disposal"] is False
    assert small["involves_disposal"] is True
    # Vague requests report low confidence and a wider band.
    vague = job_costing.calculate(service_type="junk_removal", job_size="not_sure",
                                  item_categories="not_sure", distance_miles=10)
    assert vague["confidence"] == "low"
    assert (vague["estimated_range_high"] - vague["estimated_range_low"]) \
        / vague["estimated_job_value"] > (
            (small["estimated_range_high"] - small["estimated_range_low"])
            / small["estimated_job_value"])


def _login(client):
    client.post("/admin/login", data={"username": "admin", "password": "testpass"})
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def test_assigning_outside_the_service_zip_needs_confirmation():
    """A partner who doesn't cover the ZIP is usually a misclick, so the save
    has to stop and ask rather than going through with a note afterwards."""
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    with app.app_context():
        Partner.query.get(1).service_zips = "99999"   # no longer covers 98030
        db.session.commit()
    csrf = _login(client)

    blocked = client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "sent_to_partner", "qualification": "qualified",
        "partner_id": "1", "lead_charge": "40", "admin_notes": "",
    })
    assert blocked.status_code == 400
    page = blocked.get_data(as_text=True)
    assert "outside their service area" in page
    assert "98030" in page and "99999" in page      # shows what they do cover
    with app.app_context():
        assert Lead.query.get(1).partner_id is None  # nothing was saved

    confirmed = client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "sent_to_partner", "qualification": "qualified",
        "partner_id": "1", "lead_charge": "40", "admin_notes": "",
        "confirm_zip_override": "yes",
    })
    assert confirmed.status_code == 302
    with app.app_context():
        assert Lead.query.get(1).partner_id == 1
        assert LeadActivity.query.filter_by(event_type="partner.zip_mismatch").count() == 2


def test_sending_a_lead_to_a_partner_by_email_and_sms():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    with app.app_context():
        partner = Partner.query.get(1)
        partner.notification_email = "crew@example.com"
        partner.phone = "2069440030"
        db.session.commit()
    csrf = _login(client)
    client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "new", "qualification": "pending",
        "partner_id": "1", "lead_charge": "40", "admin_notes": "",
    })

    sent = []
    import bird_client
    original = bird_client.send_notification
    bird_client.send_notification = lambda cfg, to, text: (sent.append((to, text)), ("msg_1", 0.01))[1]
    try:
        app.config["BIRD_API_KEY"] = "bk_us1_test"
        response = client.post("/admin/leads/1/send",
                               data={"csrf_token": csrf, "email": "on", "sms": "on"},
                               follow_redirects=True)
    finally:
        bird_client.send_notification = original
    assert response.status_code == 200

    assert len(sent) == 1
    to, text = sent[0]
    assert to == "+12069440030"
    assert "HC-" in text                     # carries the reference
    assert "/admin/leads/1" in text          # and a link to the full detail
    # A text sits unencrypted on a phone: no street address, and no pricing.
    assert "123 Main St" not in text
    assert "$" not in text

    with app.app_context():
        channels = {a.metadata_json for a in LeadActivity.query.filter_by(
            event_type="delivery.sent").all()}
        assert any("sms" in (m or "") for m in channels)
        assert any("email" in (m or "") for m in channels)


def test_sending_reports_which_channel_failed():
    """A partner with no phone should still get the email, and the admin
    should be told plainly that the text didn't go."""
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    with app.app_context():
        partner = Partner.query.get(1)
        partner.notification_email = "crew@example.com"
        partner.phone = ""
        db.session.commit()
    csrf = _login(client)
    client.post("/admin/leads/1", data={
        "csrf_token": csrf, "status": "new", "qualification": "pending",
        "partner_id": "1", "lead_charge": "40", "admin_notes": "",
    })
    response = client.post("/admin/leads/1/send",
                           data={"csrf_token": csrf, "email": "on", "sms": "on"},
                           follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "Sent to Demo by email" in page
    assert "No phone number on file" in page


def test_send_requires_a_partner_and_a_channel():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    csrf = _login(client)
    unassigned = client.post("/admin/leads/1/send",
                             data={"csrf_token": csrf, "email": "on"},
                             follow_redirects=True)
    assert "Assign a partner before sending" in unassigned.get_data(as_text=True)


# --------------------------------------------------------------------------
# Money must behave like money, not like binary floating point.
# --------------------------------------------------------------------------

def test_estimate_breakdown_adds_up_exactly():
    """If the parts don't sum to the total, the admin can't trust any of it."""
    import job_costing
    from decimal import Decimal
    cases = [
        dict(service_type="junk_removal", job_size="single_item",
             item_categories="mattresses", distance_miles=6),
        dict(service_type="local_move", job_size="3br", item_categories="boxes,furniture",
             special_item_types="piano", special_items_note="Upright, ~500 lb, ground floor",
             extra_services="packing", access_issues="stairs",
             stairs_flights="3_plus", distance_miles=18),
        dict(service_type="long_distance_move", job_size="4br_plus",
             item_categories="boxes", distance_miles=412.7),
        dict(service_type="hauling", job_type="dump_run", job_size="large_load",
             item_categories="construction_debris", distance_miles=31.4),
    ]
    for kwargs in cases:
        result = job_costing.calculate(**kwargs)
        parts = sum(Decimal(str(v)) for v in result["costs"].values())
        assert parts == Decimal(str(result["total_cost"])), kwargs
        # Two decimal places, always.
        for value in list(result["costs"].values()) + [result["estimated_job_value"]]:
            assert Decimal(str(value)) == Decimal(str(value)).quantize(Decimal("0.01"))
        assert result["display"]["value"].count(".") == 1
        assert len(result["display"]["value"].split(".")[1]) == 2


def test_estimate_has_no_impossible_values():
    """Negative money, NaN, or a low above the high would each be a serious
    bug in front of the person deciding what to charge."""
    import job_costing
    import math
    # Deliberately hostile inputs: empty, unknown, zero distance, huge distance.
    hostile = [
        dict(service_type="hauling", job_size="", distance_miles=0),
        dict(service_type="junk_removal", job_size="not_sure",
             item_categories="not_sure", special_item_types="not_sure",
             access_issues="not_sure", distance_miles=0),
        dict(service_type="local_move", job_size="4br_plus", distance_miles=9999),
        dict(service_type="junk_removal", job_size="single_item", distance_miles=-5),
    ]
    for kwargs in hostile:
        r = job_costing.calculate(**kwargs)
        if r.get("status") == "insufficient_information":
            # Refusing to price is a valid — and preferable — outcome here.
            assert r["missing"], kwargs
            assert r["confidence"] == "insufficient_information"
            continue
        numbers = list(r["costs"].values()) + [
            r["total_cost"], r["direct_cost"], r["estimated_job_value"],
            r["estimated_range_low"], r["estimated_range_high"]]
        for value in numbers:
            assert math.isfinite(value), (kwargs, value)
            assert value >= 0, (kwargs, value)
        assert r["estimated_range_low"] <= r["estimated_job_value"] <= r["estimated_range_high"]
        assert 0 <= r["target_margin_pct"] <= 60


def test_crew_size_and_mileage_are_each_applied_once():
    """Doubling either one silently inflates every estimate."""
    import job_costing
    base = dict(service_type="local_move", job_size="2br",
                item_categories="boxes,furniture", distance_miles=10)
    result = job_costing.calculate(**base)
    # paid_crew_hours = (handling + drive) x crew, counted a single time.
    expected = round(
        (result["handling_hours"] + result["drive_hours"]) * result["crew_size"], 2)
    assert abs(result["paid_crew_hours"] - expected) < 0.02
    # Doubling the distance must not more than double the mileage-driven costs.
    farther = job_costing.calculate(**{**base, "distance_miles": 20})
    assert farther["costs"]["fuel"] < result["costs"]["fuel"] * 2.5
    assert farther["total_miles"] == result["total_miles"] + 10


def test_estimate_is_not_exposed_to_the_public_api():
    """Re-asserted here because it is the single most damaging thing that
    could regress: HaulChime does not quote jobs."""
    app = make_app(); client = app.test_client()
    response = client.post("/api/leads", data=new_form_payload())
    assert response.status_code == 201
    body = response.get_data(as_text=True).lower()
    for leaked in ("price", "cost", "estimate", "$", "margin", "profit"):
        assert leaked not in body
    assert set(response.get_json()) == {"ok", "reference"}


# --------------------------------------------------------------------------
# Partner eligibility
# --------------------------------------------------------------------------

def _partner_with_schedule(app, **overrides):
    """A fully eligible partner: covers the ZIP, does the service, works
    Mon-Thu 10-18, has credit and headroom."""
    from models import Partner, PartnerAvailability, PartnerApplication
    from datetime import datetime, timezone
    with app.app_context():
        partner = Partner(
            name="Sound Hauling", service_zips="98030,98031",
            services_accepted="junk_removal,hauling,local_move", active=True,
            taking_leads=True, credit_balance=140, max_lead_price=70,
            daily_lead_limit=10, heavy_item_capable=True,
            commercial_capable=False, minimum_notice_hours=24,
            same_day_ok=False, billing_type="per_lead")
        for key, value in overrides.items():
            setattr(partner, key, value)
        db.session.add(partner)
        db.session.flush()
        for day in range(7):
            db.session.add(PartnerAvailability(
                partner_id=partner.id, day_of_week=day,
                available=day < 4, start_time="10:00", end_time="18:00"))
        db.session.add(PartnerApplication(
            business_name="Sound Hauling", phone="+12069440030",
            phone_verified=True, status="approved", partner_id=partner.id,
            approved_at=datetime.now(timezone.utc)))
        db.session.commit()
        return partner.id


def _lead_on(app, weekday_offset, preferred_time="afternoon", **overrides):
    """A lead whose service_date lands on a chosen weekday."""
    from datetime import date, timedelta
    with app.app_context():
        lead = Lead.query.first()
        target = date.today() + timedelta(days=3)
        while target.weekday() != weekday_offset:
            target += timedelta(days=1)
        lead.service_date = target.isoformat()
        lead.preferred_time = preferred_time
        lead.urgency = "this_week"
        for key, value in overrides.items():
            setattr(lead, key, value)
        db.session.commit()
        return lead.id


def test_partner_is_eligible_when_everything_matches():
    import partner_eligibility
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    _lead_on(app, 2)                     # a Wednesday, afternoon
    with app.app_context():
        from models import Partner
        result = partner_eligibility.evaluate(
            Partner.query.get(partner_id), Lead.query.first(), lead_price=35)
    assert result["status"] == "eligible", result["failures"] + result["unknowns"]
    assert result["failures"] == []


def test_missing_time_is_needs_review_not_eligible():
    """The whole point of the middle state: unknown is not the same as fine."""
    import partner_eligibility
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    with app.app_context():
        lead = Lead.query.first()
        lead.service_date = None
        lead.urgency = "flexible"
        lead.preferred_time = ""
        db.session.commit()
        from models import Partner
        result = partner_eligibility.evaluate(
            Partner.query.get(partner_id), Lead.query.first(), lead_price=35)
    assert result["status"] == "needs_review"
    assert result["failures"] == []
    assert any("day" in u.lower() or "date" in u.lower() for u in result["unknowns"])


def test_each_failing_condition_is_reported_by_name():
    import partner_eligibility
    from models import Partner
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())

    cases = {
        "zip": ({"service_zips": "99999"}, {}, "ZIP coverage"),
        "service": ({"services_accepted": "hauling"}, {}, "Service match"),
        "credit": ({"credit_balance": 5}, {}, "Credit balance"),
        "max_price": ({"max_lead_price": 10}, {}, "Maximum lead price"),
        "paused": ({"taking_leads": False}, {}, "Taking leads"),
        "inactive": ({"active": False}, {}, "Partner active"),
        "commercial": ({"commercial_capable": False}, {"property_type": "commercial"},
                       "Commercial capability"),
    }
    for name, (partner_kw, lead_kw, expected) in cases.items():
        app = make_app(); client = app.test_client()
        client.post("/api/leads", data=valid_payload())
        partner_id = _partner_with_schedule(app, **partner_kw)
        _lead_on(app, 2, **lead_kw)
        with app.app_context():
            result = partner_eligibility.evaluate(
                Partner.query.get(partner_id), Lead.query.first(), lead_price=35)
        assert result["status"] == "not_eligible", name
        assert any(expected in f for f in result["failures"]), (name, result["failures"])


def test_weekday_and_time_off_matching():
    """Wednesday 3pm is fine for a Mon-Thu 10-6 partner; Saturday noon is not."""
    import partner_eligibility
    from models import Partner, PartnerTimeOff
    from datetime import date, timedelta

    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    _lead_on(app, 5)                     # Saturday
    with app.app_context():
        result = partner_eligibility.evaluate(
            Partner.query.get(partner_id), Lead.query.first(), lead_price=35)
    assert result["status"] == "not_eligible"
    assert any("Saturday" in f for f in result["failures"])

    # Booked time off over the requested Wednesday.
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    lead_id = _lead_on(app, 2)
    with app.app_context():
        job_day = date.fromisoformat(Lead.query.get(lead_id).service_date)
        db.session.add(PartnerTimeOff(partner_id=partner_id,
                                      start_date=job_day - timedelta(days=1),
                                      end_date=job_day + timedelta(days=2),
                                      note="Vacation"))
        db.session.commit()
        result = partner_eligibility.evaluate(
            Partner.query.get(partner_id), Lead.query.get(lead_id), lead_price=35)
    assert result["status"] == "not_eligible"
    assert any("time off" in f.lower() for f in result["failures"])


def test_partners_sort_eligible_then_review_then_ineligible():
    import partner_eligibility
    from models import Partner, PartnerAvailability
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    good_id = _partner_with_schedule(app)
    _lead_on(app, 2)
    with app.app_context():
        bad = Partner(name="Away Movers", service_zips="99999",
                      services_accepted="junk_removal", active=True,
                      taking_leads=True, credit_balance=100, max_lead_price=70,
                      daily_lead_limit=10)
        unsure = Partner(name="Blank Slate", service_zips="98030",
                         services_accepted="local_move", active=True,
                         taking_leads=True, credit_balance=100,
                         max_lead_price=70, daily_lead_limit=10,
                         heavy_item_capable=True)
        db.session.add_all([bad, unsure])
        db.session.commit()
        # Only the three built here — make_app() also seeds a demo partner.
        subjects = Partner.query.filter(Partner.name.in_(
            ["Sound Hauling", "Blank Slate", "Away Movers"])).all()
        results = partner_eligibility.evaluate_all(
            subjects, Lead.query.first(), lead_price=35)
        order = [r["status"] for r in results]
    assert order == sorted(order, key=lambda s: partner_eligibility.SORT_ORDER[s])
    assert results[0]["partner_id"] == good_id
    assert [r["status"] for r in results] == ["eligible", "needs_review", "not_eligible"]
    assert results[1]["partner_name"] == "Blank Slate"
    assert results[-1]["partner_name"] == "Away Movers"


# --------------------------------------------------------------------------
# Partner portal
# --------------------------------------------------------------------------

def _approved_partner(app, name="Sound Hauling", phone="+12069440030"):
    """An approved partner with a login account, ready to sign in."""
    from models import Partner, PartnerAccount, PartnerApplication
    with app.app_context():
        partner = Partner(name=name, service_zips="98030", active=True,
                          taking_leads=True, services_accepted="local_move",
                          credit_balance=200, max_lead_price=100, daily_lead_limit=10)
        db.session.add(partner); db.session.flush()
        db.session.add(PartnerApplication(
            business_name=name, phone=phone, phone_verified=True,
            status="approved", partner_id=partner.id))
        account = PartnerAccount(partner_id=partner.id, phone=phone,
                                 phone_verified=True, active=True)
        db.session.add(account); db.session.commit()
        return partner.id, account.id


def _sign_in(client, account_id):
    """Put a partner session in place without going through SMS."""
    with client.session_transaction() as sess:
        sess["partner_account_id"] = account_id
        sess["partner_csrf_token"] = "test-partner-csrf"
    return "test-partner-csrf"


def _assign(app, lead_id, partner_id, **kw):
    from models import LeadAssignment
    with app.app_context():
        assignment = LeadAssignment(lead_id=lead_id, partner_id=partner_id,
                                    lead_price=35, **kw)
        db.session.add(assignment); db.session.commit()
        return assignment.id


def test_portal_requires_sign_in():
    app = make_app(); client = app.test_client()
    for path in ["/partner/", "/partner/leads", "/partner/availability",
                 "/partner/profile"]:
        response = client.get(path)
        assert response.status_code == 302
        assert "/partner/login" in response.headers["Location"]


def test_partner_cannot_see_another_partners_lead():
    """The whole security model in one test: guessing a reference must not
    reach another partner's customer."""
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    mine_id, my_account = _approved_partner(app, "Mine", "+12069440030")
    theirs_id, _ = _approved_partner(app, "Theirs", "+12069440031")
    with app.app_context():
        reference = Lead.query.first().reference
        lead_id = Lead.query.first().id
    _assign(app, lead_id, theirs_id)      # assigned to the OTHER partner

    _sign_in(client, my_account)
    assert client.get(f"/partner/leads/{reference}").status_code == 404
    # And it must not appear in any listing.
    assert reference not in client.get("/partner/leads").get_data(as_text=True)
    for action in ("accept", "decline"):
        response = client.post(f"/partner/leads/{reference}/{action}",
                               data={"csrf_token": "test-partner-csrf",
                                     "reason": "not_available"})
        assert response.status_code == 404


def test_customer_details_are_hidden_until_the_lead_is_accepted():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id, account_id = _approved_partner(app)
    with app.app_context():
        lead = Lead.query.first()
        reference, lead_id = lead.reference, lead.id
    _assign(app, lead_id, partner_id)
    csrf = _sign_in(client, account_id)

    before = client.get(f"/partner/leads/{reference}").get_data(as_text=True)
    for secret in ["Ismail", "2069440030", "123 Main St", "test@example.com"]:
        assert secret not in before, secret
    assert "unlock when you accept" in before
    # The job itself is visible, so they can decide.
    assert "98030" in before

    client.post(f"/partner/leads/{reference}/accept", data={"csrf_token": csrf})
    after = client.get(f"/partner/leads/{reference}").get_data(as_text=True)
    assert "Ismail" in after
    assert "123 Main St" in after
    assert "tel:" in after and "sms:" in after
    with app.app_context():
        from models import LeadAssignment
        a = LeadAssignment.query.first()
        assert a.status == "accepted"
        assert a.customer_details_revealed_at is not None


def test_decline_requires_a_reason_and_records_it():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id, account_id = _approved_partner(app)
    with app.app_context():
        lead = Lead.query.first(); reference, lead_id = lead.reference, lead.id
    _assign(app, lead_id, partner_id)
    csrf = _sign_in(client, account_id)

    client.post(f"/partner/leads/{reference}/decline",
                data={"csrf_token": csrf, "reason": "nonsense"})
    with app.app_context():
        from models import LeadAssignment
        assert LeadAssignment.query.first().status != "declined"

    client.post(f"/partner/leads/{reference}/decline",
                data={"csrf_token": csrf, "reason": "outside_service_area",
                      "note": "Too far north"})
    with app.app_context():
        from models import LeadAssignment, PartnerActivity
        a = LeadAssignment.query.first()
        assert a.status == "declined"
        assert a.decline_reason == "outside_service_area"
        assert PartnerActivity.query.filter_by(event_type="lead.declined").count() == 1


def test_status_updates_need_acceptance_first():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id, account_id = _approved_partner(app)
    with app.app_context():
        lead = Lead.query.first(); reference, lead_id = lead.reference, lead.id
    _assign(app, lead_id, partner_id)
    csrf = _sign_in(client, account_id)

    client.post(f"/partner/leads/{reference}/status",
                data={"csrf_token": csrf, "status": "job_booked"})
    with app.app_context():
        from models import LeadAssignment
        assert LeadAssignment.query.first().status != "job_booked"

    client.post(f"/partner/leads/{reference}/accept", data={"csrf_token": csrf})
    client.post(f"/partner/leads/{reference}/status",
                data={"csrf_token": csrf, "status": "job_booked"})
    with app.app_context():
        from models import LeadAssignment
        assert LeadAssignment.query.first().status == "job_booked"
    # "assigned" is not reachable backwards.
    assert client.post(f"/partner/leads/{reference}/status",
                       data={"csrf_token": csrf, "status": "assigned"}).status_code == 400


def test_unapproved_and_suspended_partners_lose_access():
    from models import PartnerApplication, PartnerAccount, Partner
    app = make_app(); client = app.test_client()
    partner_id, account_id = _approved_partner(app)
    _sign_in(client, account_id)
    assert client.get("/partner/").status_code == 200

    with app.app_context():                       # application put back in review
        PartnerApplication.query.filter_by(partner_id=partner_id).first().status = \
            "pending_review"
        db.session.commit()
    assert client.get("/partner/").status_code == 302

    _sign_in(client, account_id)
    with app.app_context():                       # partner deactivated by admin
        PartnerApplication.query.filter_by(partner_id=partner_id).first().status = "approved"
        Partner.query.get(partner_id).active = False
        db.session.commit()
    response = client.get("/partner/", follow_redirects=True)
    assert "currently inactive" in response.get_data(as_text=True)


def test_partner_forms_require_csrf():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id, account_id = _approved_partner(app)
    with app.app_context():
        lead = Lead.query.first(); reference, lead_id = lead.reference, lead.id
    _assign(app, lead_id, partner_id)
    _sign_in(client, account_id)
    assert client.post(f"/partner/leads/{reference}/accept", data={}).status_code == 400
    assert client.post("/partner/toggle-leads", data={}).status_code == 400


def test_partner_cannot_edit_commercial_fields_via_profile():
    """Credit, price caps and limits belong to the admin. Posting them must be
    ignored, not merely hidden in the template."""
    app = make_app(); client = app.test_client()
    partner_id, account_id = _approved_partner(app)
    csrf = _sign_in(client, account_id)
    client.post("/partner/profile", data={
        "csrf_token": csrf, "contact_person": "Sam",
        "service_zips": "98030,98031", "service_local_move": "on",
        "credit_balance": "999999", "max_lead_price": "1",
        "daily_lead_limit": "999", "active": "on", "notes": "hacked",
        "price_per_lead": "0",
    })
    with app.app_context():
        from models import Partner
        p = Partner.query.get(partner_id)
        assert p.contact_person == "Sam"          # allowed field changed
        assert float(p.credit_balance) == 200     # protected fields did not
        assert float(p.max_lead_price) == 100
        assert p.daily_lead_limit == 10
        assert p.notes != "hacked"


def test_application_cannot_be_submitted_without_a_verified_phone():
    app = make_app(); client = app.test_client()
    client.get("/partner/apply")
    with client.session_transaction() as sess:
        sess["partner_csrf_token"] = "apply-csrf"
    response = client.post("/partner/apply", data={
        "csrf_token": "apply-csrf", "business_name": "New Movers",
        "phone": "2069440030", "zip_codes": "98030",
        "service_local_move": "on",
    }, follow_redirects=True)
    assert "Verify your mobile number" in response.get_data(as_text=True)
    with app.app_context():
        from models import PartnerApplication
        assert PartnerApplication.query.count() == 0


# --------------------------------------------------------------------------
# Admin: applications, assignment, overrides, deletion
# --------------------------------------------------------------------------

def test_approving_an_application_creates_a_working_partner():
    from models import Partner, PartnerAccount, PartnerApplication, PartnerAvailability
    import json as _json
    app = make_app(); client = app.test_client()
    with app.app_context():
        db.session.add(PartnerApplication(
            business_name="Cascade Movers", contact_person="Dana",
            email="dana@example.com", phone="+12069440031", phone_verified=True,
            status="pending_review", zip_codes="98030,98031",
            services_accepted="local_move,junk_removal", crew_size=3,
            truck_capacity="16-foot box truck", heavy_item_capable=True,
            minimum_notice_hours=24,
            availability_json=_json.dumps([
                {"day": d, "available": d < 4, "start": "10:00", "end": "18:00"}
                for d in range(7)])))
        db.session.commit()
    csrf = _login(client)
    response = client.post("/admin/partner-applications/1", data={
        "csrf_token": csrf, "action": "approve", "credit_balance": "140",
        "max_lead_price": "70", "price_per_lead": "40", "daily_lead_limit": "10",
        "active": "on", "internal_notes": "Met at the Kent expo",
    })
    assert response.status_code == 302

    with app.app_context():
        partner = Partner.query.filter_by(name="Cascade Movers").one()
        assert partner.service_zips == "98030,98031"
        assert partner.heavy_item_capable is True
        assert float(partner.credit_balance) == 140      # admin-set terms stick
        assert partner.notes == "Met at the Kent expo"
        # The weekly grid carried across into real rows.
        days = {r.day_of_week: r.available for r in partner.availability}
        assert days[2] is True and days[5] is False
        # A login account now exists and is active.
        account = PartnerAccount.query.filter_by(phone="+12069440031").one()
        assert account.active and account.partner_id == partner.id
        # The application survives as the historical record.
        application = PartnerApplication.query.get(1)
        assert application.status == "approved"
        assert application.partner_id == partner.id


def test_suspending_an_application_cuts_off_portal_access():
    from models import Partner, PartnerAccount, PartnerApplication
    app = make_app(); client = app.test_client()
    partner_id, account_id = _approved_partner(app, "Sound Hauling", "+12069440030")
    with app.app_context():
        application_id = PartnerApplication.query.filter_by(
            partner_id=partner_id).one().id
    csrf = _login(client)
    client.post(f"/admin/partner-applications/{application_id}",
                data={"csrf_token": csrf, "action": "suspend",
                      "admin_message": "Unresolved complaints"})
    with app.app_context():
        assert PartnerAccount.query.get(account_id).active is False
        assert Partner.query.get(partner_id).active is False

    partner_client = app.test_client()
    _sign_in(partner_client, account_id)
    assert partner_client.get("/partner/").status_code == 302


def test_assigning_an_ineligible_partner_is_blocked_until_confirmed():
    """The core of section 9: never a silent assignment, never a hard block."""
    from models import LeadAssignment
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app, service_zips="99999")   # wrong area
    _lead_on(app, 2)
    csrf = _login(client)

    blocked = client.post("/admin/leads/1/assign", data={
        "csrf_token": csrf, "partner_id": partner_id, "lead_price": "35"})
    assert blocked.status_code == 400
    page = blocked.get_data(as_text=True)
    assert "not in service area" in page          # names the actual problem
    assert "Assign anyway" in page
    with app.app_context():
        assert LeadAssignment.query.count() == 0  # nothing happened

    confirmed = client.post("/admin/leads/1/assign", data={
        "csrf_token": csrf, "partner_id": partner_id, "lead_price": "35",
        "confirm_override": "yes"})
    assert confirmed.status_code == 302
    with app.app_context():
        a = LeadAssignment.query.one()
        assert a.assigned_with_override is True
        assert "service area" in a.override_reasons
        assert LeadActivity.query.filter_by(event_type="assignment.blocked").count() == 1
        assert LeadActivity.query.filter_by(event_type="assignment.created").count() == 1


def test_eligible_partner_assigns_without_an_override():
    from models import LeadAssignment, PartnerNotification
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    _lead_on(app, 2)
    csrf = _login(client)
    response = client.post("/admin/leads/1/assign", data={
        "csrf_token": csrf, "partner_id": partner_id, "lead_price": "35"})
    assert response.status_code == 302
    with app.app_context():
        a = LeadAssignment.query.one()
        assert a.assigned_with_override is False
        assert a.status == "assigned"
        # The notification must not carry customer details.
        note = PartnerNotification.query.one()
        assert "Ismail" not in note.message and "2069440030" not in note.message
        assert Lead.query.get(1).partner_id == partner_id


def test_assign_panel_sorts_and_labels_every_partner():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    _partner_with_schedule(app)
    _lead_on(app, 2)
    csrf = _login(client)
    page = client.get("/admin/leads/1/assign").get_data(as_text=True)
    assert "Eligible" in page
    # Readable text, not colour alone.
    assert "ZIP coverage" in page and "Credit balance" in page
    assert "Daily lead limit" in page and "Service match" in page


def test_deleting_a_lead_requires_typing_delete():
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    csrf = _login(client)

    for wrong in ("", "delete please", "yes"):
        client.post("/admin/leads/1/delete",
                    data={"csrf_token": csrf, "confirm_text": wrong})
        with app.app_context():
            assert Lead.query.count() == 1, wrong

    client.post("/admin/leads/1/delete",
                data={"csrf_token": csrf, "confirm_text": "DELETE"})
    with app.app_context():
        assert Lead.query.count() == 0


def test_removing_an_assignment_is_recorded():
    from models import LeadAssignment
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    _lead_on(app, 2)
    csrf = _login(client)
    client.post("/admin/leads/1/assign",
                data={"csrf_token": csrf, "partner_id": partner_id, "lead_price": "35"})
    with app.app_context():
        assignment_id = LeadAssignment.query.one().id
    client.post("/admin/leads/1/unassign",
                data={"csrf_token": csrf, "assignment_id": assignment_id})
    with app.app_context():
        assert LeadAssignment.query.count() == 0
        assert Lead.query.get(1).partner_id is None
        assert LeadActivity.query.filter_by(event_type="assignment.removed").count() == 1


def test_partner_photo_access_is_scoped_to_their_own_lead():
    """A partner with one valid assignment must not be able to read every
    photo in storage by swapping the key in the URL."""
    import io
    from PIL import Image
    app = make_app(); client = app.test_client()

    def payload_with_photo():
        data = dict(valid_payload())
        buf = io.BytesIO()
        Image.new("RGB", (60, 60), "red").save(buf, format="JPEG")
        buf.seek(0)
        data["photos"] = (buf, "garage.jpg")
        return data

    client.post("/api/leads", data=payload_with_photo(),
                content_type="multipart/form-data")
    client2 = app.test_client()
    other = dict(payload_with_photo())
    other["phone"] = "2065550142"
    client2.post("/api/leads", data=other, content_type="multipart/form-data")

    partner_id, account_id = _approved_partner(app)
    with app.app_context():
        leads = Lead.query.order_by(Lead.id).all()
        mine, theirs = leads[0], leads[1]
        my_reference = mine.reference
        my_key = mine.photos[0]
        their_key = theirs.photos[0]
        my_lead_id = mine.id
    _assign(app, my_lead_id, partner_id)
    _sign_in(client, account_id)

    assert client.get(f"/partner/leads/{my_reference}/photos/{my_key}").status_code == 200
    # Someone else's photo key on my own valid lead URL: must not work.
    assert client.get(f"/partner/leads/{my_reference}/photos/{their_key}").status_code == 404
    # Directory traversal must not work either.
    assert client.get(
        f"/partner/leads/{my_reference}/photos/..%2f..%2fmodels.py").status_code in (404, 400)


def test_applicant_is_told_the_outcome():
    """A company that applies and never hears anything is a partner lost."""
    from models import PartnerApplication
    app = make_app(); client = app.test_client()
    with app.app_context():
        db.session.add(PartnerApplication(
            business_name="Cascade Movers", email="dana@example.com",
            phone="+12069440031", phone_verified=True, status="pending_review",
            zip_codes="98030", services_accepted="local_move",
            availability_json="[]"))
        db.session.commit()

    sent = []
    import mailer
    original = mailer.send_email
    mailer.send_email = lambda cfg, to, subject, body: sent.append((to, subject, body))
    try:
        csrf = _login(client)
        client.post("/admin/partner-applications/1", data={
            "csrf_token": csrf, "action": "reject",
            "admin_message": "We already cover that area."})
    finally:
        mailer.send_email = original

    assert sent, "the applicant was never told"
    to, subject, body = sent[-1]
    assert to == "dana@example.com"
    assert "not approved" in subject.lower()
    assert "already cover that area" in body     # the admin's message reaches them


# --------------------------------------------------------------------------
# End to end: a real company applies, gets approved, and signs in.
# Walks the actual HTTP routes rather than poking the database, because that
# is the only way to catch a wiring bug between them.
# --------------------------------------------------------------------------

def test_apply_approve_login_end_to_end():
    from models import Partner, PartnerAccount, PartnerApplication
    app = make_app()
    app.config["DEV_OTP_CODE"] = "123456"
    app.config["PHONE_VERIFICATION_ENABLED"] = True
    applicant = app.test_client()

    # --- 1. The company verifies its number through the public API ---------
    start = applicant.post("/api/quotes/phone-verification/start", json={
        "phone": "2065550131", "quote_draft_id": "partner_apply",
        "session_id": "partner_apply", "company_website": ""})
    assert start.status_code == 200, start.get_json()
    attempt_id = start.get_json()["verification_attempt_id"]

    done = applicant.post("/api/quotes/phone-verification/complete", json={
        "quote_draft_id": "partner_apply", "verification_attempt_id": attempt_id,
        "session_id": "partner_apply", "code": "123456"})
    assert done.status_code == 200, done.get_json()

    # --- 2. The application form is submitted ------------------------------
    applicant.get("/partner/apply")
    with applicant.session_transaction() as sess:
        csrf = sess["partner_csrf_token"]
    submitted = applicant.post("/partner/apply", data={
        "csrf_token": csrf, "verification_attempt_id": attempt_id,
        "business_name": "Cascade Movers", "contact_person": "Dana",
        "email": "dana@example.com", "phone": "2065550131",
        "zip_codes": "98030, 98031", "service_local_move": "on",
        "service_junk_removal": "on", "crew_size": "3",
        "truck_capacity": "16-foot box truck", "heavy_item_capable": "on",
        "minimum_notice_hours": "24",
        "available_0": "on", "start_0": "10:00", "end_0": "18:00",
        "available_2": "on", "start_2": "10:00", "end_2": "18:00",
    }, follow_redirects=True)
    assert submitted.status_code == 200
    assert "being reviewed" in submitted.get_data(as_text=True)

    with app.app_context():
        application = PartnerApplication.query.one()
        assert application.status == "pending_review"
        assert application.phone_verified is True
        assert application.phone == "+12065550131"
        assert application.zip_codes == "98030,98031"
        assert "local_move" in application.services_accepted

    # An unapproved applicant has no portal access.
    assert applicant.get("/partner/", follow_redirects=False).status_code == 302

    # --- 3. The admin approves --------------------------------------------
    admin = app.test_client()
    admin_csrf = _login(admin)
    approved = admin.post("/admin/partner-applications/1", data={
        "csrf_token": admin_csrf, "action": "approve", "credit_balance": "140",
        "max_lead_price": "70", "price_per_lead": "40",
        "daily_lead_limit": "10", "active": "on"})
    assert approved.status_code == 302

    with app.app_context():
        partner = Partner.query.filter_by(name="Cascade Movers").one()
        assert partner.active is True
        assert PartnerAccount.query.filter_by(phone="+12065550131").one().active

    # --- 4. The partner signs in with an SMS code -------------------------
    portal = app.test_client()
    portal.get("/partner/login")
    with portal.session_transaction() as sess:
        login_csrf = sess["partner_csrf_token"]
    step1 = portal.post("/partner/login", data={
        "csrf_token": login_csrf, "step": "phone", "phone": "2065550131"})
    assert step1.status_code == 200
    assert "code" in step1.get_data(as_text=True).lower()

    step2 = portal.post("/partner/login", data={
        "csrf_token": login_csrf, "step": "code", "code": "123456"},
        follow_redirects=True)
    assert step2.status_code == 200
    page = step2.get_data(as_text=True)
    assert "Cascade Movers" in page, "sign-in did not land on the dashboard"
    assert "Taking leads" in page

    # And the portal is genuinely usable, not just reachable.
    for path in ["/partner/leads", "/partner/availability", "/partner/profile"]:
        assert portal.get(path).status_code == 200, path

    with app.app_context():
        assert PartnerAccount.query.one().last_login_at is not None


def test_login_is_refused_before_approval():
    """The same flow, stopped at the gate."""
    from models import PartnerApplication, PartnerAccount, Partner
    app = make_app()
    app.config["DEV_OTP_CODE"] = "123456"
    partner_id, account_id = _approved_partner(app, "Pending Co", "+12065550132")
    with app.app_context():
        PartnerApplication.query.filter_by(partner_id=partner_id).one().status = \
            "pending_review"
        db.session.commit()

    portal = app.test_client()
    portal.get("/partner/login")
    with portal.session_transaction() as sess:
        csrf = sess["partner_csrf_token"]
    portal.post("/partner/login", data={
        "csrf_token": csrf, "step": "phone", "phone": "2065550132"})
    response = portal.post("/partner/login", data={
        "csrf_token": csrf, "step": "code", "code": "123456"},
        follow_redirects=True)
    body = response.get_data(as_text=True)
    assert "under review" in body
    with portal.session_transaction() as sess:
        assert "partner_account_id" not in sess


def test_every_admin_page_renders():
    """A stylesheet rewrite is exactly the sort of change that silently breaks
    one template, so walk all of them."""
    app = make_app(); client = app.test_client()
    client.post("/api/leads", data=valid_payload())
    partner_id = _partner_with_schedule(app)
    _lead_on(app, 2)
    with app.app_context():
        from models import PartnerApplication
        application_id = PartnerApplication.query.first().id
    csrf = _login(client)
    client.post("/admin/leads/1/assign",
                data={"csrf_token": csrf, "partner_id": partner_id, "lead_price": "35"})

    pages = ["/admin/", "/admin/leads", "/admin/leads/1", "/admin/partners",
             "/admin/partner-applications", f"/admin/partner-applications/{application_id}",
             "/admin/leads/1/assign", "/admin/logs", "/admin/settings"]
    for path in pages:
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
        body = response.get_data(as_text=True)
        # A Jinja mistake often renders as a literal brace rather than a 500.
        # `${{...}}` is excluded: the settings page documents Railway's own
        # variable-reference syntax, which legitimately contains braces.
        leftovers = re.findall(r'(?<!\$)\{\{\s*\w', body)
        assert not leftovers, f"unrendered template expression on {path}"
        assert "nav-item active" in body or "nav-item" in body, path


def test_admin_buttons_use_the_shared_styles():
    """Bare <button> elements are what made the admin look unfinished."""
    import pathlib
    bad = []
    for template in pathlib.Path("templates/admin").glob("*.html"):
        text = template.read_text()
        for match in re.finditer(r'<button(?![^>]*class=)[^>]*>', text):
            bad.append(f"{template.name}: {match.group(0)[:60]}")
    assert not bad, "unstyled buttons:\n" + "\n".join(bad)


def test_estimate_refuses_to_price_a_piano_with_no_destination():
    """The exact case from the admin screenshot: a heavy-item move with no
    destination was showing a confident $939 built on an invented 12-mile
    trip and a flat $250 equipment charge."""
    import job_costing
    result = job_costing.calculate(
        service_type="local_move", job_type="single_heavy_item",
        job_size="single_item", item_categories="heavy_specialty",
        special_item_types="piano", special_items_note="",
        access_issues="stairs", stairs_flights="2",
        distance_basis="no_destination", destination_known=False)

    assert result["status"] == "insufficient_information"
    assert "estimated_job_value" not in result
    assert "costs" not in result
    assert result["confidence"] == "insufficient_information"
    joined = " ".join(result["missing"]).lower()
    assert "destination" in joined
    assert "piano" in joined
    # It still tells the admin what crew the job needs.
    assert result["recommended_crew"] >= 3


def test_estimate_returns_once_the_gaps_are_filled():
    import job_costing
    result = job_costing.calculate(
        service_type="local_move", job_type="single_heavy_item",
        job_size="single_item", item_categories="heavy_specialty",
        special_item_types="piano",
        special_items_note="Upright, roughly 500 lb, ground floor both ends",
        access_issues="none", distance_miles=14, distance_basis="coordinates")
    assert result["estimated_job_value"] > 0
    assert result["crew_size"] >= 3


def test_crew_recommendation_and_override_drive_the_cost():
    import job_costing
    base = dict(service_type="local_move", job_size="2br",
                item_categories="boxes,furniture", distance_miles=12,
                distance_basis="coordinates")
    recommended = job_costing.calculate(**base)
    bigger = job_costing.calculate(**base, crew_override=4)
    smaller = job_costing.calculate(**base, crew_override=2)

    assert bigger["crew_size"] == 4 and smaller["crew_size"] == 2
    # Crew flows through paid hours, labour and therefore the price.
    assert bigger["paid_crew_hours"] > recommended["paid_crew_hours"]
    assert bigger["costs"]["labor"] > smaller["costs"]["labor"]
    assert bigger["estimated_job_value"] > smaller["estimated_job_value"]

    # A grand-scale job recommends more people, with the reasoning shown.
    crew, reasons = job_costing.recommend_crew(
        service_type="local_move", job_size="single_item",
        item_categories="heavy_specialty", special_item_types="piano",
        access_issues="stairs", stairs_flights="3_plus")
    assert crew == 4
    assert any("piano" in r for r in reasons)
    assert any("stairs" in r for r in reasons)


def test_partner_owning_the_gear_is_not_charged_to_hire_it():
    """A flat per-piano equipment charge inflated every heavy-item estimate."""
    import job_costing

    class FakePartner:
        heavy_item_capable = True
        equipment_owned = "piano,safe"

    common = dict(service_type="local_move", job_size="single_item",
                  item_categories="heavy_specialty", special_item_types="piano",
                  special_items_note="Upright, 500 lb", distance_miles=14,
                  distance_basis="coordinates")
    hired = job_costing.calculate(**common)
    owned = job_costing.calculate(**common, partner=FakePartner())

    assert owned["costs"]["special_equipment"] < hired["costs"]["special_equipment"]
    assert owned["costs"]["special_equipment"] > 0        # consumables remain
    assert hired["costs"]["special_equipment"] < 200      # not the old flat $250
