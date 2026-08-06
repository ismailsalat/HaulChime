"""
Internal job-economics model — ADMIN EYES ONLY.

============================================================================
THIS NEVER REACHES A CUSTOMER. NOT IN THE API RESPONSE, NOT IN THE
CONFIRMATION EMAIL, NOT ON THE THANK-YOU PAGE. NOT EVER.
============================================================================

HaulChime is a referral service. The partner inspects the job, quotes it, and
agrees the price directly with the customer. We do not quote work, we do not
promise a price, and we never show a customer a number.

So why compute a cost at all? Because the admin needs to know what a lead is
plausibly worth before deciding what to charge a partner for it. A 4-bedroom
move with a piano and three flights of stairs is worth more to a partner than
a single-mattress pickup, and pricing every lead identically leaves money on
one side of the table and gouges the other. The output of this module feeds
the admin dashboard and nothing else.

Every rate below is a real 2026 market figure and every one is overridable
from .env, because they move constantly and vary hugely by metro:

  Disposal      National average landfill tipping fee was $62.28/ton in 2024
                (EREF survey of 494 landfills, published 2026), up 10% in a
                year. The spread is enormous: ~$32/ton in Mississippi to
                ~$124/ton in Alaska; the Northeast averages ~$81/ton. Puget
                Sound transfer stations run far above the national average,
                so DISPOSAL_FEE_PER_TON defaults high for this market — reset
                it for yours.
  Labor         Local movers bill roughly $80/hour per mover ($90-$150/hour
                for the common two-mover-and-a-truck crew, higher in dense
                metros). Loaded cost to the operator (wage + payroll tax +
                workers' comp + insurance) is far below the billed rate.
  Fuel          A 26-foot box truck returns about 8-10 mpg loaded.
  Access        Stairs commonly carry $50-$75 per flight and long carries
                $100-$300 on full-service jobs; here they are modelled as the
                extra crew-time they actually consume.

Confidence is reported alongside the number. A request full of "Not sure"
answers produces a wide, low-confidence range, and the admin should treat it
as such rather than as a quote.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def _money(value) -> Decimal:
    """Every dollar figure goes through here.

    Binary floats cannot represent 0.10 exactly, so chaining rate multiplications
    in float drifts and produces the odd cent that makes a breakdown fail to add
    up. Money is computed as Decimal and quantised to cents at each boundary,
    with ROUND_HALF_UP because that is what people expect when they check the
    arithmetic by hand.
    """
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except Exception:
            return Decimal("0.00")
    if not d.is_finite():          # NaN or Infinity must never reach a page
        return Decimal("0.00")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

# --------------------------------------------------------------- defaults
# Every value here is read from Flask config at call time; these are only the
# fallbacks used when a key is absent.
DEFAULTS = {
    # Disposal
    "DISPOSAL_FEE_PER_TON": 150.0,      # Puget Sound transfer station rate
    "DISPOSAL_MINIMUM_FEE": 30.0,       # gate minimum on any dump visit
    "CONSTRUCTION_DEBRIS_MULTIPLIER": 1.6,   # C&D tips at a premium per ton
    # Labor (operator cost, not the customer-facing rate)
    "LABOR_COST_PER_MOVER_HOUR": 32.0,  # wage + payroll burden + comp
    "LABOR_BILLED_PER_MOVER_HOUR": 80.0,
    "MINIMUM_BILLABLE_HOURS": 2.0,      # movers only; junk is priced by volume
    "MINIMUM_JOB_PRICE": 95.0,          # the gate minimum on any truck roll
    "TRUCK_DISPATCH_COST": 0.0,         # cost of rolling a truck at all
    "DISPATCH_CHARGE": 0.0,             # what a partner bills for showing up
    "MILEAGE_RATE": 0.0,                # charged per mile, on top of hours
    "HEAVY_ITEM_SURCHARGE": 0.0,
    "STAIRS_SURCHARGE_PER_FLIGHT": 0.0,
    "SAME_DAY_SURCHARGE": 0.0,
    # Vehicle
    "FUEL_PRICE_PER_GALLON": 4.35,
    "TRUCK_MPG": 8.5,
    "VEHICLE_COST_PER_MILE": 0.38,      # tyres, brakes, depreciation, maint.
    "BASE_ROUND_TRIP_MILES": 14.0,      # yard -> job -> yard
    "DUMP_DETOUR_MILES": 16.0,
    # Business
    "OVERHEAD_PER_JOB": 40.0,           # insurance, dispatch, admin, software
    "OVERHEAD_RATE": 0.12,              # % of direct cost
    "TARGET_MARGIN": 0.30,              # gross margin the partner aims for
    "PRICE_RANGE_SPREAD": 0.12,         # +/- around the midpoint
}

# --------------------------------------------------------------- job shape
# Typical payload weight in pounds. Moving weights follow the industry rule of
# thumb that a 3-bedroom household runs 7,000-9,000 lb.
WEIGHT_LBS = {
    "single_item": 120, "few_items": 400,
    "quarter_truck": 850, "half_truck": 1700,
    "full_truck": 3400, "multi_truck": 6800,
    "small_load": 650, "medium_load": 1600,
    "large_load": 3100, "multiple_loads": 6200,
    "studio": 1800, "1br": 2600, "2br": 5000, "3br": 7500,
    "3br_plus": 8000, "4br_plus": 10500, "office": 6000, "labor_only": 2200,
    "commercial": 6500, "not_sure": 1500,
}

# On-site handling hours (clock time, not crew-hours), before access
# penalties and travel. Split by service because the work is not the same:
# movers wrap, pad and place furniture room by room, while a junk crew carries
# things straight out to the truck. A studio at ~3 hours and a 3-bed at 7-10
# hours matches what moving companies publish; a junk crew clears a half-truck
# load in well under two.
BASE_HOURS = {
    "moving": {
        "few_items": 1.5, "studio": 3.0, "1br": 4.0, "2br": 6.0, "3br": 8.0,
        "3br_plus": 8.5, "4br_plus": 10.5, "office": 7.0, "labor_only": 3.0,
        "commercial": 7.0, "single_item": 0.8, "not_sure": 3.0,
    },
    "junk_removal": {
        "single_item": 0.35, "few_items": 0.6, "quarter_truck": 0.9,
        "half_truck": 1.3, "full_truck": 2.0, "multi_truck": 4.0,
        "commercial": 3.0, "not_sure": 1.0,
    },
    "hauling": {
        "single_item": 0.4, "few_items": 0.7, "small_load": 0.9,
        "medium_load": 1.3, "large_load": 2.0, "multiple_loads": 4.0,
        "quarter_truck": 0.9, "half_truck": 1.3, "full_truck": 2.0,
        "commercial": 3.0, "not_sure": 1.0,
    },
}

CREW_SIZE = {
    "single_item": 2, "few_items": 2, "quarter_truck": 2, "half_truck": 2,
    "full_truck": 3, "multi_truck": 4,
    "small_load": 2, "medium_load": 2, "large_load": 3, "multiple_loads": 4,
    "studio": 2, "1br": 2, "2br": 3, "3br": 3, "3br_plus": 4,
    "4br_plus": 4, "office": 4, "labor_only": 2, "commercial": 4,
    "not_sure": 2,
}

# What the mix of items does to the payload weight.
CATEGORY_WEIGHT_FACTOR = {
    "construction_debris": 1.40, "building_materials": 1.35,
    "appliances": 1.18, "equipment": 1.30, "yard_waste": 1.12,
    "furniture": 1.05, "garage_storage": 1.05, "mattresses": 0.90,
    "boxes": 0.95, "household": 0.95, "electronics": 0.80,
}

# Flat disposal surcharges the facility charges on top of tonnage. Mattresses,
# freon appliances and e-waste are all handled (and billed) separately.
CATEGORY_DISPOSAL_SURCHARGE = {
    "mattresses": 40.0,
    "appliances": 45.0,       # refrigerant recovery
    "electronics": 30.0,      # e-waste handling
    "yard_waste": 15.0,
    "construction_debris": 55.0,
}

# Special items cost TIME, and sometimes gear. The two are separate on purpose.
#   hours      extra handling time, always real
#   consumable straps, pads, skid board — small, always incurred
#   rental     only when the crew has to hire gear they don't own. A partner
#              who owns a piano board and stair-climber incurs none of this,
#              so it is added only when we know they lack the equipment.
# The old model charged $250 flat for "piano" whether or not any equipment was
# hired, which inflated every heavy-item estimate.
SPECIAL_ITEM_COST = {
    "piano":              {"hours": 1.5, "consumable": 25.0, "rental": 120.0, "crew": 3},
    "safe":               {"hours": 1.5, "consumable": 25.0, "rental": 140.0, "crew": 3},
    "pool_table":         {"hours": 2.0, "consumable": 30.0, "rental": 80.0,  "crew": 3},
    "heavy_equipment":    {"hours": 1.5, "consumable": 20.0, "rental": 150.0, "crew": 3},
    "oversized_furniture":{"hours": 0.75,"consumable": 15.0, "rental": 0.0,   "crew": 3},
    "large_appliance":    {"hours": 0.5, "consumable": 12.0, "rental": 0.0,   "crew": 2},
    # Hazardous material can't ride in a normal truck to a normal landfill.
    "hazardous":          {"hours": 0.5, "consumable": 0.0,  "rental": 0.0,   "crew": 2},
    "not_sure":           {"hours": 0.5, "consumable": 10.0, "rental": 0.0,   "crew": 2},
}

# Heavy items whose handling genuinely depends on details we must ask for —
# an upright and a grand piano are different jobs with different crews.
NEEDS_DETAIL = {"piano", "safe", "pool_table", "heavy_equipment"}

# Access problems cost time, so they are priced as time, not as a flat fee.
ACCESS_HOURS = {
    "long_walk": 0.5, "narrow": 0.3,
    "limited_parking": 0.35, "gate_security": 0.15,
    "elevator": 0.4,        # waiting for and loading a lift is slow
    "not_sure": 0.2,
}
STAIRS_HOURS = {"1": 0.35, "2": 0.7, "3_plus": 1.2, "not_sure": 0.35}

EXTRA_SERVICE_COST = {
    "packing": (2.5, 70.0),          # crew-hours, materials
    "disassembly": (1.0, 0.0),
    "reassembly": (1.0, 0.0),
    "blankets_protection": (0.25, 25.0),
    "loading_only": (0.0, 0.0),
    "unloading_only": (0.0, 0.0),
}

# Junk and hauling crews run routes: a single mattress is one stop of several
# on the same truck roll, so that job should only carry its share of the
# depot-and-dump mileage. Moves get a dedicated truck and carry all of it.
STOPS_PER_RUN = {
    "single_item": 3.5, "few_items": 2.5, "quarter_truck": 2.0,
    "small_load": 2.5, "medium_load": 1.8,
    "half_truck": 1.5, "large_load": 1.5,
    "full_truck": 1.0, "multi_truck": 1.0, "multiple_loads": 1.0,
    "not_sure": 2.0,
}

# Jobs that involve a trip to a disposal facility.
DISPOSING_SERVICES = {"junk_removal"}
DISPOSING_JOB_TYPES = {"dump_run", "yard_construction_debris"}


SERVICES_NEEDING_DESTINATION = {"local_move", "long_distance_move"}
JOB_TYPES_NEEDING_DESTINATION = {"pickup_delivery", "material_transport",
                                 "equipment_hauling", "furniture_appliance"}


def missing_information(*, service_type, job_type, job_size, item_categories,
                        special_item_types, special_items_note, access_issues,
                        destination_access_issues, distance_basis,
                        destination_known=True):
    """What we would need before a number means anything.

    Returns (blocking, advisory). A blocking gap stops the estimate outright:
    a confident $939 on a piano job with no destination is worse than no
    figure, because it looks like an answer. Advisory gaps only widen the band.
    """
    items = _listify(item_categories)
    specials = [s for s in _listify(special_item_types) if s not in ("none", "not_sure")]
    access = _listify(access_issues)
    blocking, advisory = [], []

    needs_destination = (service_type in SERVICES_NEEDING_DESTINATION
                         or job_type in JOB_TYPES_NEEDING_DESTINATION)
    if needs_destination and (not destination_known or distance_basis == "no_destination"):
        blocking.append("Destination — the trip length drives most of the cost")

    if not job_size or job_size == "not_sure":
        if not items:
            blocking.append("Job size or an item list — nothing to size the job from")
        else:
            advisory.append("Job size (estimated from the item list instead)")
    if not items:
        advisory.append("What is being moved or removed")

    # An upright and a grand piano are different jobs with different crews, so
    # "piano" on its own is not enough to size the work.
    for item in specials:
        if item in NEEDS_DETAIL and not (special_items_note or "").strip():
            blocking.append(f"{item.replace('_', ' ').capitalize()} details — "
                            f"type, approximate weight and how it gets out")

    if not access or access == ["not_sure"]:
        advisory.append("Access at the pickup")
    if needs_destination and not _listify(destination_access_issues):
        advisory.append("Access at the destination")
    return blocking, advisory


def recommend_crew(*, service_type, job_size, item_categories,
                   special_item_types, access_issues, stairs_flights):
    """How many people this job actually needs, and why.

    Returned separately from the costing so the admin can see the reasoning
    and override it — the number then flows through labour, time and margin.
    """
    fam = ("moving" if service_type in ("local_move", "long_distance_move")
           else "junk_removal" if service_type == "junk_removal" else "hauling")
    crew = CREW_SIZE.get(job_size, 2)
    reasons = [f"{job_size.replace('_', ' ') or 'unknown size'} job → {crew}"]

    specials = [s for s in _listify(special_item_types) if s not in ("none", "not_sure")]
    for item in specials:
        needed = SPECIAL_ITEM_COST.get(item, {}).get("crew", 2)
        if needed > crew:
            crew = needed
            reasons.append(f"{item.replace('_', ' ')} needs {needed}")

    access = _listify(access_issues)
    if "stairs" in access and stairs_flights == "3_plus" and crew < 4:
        crew += 1
        reasons.append("three or more flights of stairs → +1")
    elif "long_walk" in access and fam == "moving" and crew < 4:
        crew += 1
        reasons.append("long carry → +1")

    return min(crew, 5), reasons


def _rate(partner, cfg, partner_field, config_key, scale=1.0):
    """A rate-card value if the assigned partner set one, otherwise the
    regional default.

    Resolved per field rather than all-or-nothing, so a partner who has only
    filled in their hourly rate still gets their hourly rate used while
    everything else falls back. Blank and zero both mean "not set" — a partner
    charging nothing per mile is not a case worth modelling.
    """
    if partner is not None:
        raw = getattr(partner, partner_field, None)
        if raw is not None:
            try:
                value = float(raw) * scale
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return _setting(cfg, config_key)


def _setting(cfg, key):
    if cfg is None:
        return DEFAULTS[key]
    try:
        return float(cfg.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return DEFAULTS[key]


def _listify(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def estimate_distance_miles(*, service_type: str,
                            pickup_lat=None, pickup_lng=None,
                            dest_lat=None, dest_lng=None,
                            pickup_zip: str = "", dest_zip: str = "") -> tuple:
    """Loaded travel distance, and how we arrived at it.

    Uses the real coordinates when Smarty verified both addresses, then falls
    back to ZIP comparison, then to a service-type default. The fallbacks are
    deliberately conservative — an under-estimate here quietly understates the
    cost of the job.
    """
    if None not in (pickup_lat, pickup_lng, dest_lat, dest_lng):
        import math
        radius_miles = 3958.8
        lat1, lng1, lat2, lng2 = map(math.radians, [
            float(pickup_lat), float(pickup_lng), float(dest_lat), float(dest_lng)])
        a = (math.sin((lat2 - lat1) / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
        straight_line = 2 * radius_miles * math.asin(min(1.0, math.sqrt(a)))
        # Roads are not straight lines; 1.28 is the usual circuity factor.
        return round(straight_line * 1.28, 1), "coordinates"

    if service_type == "long_distance_move":
        return 350.0, "long_distance_default"
    if pickup_zip and dest_zip:
        if pickup_zip == dest_zip:
            return 6.0, "same_zip"
        if pickup_zip[:3] == dest_zip[:3]:
            return 16.0, "same_zip3"
        return 45.0, "different_zip3"
    return 12.0, "no_destination"


def calculate(*, cfg=None, service_type: str, job_type: str = "",
              job_size: str = "", item_categories=None, extra_services=None,
              special_item_types=None, access_issues=None,
              stairs_flights: str = "", destination_access_issues=None,
              destination_stairs_flights: str = "",
              property_type: str = "", urgency: str = "flexible",
              distance_miles: Optional[float] = None,
              photo_count: int = 0, special_items_note: str = "",
              distance_basis: str = "", destination_known: bool = True,
              address_verified: bool = False, crew_override=None,
              partner=None) -> dict:
    """Return the internal cost/price picture for one job. Admin use only."""
    blocking, advisory = missing_information(
        service_type=service_type, job_type=job_type, job_size=job_size,
        item_categories=item_categories, special_item_types=special_item_types,
        special_items_note=special_items_note, access_issues=access_issues,
        destination_access_issues=destination_access_issues,
        distance_basis=distance_basis, destination_known=destination_known)

    recommended_crew, crew_reasons = recommend_crew(
        service_type=service_type, job_size=job_size,
        item_categories=item_categories, special_item_types=special_item_types,
        access_issues=access_issues, stairs_flights=stairs_flights)

    if blocking:
        # No numbers at all. A precise figure built on a guessed destination
        # reads as an answer, and the admin would price the lead against it.
        return {
            "status": "insufficient_information",
            "currency": "USD",
            "missing": blocking,
            "advisory": advisory,
            "recommended_crew": recommended_crew,
            "crew_reasons": crew_reasons,
            "confidence": "insufficient_information",
            "internal_only": True,
            "disclaimer": "Internal planning figure. Never shown or quoted to the "
                          "customer — the partner sets the price.",
        }

    items = _listify(item_categories)
    extras = _listify(extra_services)
    specials = [s for s in _listify(special_item_types) if s != "none"]
    access = [a for a in _listify(access_issues) if a != "none"]
    dest_access = [a for a in _listify(destination_access_issues) if a != "none"]

    unknowns = sum([
        job_size in ("", "not_sure"),
        "not_sure" in items or not items,
        "not_sure" in specials,
        "not_sure" in access,
        job_type in ("", "not_sure"),
    ])

    # ---- payload weight ------------------------------------------------
    weight = float(WEIGHT_LBS.get(job_size, WEIGHT_LBS["not_sure"]))
    factors = [CATEGORY_WEIGHT_FACTOR[c] for c in items if c in CATEGORY_WEIGHT_FACTOR]
    if factors:
        weight *= sum(factors) / len(factors)
    weight = round(weight)

    # ---- crew time -----------------------------------------------------
    fam = ("moving" if service_type in ("local_move", "long_distance_move")
           else "junk_removal" if service_type == "junk_removal" else "hauling")
    # The admin may override the crew; everything downstream follows it.
    crew = int(crew_override) if crew_override else recommended_crew
    crew = max(1, min(crew, 6))
    table = BASE_HOURS[fam]
    hours = float(table.get(job_size, table["not_sure"]))

    access_hours = 0.0
    for issue in access:
        access_hours += STAIRS_HOURS.get(stairs_flights, 0.6) if issue == "stairs" \
            else ACCESS_HOURS.get(issue, 0.0)
    for issue in dest_access:
        access_hours += STAIRS_HOURS.get(destination_stairs_flights, 0.6) if issue == "stairs" \
            else ACCESS_HOURS.get(issue, 0.0)
    hours += access_hours

    special_hours = 0.0
    special_equipment = 0.0
    owned = set()
    if partner is not None:
        owned = {e.strip() for e in
                 (getattr(partner, "equipment_owned", "") or "").split(",") if e.strip()}
        if getattr(partner, "heavy_item_capable", False):
            # A heavy-item-capable partner is assumed to carry the basic gear.
            owned |= {"piano", "safe", "pool_table", "heavy_equipment"}
    for item in specials:
        spec = SPECIAL_ITEM_COST.get(item, {})
        special_hours += spec.get("hours", 0.0)
        special_equipment += spec.get("consumable", 0.0)
        if item not in owned:
            special_equipment += spec.get("rental", 0.0)
    hours += special_hours

    extra_hours = 0.0
    materials = 0.0
    for service in extras:
        if service in ("none", "not_sure"):
            continue
        add_hours, add_cost = EXTRA_SERVICE_COST.get(service, (0.0, 0.0))
        extra_hours += add_hours
        materials += add_cost
    hours += extra_hours

    # Movers enforce a 2-3 hour minimum; junk and hauling crews don't — they
    # enforce a minimum *price* instead, applied at the end.
    if fam == "moving":
        hours = max(hours, _setting(cfg, "MINIMUM_BILLABLE_HOURS"))
    crew_hours = round(hours * crew, 2)

    # ---- travel --------------------------------------------------------
    if distance_miles is None:
        distance_miles = 12.0
    disposing = (service_type in DISPOSING_SERVICES or job_type in DISPOSING_JOB_TYPES)
    overhead_miles = _setting(cfg, "BASE_ROUND_TRIP_MILES")
    if disposing:
        overhead_miles += _setting(cfg, "DUMP_DETOUR_MILES")
    # Split the depot/dump mileage across the stops that share the truck roll.
    stops = 1.0 if fam == "moving" else STOPS_PER_RUN.get(job_size, 1.5)
    miles = float(distance_miles) + overhead_miles / max(1.0, stops)
    mpg = max(1.0, _setting(cfg, "TRUCK_MPG"))
    fuel_cost = round(miles / mpg * _setting(cfg, "FUEL_PRICE_PER_GALLON"), 2)
    vehicle_cost = round(
        miles * _rate(partner, cfg, "vehicle_cost_per_mile", "VEHICLE_COST_PER_MILE")
        + _rate(partner, cfg, "truck_dispatch_cost", "TRUCK_DISPATCH_COST"), 2)
    # Driving time is paid time. Assume an average 32 mph including loading
    # the truck at the yard and traffic.
    drive_hours = round(miles / 32.0, 2)
    paid_crew_hours = round(crew_hours + drive_hours * crew, 2)

    # ---- disposal ------------------------------------------------------
    disposal_cost = 0.0
    if disposing:
        tons = weight / 2000.0
        per_ton = _setting(cfg, "DISPOSAL_FEE_PER_TON")
        if "construction_debris" in items or "building_materials" in items:
            per_ton *= _setting(cfg, "CONSTRUCTION_DEBRIS_MULTIPLIER")
        disposal_cost = max(tons * per_ton, _setting(cfg, "DISPOSAL_MINIMUM_FEE"))
        for category in items:
            disposal_cost += CATEGORY_DISPOSAL_SURCHARGE.get(category, 0.0)
        if "hazardous" in specials:
            disposal_cost += 150.0    # special-handling gate rate
        disposal_cost = round(disposal_cost, 2)

    # ---- roll up (Decimal from here down) -------------------------------
    labor_rate = _rate(partner, cfg, "loaded_labor_cost_per_hour",
                       "LABOR_COST_PER_MOVER_HOUR")
    labor_cost = _money(Decimal(str(paid_crew_hours)) * Decimal(str(labor_rate)))
    fuel_cost = _money(fuel_cost)
    vehicle_cost = _money(vehicle_cost)
    disposal_cost = _money(disposal_cost)
    special_equipment = _money(special_equipment)
    materials = _money(materials)

    direct_cost = _money(labor_cost + fuel_cost + vehicle_cost + disposal_cost
                         + special_equipment + materials)
    overhead = _money(Decimal(str(_setting(cfg, "OVERHEAD_PER_JOB")))
                      + direct_cost * Decimal(str(_setting(cfg, "OVERHEAD_RATE"))))
    total_cost = _money(direct_cost + overhead)

    partner_margin = getattr(partner, "target_margin_pct", None) if partner else None
    margin = (Decimal(str(partner_margin)) / Decimal("100")
              if partner_margin else Decimal(str(_setting(cfg, "TARGET_MARGIN"))))
    if urgency == "today":
        margin += Decimal("0.05")       # same-day work commands a premium
    elif urgency == "48_hours":
        margin += Decimal("0.02")
    # Clamp: a margin at or above 1 would divide by zero or go negative.
    margin = max(Decimal("0"), min(margin, Decimal("0.60")))

    price_mid = _money(total_cost / (Decimal("1") - margin))

    # Moves are sold by the hour, so cross-check against the rate a mover
    # would actually quote and take whichever is higher. Junk and hauling are
    # sold by volume, so they get a floor price instead of an hourly floor.
    # What this partner's own rate card would produce. Two shapes exist and
    # mixing them is the classic double-count: a crew hourly rate already
    # covers the whole crew, so it must NOT be multiplied by crew size again.
    rate_card = None
    if partner is not None and getattr(partner, "crew_hourly_rate", None):
        billable_hours = max(float(hours),
                             _rate(partner, cfg, "minimum_billable_hours",
                                   "MINIMUM_BILLABLE_HOURS"))
        rate_card = (Decimal(str(billable_hours))
                     * Decimal(str(float(partner.crew_hourly_rate))))
    elif partner is not None and getattr(partner, "billed_rate_per_worker_hour", None):
        rate_card = (Decimal(str(paid_crew_hours))
                     * Decimal(str(float(partner.billed_rate_per_worker_hour))))
    elif fam == "moving":
        rate_card = (Decimal(str(paid_crew_hours))
                     * Decimal(str(_setting(cfg, "LABOR_BILLED_PER_MOVER_HOUR"))))

    if rate_card is not None:
        rate_card += Decimal(str(_rate(partner, cfg, "dispatch_charge",
                                       "DISPATCH_CHARGE")))
        rate_card += Decimal(str(miles)) * Decimal(str(
            _rate(partner, cfg, "mileage_rate", "MILEAGE_RATE")))
        if specials:
            rate_card += Decimal(str(_rate(partner, cfg, "heavy_item_surcharge",
                                           "HEAVY_ITEM_SURCHARGE")))
        flights = {"1": 1, "2": 2, "3_plus": 3}.get(stairs_flights, 0)
        if flights:
            rate_card += Decimal(str(flights)) * Decimal(str(
                _rate(partner, cfg, "stairs_surcharge_per_flight",
                      "STAIRS_SURCHARGE_PER_FLIGHT")))
        if urgency in ("today", "asap"):
            rate_card += Decimal(str(_rate(partner, cfg, "same_day_surcharge",
                                           "SAME_DAY_SURCHARGE")))
        rate_card += disposal_cost + special_equipment + materials
        price_mid = max(price_mid, _money(rate_card))

    price_mid = max(price_mid, _money(_rate(partner, cfg, "minimum_job_price",
                                            "MINIMUM_JOB_PRICE")))

    spread = Decimal(str(_setting(cfg, "PRICE_RANGE_SPREAD")))
    # A vague request gets a visibly wider band so nobody mistakes it for firm.
    spread += Decimal("0.05") * unknowns
    spread = max(Decimal("0"), min(spread, Decimal("0.60")))
    price_low = _money(price_mid * (Decimal("1") - spread))
    price_high = _money(price_mid * (Decimal("1") + spread))

    # Invariants. These are cheap, and every one of them has been a real bug in
    # some pricing system somewhere.
    price_low = max(Decimal("0.00"), price_low)
    price_mid = max(price_low, price_mid)
    price_high = max(price_mid, price_high)

    confidence = "high" if unknowns == 0 else ("medium" if unknowns <= 2 else "low")
    if photo_count >= 3 and confidence == "medium":
        confidence = "high"     # photos resolve most of the ambiguity

    return {
        "currency": "USD",
        "estimated_weight_lbs": weight,
        "crew_size": crew,
        "handling_hours": round(hours, 2),
        "drive_hours": drive_hours,
        "paid_crew_hours": paid_crew_hours,
        "distance_miles": round(float(distance_miles), 1),
        "total_miles": round(miles, 1),
        "involves_disposal": disposing,
        "costs": {
            "labor": float(labor_cost),
            "fuel": float(fuel_cost),
            "vehicle": float(vehicle_cost),
            "disposal": float(disposal_cost),
            "special_equipment": float(special_equipment),
            "materials": float(materials),
            "overhead": float(overhead),
        },
        "direct_cost": float(direct_cost),
        "total_cost": float(total_cost),
        "target_margin_pct": float(_money(margin * 100)),
        "estimated_job_value": float(price_mid),
        "estimated_range_low": float(price_low),
        "estimated_range_high": float(price_high),
        "estimated_profit": float(_money(price_mid - total_cost)),
        # Pre-formatted for templates, so no page can render $1234.5 or
        # $1234.5000000001.
        "display": {
            "value": f"{price_mid:.2f}", "low": f"{price_low:.2f}",
            "high": f"{price_high:.2f}", "cost": f"{total_cost:.2f}",
            "profit": f"{_money(price_mid - total_cost):.2f}",
        },
        "confidence": confidence,
        "unknown_answers": unknowns,
        # Repeated here so it travels with the number wherever it is rendered.
        "internal_only": True,
        "disclaimer": "Internal planning figure. Never shown or quoted to the "
                      "customer — the partner sets the price.",
    }
