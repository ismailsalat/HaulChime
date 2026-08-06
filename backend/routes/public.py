"""Public HaulChime REST API: configuration, lead intake and private uploads."""
import json
import re
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, current_app, g, jsonify, request, send_from_directory

import logger
from logger import audit
from models import Lead, Partner, db, make_reference, utcnow
from mailer import send_templated
from security import rate_limited, honeypot_triggered
import sms_verification as fv
import phone as phone_util
import scoring
import labels
import job_costing
from storage import StorageError, get_storage

bp = Blueprint("public", __name__, url_prefix="/api")

SERVICE_TYPES = {"junk_removal", "hauling", "local_move", "long_distance_move"}
JOB_SIZES = {
    # shared / junk-removal load sizes
    "single_item", "few_items", "quarter_truck", "half_truck", "full_truck",
    "multi_truck", "commercial",
    # moving sizes
    "studio", "1br", "2br", "3br", "3br_plus", "4br_plus", "office", "labor_only",
    # hauling load sizes
    "small_load", "medium_load", "large_load", "multiple_loads",
    # the customer genuinely may not know
    "not_sure",
}
# "What best describes the job?" — the sub-type inside the chosen service.
JOB_TYPES = {
    # moving
    "full_home_move", "apartment_move", "few_items_move", "single_heavy_item",
    "office_move", "load_unload_only",
    # junk removal
    "one_item", "a_few_items", "room_cleanout", "garage_basement_cleanout",
    "full_property_cleanout", "yard_construction_debris",
    # hauling
    "pickup_delivery", "dump_run", "furniture_appliance", "material_transport",
    "equipment_hauling", "other",
    "not_sure",
}
URGENCIES = {"today", "48_hours", "this_week", "flexible"}
# Friendly timing buttons -> the urgency values the scoring engine understands.
TIMING_TO_URGENCY = {
    "asap": "today",
    "2_3_days": "48_hours",
    "one_week": "this_week",
    "specific_date": "this_week",
    "flexible": "flexible",
}
PROPERTY_TYPES = {
    "house", "apartment", "townhouse", "commercial", "office", "storage",
    "storage_unit", "construction_site", "other", "not_sure",
}
ACCESS = {"unknown", "ground_level", "one_flight", "two_plus_flights", "elevator", "long_carry"}
PARKING = {"unknown", "close", "moderate", "difficult"}
# Multi-select vocabularies. Anything not listed is dropped rather than
# rejected, so an older cached form can never hard-fail a real customer.
ITEM_CATEGORIES = {
    "boxes", "furniture", "appliances", "mattresses", "electronics",
    "office_equipment", "heavy_specialty", "yard_waste", "construction_debris",
    "garage_storage", "building_materials", "equipment", "household",
    "other", "not_sure",
}
EXTRA_SERVICES = {
    "packing", "disassembly", "reassembly", "loading_only", "unloading_only",
    "blankets_protection", "none", "not_sure",
}
SPECIAL_ITEMS = {
    "piano", "safe", "pool_table", "large_appliance", "oversized_furniture",
    "heavy_equipment", "hazardous", "none", "not_sure",
}
ACCESS_ISSUES = {
    "stairs", "elevator", "long_walk", "narrow", "limited_parking",
    "gate_security", "none", "not_sure",
}
FLIGHTS = {"1", "2", "3_plus", "not_sure"}
CONTACTS = {"phone", "text", "email", "either"}
CONTACT_TIMES = {"morning", "afternoon", "evening", "anytime"}
PREFERRED_TIMES = {"morning", "afternoon", "evening", "no_preference"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZIP_RE = re.compile(r"^\d{5}$")
NAME_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")
SPAMMY = re.compile(r"https?://|<a\s|\bviagra\b|\bcasino\b|\bcrypto\b|\bseo\b", re.I)


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


@bp.route("/leads", methods=["OPTIONS"])
@bp.route("/config", methods=["OPTIONS"])
def preflight():
    return ("", 204)


@bp.get("/config")
def config():
    cfg = current_app.config
    return jsonify(
        brand=cfg["BRAND_NAME"],
        consentText=cfg["CONSENT_TEXT"].format(brand=cfg["BRAND_NAME"]),
        phoneVerificationEnabled=cfg["PHONE_VERIFICATION_ENABLED"],
        phoneVerificationRequired=cfg["REQUIRE_PHONE_VERIFICATION"],
        # The form degrades to plain typing when address lookup is off.
        addressLookupEnabled=bool(cfg.get("SMARTY_AUTH_ID") and cfg.get("SMARTY_AUTH_TOKEN")),
        maxPhotos=cfg["MAX_PHOTOS"],
        maxPhotoMb=cfg["MAX_PHOTO_MB"],
        resendDelaySeconds=cfg["PHONE_VERIFICATION_RESEND_DELAY_SECONDS"],
    )


def _short(value, size):
    value = (value or "").strip()
    return value[:size] or None


def _is_move(service_type):
    return service_type in {"local_move", "long_distance_move"}


def _slugs(form, key, allowed):
    """Read a multi-select answer.

    Accepts either repeated fields (checkbox style) or one comma-separated
    value, and silently drops anything outside the allowed vocabulary — an
    unknown slug from a stale cached page should never fail a submission.
    """
    raw = form.getlist(key) if hasattr(form, "getlist") else [form.get(key) or ""]
    values = []
    for chunk in raw:
        for piece in (chunk or "").split(","):
            piece = piece.strip()
            if piece and piece in allowed and piece not in values:
                values.append(piece)
    return values


def _access_from_issues(issues, flights):
    """Collapse the customer's access checkboxes into the single enum the
    scoring engine and the admin UI already speak."""
    if not issues:
        return "unknown"
    if "stairs" in issues:
        if flights == "1":
            return "one_flight"
        if flights in ("2", "3_plus"):
            return "two_plus_flights"
        return "one_flight"
    if "elevator" in issues:
        return "elevator"
    if "long_walk" in issues:
        return "long_carry"
    if "none" in issues:
        return "ground_level"
    return "unknown"


def _parking_from_issues(issues):
    if not issues:
        return "unknown"
    if "limited_parking" in issues:
        return "difficult"
    if "gate_security" in issues or "narrow" in issues:
        return "moderate"
    if "none" in issues:
        return "close"
    return "unknown"


# Human-readable text built from the category checkboxes, so partners and the
# scoring engine still get a sentence even when the customer typed nothing.
CATEGORY_TEXT = {
    "boxes": "boxes", "furniture": "furniture", "appliances": "appliances",
    "mattresses": "mattresses", "electronics": "electronics",
    "office_equipment": "office equipment", "heavy_specialty": "heavy or specialty items",
    "yard_waste": "yard waste", "construction_debris": "construction debris",
    "garage_storage": "garage or storage items", "building_materials": "building materials",
    "equipment": "equipment", "household": "boxes or household items",
    "other": "other items", "not_sure": "items to be confirmed",
}
SPECIAL_TEXT = {
    "piano": "piano", "safe": "safe", "pool_table": "pool table",
    "large_appliance": "large appliance", "oversized_furniture": "oversized furniture",
    "heavy_equipment": "heavy equipment", "hazardous": "chemicals or hazardous material",
    "not_sure": "possible heavy item (customer unsure)",
}


def _sentence(slugs, mapping):
    words = [mapping.get(s, s.replace("_", " ")) for s in slugs if s not in ("none",)]
    if not words:
        return ""
    if len(words) == 1:
        return words[0].capitalize()
    return (", ".join(words[:-1]) + " and " + words[-1]).capitalize()


def _verify_pickup_address(street, unit, city, state, zip_code):
    """Best-effort Smarty check. Never blocks a submission — a customer with a
    brand-new address must still be able to ask for a quote."""
    import smarty_client
    if not smarty_client.is_configured(current_app.config) or not street:
        return None
    try:
        return smarty_client.verify(current_app.config, street=street,
                                    secondary=unit or "", city=city or "",
                                    state=state or "", zipcode=zip_code or "")
    except Exception:
        logger.warn("intake.address_verify_unavailable")
        return None


@bp.post("/leads")
def create_lead():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if rate_limited(ip, current_app.config["RATE_LIMIT_SUBMISSIONS"],
                    current_app.config["RATE_LIMIT_WINDOW_SECONDS"]):
        logger.warn("intake.rate_limited", ip_hash=logger.hash_ip(ip))
        return jsonify(error="Too many submissions. Please try again later.", request_id=g.request_id), 429

    form = request.form
    submission_id = logger.new_id("sub")
    if honeypot_triggered(form):
        logger.warn("intake.honeypot_triggered", submission_id=submission_id,
                    ip_hash=logger.hash_ip(ip))
        return jsonify(ok=True, reference="HC-00000000-0000"), 201

    errors = {}
    full_name = (form.get("full_name") or "").strip()
    if len(full_name) < 2 or not NAME_RE.search(full_name):
        errors["full_name"] = "Please enter your full name."
    parts = full_name.split(None, 1)
    first = parts[0][:80] if parts else ""
    last = parts[1][:80] if len(parts) > 1 else ""

    phone_raw = (form.get("phone") or "").strip()
    phone_result = phone_util.validate_us_mobile(phone_raw)
    phone_ok = phone_result.ok
    phone_digits = phone_result.e164[-10:] if phone_ok else None
    if not phone_ok:
        errors["phone"] = phone_util.USER_ERROR

    attempt = None
    verification_method = None
    attempt_id = (form.get("verification_attempt_id") or "").strip()
    quote_draft_id = (form.get("quote_draft_id") or "").strip()
    if phone_ok and attempt_id:
        try:
            attempt = fv.attempt_for_quote(quote_draft_id, attempt_id, phone_result.e164)
        except Exception:
            logger.warn("intake.verification_lookup_failed")
    if attempt:
        verification_method = "sms_otp"
    if current_app.config["REQUIRE_PHONE_VERIFICATION"] and not attempt:
        errors["phone_verification"] = "Please verify your phone number with the code we texted you."

    email = (form.get("email") or "").strip()
    email_ok = bool(EMAIL_RE.match(email)) if email else False
    if email and not email_ok:
        errors["email"] = "Enter a valid email address or leave it blank."

    service_type = (form.get("service_type") or "").strip()
    job_type = (form.get("job_type") or "").strip()
    job_size = (form.get("job_size") or "").strip()
    property_type = (form.get("property_type") or "other").strip()
    zip_code = (form.get("zip_code") or "").strip()
    destination_zip = (form.get("destination_zip") or "").strip()
    pickup_address = (form.get("pickup_address") or "").strip()
    pickup_unit = (form.get("pickup_unit") or "").strip()
    pickup_state = (form.get("pickup_state") or "").strip()
    destination_address = (form.get("destination_address") or "").strip()
    destination_unit = (form.get("destination_unit") or "").strip()
    destination_state = (form.get("destination_state") or "").strip()
    destination_known = (form.get("destination_known") or "true").strip().lower() != "false"
    description = (form.get("description") or "").strip()

    # Timing: the form sends friendly buttons ("asap", "2_3_days"); older
    # clients send urgency directly. Either is accepted.
    timing = (form.get("timing") or "").strip()
    urgency = (form.get("urgency") or "").strip()
    if not urgency:
        urgency = TIMING_TO_URGENCY.get(timing, "flexible")
    preferred_time = (form.get("preferred_time") or "").strip()

    # Multi-select answers.
    item_categories = _slugs(form, "item_categories", ITEM_CATEGORIES)
    extra_services = _slugs(form, "extra_services", EXTRA_SERVICES)
    special_item_slugs = _slugs(form, "special_item_types", SPECIAL_ITEMS)
    access_issues = _slugs(form, "access_issues", ACCESS_ISSUES)
    destination_access_issues = _slugs(form, "destination_access_issues", ACCESS_ISSUES)
    stairs_flights = (form.get("stairs_flights") or "").strip()
    destination_stairs_flights = (form.get("destination_stairs_flights") or "").strip()
    if stairs_flights and stairs_flights not in FLIGHTS:
        stairs_flights = ""
    if destination_stairs_flights and destination_stairs_flights not in FLIGHTS:
        destination_stairs_flights = ""

    # Access: prefer the checkbox answers, fall back to the legacy enums.
    if access_issues:
        pickup_access = _access_from_issues(access_issues, stairs_flights)
        parking_access = _parking_from_issues(access_issues)
    else:
        pickup_access = (form.get("pickup_access") or "unknown").strip()
        parking_access = (form.get("parking_access") or "unknown").strip()
    if destination_access_issues:
        destination_access = _access_from_issues(destination_access_issues,
                                                 destination_stairs_flights)
    else:
        destination_access = (form.get("destination_access") or "unknown").strip()

    # Inventory: free text if given, otherwise a sentence built from the
    # category checkboxes so the partner always receives readable detail.
    inventory = (form.get("inventory") or "").strip()
    if not inventory and item_categories:
        inventory = _sentence(item_categories, CATEGORY_TEXT)

    # Heavy / special items: checkbox list plus the optional "tell us what it
    # is" note. Legacy clients send one free-text field.
    special_items_note = (form.get("special_items_note") or "").strip()
    special_items = (form.get("special_items") or "").strip()
    real_specials = [s for s in special_item_slugs if s not in ("none",)]
    if not special_items and real_specials:
        special_items = _sentence(real_specials, SPECIAL_TEXT)
        if special_items_note:
            special_items = f"{special_items} — {special_items_note}"

    if service_type not in SERVICE_TYPES:
        errors["service_type"] = "Choose moving, junk removal or hauling."
    if job_type and job_type not in JOB_TYPES:
        job_type = "not_sure"
    if job_size not in JOB_SIZES:
        errors["job_size"] = "Choose the job size."
    if urgency not in URGENCIES:
        errors["urgency"] = "Choose when you need service."
    if property_type not in PROPERTY_TYPES:
        errors["property_type"] = "Choose a property type."
    if pickup_access not in ACCESS:
        errors["pickup_access"] = "Choose the pickup access."
    if destination_access not in ACCESS:
        errors["destination_access"] = "Choose the destination access."
    if parking_access not in PARKING:
        errors["parking_access"] = "Choose the parking access."
    if preferred_time and preferred_time not in PREFERRED_TIMES:
        preferred_time = ""
    if not pickup_address or len(pickup_address) < 5:
        errors["pickup_address"] = "Enter the pickup street address."
    if not ZIP_RE.match(zip_code) or zip_code == "00000":
        errors["zip_code"] = "Enter a valid 5-digit pickup ZIP code."
    if _is_move(service_type):
        # The exact destination street can wait — plenty of people are still
        # house hunting. The destination ZIP cannot, because it sets the
        # distance and therefore who can take the job.
        if destination_known and (not destination_address or len(destination_address) < 5):
            errors["destination_address"] = "Enter the destination street address."
        if not ZIP_RE.match(destination_zip) or destination_zip == "00000":
            errors["destination_zip"] = "Enter the destination city or ZIP code."
    elif destination_zip and not ZIP_RE.match(destination_zip):
        errors["destination_zip"] = "Enter a valid destination ZIP code."
    if len(inventory) < 3:
        errors["inventory"] = "Tell us what needs to be moved or removed."
    if form.get("preferred_contact", "text") not in CONTACTS:
        errors["preferred_contact"] = "Choose a contact method."
    if form.get("contact_time", "anytime") not in CONTACT_TIMES:
        errors["contact_time"] = "Choose the best contact time."
    if form.get("preferred_contact") == "email" and not email_ok:
        errors["email"] = "Add an email so a provider can reach you that way."
    if form.get("consent") != "true":
        errors["consent"] = "Consent is required to share your request."

    if errors:
        logger.info("intake.validation_failed", submission_id=submission_id,
                    fields=sorted(errors.keys()))
        return jsonify(errors=errors, request_id=g.request_id), 400

    storage = get_storage(current_app.config)
    photo_keys, photo_errors = [], []
    files = request.files.getlist("photos")[: current_app.config["MAX_PHOTOS"]]
    max_bytes = current_app.config["MAX_PHOTO_MB"] * 1024 * 1024
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        blob = uploaded.read()
        uploaded.seek(0)
        if len(blob) > max_bytes:
            photo_errors.append(f"{uploaded.filename}: over {current_app.config['MAX_PHOTO_MB']} MB.")
            continue
        try:
            photo_keys.append(storage.save(uploaded, current_app.config["PHOTO_MAX_DIMENSION"]))
        except StorageError as exc:
            photo_errors.append(f"{uploaded.filename}: {exc}")
    if photo_errors:
        return jsonify(errors={"photos": " ".join(photo_errors)}, request_id=g.request_id), 400

    duplicate_ref = None
    since = utcnow() - timedelta(days=30)
    for prior in Lead.query.filter(Lead.created_at >= since).all():
        if phone_digits and re.sub(r"\D", "", prior.phone or "")[-10:] == phone_digits:
            duplicate_ref = prior.reference
            break

    # Verify the pickup address with Smarty. Advisory only: an unconfirmed
    # address is recorded as such, never rejected.
    address_check = _verify_pickup_address(pickup_address, pickup_unit,
                                           form.get("pickup_city"), pickup_state, zip_code)
    address_ok = bool(address_check and address_check.get("status") in
                      ("verified", "unit_mismatch", "unit_missing"))
    if address_check and address_check.get("status") == "verified":
        # Trust Smarty's normalized spelling over the customer's typing.
        pickup_address = address_check.get("street") or pickup_address
        zip_code = address_check.get("zip") or zip_code
        pickup_state = address_check.get("state") or pickup_state

    # Profile the opportunity independently of current partner coverage. A lead
    # should not become cheaper merely because a partner has not been added yet.
    result = scoring.calculate(
        service_type=service_type, job_size=job_size, urgency=urgency,
        inventory=inventory, description=description, photo_count=len(photo_keys),
        pickup_access=pickup_access, destination_access=destination_access,
        parking_access=parking_access, special_items=special_items,
        phone_valid=phone_ok, phone_verified=bool(attempt), email=email or None,
        in_coverage=True, service_accepted=True,
        duplicate=bool(duplicate_ref), suspicious=bool(SPAMMY.search(description + " " + inventory)),
        destination_complete=(not _is_move(service_type)) or bool(destination_address and destination_zip),
    )

    # ---- INTERNAL job economics (admin only) -----------------------------
    # Computed here so the admin can price the lead. Deliberately never added
    # to the response body, the customer email or the thank-you page.
    dest_check = None
    if destination_address and _is_move(service_type):
        dest_check = _verify_pickup_address(destination_address, destination_unit,
                                            form.get("destination_city"),
                                            destination_state, destination_zip)
    miles, miles_basis = job_costing.estimate_distance_miles(
        service_type=service_type,
        pickup_lat=(address_check or {}).get("latitude"),
        pickup_lng=(address_check or {}).get("longitude"),
        dest_lat=(dest_check or {}).get("latitude"),
        dest_lng=(dest_check or {}).get("longitude"),
        pickup_zip=zip_code, dest_zip=destination_zip)
    economics = job_costing.calculate(
        cfg=current_app.config, service_type=service_type, job_type=job_type,
        job_size=job_size, item_categories=item_categories,
        extra_services=extra_services, special_item_types=special_item_slugs,
        access_issues=access_issues, stairs_flights=stairs_flights,
        destination_access_issues=destination_access_issues,
        destination_stairs_flights=destination_stairs_flights,
        property_type=property_type, urgency=urgency,
        distance_miles=miles, photo_count=len(photo_keys),
        special_items_note=special_items_note, distance_basis=miles_basis,
        destination_known=destination_known,
        address_verified=address_ok)
    economics["distance_basis"] = miles_basis

    eligible = []
    needs_heavy = bool(special_items)
    needs_commercial = property_type == "commercial" or job_size in {"commercial", "office"}
    for candidate in Partner.query.filter_by(active=True).all():
        accepted = {x.strip() for x in (candidate.services_accepted or "").split(",") if x.strip()}
        if not candidate.serves_zip(zip_code):
            continue
        if accepted and service_type not in accepted:
            continue
        if float(candidate.max_lead_price or 70) < result["price"]:
            continue
        if needs_heavy and not candidate.heavy_item_capable:
            continue
        if needs_commercial and not candidate.commercial_capable:
            continue
        eligible.append(candidate)

    partner = eligible[0] if eligible else None
    funded_partner = next(
        (candidate for candidate in eligible
         if Decimal(str(candidate.credit_balance or 0)) >= Decimal(str(result["price"]))),
        None,
    )
    in_coverage = partner is not None
    auto_purchase = bool(current_app.config["AUTO_ROUTE_LEADS"] and result["billable"]
                         and funded_partner and not duplicate_ref)
    if auto_purchase:
        partner = funded_partner
        partner.credit_balance = (Decimal(str(partner.credit_balance or 0))
                                  - Decimal(str(result["price"])))

    if duplicate_ref:
        status = "duplicate"
    elif not in_coverage:
        status = "outside_service_area"
    elif auto_purchase:
        status = "sent_to_partner"
    else:
        status = "new"

    reference = make_reference()
    for _ in range(5):
        if not Lead.query.filter_by(reference=reference).first():
            break
        reference = make_reference()

    details = {
        "service_type": service_type, "job_type": job_type, "job_size": job_size,
        "pickup_address": pickup_address, "pickup_unit": pickup_unit,
        "pickup_city": form.get("pickup_city"), "pickup_state": pickup_state,
        "pickup_zip": zip_code,
        "destination_known": destination_known,
        "destination_address": destination_address, "destination_unit": destination_unit,
        "destination_city": form.get("destination_city"),
        "destination_state": destination_state, "destination_zip": destination_zip,
        "property_type": property_type,
        "item_categories": item_categories, "extra_services": extra_services,
        "special_item_types": special_item_slugs, "special_items_note": special_items_note,
        "access_issues": access_issues, "stairs_flights": stairs_flights,
        "destination_access_issues": destination_access_issues,
        "destination_stairs_flights": destination_stairs_flights,
        "pickup_access": pickup_access,
        "destination_access": destination_access, "parking_access": parking_access,
        "inventory": inventory, "special_items": special_items,
        "timing": timing or urgency, "preferred_time": preferred_time,
        "service_date": form.get("service_date"), "urgency": urgency,
        "address_verification": address_check,
        "photo_count": len(photo_keys),
    }
    original = {k: v for k, v in form.items() if k not in ("turnstile_token", "code", "company_website")}
    original["photo_count"] = len(photo_keys)
    original["submission_id"] = submission_id

    lead = Lead(
        reference=reference, first_name=first, last_name=last,
        phone=phone_util.national_format(phone_result.e164), email=email[:255] or None,
        preferred_contact=form.get("preferred_contact", "text"),
        contact_time=form.get("contact_time", "anytime"),
        zip_code=zip_code, city=_short(form.get("pickup_city"), 80),
        property_type=property_type, pickup_address=pickup_address[:300],
        pickup_unit=_short(pickup_unit, 40), pickup_state=_short(pickup_state, 10),
        destination_address=destination_address[:300] or None,
        destination_unit=_short(destination_unit, 40),
        destination_city=_short(form.get("destination_city"), 80),
        destination_state=_short(destination_state, 10),
        destination_zip=destination_zip[:10] or None,
        destination_known=destination_known,
        address_verified=address_ok,
        address_verification=json.dumps(address_check) if address_check else None,
        service_type=service_type, pest_type=service_type,
        job_type=_short(job_type, 60),
        job_size=job_size, inventory=inventory[:5000],
        item_categories=",".join(item_categories) or None,
        extra_services=",".join(extra_services) or None,
        special_items=_short(special_items, 1000),
        special_items_note=_short(special_items_note, 1000),
        access_issues=",".join(access_issues) or None,
        destination_access_issues=",".join(destination_access_issues) or None,
        stairs_flights=_short(stairs_flights, 10),
        destination_stairs_flights=_short(destination_stairs_flights, 10),
        pickup_access=pickup_access, destination_access=destination_access,
        parking_access=parking_access, service_date=_short(form.get("service_date"), 30),
        preferred_time=_short(preferred_time, 20),
        location_seen=pickup_access, urgency=urgency,
        description=description[:5000], comments=_short(form.get("comments"), 2000),
        # May be absent: when required information is missing the model
        # deliberately returns no figure rather than a guessed one.
        estimated_job_value=economics.get("estimated_job_value"),
        cost_breakdown=json.dumps(economics),
        cost_confidence=economics.get("confidence"),
        difficulty_score=result["difficulty_score"], information_score=result["information_score"],
        lead_tier=result["tier"], lead_price=result["price"], lead_charge=result["price"],
        job_details=json.dumps(details, default=str), photo_keys=",".join(photo_keys),
        consent_text=current_app.config["CONSENT_TEXT"].format(brand=current_app.config["BRAND_NAME"]),
        consent_timestamp=utcnow(),
        referrer_url=_short(form.get("referrer_url"), 500),
        utm_source=_short(form.get("utm_source"), 120),
        utm_medium=_short(form.get("utm_medium"), 120),
        utm_campaign=_short(form.get("utm_campaign"), 120),
        gclid=_short(form.get("gclid"), 255), fbclid=_short(form.get("fbclid"), 255),
        landing_page=_short(form.get("landing_page"), 500),
        original_submission=json.dumps(original, default=str),
        score=result["score"], quality=result["grade"],
        score_breakdown=json.dumps(result["components"]), billable=result["billable"],
        phone_verified=bool(attempt),
        phone_verification_status="verified" if attempt else "not_started",
        phone_verified_at=attempt.verified_at if attempt else None,
        phone_verification_method=verification_method,
        phone_verification_attempt_id=attempt.attempt_id if attempt else None,
        phone_risk_flags=attempt.risk_flags if attempt else None,
        duplicate_of=duplicate_ref, status=status,
        partner_id=partner.id if (partner and status == "sent_to_partner") else None,
    )
    db.session.add(lead)
    db.session.flush()
    audit("lead.created", lead, actor_type="customer", submission_id=submission_id,
          service=service_type, zip=zip_code, score=result["score"],
          tier=result["tier"], lead_price=result["price"], lead_status=status,
          verified=bool(attempt), photos=len(photo_keys))
    audit("lead.cost_modelled", lead, actor_type="system",
          new_value=(f"${economics['estimated_job_value']}"
                     if economics.get("estimated_job_value") is not None
                     else "not enough information"),
          confidence=economics.get("confidence"),
          missing="; ".join(economics.get("missing", []))[:200] or None,
          total_cost=economics.get("total_cost"),
          weight_lbs=economics.get("estimated_weight_lbs"),
          miles=economics.get("total_miles"))
    audit("lead.scored", lead, actor_type="system",
          new_value=f"{result['tier']} ${result['price']}", **result["components"])
    if attempt:
        audit("lead.phone_verified", lead, actor_type="customer",
              method=verification_method, attempt_id=attempt.attempt_id)
    if duplicate_ref:
        audit("lead.duplicate_detected", lead, status="warn", new_value=duplicate_ref)
    if partner and status == "sent_to_partner":
        audit("routing.matched", lead, contractor_id=partner.id, new_value=partner.name)
        audit("partner.credit_debited", lead, contractor_id=partner.id,
              previous_value=float(Decimal(str(partner.credit_balance or 0)) + Decimal(str(result["price"]))),
              new_value=float(partner.credit_balance or 0), amount=result["price"])
    elif partner:
        audit("routing.suggested", lead, contractor_id=partner.id, new_value=partner.name)
    else:
        audit("routing.no_contractor", lead, status="warn", zip=zip_code)
    db.session.commit()

    cfg = current_app.config
    context = dict(
        brand=cfg["BRAND_NAME"], reference=reference, first_name=first,
        full_name=full_name, phone=phone_raw, email=email or "(not provided)",
        zip_code=zip_code, destination_zip=destination_zip or "—",
        service_type=service_type.replace("_", " ").title(),
        job_size=job_size.replace("_", " ").title(), urgency=urgency.replace("_", " ").title(),
        property_type=property_type.title(), contact_time=lead.contact_time,
        description=description[:600], inventory=inventory[:600],
        quality=result["grade"], score=result["score"], tier=result["tier"].replace("_", " ").title(),
        lead_price=result["price"], photo_count=len(photo_keys),
        source=lead.utm_source or "direct", campaign=lead.utm_campaign or "-",
        contact_method=labels.contact_label(lead.preferred_contact),
        lead_id=lead.id,
        landing_page=lead.landing_page or "-",
        duplicate_status=f"Possible duplicate of {duplicate_ref}" if duplicate_ref else "No",
        contractor=partner.name if partner else "UNASSIGNED", site_url=cfg["SITE_URL"],
    )
    deliveries = [("customer_confirmation", email or None, "customer", None),
                  ("admin_notification", cfg["ADMIN_NOTIFY_EMAIL"], "admin", None)]
    if partner and status == "sent_to_partner" and partner.notification_email:
        deliveries.append(("partner_notification", partner.notification_email, "contractor", partner.id))
    _dispatch_notifications(current_app._get_current_object(), lead.id, deliveries, context)
    return jsonify(ok=True, reference=reference), 201


@bp.get("/photos/<path:key>")
def photo(key):
    """Serve a customer photo.

    Local disk streams the file; object storage redirects to a short-lived
    pre-signed URL. Either way the bucket itself stays private — these images
    show the inside of someone's home.
    """
    import os.path
    safe_key = os.path.basename(key)     # no traversal, whatever the router did
    if current_app.config["STORAGE_BACKEND"] == "local":
        return send_from_directory(current_app.config["UPLOAD_DIR"], safe_key)
    try:
        from flask import redirect
        return redirect(get_storage(current_app.config).url_for(safe_key), code=302)
    except Exception:
        logger.error("photo.url_failed", exc_info=True)
        return jsonify(error="Photo unavailable"), 404


def _dispatch_notifications(app, lead_id, deliveries, context):
    import threading
    if app.config.get("TESTING"):
        try:
            _send_notifications(lead_id, deliveries, context)
        except Exception:
            logger.error("delivery.dispatch_failed", exc_info=True, lead_id=lead_id)
        return

    def _run():
        with app.app_context():
            try:
                _send_notifications(lead_id, deliveries, context)
            except Exception:
                logger.error("delivery.dispatch_failed", exc_info=True, lead_id=lead_id)
    threading.Thread(target=_run, daemon=True).start()


def _send_notifications(lead_id, deliveries, context):
    lead = Lead.query.get(lead_id)
    if lead is None:
        return
    cfg = current_app.config
    for template, to, audience, contractor_id in deliveries:
        if not to:
            logger.info("delivery.skipped", lead=lead.reference, template=template, reason="no address")
            continue
        delivery_id = logger.new_id("dlv")
        audit("delivery.attempted", lead, contractor_id=contractor_id,
              delivery_id=delivery_id, template=template, to_role=audience)
        try:
            with logger.external_call(cfg["MAIL_BACKEND"], "send_email") as call:
                call["template"] = template
                call["delivery_id"] = delivery_id
                send_templated(cfg, template, to, **context)
            audit("delivery.sent", lead, delivery_id=delivery_id, template=template)
        except Exception:
            logger.error("delivery.failed", exc_info=True, lead=lead.reference,
                         delivery_id=delivery_id, template=template)
            audit("delivery.failed", lead, status="failed", delivery_id=delivery_id, template=template)
    db.session.commit()
