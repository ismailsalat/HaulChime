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
                    "special_item_types": "piano"})
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
        assert economics["costs"]["special_equipment"] >= 250
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
                                special_item_types="piano", distance_miles=22)
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
