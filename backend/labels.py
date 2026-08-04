"""
Plain-language labels for the admin UI. One source of truth so every page
uses identical wording and colors. Raw event names / slugs stay in the
database and developer logs; humans see these.
"""

# Lifecycle status -> (label, badge color class)
STATUS_META = {
    "new":                 ("New", "gray"),
    "validation_needed":   ("Needs review", "yellow"),
    "sent_to_partner":     ("Sent to partner", "green"),
    "duplicate":           ("Duplicate", "red"),
    "invalid":             ("Invalid / spam", "red"),
    "outside_service_area":("Outside service area", "yellow"),
    # legacy
    "accepted": ("Sent to partner", "green"), "declined": ("Needs review", "yellow"),
}

QUALIFICATION_META = {
    "pending":     ("Needs decision", "yellow"),
    "qualified":   ("Qualified", "green"),
    "unqualified": ("Unqualified", "red"),
}

# Automatic quality score -> single friendly label
QUALITY_META = {
    "A":        ("A — Premium", "green"),
    "B":        ("B — Standard", "green"),
    "C":        ("C — Review", "yellow"),
    "D":        ("D — Weak", "yellow"),
    "rejected": ("Rejected", "red"),
    # legacy values from leads scored before the LQS engine
    "hot": ("A — Premium", "green"), "qualified": ("B — Standard", "green"),
    "needs_review": ("C — Review", "yellow"),
}

SERVICE_LABELS = {
    "junk_removal": "Junk removal",
    "hauling": "Hauling / delivery",
    "local_move": "Local move",
    "long_distance_move": "Long-distance move",
}

# "What best describes the job?"
JOB_TYPE_LABELS = {
    "full_home_move": "Full home move",
    "apartment_move": "Apartment move",
    "few_items_move": "A few items",
    "single_heavy_item": "One heavy item",
    "office_move": "Office or business move",
    "load_unload_only": "Loading or unloading only",
    "one_item": "One item",
    "a_few_items": "A few items",
    "room_cleanout": "Room cleanout",
    "garage_basement_cleanout": "Garage or basement cleanout",
    "full_property_cleanout": "Full property or estate cleanout",
    "yard_construction_debris": "Yard or construction debris",
    "pickup_delivery": "Pickup and delivery",
    "dump_run": "Dump run",
    "furniture_appliance": "Furniture or appliance hauling",
    "material_transport": "Building-material transport",
    "equipment_hauling": "Equipment hauling",
    "other": "Other",
    "not_sure": "Not sure yet",
}

JOB_SIZE_LABELS = {
    "single_item": "One item", "few_items": "A few items",
    "quarter_truck": "About 1/4 truck", "half_truck": "About 1/2 truck",
    "full_truck": "About one full truck", "multi_truck": "More than one truck",
    "commercial": "Commercial load",
    "studio": "Studio", "1br": "1 bedroom", "2br": "2 bedrooms",
    "3br": "3 bedrooms", "3br_plus": "3 or more bedrooms",
    "4br_plus": "4 or more bedrooms", "office": "Office or commercial space",
    "labor_only": "Labor only",
    "small_load": "Small load", "medium_load": "Medium load",
    "large_load": "Large load", "multiple_loads": "Multiple loads",
    "not_sure": "Not sure yet",
}

TIMING_LABELS = {
    "asap": "As soon as possible", "2_3_days": "Within 2–3 days",
    "one_week": "Within one week", "specific_date": "On a chosen date",
    "flexible": "Date is flexible",
    "today": "Today or ASAP", "48_hours": "Within 48 hours",
    "this_week": "Within one week",
}

ITEM_LABELS = {
    "boxes": "Boxes", "furniture": "Furniture", "appliances": "Appliances",
    "mattresses": "Mattresses", "electronics": "Electronics",
    "office_equipment": "Office equipment", "heavy_specialty": "Heavy or specialty items",
    "yard_waste": "Yard waste", "construction_debris": "Construction debris",
    "garage_storage": "Garage or storage items", "building_materials": "Building materials",
    "equipment": "Equipment", "household": "Boxes or household items",
    "other": "Other", "not_sure": "Not sure",
}

EXTRA_SERVICE_LABELS = {
    "packing": "Packing", "disassembly": "Furniture disassembly",
    "reassembly": "Furniture reassembly", "loading_only": "Loading only",
    "unloading_only": "Unloading only", "blankets_protection": "Blankets or protection",
    "none": "None", "not_sure": "Not sure",
}

SPECIAL_ITEM_LABELS = {
    "piano": "Piano", "safe": "Safe", "pool_table": "Pool table",
    "large_appliance": "Large appliance", "oversized_furniture": "Oversized furniture",
    "heavy_equipment": "Heavy equipment", "hazardous": "Chemicals or hazardous material",
    "none": "None", "not_sure": "Not sure",
}

ACCESS_LABELS = {
    "stairs": "Stairs", "elevator": "Elevator", "long_walk": "Long walking distance",
    "narrow": "Narrow doorway or hallway", "limited_parking": "Limited truck parking",
    "gate_security": "Gate or security access", "none": "No access issues",
    "not_sure": "Not sure",
}

