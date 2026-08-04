"""
HaulChime self-check — run this first whenever something is not working.

    python doctor.py

Reports what is configured, what is missing, and exactly what to do about
each problem. Never prints secret values, only whether they are set.
"""
import os
import sys

# The app prints its own banner at boot; suppress it so this report is clean.
os.environ["HAULCHIME_QUIET_STARTUP"] = "1"

CHECK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def line(status, label, detail=""):
    print(f"  {status} {label}")
    if detail:
        for part in detail.split("\n"):
            print(f"         {part}")


def main():
    print("=" * 66)
    print("  HaulChime doctor")
    print("=" * 66)
    problems = 0

    # --- Python dependencies ---
    print("\nDependencies")
    for module, why in [("flask", "web framework"),
                        ("flask_sqlalchemy", "database"),
                        ("PIL", "photo processing"),
                        ("phonenumbers", "US phone validation"),
                        ("bird", "SMS phone verification (messagebird-sdk)")]:
        try:
            __import__(module)
            line(CHECK, module)
        except ImportError:
            problems += 1
            line(FAIL, module,
                 f"Missing ({why}).\nFix: pip install -r requirements.txt")

    # --- Environment file ---
    print("\nConfiguration")
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        problems += 1
        line(FAIL, ".env file",
             "Not found.\nFix: copy .env.example .env   then run: python seed.py")
        print("\nStopping here — fix the .env file first.\n")
        return 1
    line(CHECK, ".env file")

    from app import create_app
    app = create_app()
    cfg = app.config

    def need(key, label, fix):
        nonlocal problems
        if cfg.get(key):
            line(CHECK, label)
        else:
            problems += 1
            line(FAIL, label, fix)

    need("SECRET_KEY", "SECRET_KEY", "Fix: run python seed.py to generate one")
    need("ADMIN_PASSWORD_HASH", "Admin password",
         "Fix: python -c \"from werkzeug.security import generate_password_hash as g; print(g(\'yourpassword\'))\"\n"
         "     then paste into ADMIN_PASSWORD_HASH in .env")
    need("PHONE_VERIFICATION_HMAC_SECRET", "Phone verification secret",
         "Fix: run python seed.py (it generates this automatically)")

    if cfg.get("BIRD_API_KEY"):
        line(CHECK, "Bird SMS key")
    else:
        problems += 1
        line(FAIL, "Bird SMS key",
             "BIRD_API_KEY not set — codes cannot be sent.\n"
             "Fix: paste your bk_us1_/bk_eu1_ key into BIRD_API_KEY in .env")

    # --- Database ---
    print("\nDatabase")
    from models import Lead, Partner, PhoneVerificationAttempt, db
    with app.app_context():
        try:
            leads = Lead.query.count()
            partners = Partner.query.count()
            attempts = PhoneVerificationAttempt.query.count()
            line(CHECK, "Connection",
                 f"{leads} lead(s), {partners} partner(s), {attempts} verification attempt(s)")
            if partners == 0:
                line(WARN, "Partners",
                     "None yet — leads cannot be assigned.\n"
                     "Fix: run python seed.py, or add one in the admin")
        except Exception as exc:
            problems += 1
            line(FAIL, "Connection", f"{type(exc).__name__}: {exc}")

    # --- Behaviour summary ---
    print("\nBehaviour")
    line(CHECK, "Phone verification",
         f"enabled={cfg.get('PHONE_VERIFICATION_ENABLED')} "
         f"required={cfg.get('REQUIRE_PHONE_VERIFICATION')}")
    line(CHECK, "Auto-routing",
         f"{cfg.get('AUTO_ROUTE_LEADS')} (false = you assign partners manually)")
    line(CHECK, "Email backend", str(cfg.get("MAIL_BACKEND")))
    line(CHECK, "Allowed origins", ", ".join(cfg.get("ALLOWED_ORIGINS", [])))
    line(CHECK, "Log level", os.getenv("LOG_LEVEL", "INFO") +
         "  (set LOG_LEVEL=DEBUG in .env for detailed tracing)")

    print("\n" + "=" * 66)
    if problems:
        print(f"  {problems} problem(s) found — see the Fix lines above.")
    else:
        print("  No problems found. Start with: flask --app app run --port 5002")
    print("=" * 66 + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
