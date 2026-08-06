# Partner portal — what changed, how to ship it, how to undo it

Everything below is implemented and tested: **41 backend tests, 18 DOM tests.**

---

## 1. What changed

**Partner portal.** A company applies at `/partner/apply`, verifies its mobile
number by SMS, and waits for review. You approve from
`/admin/partner-applications`. Approved partners sign in with an SMS code at
`/partner/login` and see only the leads you assign them.

**Eligibility engine.** One server-side function decides whether a partner can
take a lead, checking fourteen conditions and returning *eligible*, *needs
review*, or *not eligible*. The middle state exists because "the customer said
flexible" is not the same as "the partner is free" — treating unknown as fine
would quietly assign jobs to unavailable partners.

**Assignment with real confirmation.** `/admin/leads/<id>/assign` ranks every
partner best-first with a per-condition table using icons *and* text.
Assigning anyone with a warning is refused with a 400 and a panel listing
every reason, an acknowledgement checkbox and an explicit "Assign anyway".
Both the blocked attempt and the override go into the audit trail.

**Two screenshot bugs fixed.** Duplicate "Choose File" controls were caused by
a stale `.photo-drop input{display:block}` rule overriding the `hidden`
attribute. Duplicate REQUIRED badges were two helpers each rendering one; a
section now shows at most one required badge, and fields carry real `required`
and `aria-required` so validation never depends on a badge.

**Money is Decimal.** The cost breakdown now sums exactly to the total. No
negatives, no NaN, low ≤ mid ≤ high, margin clamped below 1.

**Ad landing page.** `/quote` is now a bare page — brand bar, one trust line,
the form. The only links on it are Privacy and Terms.

**Email.** Company address is `asalat@haulchime.com`. The new-lead email is
short and links to the admin page rather than carrying customer details.

---

## 2. Files changed

**New**

```
backend/partner_eligibility.py        eligibility engine
backend/partner_delivery.py           lead delivery to partners (email + SMS)
backend/routes/partner.py             portal: apply, login, leads, availability, profile
backend/routes/admin_partners.py      application review + assignment
backend/templates/partner/*.html      7 portal templates
backend/templates/admin/assign_panel.html
backend/templates/admin/partner_applications.html
backend/templates/admin/partner_application_detail.html
frontend/test/quote-form.test.js      18 DOM tests
frontend/site.prod.json               production build config
firebase.json / .firebaserc           Firebase Hosting
.python-version                       pins Python 3.12
```

**Changed**

```
backend/models.py                     7 new tables, 5 new Partner columns
backend/job_costing.py                Decimal money, invariants
backend/config.py                     Smarty, storage, costing, email defaults
backend/app.py                        registers the partner blueprint
backend/routes/admin.py               typed DELETE gate, registers admin_partners
backend/routes/public.py              lead_id in email context
backend/storage.py                    S3Storage implemented
backend/templates/admin/base.html     confirmation handler, Applications nav
backend/templates/admin/lead_detail.html   assign panel, typed delete
backend/templates/emails/*.txt        rewritten
backend/tests/test_app.py             41 tests
frontend/static/js/main.js            badges, photo picker, address blocks
frontend/static/css/styles.css        [hidden] fix, portal + landing styles
frontend/build.js                     bare layout, --prod flag
.github/workflows/ci.yml              DOM tests, fixed secret scan
```

---

## 3. Database migration

**No commands to run.** `app.py` runs a schema check on boot that creates
missing tables and `ALTER TABLE`s missing columns. Deploying to Railway
applies it.

New tables: `partner_applications`, `partner_accounts`, `partner_availability`,
`partner_time_off`, `lead_assignments`, `partner_activity`,
`partner_notifications`.

New `partners` columns: `jobs_not_accepted`, `taking_leads`,
`minimum_notice_hours`, `same_day_ok`, `approved_at`.

New `leads` columns: `cost_breakdown`, `cost_confidence`.

Nothing is dropped or renamed, so **existing partners and leads are
untouched**. Partners you created manually keep working — they simply have no
application attached, which the eligibility engine treats as fine rather than
as a failure.

Take a backup first anyway: Railway → Postgres → Data → snapshot, plus
**Settings → Download backup** in the admin.

---

## 4. Railway environment variables

Nothing new is *required*. Confirm these are set:

```
ADMIN_NOTIFY_EMAIL=asalat@haulchime.com
MAIL_BACKEND=smtp            # or resend — "console" sends nothing
SITE_URL=https://haulchime.com
ALLOWED_ORIGINS=https://haulchime.com,https://www.haulchime.com
STORAGE_BACKEND=local
UPLOAD_DIR=/data/uploads     # your volume, or photos vanish each deploy
BIRD_API_KEY=...             # partner SMS login needs this
```