FLIGHT_LABELS = {"1": "1 flight", "2": "2 flights", "3_plus": "3+ flights",
                 "not_sure": "Not sure how many"}

PROPERTY_LABELS = {
    "house": "House", "apartment": "Apartment or condo", "townhouse": "Townhouse",
    "commercial": "Office or business", "office": "Office or business",
    "storage": "Storage unit", "storage_unit": "Storage unit",
    "construction_site": "Construction site", "other": "Other", "not_sure": "Not sure",
}

CONTACT_LABELS = {"text": "Text message", "phone": "Phone call",
                  "email": "Email", "either": "Either is fine"}

PREFERRED_TIME_LABELS = {"morning": "Morning", "afternoon": "Afternoon",
                         "evening": "Evening", "no_preference": "No preference",
                         "anytime": "Anytime"}


EVENT_STATUS_META = {
    "new":                 ("New", "gray"),
    "validation_needed":   ("Needs review", "yellow"),
    "sent_to_partner":     ("Sent to partner", "green"),
    "duplicate":           ("Duplicate", "red"),
    "invalid":             ("Invalid / spam", "red"),
    "outside_service_area":("Outside service area", "yellow"),
    # legacy
    "accepted": ("Sent to partner", "green"), "declined": ("Needs review", "yellow"),
}

# Raw audit event -> plain English sentence fragment.
EVENT_LABELS = {
    "lead.created":            "Lead submitted",
    "lead.scored":             "Lead quality calculated",
    "lead.duplicate_detected": "Possible duplicate detected",
    "routing.matched":         "Partner matched",
    "routing.suggested":       "Partner available for this area",
    "routing.no_contractor":   "No partner available for this area",
    "delivery.attempted":      "Notification being sent",
    "delivery.sent":           "Notification sent",
    "delivery.failed":         "Notification failed",
    "delivery.skipped":        "Notification skipped",
    "admin.login_success":     "Admin signed in",
    "admin.login_failed":      "Failed sign-in attempt",
    "admin.lead_updated":      "Lead updated by admin",
    "admin.partner_saved":     "Partner saved",
    "admin.csv_exported":      "Leads exported to CSV",
    "admin.lead_deleted":      "Lead permanently deleted",
    "admin.partner_deleted":   "Partner deleted",
    "admin.partner_quota_reset": "Partner monthly counter reset",
    "partner.quota_exceeded":  "Partner is over their monthly quota",
    "partner.zip_mismatch":    "Assigned outside the partner's service area",
    "partner.credit_debited":  "Partner credit charged for lead",
    "partner.credit_refunded": "Partner credit refunded",
    "intake.honeypot_triggered": "Spam submission blocked",
    "verify.send_approved":    "Verification code approved to send",
    "verify.completed":        "Phone number verified",
    "verify.reused":           "Phone already verified this session",
    "lead.phone_verified":     "Phone number verified",
}


def event_label(event_type):
    if event_type in EVENT_LABELS:
        return EVENT_LABELS[event_type]
    if event_type.startswith("lead.status_"):
        raw = event_type.replace("lead.status_", "")
        return "Status changed to " + STATUS_META.get(raw, (raw.replace("_", " "), ""))[0].lower()
    return event_type.replace(".", " ").replace("_", " ").capitalize()


def status_label(s):
    return STATUS_META.get(s, (s.replace("_", " ").capitalize(), "gray"))[0]


def status_color(s):
    return STATUS_META.get(s, ("", "gray"))[1]


def quality_label(q):
    return QUALITY_META.get(q, (q or "—", "gray"))[0]


def quality_color(q):
    return QUALITY_META.get(q, ("", "gray"))[1]


def service_label(value):
    return SERVICE_LABELS.get(value, (value or "—").replace("-", " ").replace("_", " ").title())


def _titleize(value):
    return (value or "—").replace("-", " ").replace("_", " ").capitalize()


def job_type_label(value):
    return JOB_TYPE_LABELS.get(value, _titleize(value))


def job_size_label(value):
    return JOB_SIZE_LABELS.get(value, _titleize(value))


def timing_label(value):
    return TIMING_LABELS.get(value, _titleize(value))


def property_label(value):
    return PROPERTY_LABELS.get(value, _titleize(value))


def contact_label(value):
    return CONTACT_LABELS.get(value, _titleize(value))


def preferred_time_label(value):
    return PREFERRED_TIME_LABELS.get(value, _titleize(value))


def flight_label(value):
    return FLIGHT_LABELS.get(value, _titleize(value))


def _join(value, mapping):
    """Comma-separated slugs (or a list) -> 'Furniture, Mattresses'."""
    if not value:
        return ""
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = [mapping.get(v.strip(), _titleize(v.strip())) for v in items if str(v).strip()]
    return ", ".join(out)


def item_list(value):
    return _join(value, ITEM_LABELS)


def extra_service_list(value):
    return _join(value, EXTRA_SERVICE_LABELS)


def special_item_list(value):
    return _join(value, SPECIAL_ITEM_LABELS)


def access_list(value):
    return _join(value, ACCESS_LABELS)

# Backward-compatible name used by older templates.
def pest_label(value):
    return service_label(value)
