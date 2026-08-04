# HaulChime changelog

## 1.0.0 — August 3, 2026

First production release. The quote flow, phone verification, address
validation, internal job economics and partner delivery are all in place, and
the project is ready to deploy from GitHub to Railway.

Everything below under 0.3.0 and 0.3.1 shipped as part of this release; the
sections are kept separate because they cover different areas of the system.

### Deployment readiness

- `.gitignore` covering secrets, the bundled Windows virtualenv, the SQLite
  database, uploads and build output.
- GitHub Actions CI: backend tests, frontend build, and a secret scan that
  fails the build if `.env` is tracked or a key-shaped string is committed.
- Root `railway.json` with build, start, health check and restart policy.
- `DEPLOYMENT.md` with step-by-step GitHub, Railway, storage and Namecheap DNS
  instructions.

---

## 0.3.1 — included in 1.0.0

### The mid-word text wrapping is fixed

The screenshot showed "Mattresse / s" and "Construct / ion debris". Cause:
`overflow-wrap: anywhere` on the option labels, which lets the browser break
*inside* a word rather than only between words. That plus a 128px minimum
column width meant longer labels shattered.

- Labels now use `overflow-wrap: break-word` with `word-break: normal` and
  `hyphens: none`, so words only break between words.
- Option grids widened: 178px minimum for the compact grid, 210px for the
  standard one, 200px for two-up. Below 400px they drop to a single column
  rather than squeezing two.
- Cards in a row now stretch to a shared height, so a two-line label no longer
  leaves a ragged row.

### Assigning outside a partner's service area now stops and asks

It used to go through and mention it afterwards, which is backwards — that is
usually a misclick, and by the time you read the notice the credit is already
debited.

Now the save is refused with a `400` and a warning panel that names the
partner, the lead's ZIP, and the ZIPs they *do* cover, with three ways out:
**Yes, assign anyway** (a second, explicit confirmation), **Cancel**, or
**Edit their service ZIPs**. Both the blocked attempt and the confirmed
override are written to the audit trail, so the record shows the decision was
deliberate.

### Leads can be sent to partners by email and text

New `partner_delivery.py` and a **Send this lead to the partner** panel on the
lead page, with checkboxes for each channel and a confirmation naming the
partner.

- Deliberately separate from saving the lead. Assigning a partner and telling
  that partner are different decisions; merging them means every stray save
  fires a text.
