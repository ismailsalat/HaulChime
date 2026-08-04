"""
Smarty (smarty.com) address intelligence client.

Two jobs:
  suggest()  -> US Autocomplete Pro. Powers the type-ahead in the quote form so
                the customer taps their address instead of typing city/state/ZIP.
  verify()   -> US Street API. Confirms the chosen address is real and returns
                clean components (street, city, state, ZIP, county, lat/lng).

Auth: the auth-id / auth-token pair is a SECRET key pair, so every call happens
here on the server. The browser only ever talks to our own /api/address/*
endpoints — the token never reaches the page. (Smarty's terms require this.)

Failure policy: address lookup is a convenience, never a gate. If Smarty is
unreachable, unconfigured or rate-limited, we return "unavailable" and the
quote form quietly falls back to plain manual entry. A customer must never be
blocked from requesting a quote because a third-party API had a bad minute.
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

import logger

AUTOCOMPLETE_URL = "https://us-autocomplete-pro.api.smarty.com/lookup"
STREET_URL = "https://us-street.api.smarty.com/street-address"
TIMEOUT_SECONDS = 4


class SmartyUnavailable(Exception):
    """Smarty could not answer. Callers fall back to manual entry."""


def is_configured(cfg) -> bool:
    return bool(cfg.get("SMARTY_AUTH_ID") and cfg.get("SMARTY_AUTH_TOKEN"))


def _call(cfg, url, params):
    query = dict(params)
    query["auth-id"] = cfg["SMARTY_AUTH_ID"]
    query["auth-token"] = cfg["SMARTY_AUTH_TOKEN"]
    full = url + "?" + urllib.parse.urlencode(query, doseq=True)
    request = urllib.request.Request(full, headers={
        "Accept": "application/json",
        "User-Agent": "HaulChime/1.0",
        # Smarty uses Referer for website keys; harmless (and useful in their
        # logs) for secret-key calls.
        "Referer": cfg.get("SITE_URL", ""),
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        # 401/402 mean the key is wrong or the subscription lapsed — worth a
        # loud log, because the site owner needs to fix it.
        level = logger.error if exc.code in (401, 402, 403) else logger.warn
        level("smarty.http_error", status=exc.code, endpoint=url.rsplit("/", 1)[-1])
        raise SmartyUnavailable(f"http_{exc.code}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        logger.warn("smarty.unreachable", endpoint=url.rsplit("/", 1)[-1])
        raise SmartyUnavailable("unreachable") from exc
    except ValueError as exc:
        logger.warn("smarty.bad_payload", endpoint=url.rsplit("/", 1)[-1])
        raise SmartyUnavailable("bad_payload") from exc


def suggest(cfg, search: str, selected: str = "", max_results: int = 7) -> list:
    """Type-ahead suggestions for a partial street address.

    `selected` is Smarty's mechanism for drilling into a building that has
    multiple units: pass back the suggestion the customer tapped and Smarty
    returns its apartment/suite list.
    """
    search = (search or "").strip()
    if len(search) < 3:
        return []
    if not is_configured(cfg):
        raise SmartyUnavailable("not_configured")

    params = {
        "search": search[:120],
        "max_results": max(1, min(int(max_results or 7), 10)),
        "source": "all",
    }
    if selected:
        params["selected"] = selected[:200]
    prefer = [s.strip() for s in (cfg.get("SMARTY_PREFER_STATES") or "").split(",") if s.strip()]
    if prefer:
        params["prefer_states"] = ";".join(prefer)
        params["prefer_ratio"] = 3

    payload = _call(cfg, AUTOCOMPLETE_URL, params)
    out = []
    for item in (payload.get("suggestions") or [])[:10]:
        street = (item.get("street_line") or "").strip()
        secondary = (item.get("secondary") or "").strip()
        entries = int(item.get("entries") or 0)
        city = (item.get("city") or "").strip()
        state = (item.get("state") or "").strip()
        zipcode = (item.get("zipcode") or "").strip()
        # "123 Main St Apt (14 entries)" — tapping it expands the unit list.
        label_street = " ".join(x for x in (street, secondary) if x)
        suffix = f" ({entries} units)" if entries > 1 else ""
        out.append({
            "street": street,
            "secondary": secondary,
            "entries": entries,
            "city": city,
            "state": state,
            "zip": zipcode,
            "label": f"{label_street}{suffix}",
            "sublabel": ", ".join(x for x in (city, f"{state} {zipcode}".strip()) if x),
            # Echoed straight back to /suggest when the customer taps a
            # multi-unit building.
            "selected": f"{label_street} {city} {state} {zipcode}".strip(),
            "needs_unit": entries > 1,
        })
    return out


def verify(cfg, street: str, city: str = "", state: str = "",
           zipcode: str = "", secondary: str = "") -> dict:
    """Confirm one address exists and return clean, mail-ready components."""
    street = (street or "").strip()
    if not street:
        return {"status": "empty"}
    if not is_configured(cfg):
        raise SmartyUnavailable("not_configured")

    params = {"street": street[:120], "candidates": 3}
    if secondary:
        params["secondary"] = secondary[:40]
    if city:
        params["city"] = city[:60]
    if state:
        params["state"] = state[:20]
    if zipcode:
        params["zipcode"] = zipcode[:10]

    candidates = _call(cfg, STREET_URL, params)
    if not isinstance(candidates, list) or not candidates:
        return {"status": "not_found"}

    best = candidates[0]
    components = best.get("components") or {}
    metadata = best.get("metadata") or {}
    analysis = best.get("analysis") or {}

    number = components.get("primary_number", "")
    predirection = components.get("street_predirection", "")
    name = components.get("street_name", "")
    suffix = components.get("street_suffix", "")
    postdirection = components.get("street_postdirection", "")
    unit_type = components.get("secondary_designator", "")
    unit_number = components.get("secondary_number", "")
    line1 = best.get("delivery_line_1") or " ".join(
        x for x in (number, predirection, name, suffix, postdirection) if x)

    dpv = (analysis.get("dpv_match_code") or "").upper()
    # Y = confirmed. S = confirmed building, unit number missing/wrong.
    # D = confirmed building, unit number required but not supplied.
    status = {"Y": "verified", "S": "unit_mismatch", "D": "unit_missing"}.get(dpv, "unconfirmed")

    return {
        "status": status,
        "dpv": dpv or None,
        "street": line1,
        "secondary": " ".join(x for x in (unit_type, unit_number) if x),
        "city": components.get("city_name", ""),
        "state": components.get("state_abbreviation", ""),
        "zip": components.get("zipcode", ""),
        "plus4": components.get("plus4_code", ""),
        "county": metadata.get("county_name", ""),
        "latitude": metadata.get("latitude"),
        "longitude": metadata.get("longitude"),
        # residential / commercial — useful context for the partner.
        "rdi": metadata.get("rdi", ""),
        "formatted": ", ".join(x for x in (
            " ".join(y for y in (line1, unit_type, unit_number) if y),
            components.get("city_name", ""),
            f"{components.get('state_abbreviation', '')} {components.get('zipcode', '')}".strip(),
        ) if x),
        "candidate_count": len(candidates),
    }