`SITE_URL` now matters more than before: it builds the admin link in the
new-lead email, the partner sign-in link in assignment texts, and the API URL
the partner application page calls for SMS verification.

---

## 5. Local setup and tests

```bash
# Backend
cd backend
pip install -r requirements.txt
HAULCHIME_QUIET_STARTUP=1 python -m pytest tests/ -q     # 41 tests

# Frontend
cd frontend
npm install
npm test                                                  # build + 18 DOM tests

# Run it
cd backend && python -m flask --app app run --port 5002
cd frontend && node build.js && npx serve dist -l 8080
```

---

## 6. Deploying

**Backend (Railway):** push to `main`. CI runs both suites; Railway deploys on
green and applies the schema changes on boot.

**Frontend (Firebase):**

```powershell
cd frontend
npm run deploy          # build --prod + firebase deploy --only hosting
```

The portal and admin are served by Railway, not Firebase — they're Flask
templates. Partners use `https://api.haulchime.com/partner/login`. If you'd
rather they used `partners.haulchime.com`, add that as a second custom domain
on the Railway API service and point a CNAME at it.

---

## 7. Manual test checklist

**Application**
- [ ] `/partner/apply` loads; submit is disabled until the phone is verified
- [ ] "Text me a code" arrives; entering it enables submit
- [ ] Submitting shows "Application received"
- [ ] `asalat@haulchime.com` gets the new-application email with a review link
- [ ] The application appears at `/admin/partner-applications` as pending

**Approval**
- [ ] Set credit, max lead price, per-lead price and daily limit, then approve
- [ ] The partner appears in the normal admin partner list, fully editable
- [ ] Their weekly schedule carried across from the application

**Partner sign-in**
- [ ] `/partner/login` with the verified number sends a code and signs in
- [ ] An unknown number gives the same neutral "if that number is on an
      account" message (it must not confirm who is a partner)
- [ ] A pending applicant sees the review message, not the portal

**Assignment**
- [ ] `/admin/leads/<id>/assign` lists partners eligible → review → not eligible
- [ ] An eligible partner assigns with a single normal confirmation
- [ ] An ineligible one is refused, lists every reason, and needs the checkbox
- [ ] The lead page shows an "Override" badge afterwards
- [ ] The partner gets the SMS, and it contains **no customer details**

**Partner lead flow**
- [ ] Before accepting: no name, phone, email or exact address anywhere
- [ ] Accept reveals them plus working Call and Text buttons
- [ ] Decline requires a reason; it shows in the admin
- [ ] Status updates save and appear in the admin activity log
- [ ] **Sign in as partner B and open partner A's reference — must be 404**

**Quote form**
- [ ] No "Choose File / No file chosen" controls anywhere
- [ ] Photos add, preview, remove, and refuse duplicates
- [ ] No section shows REQUIRED twice
- [ ] `/quote` opens directly, refreshes cleanly, has no nav links
- [ ] A full submission still reaches the admin

**Nothing broke**
- [ ] Existing leads and partners still visible and editable
- [ ] Admin login, photo upload, backup download all still work

---

## 8. Rollback

Every change is additive, so rollback is a revert — no data restore needed in
the normal case.

**One bad deploy:** Railway → Deployments → the previous green build →
**Redeploy**. Roughly a minute.

**Back out the whole portal:**

```bash
git revert --no-commit 38b3ee3 7444d8f b506638
git commit -m "Revert partner portal"
git push
```

The new tables stay behind, empty and unreferenced. That's deliberate: dropping
them would destroy application and assignment history for no benefit, and they
cost nothing idle. Existing partners and leads are unaffected because nothing
was migrated out of them.

**Frontend:** Firebase console → Hosting → Release history → **Rollback**.

**Emergency stop without a deploy:** set a partner's `active` to false in the
admin to cut off their portal access immediately, or suspend the application.

---

## 9. Known limitations

- **The portal is served from the API domain.** Partners sign in at
  `api.haulchime.com/partner/login`. Works fine; a `partners.haulchime.com`
  CNAME would read better.
- **Rate limiting is in-memory.** Correct on one Railway instance; scaling to
  multiple workers needs a Redis-backed limiter. Same caveat as before this
  change.
- **The daily lead limit counts assignments, not accepted jobs.** A partner
  who declines still consumes their daily slot. Deliberate for now — it stops
  one partner being flooded — but worth revisiting.
- **Approval doesn't email the applicant.** They see the status when they sign
  in. Wire an email or SMS in `_approve()` when you're ready.
- **Photos in the portal show a count, not thumbnails.** Serving them to
  partners needs a signed URL scoped to the assignment; not built yet.
- **Application resubmission after "changes requested"** reuses the same form
  but doesn't pre-fill it.
- **Consent and partner terms wording still needs a lawyer** before you
  advertise. You are sharing personal contact details with third parties and
  sending automated SMS; TCPA penalties are per message.
