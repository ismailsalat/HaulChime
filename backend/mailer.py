"""
Email sending with editable templates.

Templates live in templates/emails/*.txt. First line = subject, rest = body.
Placeholders use Python str.format() keys. Edit the .txt files freely —
no code changes needed.

MAIL_BACKEND=console  -> prints emails to the server log (development)
MAIL_BACKEND=smtp     -> real delivery via SMTP_* env vars
"""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("mailer")

EMAIL_DIR = os.path.join(os.path.dirname(__file__), "templates", "emails")


def render_template(_template_name, **context):
    path = os.path.join(EMAIL_DIR, f"{_template_name}.txt")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    subject, _, body = raw.partition("\n")
    return subject.replace("Subject:", "").strip().format(**context), body.strip().format(**context)


def _send_via_resend(config, to, subject, body):
    """Send through Resend's HTTPS API (port 443). Used where outbound SMTP is
    blocked. Uses stdlib urllib so there's no extra dependency."""
    import json as _json
    import urllib.request
    import urllib.error

    api_key = config.get("RESEND_API_KEY", "")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    payload = _json.dumps({
        "from": config["MAIL_FROM"],
        "to": [to],
        "subject": subject,
        "text": body,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 # Cloudflare fronts the Resend API and blocks the default
                 # "Python-urllib/x" agent with error 1010. Identify normally.
                 "User-Agent": "HaulChime/1.0 (+https://haulchime.com)",
                 "Accept": "application/json"})
    timeout = int(config.get("MAIL_TIMEOUT", 10))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()  # 200/202 = accepted
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"Resend returned {e.code}: {detail}")


def send_email(config, to, subject, body):
    if not to:
        return
    backend = config["MAIL_BACKEND"]
    if backend == "resend":
        _send_via_resend(config, to, subject, body)
    elif backend == "smtp":
        msg = EmailMessage()
        msg["From"] = config["MAIL_FROM"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        host = config["SMTP_HOST"]
        port = int(config["SMTP_PORT"])
        # A timeout is essential: without it a blocked outbound port (many
        # hosts, including Railway, block SMTP entirely) makes the connection
        # hang until the web worker is killed. With it, a bad connection fails
        # fast and the caller records delivery.failed while the lead is kept.
        timeout = int(config.get("SMTP_TIMEOUT", 20))
        # Port 465 speaks SSL from the first byte (SMTP_SSL); 587/25 use
        # STARTTLS to upgrade a plain connection.
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
        with smtp as s:
            if port != 465:
                s.starttls()
            if config["SMTP_USER"]:
                s.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        # Console backend: never log customer PII beyond the recipient needed
        # for debugging; body is intentionally not printed in full.
        from logger import mask_email
        log.info("EMAIL (console backend) to=%s subject=%s", mask_email(to), subject)


def send_templated(config, template, to, **context):
    """Render + send. Raises on failure so the caller can audit
    delivery.failed — callers must catch (lead capture never depends on
    email succeeding)."""
    subject, body = render_template(template, **context)
    send_email(config, to, subject, body)
