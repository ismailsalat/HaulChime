"""
Log/audit retention — run manually or as a Railway cron:  python retention.py

Application logs go to stdout (Railway retains them per your plan), so this
script only manages database audit rows. By default it deletes NOTHING:
lead audit records are proof-of-lead evidence. Deletion happens only when
AUDIT_RETENTION_DAYS is explicitly set, and even then lead-lifecycle proof
events (lead.created, delivery.*, consent-bearing events) are kept.
"""
import os
from datetime import timedelta

from app import create_app
from models import LeadActivity, db, utcnow
import logger

PROTECTED_PREFIXES = ("lead.created", "lead.scored", "delivery.", "routing.")

app = create_app()
with app.app_context():
    days = os.getenv("AUDIT_RETENTION_DAYS")
    if not days:
        print("AUDIT_RETENTION_DAYS not set — nothing deleted (default policy).")
    else:
        cutoff = utcnow() - timedelta(days=int(days))
        q = LeadActivity.query.filter(LeadActivity.created_at < cutoff)
        for prefix in PROTECTED_PREFIXES:
            q = q.filter(~LeadActivity.event_type.startswith(prefix))
        n = q.delete(synchronize_session=False)
        db.session.commit()
        logger.info("retention.purged", rows=n, older_than_days=int(days))
        print(f"Deleted {n} non-protected audit rows older than {days} days.")
