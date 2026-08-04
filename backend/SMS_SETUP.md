# SMS Phone Verification — Setup & Operations

HaulChime generates, stores and validates its own OTP codes. Telnyx only
delivers the text. We do **not** use Telnyx Verify.

## ⚠️ First: key hygiene

If an API key is ever pasted into chat, a screenshot, a commit, or a support
ticket — **rotate it immediately**: Telnyx Portal → Auth → API Keys → delete
the exposed key → create a new one → update the Railway variable.

Secrets live only in environment variables. Never in code, `build.js`, the
frontend bundle, logs, or git. `backend/.env` is gitignored.

## Environment variables

```env
# Telnyx (backend only)
TELNYX_API_KEY=                      # rotate if ever exposed
TELNYX_MESSAGING_PROFILE_ID=40019f92-9fde-4651-a20e-f9d9aaf2e793
TELNYX_FROM_NUMBER=+12069440030      # your SMS-enabled Telnyx number
TELNYX_PUBLIC_KEY=                   # Portal -> Account -> Keys -> Public Key
APP_BASE_URL=https://your-app.up.railway.app   # must be HTTPS for webhooks
SMS_ENABLED=true                     # false = kill switch, stops all sending
REQUIRE_PHONE_VERIFICATION=false     # true = no unverified lead can submit

# Secrets — generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
OTP_HMAC_SECRET=
PHONE_HASH_SECRET=                   # must differ from OTP_HMAC_SECRET

# OTP behaviour
OTP_EXPIRATION_SECONDS=300
OTP_RESEND_DELAY_SECONDS=60
OTP_MAX_SENDS_PER_QUOTE=2
OTP_MAX_ATTEMPTS=5

# Abuse limits (all enforced before any paid call)
SMS_MAX_PER_PHONE_HOUR=3
SMS_MAX_PER_PHONE_DAY=5
SMS_MAX_PER_IP_HOUR=8
SMS_MAX_PER_IP_DAY=20
SMS_MAX_UNIQUE_PHONES_PER_IP_HOUR=4
SMS_GLOBAL_MAX_PER_MINUTE=20
SMS_GLOBAL_DAILY_LIMIT=200
PHONE_VERIFICATION_REUSE_DAYS=30

# Cloudflare Turnstile
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET=
```

## Telnyx Portal checklist (manual, must be done by you)

1. **Rotate the API key** and put the new one in Railway variables.
2. **Messaging Profile** (`TestingHaulChimeSMS`, ID above):
   - API Version: **V2**
   - Outbound → keep **Smart Encoding** on
   - Outbound → set a **daily spend limit** (belt and braces with our app limit)
   - Allowed destinations → **United States only**
   - Senders → your number `+1 206-944-0030` is assigned ✓
3. **Webhook URL**: `https://<your-railway-domain>/api/webhooks/telnyx/messaging`
   set on the Messaging Profile (Inbound + Outbound webhooks).
4. **Public key**: Account → Keys → copy the Public Key into `TELNYX_PUBLIC_KEY`.
   Without it, every webhook is rejected (fail closed, by design).
5. **10DLC registration** — required for A2P traffic on US long codes. Register
   your brand and campaign (use-case: **2FA / account verification**, not
   marketing). **This takes days to weeks and must be approved before real
   messages deliver reliably.** Start it now.
6. Keep the STOP/HELP auto-responses enabled on the profile.

## The verification flow

1. Customer fills the whole quiz (pest → location → property → urgency → ZIP →
   description → contact).
2. Final step shows the number, the consent notice, and a Turnstile widget.
3. Customer presses **Send code**. The backend then runs, in order and *before
   any paid call*: Turnstile check → honeypot → US phone validation
   (`phonenumbers`, region must be US) → opt-out check → recent-verification
   reuse → existing-challenge check → per-phone/IP/session/global rate limits →
   daily budget.
4. If everything passes, the backend generates a 6-digit code with `secrets`,
   stores only an HMAC digest, and sends exactly one SMS.
5. Customer types the code (6 boxes, paste-friendly, auto-advance). Backend
   compares with `hmac.compare_digest`; success invalidates the code.
6. Quote submits with the challenge id. The backend re-checks that the verified
   challenge matches the submitted phone number.

## Cost controls

| Layer | Limit |
|---|---|
| Per quote | 2 sends max, 60s cooldown between them |
| Per phone | 3/hour, 5/day |
| Per IP | 8/hour, 20/day, 4 unique numbers/hour |
| Per session | 5/hour |
| Global | 20/minute, 200/day |
| Reuse | Same phone + same session verified in last 30 days = **no SMS** |

Every limit fails closed: if it trips, Telnyx is never called. Admin →
Settings shows sends, verification rate, SMS per verified quote, and cost per
verified quote.

## Incident response

**Stop all SMS immediately:** set `SMS_ENABLED=false` in Railway variables and
redeploy (~30 seconds). The quiz then shows a friendly unavailable message;
quotes still submit if `REQUIRE_PHONE_VERIFICATION=false`.

**Suspected key compromise:** rotate the Telnyx key first, then set a low
Messaging Profile spend limit while you investigate.

**Runaway sending:** check Admin → Activity & Logs → filter `otp.sent`, and the
daily counter on Settings. Lower `SMS_GLOBAL_DAILY_LIMIT` and redeploy.

## Testing

```bash
cd backend && python -m pytest tests/test_sms.py -v
```

38 tests, all with Telnyx and Turnstile mocked — **no real messages are ever
sent from the test suite, and no credentials are required to run it.**

Coverage includes: US format normalization, Canadian/Caribbean/toll-free
rejection, OTP send + verify, plaintext never stored, lockout after 5 wrong
attempts, expiry, resend cooldown, 2-send cap, per-phone/IP/unique-phone/daily
limits, opt-out, honeypot, verification reuse (same session yes, different
session no), provider auth/balance/rate-limit/timeout failures, no-auto-retry
on timeout, webhook signature rejection, webhook idempotency, STOP/START
keywords, lead gating before/after verification, and no secrets in responses.

## Turning verification on

Ship with `REQUIRE_PHONE_VERIFICATION=false` first so you can watch the funnel
without blocking anyone. Once the completion rate looks healthy in Admin →
Settings (aim for 80%+), flip it to `true` — from then on, unverified quotes
can't be submitted or sent to partners.

Verified phones add roughly 7 points to the Lead Quality Score
(Contactability rises from 65 to 100), which is exactly the argument you make
to partners when charging more per lead.
