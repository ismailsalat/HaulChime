"""
Central configuration. Every business-specific value (brand, city, consent
wording, email addresses) is set here via environment variables so the site
can be re-targeted to a new city or brand without code changes.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Core ---
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    # SQLite for local dev. Set DATABASE_URL=postgresql+psycopg://... in prod.
    _db_url = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'leads.db')}")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Brand / market (customizable at any time) ---
    BRAND_NAME = os.getenv("BRAND_NAME", "HaulChime")
    TARGET_CITY = os.getenv("TARGET_CITY", "Kent")
    TARGET_STATE = os.getenv("TARGET_STATE", "WA")
    PUBLIC_PHONE = os.getenv("PUBLIC_PHONE", "(555) 555-0100")
    SITE_URL = os.getenv("SITE_URL", "http://localhost:8080")

    # --- Consent wording (MUST be legally reviewed before launch) ---
    # The exact text shown to the customer is recorded with every lead.
    CONSENT_TEXT = os.getenv(
        "CONSENT_TEXT",
        "By submitting this form, I agree that {brand} may share my request "
        "with independent moving, junk-removal or hauling providers serving my area, "
        "and those providers may contact me by phone, text or email about this "
        "specific request. Message/data rates may apply. Consent is not a "
        "condition of purchase.",
    )

    # --- Email ---
    MAIL_BACKEND = os.getenv("MAIL_BACKEND", "console")  # console | smtp | resend
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    # Resend HTTP API (works where outbound SMTP ports are blocked, e.g. Railway).
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")
    ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL", "owner@example.com")

    # --- Admin auth ---
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    # Generate with: python -c "from werkzeug.security import generate_password_hash as g; print(g('yourpassword'))"
    ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

    # --- Lead routing: when false (default), new leads arrive as "New" and an
    # admin assigns the partner manually. Set true later to restore automatic
    # routing + partner notification.
    AUTO_ROUTE_LEADS = os.getenv("AUTO_ROUTE_LEADS", "false").lower() == "true"


    # --- Bird (SMS OTP delivery). Bird is a dumb pipe: HaulChime generates
    # and validates its own codes; Bird only carries the text. ---
    # The API key must live in the environment, never in code. The Bird SDK
    # also reads BIRD_API_KEY directly and infers the region from the key
    # prefix (bk_us1_ / bk_eu1_).
    BIRD_API_KEY = os.getenv("BIRD_API_KEY", "").split("#")[0].strip().strip('"')
    BIRD_REGION = os.getenv("BIRD_REGION", "").strip()  # optional; usually inferred
    # Registered OTP template name (recommended for A2P deliverability). The
    # code we generate is passed as its {code} parameter. Leave blank to send
    # a plain-text message instead.
    BIRD_OTP_TEMPLATE = os.getenv("BIRD_OTP_TEMPLATE", "bird_otp_verification")
    BIRD_SMS_FROM = os.getenv("BIRD_SMS_FROM", "").strip()  # sender id / number, optional
    # Fixed code used for fictional 555 test numbers outside production.
    DEV_OTP_CODE = os.getenv("DEV_OTP_CODE", "123456")

    # Master switch for phone verification, and whether it is mandatory.
    PHONE_VERIFICATION_ENABLED = os.getenv("PHONE_VERIFICATION_ENABLED", "true").lower() == "true"
    REQUIRE_PHONE_VERIFICATION = os.getenv("REQUIRE_PHONE_VERIFICATION", "true").lower() == "true"
    APP_ENV = os.getenv("APP_ENV", "development")

    # Secret that keys both the phone hash and the stored OTP digest.
    PHONE_VERIFICATION_HMAC_SECRET = os.getenv("PHONE_VERIFICATION_HMAC_SECRET", "")
    PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS = int(os.getenv("PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS", "600"))
    # Wrong-code guesses allowed before an attempt is locked.
    PHONE_VERIFICATION_MAX_ATTEMPTS = int(os.getenv("PHONE_VERIFICATION_MAX_ATTEMPTS", "5"))
    PHONE_VERIFICATION_RESEND_DELAY_SECONDS = int(os.getenv("PHONE_VERIFICATION_RESEND_DELAY_SECONDS", "60"))
    PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE", "2"))
    PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_HOUR = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_HOUR", "3"))
    PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_DAY = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_DAY", "5"))
    PHONE_VERIFICATION_MAX_SENDS_PER_IP_HOUR = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_IP_HOUR", "8"))
    PHONE_VERIFICATION_MAX_SENDS_PER_IP_DAY = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_IP_DAY", "20"))
    PHONE_VERIFICATION_MAX_UNIQUE_PHONES_PER_IP_HOUR = int(os.getenv("PHONE_VERIFICATION_MAX_UNIQUE_PHONES_PER_IP_HOUR", "4"))
    PHONE_VERIFICATION_MAX_SENDS_PER_SESSION_HOUR = int(os.getenv("PHONE_VERIFICATION_MAX_SENDS_PER_SESSION_HOUR", "5"))
    PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT = int(os.getenv("PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT", "200"))
    PHONE_VERIFICATION_REUSE_DAYS = int(os.getenv("PHONE_VERIFICATION_REUSE_DAYS", "30"))

    # --- Smarty (smarty.com) address autocomplete + verification ---
    # This is a SECRET key pair, so it is only ever used server-side by
    # smarty_client.py. The browser calls /api/address/* instead. Set both
    # values in .env; never hardcode them here and never ship them to the page.
    SMARTY_AUTH_ID = os.getenv("SMARTY_AUTH_ID", "").split("#")[0].strip().strip('"')
    SMARTY_AUTH_TOKEN = os.getenv("SMARTY_AUTH_TOKEN", "").split("#")[0].strip().strip('"')
    # Bias suggestions toward the states you actually serve (comma separated).
    # Blank = no preference, results stay nationwide.
    SMARTY_PREFER_STATES = os.getenv("SMARTY_PREFER_STATES", "WA")
    # Type-ahead fires per keystroke; this per-IP hourly ceiling only exists to
    # stop a script from burning through the Smarty subscription.
    ADDRESS_LOOKUP_LIMIT_PER_HOUR = int(os.getenv("ADDRESS_LOOKUP_LIMIT_PER_HOUR", "300"))

    # --- Internal job-economics model (ADMIN ONLY, never shown to customers) ---
    # Real 2026 market rates; override per market in .env. See job_costing.py
    # for the sourcing behind each default.
    DISPOSAL_FEE_PER_TON = float(os.getenv("DISPOSAL_FEE_PER_TON", "150"))
    MINIMUM_JOB_PRICE = float(os.getenv("MINIMUM_JOB_PRICE", "95"))
    DISPOSAL_MINIMUM_FEE = float(os.getenv("DISPOSAL_MINIMUM_FEE", "30"))
    CONSTRUCTION_DEBRIS_MULTIPLIER = float(os.getenv("CONSTRUCTION_DEBRIS_MULTIPLIER", "1.6"))
    LABOR_COST_PER_MOVER_HOUR = float(os.getenv("LABOR_COST_PER_MOVER_HOUR", "32"))
    LABOR_BILLED_PER_MOVER_HOUR = float(os.getenv("LABOR_BILLED_PER_MOVER_HOUR", "80"))
    MINIMUM_BILLABLE_HOURS = float(os.getenv("MINIMUM_BILLABLE_HOURS", "2"))
    FUEL_PRICE_PER_GALLON = float(os.getenv("FUEL_PRICE_PER_GALLON", "4.35"))
    TRUCK_MPG = float(os.getenv("TRUCK_MPG", "8.5"))
    VEHICLE_COST_PER_MILE = float(os.getenv("VEHICLE_COST_PER_MILE", "0.38"))
    BASE_ROUND_TRIP_MILES = float(os.getenv("BASE_ROUND_TRIP_MILES", "14"))
    DUMP_DETOUR_MILES = float(os.getenv("DUMP_DETOUR_MILES", "16"))
    OVERHEAD_PER_JOB = float(os.getenv("OVERHEAD_PER_JOB", "45"))
    OVERHEAD_RATE = float(os.getenv("OVERHEAD_RATE", "0.12"))
    TARGET_MARGIN = float(os.getenv("TARGET_MARGIN", "0.35"))
    PRICE_RANGE_SPREAD = float(os.getenv("PRICE_RANGE_SPREAD", "0.12"))

    # --- CORS: origins allowed to POST leads (the static site) ---
    ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080"
    ).split(",") if o.strip()]

    # --- Uploads ---
    # local  = disk (dev, or a Railway Volume mounted at UPLOAD_DIR)
    # s3     = any S3-compatible bucket: AWS S3, Cloudflare R2, Backblaze B2
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
    S3_BUCKET = os.getenv("S3_BUCKET", "")
    S3_PREFIX = os.getenv("S3_PREFIX", "photos")
    S3_REGION = os.getenv("S3_REGION", "us-east-1")
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")   # blank for AWS
    S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
    # Photos are private; links expire. 15 minutes is plenty for the admin.
    S3_URL_EXPIRY_SECONDS = int(os.getenv("S3_URL_EXPIRY_SECONDS", "900"))
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "40")) * 1024 * 1024
    MAX_PHOTO_MB = int(os.getenv("MAX_PHOTO_MB", "8"))
    MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "10"))
    ALLOWED_PHOTO_EXT = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png", "image/webp"}
    # Images wider than this are resized server-side to save space.
    PHOTO_MAX_DIMENSION = int(os.getenv("PHOTO_MAX_DIMENSION", "1920"))

    # --- Rate limiting (simple in-memory; use Redis-backed limiter in prod) ---
    RATE_LIMIT_SUBMISSIONS = int(os.getenv("RATE_LIMIT_SUBMISSIONS", "5"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "3600"))

    # --- Cookies (enable in production over HTTPS) ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", "false")

    @property
    def consent_text_rendered(self):
        return self.CONSENT_TEXT.format(brand=self.BRAND_NAME)
