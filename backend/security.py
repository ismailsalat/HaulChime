"""
Spam and abuse protection.

- Honeypot: the form includes a hidden "company_website" field. Humans never
  see it; bots fill it in. Any value => silently reject.
- Rate limit: per-IP sliding window, in-memory. Fine for a single-process
  deployment; swap for a Redis-backed limiter (e.g. Flask-Limiter) when you
  scale to multiple workers.
- CAPTCHA integration point: verify_captcha() is called on submission and
  currently always passes. Wire in Cloudflare Turnstile or hCaptcha there
  if spam becomes a problem — no other code needs to change.
"""
import time
from collections import defaultdict, deque

_hits = defaultdict(deque)


def rate_limited(ip, limit, window_seconds):
    now = time.time()
    q = _hits[ip]
    while q and q[0] < now - window_seconds:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def honeypot_triggered(payload):
    return bool((payload.get("company_website") or "").strip())


def verify_captcha(payload, remote_ip):
    """CAPTCHA hook. Return False to reject. Integrate Turnstile/hCaptcha here."""
    return True
