"""
Development helper — clears phone-verification rate limits.

    python reset_limits.py

Deletes local verification attempts and today's SMS counters so you can keep
testing without waiting out an hourly limit. Never run this in production:
the limits exist to stop real abuse and real spending.
"""
import os

from app import create_app
from models import PhoneVerificationAttempt, SmsBudget, db

app = create_app()
with app.app_context():
    if os.getenv("APP_ENV") == "production":
        raise SystemExit("Refusing to clear rate limits in production.")
    attempts = PhoneVerificationAttempt.query.delete()
    budgets = SmsBudget.query.delete()
    db.session.commit()
    print(f"Cleared {attempts} verification attempt(s) and "
          f"{budgets} daily counter(s).")
    print("Rate limits reset. You can request codes again immediately.")
