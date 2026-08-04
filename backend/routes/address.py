"""
Address autocomplete + verification endpoints (Smarty behind the scenes).

    GET  /api/address/suggest?q=123+main&selected=...
    POST /api/address/verify   {street, secondary, city, state, zip}

The browser never sees the Smarty secret key pair — it talks to these routes
and this server talks to Smarty. If Smarty is unconfigured or unreachable both
routes answer 200 with {"available": false}, and the quote form falls back to
plain manual typing. Address help must never block a customer.
"""
from flask import Blueprint, current_app, jsonify, request

import logger
import smarty_client
from security import rate_limited

bp = Blueprint("address", __name__, url_prefix="/api/address")


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


@bp.route("/suggest", methods=["OPTIONS"])
@bp.route("/verify", methods=["OPTIONS"])
def preflight():
    return ("", 204)


def _client_ip() -> str:
    return request.headers.get(
        "X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _too_many_lookups() -> bool:
    """Type-ahead fires per keystroke, so this ceiling is deliberately high —
    it only exists to stop a script from burning the Smarty subscription."""
    cfg = current_app.config
    return rate_limited("addr:" + _client_ip(),
                        cfg.get("ADDRESS_LOOKUP_LIMIT_PER_HOUR", 300), 3600)


@bp.get("/suggest")
def suggest():
    query = (request.args.get("q") or "").strip()
    selected = (request.args.get("selected") or "").strip()
    if len(query) < 3:
        return jsonify(available=True, suggestions=[]), 200
    if _too_many_lookups():
        return jsonify(available=False, reason="rate_limited", suggestions=[]), 200
    try:
        results = smarty_client.suggest(current_app.config, query, selected)
    except smarty_client.SmartyUnavailable as exc:
        return jsonify(available=False, reason=str(exc), suggestions=[]), 200
    except Exception:
        logger.error("address.suggest_failed", exc_info=True)
        return jsonify(available=False, reason="error", suggestions=[]), 200
    return jsonify(available=True, suggestions=results), 200


@bp.post("/verify")
def verify():
    data = request.get_json(silent=True) or request.form
    if _too_many_lookups():
        return jsonify(available=False, reason="rate_limited"), 200
    try:
        result = smarty_client.verify(
            current_app.config,
            street=(data.get("street") or ""),
            secondary=(data.get("secondary") or ""),
            city=(data.get("city") or ""),
            state=(data.get("state") or ""),
            zipcode=(data.get("zip") or data.get("zipcode") or ""),
        )
    except smarty_client.SmartyUnavailable as exc:
        return jsonify(available=False, reason=str(exc)), 200
    except Exception:
        logger.error("address.verify_failed", exc_info=True)
        return jsonify(available=False, reason="error"), 200
    return jsonify(available=True, address=result), 200
