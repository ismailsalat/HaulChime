"""Initialize local secrets and add an example HaulChime partner."""
import os
import secrets
from werkzeug.security import generate_password_hash

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def ensure_env():
    if not os.path.exists(ENV_PATH):
        src = os.path.join(os.path.dirname(ENV_PATH), ".env.example")
        with open(src, "r", encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("SECRET_KEY=change-this-to-a-long-random-string",
                            "SECRET_KEY=" + secrets.token_urlsafe(48))
        text = text.replace("PHONE_VERIFICATION_HMAC_SECRET=",
                            "PHONE_VERIFICATION_HMAC_SECRET=" + secrets.token_urlsafe(48))
        text = text.replace("ADMIN_PASSWORD_HASH=",
                            "ADMIN_PASSWORD_HASH=" + generate_password_hash("haulchime123"))
        with open(ENV_PATH, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("Created .env with local admin password: haulchime123")


def seed_partner():
    # Import only after .env exists so Config loads the generated settings.
    os.environ.setdefault("HAULCHIME_QUIET_STARTUP", "1")
    from app import app
    from models import Partner, db
    with app.app_context():
        if Partner.query.first():
            print("A partner already exists; nothing seeded.")
            return
        db.session.add(Partner(
            name="Demo Hauling & Moving (replace me)",
            contact_person="Demo Partner",
            email="partner@example.com",
            notification_email="partner@example.com",
            phone="(253) 555-0100",
            service_zips="98030,98031,98032,98042,98055,98057,98058",
            services_accepted="junk_removal,hauling,local_move,long_distance_move",
            billing_type="per_lead",
            price_per_lead=55,
            credit_balance=250,
            max_lead_price=70,
            daily_lead_limit=10,
            crew_size=3,
            truck_capacity="16-foot box truck",
            heavy_item_capable=True,
            commercial_capable=True,
            notes="Demo record. Replace before public launch.",
        ))
        db.session.commit()
        print("Seeded the demo partner.")


if __name__ == "__main__":
    ensure_env()
    seed_partner()
