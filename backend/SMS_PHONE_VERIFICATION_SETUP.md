# SMS Phone Verification — Setup Guide (Bird)

HaulChime sends and validates its own one-time codes and uses **Bird** only as
the SMS "pipe."

- **HaulChime** generates the 6-digit code with a cryptographic RNG, stores
  only an HMAC digest of it, decides *whether* an SMS may be sent (all the free
  abuse checks and rate limits below), and validates the code the customer
  types back.
- **Bird** just carries the text message. It never generates or checks the code.

**Customers never create a HaulChime account.** There is no login, password, or
dashboard — only a one-time code to prove the phone is reachable.

## 1. Get a Bird API key

1. Sign in to the Bird dashboard and open **Developers → API keys**.
2. Create an access key. It looks like `bk_us1_XXXXXXXX` (US) or `bk_eu1_...`
   (EU). The region is inferred from that prefix.
3. Keep it secret. **Never commit it** to git and never paste it into frontend
   code — it belongs only in the backend `.env`.

> If a key is ever exposed (e.g. pasted into chat, a screenshot, or a commit),
> rotate it immediately in the Bird dashboard and replace it in `.env`.

## 2. Register the OTP template (recommended)

For reliable delivery of authentication traffic, create an SMS template in Bird
named **`bird_otp_verification`** with a `code` parameter, e.g.:

> Your HaulChime verification code is {{code}}. It expires in 5 minutes.

The code HaulChime generates is passed in as that `{{code}}` parameter. If you
prefer a plain-text message instead, leave `BIRD_OTP_TEMPLATE` blank and the
backend sends its own one-segment message.

## 3. Configure `backend/.env`

```
BIRD_API_KEY=bk_us1_your_rotated_key   # required — codes can't send without it
BIRD_OTP_TEMPLATE=bird_otp_verification
BIRD_SMS_FROM=                         # optional sender id / number
BIRD_REGION=                           # optional (us1 | eu1); usually inferred
DEV_OTP_CODE=123456                    # code accepted for 555 test numbers in dev

PHONE_VERIFICATION_ENABLED=true        # master switch
REQUIRE_PHONE_VERIFICATION=false       # true = a lead can't submit unverified
APP_ENV=development                    # production disables the 555 test path
PHONE_VERIFICATION_HMAC_SECRET=        # run `python seed.py` to auto-generate
```

Then generate the secret and boot:

```
cd backend
python -m venv .venv && .venv/Scripts/activate     # (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python seed.py            # fills PHONE_VERIFICATION_HMAC_SECRET if blank
flask --app app run --port 5002
```

The backend prints a startup check showing whether the Bird key and secret are
set. `python doctor.py` runs the same checks any time.

## 4. Local testing without spending money

Any number with a `555` exchange (e.g. `+1 206 555 0142`) is treated as a
**fictional test number** while `APP_ENV` is not `production`: no SMS is sent,
no budget is spent, rate limits are skipped, and the code is `DEV_OTP_CODE`
(default `123456`). Use a real number to exercise the actual Bird send.

## Abuse & cost controls (all preserved, all env-tunable)

Every check runs **before** any paid Bird call and fails closed:

| Limit | Env var | Default |
|---|---|---|
| Resend cooldown | `PHONE_VERIFICATION_RESEND_DELAY_SECONDS` | 60s |
| Sends per quote | `PHONE_VERIFICATION_MAX_SENDS_PER_QUOTE` | 2 |
| Sends per phone / hour | `PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_HOUR` | 3 |
| Sends per phone / day | `PHONE_VERIFICATION_MAX_SENDS_PER_PHONE_DAY` | 5 |
| Sends per IP / hour | `PHONE_VERIFICATION_MAX_SENDS_PER_IP_HOUR` | 8 |
| Sends per IP / day | `PHONE_VERIFICATION_MAX_SENDS_PER_IP_DAY` | 20 |
| Unique phones per IP / hour | `PHONE_VERIFICATION_MAX_UNIQUE_PHONES_PER_IP_HOUR` | 4 |
| Sends per session / hour | `PHONE_VERIFICATION_MAX_SENDS_PER_SESSION_HOUR` | 5 |
| **Global daily send cap** | `PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT` | **200** |
| Wrong-code guesses before lockout | `PHONE_VERIFICATION_MAX_ATTEMPTS` | 5 |
| Code lifetime | `PHONE_VERIFICATION_ATTEMPT_TTL_SECONDS` | 600s |
| Verified-number reuse (same session) | `PHONE_VERIFICATION_REUSE_DAYS` | 30 days |

The hidden honeypot field and the per-IP submission rate limiter
(`RATE_LIMIT_SUBMISSIONS` / `RATE_LIMIT_WINDOW_SECONDS`) remain in force. The
`sms_budget` table tracks attempted/sent/verified counts and spend per day; the
global daily cap is your hard stop against runaway cost.
