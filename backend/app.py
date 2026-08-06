"""Flask application factory. Run with:  flask --app app run --port 5002"""
import os
import time

from flask import Flask, g, jsonify, request

import logger
from config import Config
from models import db


def _ensure_schema():
    """Auto-migration: add any table columns the models define but the
    database is missing (happens when an old leads.db meets newer code).
    ADD COLUMN is safe and non-destructive on both SQLite and PostgreSQL."""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(db.engine.dialect)
            db.session.execute(text(
                f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'))
            logger.warn("db.schema_migrated",
                        f"added missing column {table.name}.{col.name}",
                        table=table.name, column=col.name)

        # Widen any VARCHAR that is now shorter than the model declares.
        # ADD COLUMN handles new fields, but a column whose length grew is
        # invisible to it — and Postgres rejects the oversized value while
        # SQLite shrugs, so the failure only ever appears in production.
        # This is additive and non-destructive: widening never truncates.
        if db.engine.dialect.name == "postgresql":
            sizes = {c["name"]: getattr(c["type"], "length", None)
                     for c in inspector.get_columns(table.name)}
            for col in table.columns:
                wanted = getattr(col.type, "length", None)
                current = sizes.get(col.name)
                if (wanted and current and current < wanted
                        and col.type.__class__.__name__ in ("String", "VARCHAR")):
                    db.session.execute(text(
                        f'ALTER TABLE {table.name} ALTER COLUMN {col.name} '
                        f'TYPE VARCHAR({wanted})'))
                    logger.warn("db.column_widened",
                                f"widened {table.name}.{col.name} "
                                f"from {current} to {wanted}",
                                table=table.name, column=col.name)
    db.session.commit()


def _startup_diagnostics(app):
    """Print a plain-English readiness report at boot. Never prints secrets —
    only whether each one is present."""
    import os as _os
    if _os.getenv("HAULCHIME_QUIET_STARTUP") == "1":
        return
    cfg = app.config
    checks = []
    checks.append(("Admin login", bool(cfg.get("ADMIN_PASSWORD_HASH")),
                   "Set ADMIN_PASSWORD_HASH in .env "
                   "(python -c \"from werkzeug.security import generate_password_hash as g; print(g('yourpassword'))\")"))
    _mail_ok = (
        (cfg.get("MAIL_BACKEND") == "console")
        or (cfg.get("MAIL_BACKEND") == "smtp" and bool(cfg.get("SMTP_HOST")))
        or (cfg.get("MAIL_BACKEND") == "resend" and bool(cfg.get("RESEND_API_KEY")))
    )
    checks.append(("Email sending", _mail_ok,
                   "Set the mail backend's credentials — emails will fail "
                   "(smtp needs SMTP_HOST; resend needs RESEND_API_KEY)"))
    sms_secrets = bool(cfg.get("PHONE_VERIFICATION_HMAC_SECRET"))
    try:
        import bird  # noqa: F401  (the messagebird-sdk import package)
        sdk_ok = True
    except ImportError:
        sdk_ok = False
    checks.append(("messagebird-sdk installed", sdk_ok,
                   "Run: pip install -r requirements.txt  (in the backend folder)"))
    checks.append(("Bird SMS key", bool(cfg.get("BIRD_API_KEY")),
                   "Set BIRD_API_KEY in .env (bk_us1_... or bk_eu1_...) — "
                   "codes cannot be sent without it"))
    checks.append(("Address autocomplete (Smarty)",
                   bool(cfg.get("SMARTY_AUTH_ID") and cfg.get("SMARTY_AUTH_TOKEN")),
                   "Set SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN in .env — the quote "
                   "form still works, it just falls back to manual address typing"))
    checks.append(("SMS OTP verification",
                   bool(cfg.get("PHONE_VERIFICATION_HMAC_SECRET"))
                   and bool(cfg.get("BIRD_API_KEY")),
                   "Set PHONE_VERIFICATION_HMAC_SECRET and BIRD_API_KEY — "
                   "see SMS_PHONE_VERIFICATION_SETUP.md"))

    print("\n" + "=" * 62)
    print("  HaulChime backend — startup check")
    print("=" * 62)
    for name, ok, hint in checks:
        print(f"  [{'OK ' if ok else '-- '}] {name}")
        if not ok and hint:
            print(f"         {hint}")
    if not sms_secrets:
        print("\n  Phone verification is DISABLED until its secret is set.")
        print("  Quotes still work: REQUIRE_PHONE_VERIFICATION is "
              f"{'ON' if cfg.get('REQUIRE_PHONE_VERIFICATION') else 'OFF'}.")
    print(f"\n  Verification:    SMS OTP via Bird"
          f" (required: {'YES' if cfg.get('REQUIRE_PHONE_VERIFICATION') else 'no'})")
    print(f"  API base:        http://localhost:5002")
    print(f"  Admin dashboard: http://localhost:5002/admin")
    print(f"  Allowed origins: {', '.join(cfg.get('ALLOWED_ORIGINS', []))}")
    print("=" * 62 + "\n")


def _app_version():
    """Release version from the repo VERSION file (falls back to 'dev')."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "VERSION")) as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def create_app(config_object=Config):
    logger.init_logging()
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)

    # SQLite ignores foreign keys unless you ask it not to. Production runs on
    # Postgres, which enforces them strictly — so without this a delete that
    # orphans a row passes every local test and 500s in production. That is
    # exactly how "delete lead" shipped broken.
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _enforce_sqlite_foreign_keys(dbapi_connection, _record):
        if "sqlite3" in type(dbapi_connection).__module__:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    from routes.public import bp as public_bp
    from routes.admin import bp as admin_bp
    from routes.verification import bp as verification_bp
    from routes.address import bp as address_bp
    from routes.partner import bp as partner_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(verification_bp)
    app.register_blueprint(address_bp)
    app.register_blueprint(partner_bp)

    with app.app_context():
        try:
            db.create_all()
            _ensure_schema()
            logger.info("db.connected", uri_scheme=app.config["SQLALCHEMY_DATABASE_URI"].split(":")[0])
        except Exception:
            logger.critical("db.connection_failed", exc_info=True)
            raise

    _startup_diagnostics(app)

    logger.info("app.startup",
                version=os.getenv("RAILWAY_GIT_COMMIT_SHA", os.getenv("APP_VERSION", _app_version()))[:12],
                env=os.getenv("FLASK_ENV", "production"))

    @app.template_filter("fromjson")
    def _fromjson(s):
        import json as _json
        try:
            return _json.loads(s or "{}")
        except Exception:
            return {}

    # ---- request ID + API request/response logging ----
    @app.before_request
    def start_request():
        g.request_id = request.headers.get("X-Request-ID") or logger.new_id("req")
        g._start_time = time.monotonic()

    @app.after_request
    def finish_request(resp):
        resp.headers["X-Request-ID"] = getattr(g, "request_id", "")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        duration = round((time.monotonic() - getattr(g, "_start_time", time.monotonic())) * 1000)
        # Health-check noise stays at debug; everything else is info.
        log = logger.debug if request.path == "/api/config" else logger.info
        log("api.request", method=request.method, path=request.path,
            status=resp.status_code, duration_ms=duration,
            actor=("admin:" + g.admin_user) if getattr(g, "admin_user", None) else None)
        return resp

    # Errors never leak stack traces or secrets to clients.
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(413)
    def too_large(e):
        logger.warn("api.upload_too_large", path=request.path)
        return jsonify({"error": "Upload too large."}), 413

    @app.errorhandler(500)
    def server_error(e):
        logger.error("api.unhandled_error", exc_info=True, path=request.path)
        return jsonify({"error": "Something went wrong on our end.",
                        "request_id": getattr(g, "request_id", None)}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=5002, debug=True)
