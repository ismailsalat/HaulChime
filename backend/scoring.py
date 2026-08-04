"""HaulChime lead profiling.

The engine does not estimate what a customer should pay for the job. Final job
pricing is always discussed directly between the customer and the service
partner.

Internally, HaulChime classifies leads using three separate signals:
- scope: the approximate size/type of the requested work
- difficulty: access and handling complexity
- information quality: how complete and contactable the request is

Those signals determine the internal lead tier and provider lead price.
"""
from typing import Optional

TIER_PRICES = {"standard": 40, "high_value": 55, "premium": 70}

SERVICE_SCOPE = {
    "junk_removal": 16,
    "hauling": 18,
    "local_move": 24,
    "long_distance_move": 32,
}

SIZE_SCOPE = {
    # junk removal / shared
    "single_item": 4, "few_items": 9, "quarter_truck": 14,
    "half_truck": 23, "full_truck": 34, "multi_truck": 42, "commercial": 38,
    # moving
    "studio": 10, "1br": 18, "2br": 28, "3br": 36, "3br_plus": 39,
    "4br_plus": 44, "office": 40, "labor_only": 8,
    # hauling
    "small_load": 12, "medium_load": 22, "large_load": 32, "multiple_loads": 42,
    # "Not sure" scores like a mid-size job: it should neither inflate nor
    # punish the lead, because the partner will pin it down on the call.
    "not_sure": 16,
}


def _clamp(value, low=0, high=100):
    return max(low, min(high, value))


def calculate(*, service_type: str, job_size: str, urgency: str,
              inventory: str, description: str, photo_count: int,
              pickup_access: str = "unknown",
              destination_access: str = "unknown",
              parking_access: str = "unknown",
              special_items: str = "", phone_valid: bool = True,
              phone_verified: bool = False, email: Optional[str] = None,
              in_coverage: bool = True, service_accepted: bool = True,
              duplicate: bool = False, suspicious: bool = False,
              destination_complete: bool = True) -> dict:
    scope = SERVICE_SCOPE.get(service_type, 10) + SIZE_SCOPE.get(job_size, 5)
    if urgency == "today":
        scope += 9
    elif urgency == "48_hours":
        scope += 5
    if special_items and len(special_items.strip()) >= 3:
        scope += 5
    scope = _clamp(scope)

    difficulty = 5
    access_values = [pickup_access, destination_access]
    difficulty += 8 * sum(a == "one_flight" for a in access_values)
    difficulty += 16 * sum(a == "two_plus_flights" for a in access_values)
    difficulty += 10 * sum(a == "long_carry" for a in access_values)
    if parking_access == "moderate":
        difficulty += 8
    elif parking_access == "difficult":
        difficulty += 16
    if special_items:
        difficulty += 12
    if service_type == "long_distance_move":
        difficulty += 14
    difficulty = _clamp(difficulty)

    information = 0
    information += 20 if phone_valid else 0
    information += 20 if phone_verified else 0
    information += 6 if email else 0
    information += 20 if len((inventory or "").strip()) >= 15 else 10
    information += 8 if len((description or "").strip()) >= 25 else 0
    information += min(photo_count * 4, 20)
    information += 6 if destination_complete else 0
    information = _clamp(information)

    intent = 8
    if urgency == "today":
        intent = 22
    elif urgency == "48_hours":
        intent = 18
    elif urgency == "this_week":
        intent = 14
    if photo_count >= 3:
        intent += 5
    intent = _clamp(intent)

    penalties = 0
    if duplicate:
        penalties += 35
    if suspicious:
        penalties += 45
    if not in_coverage:
        penalties += 30
    if not service_accepted:
        penalties += 30

    total = _clamp(round(scope * .48 + information * .34 + intent * .14
                         + min(difficulty, 50) * .04 - penalties))
    auto_reject = (not phone_valid) or suspicious

    if auto_reject:
        tier, grade, price = "standard", "rejected", 40
    elif total >= 62 and scope >= 48 and information >= 48:
        tier, grade, price = "premium", "A", 70
    elif total >= 38 and scope >= 30:
        tier, grade, price = "high_value", "B", 55
    else:
        tier, grade, price = "standard", "C", 40

    return {
        "score": total,
        "grade": grade,
        "tier": tier,
        "price": price,
        "difficulty_score": round(difficulty),
        "information_score": round(information),
        "billable": (not auto_reject) and information >= 30,
        "components": {
            "scope": round(scope), "difficulty": round(difficulty),
            "information": round(information), "intent": round(intent),
            "penalties": round(penalties),
        },
        "auto_rejected": auto_reject,
    }