- The channels are independent. If the text fails, the email still counts, and
  the admin is told exactly which one failed and why ("No phone number on
  file", "The carrier rejected that number", "SMS isn't configured").
- **The SMS carries no street address and no pricing.** A text sits
  unencrypted on a phone that may be shared or lost, so it holds the
  reference, service, area, timing and customer phone, plus a link to the full
  detail behind the admin login. A test asserts this.
- `bird_client.send_notification()` sends with category `message`, not
  `authentication`. Mislabelling business traffic as OTP traffic is a fast
  route to carrier filtering.
- **Test contact** on the partners page sends a real email and text using your
  most recent lead, so you can prove a partner's details work before a live
  job depends on them.

### Every admin action confirms first

One handler in the admin shell: any form or button carrying `data-confirm`
must be acknowledged before it fires. Covers saving a lead, saving a partner,
pausing/resuming, deleting a lead, deleting a partner, importing a backup,
sending to a partner, and the test message. Forms marked `data-warn-unsaved`
also warn before you navigate away mid-edit.

Deleting a lead moved out of the save form into its own **Danger zone** card —
partly so the destructive action isn't a stray click from "Save", and partly
because it was nested inside another form, which HTML doesn't allow and
browsers handle unpredictably.

### Production storage, and everything needed for GitHub + Railway

- `S3Storage` is implemented (it was a stub that raised). Works with AWS S3,
  Cloudflare R2 and Backblaze B2 via `S3_ENDPOINT_URL`. Buckets stay private
  and `/api/photos/<key>` redirects to a pre-signed URL that expires after 15
  minutes — these images show the inside of customers' homes next to their
  addresses.
- `.gitignore` rewritten to cover `.env`, the bundled 127 MB Windows
  virtualenv, the SQLite database, uploads and build output.
- GitHub Actions CI: backend tests, frontend build, and a secret scan that
  fails the build if `.env` is ever tracked or a key-shaped string is
  committed.
- Root `railway.json` with the build and start commands, health check and
  restart policy.
- `DEPLOYMENT.md` walks through pushing to GitHub, Railway with Postgres,
  photo storage (Railway Volume or R2), the static site, DNS for apex/www/api,
  and a go-live checklist.

**One thing to do before your first push:** `backend/.env` currently holds
live Smarty, Bird and admin credentials. It's gitignored now, but confirm with
`git status --short | grep .env` before pushing. If it ever reaches a remote,
deleting it later doesn't help — it stays in history and in every clone, and
you'd need to rotate every key in it.

---

## 0.3.0 — included in 1.0.0

### The quote form was rebuilt

Four short steps with conditional questions. A simple junk-removal request now
answers about eight required questions plus phone verification; a complicated
move naturally collects more, because the extra questions only appear when an
earlier answer makes them relevant.

- Big tappable cards instead of dropdowns. Every control is at least 48px tall.
- Multi-select for items, access issues, heavy items and extra help. "None of
  these" and "Not sure" are mutually exclusive with everything else, so nobody
  can report no access issues *and* stairs.
- "Not sure" is a real answer on every question where a person might genuinely
  not know. Picking "Not sure" for the service itself gets one friendly
  follow-up rather than a dead end.
- Junk-removal customers never see moving questions, and vice versa. Stairs
  only ask about flights. Only moving and delivery jobs ask for a destination.
- Moves can say "I don't know the destination yet" and give a city or ZIP.
- Answers autosave to sessionStorage, so going back, refreshing or failing
  verification never loses work.
- Photos via camera or gallery, with thumbnails and per-file removal.
- Layout uses `gap` and margins throughout — nothing can overlap at any width.
  Verified down to 360px. Inputs are 16px so iOS never zooms on focus.

### Phone verification now works the PestChime way

Type the number, tap "Text me a code", six digit boxes appear, and the code
verifies itself the moment the sixth digit lands — no extra button. Paste and
SMS autofill spread across the boxes. Backspace walks backwards. There's a
resend countdown, a "use a different number" escape hatch, and a green
"✓ Phone number verified" badge. Changing the number after verifying clears
the verification, as it must.

### Address validation via Smarty

- `smarty_client.py` wraps US Autocomplete Pro (type-ahead, including
  drilling into multi-unit buildings) and US Street (verification, returning
  clean components plus coordinates).
- `GET /api/address/suggest` and `POST /api/address/verify` proxy it.

**The auth-id / auth-token pair is a secret key pair, so it lives in `.env`
and is only ever used server-side.** The browser talks to our own endpoints and
never sees the token. Embedding it in page JavaScript would expose it publicly
and breach Smarty's terms — if you want a browser-side key, generate a separate
*website key* (auth-web + auth-referer) restricted to your domain.

If Smarty is unconfigured, down or rate-limited, both endpoints answer 200 with
`available: false` and the form silently falls back to manual typing. A
customer is never blocked by a third-party outage.

### Internal job-economics model (ADMIN ONLY)

`job_costing.py` models what a job plausibly costs to run and what it's worth:

- **Labor** — crew size and on-site hours per job size, split by service
  (movers wrap and place furniture; a junk crew carries it straight out), plus
  paid drive time.
- **Fuel and vehicle** — miles ÷ truck mpg × fuel price, plus per-mile wear.
  Distance comes from the real Smarty coordinates of both addresses when
  available, then ZIP proximity, then a service default.
- **Disposal** — payload weight × tipping fee per ton, with construction
  debris at a premium multiplier and flat surcharges for mattresses, freon
  appliances, e-waste and hazardous material. Gate minimum applied.
- **Access** — stairs, long carries, narrow doorways and bad parking priced as
  the crew time they actually consume.
- **Special items** — piano, safe, pool table and heavy equipment carry both
  extra hours and equipment cost.
- **Route sharing** — a single mattress is one stop on a truck roll, so it
  only carries its share of the depot-and-dump mileage. Moves get a dedicated
  truck and carry all of it.
- **Overhead and margin** on top, with a wider band and a "low confidence"
  flag when the customer answered "Not sure" a lot.

Defaults are real 2026 figures (national average landfill tipping fee was
$62.28/ton per the EREF survey, and this market's default is set higher for
Puget Sound; movers bill roughly $80/hour per mover). Every rate is
overridable from `.env` because they vary enormously by metro.

**This never reaches a customer.** Not in the API response, not in the
confirmation email, not on the thank-you page. HaulChime is a referral service:
the partner inspects the job, quotes it, and agrees the price with the customer
directly. The figure exists so you can decide what a lead is worth to a
partner. It renders only on the admin lead page, inside a red-bordered
"Admin only" card, and a test asserts that no price, cost or estimate can leak
into the public response.

### Confirmation

The thank-you page now leads with a clear "Thank you!", states that a local
partner will contact them shortly, and shows a three-step timeline
(received → picked up → they contact you) alongside the reference number,
service, contact number and preferred contact method. The customer
confirmation email was rewritten to match. The receipt travels in
sessionStorage rather than the URL, so a phone number never lands in browser
history or a referrer header.

---

## 0.2.0 — August 3, 2026

- Removed customer-facing lead prices, cost estimates and estimated job values.
- Simplified the customer quote process from five steps to four.
- Made only the key customer and job fields mandatory.
- Made photos, dates, email, property type, access and extra notes optional.
- Made SMS phone verification mandatory before submission.
- Restored the private Bird and verification settings from the supplied PestChime environment.
- Rebuilt the verification controls to prevent button and text overlap.
- Added responsive verification and action-button layouts for narrow screens.
- Changed internal scoring from estimated job value to job scope, difficulty and information quality.
- Updated customer copy, partner copy and the thank-you page.
- Added tests for optional fields and mandatory OTP verification.

## 0.1.0 — August 3, 2026

- Converted the original lead-generation codebase into a separate HaulChime MVP.
- Added a new customer-facing layout and brand system.
- Added branching moving, junk-removal and hauling questionnaires.
- Added pickup/destination addresses, inventory, access, dates and photos.
- Added internal Standard, High Value and Premium lead profiles.
- Added prepaid credit and partner capability fields.
