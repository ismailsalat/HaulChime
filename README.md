# HaulChime v1.0.0

HaulChime is a lead-generation platform for moving, junk removal and hauling.
Customers complete a short request and verify their mobile number. A matched
service partner then contacts the customer directly to discuss the job, price,
schedule and final service terms.

HaulChime does **not** quote jobs, does **not** show customers a price, and
does **not** take a percentage of the completed work. An internal cost model
exists so the admin can decide what a lead is worth to a partner — it never
reaches a customer, and a test enforces that.

## Quick start

```bash
# Backend  (http://localhost:5002)
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill it in
python -m flask --app app run --port 5002

# Frontend (http://localhost:8080)
cd frontend
node build.js
npx serve dist -l 8080
```

Windows users can run `RUN_WINDOWS.bat`; macOS and Linux, `RUN_MAC_LINUX.sh`.

Admin: <http://localhost:5002/admin>

## Going live

See **[DEPLOYMENT.md](DEPLOYMENT.md)** — GitHub, Railway with Postgres, photo
storage, and Namecheap DNS, with a go-live checklist.

> Before your first push: `backend/.env` holds live credentials. It is
> gitignored, but confirm with `git status --short | grep .env` (should print
> nothing). A secret pushed to a remote stays in history forever and must be
> rotated, not just deleted.

## How it fits together

| Piece | What it does |
|---|---|
| `frontend/` | Static marketing site and the four-step quote form. Built by `node build.js` into `dist/`. |
| `backend/routes/public.py` | Lead intake, validation and the public config endpoint. |
| `backend/routes/verification.py` | SMS OTP start/complete (codes generated and checked here; Bird only carries the text). |
| `backend/routes/address.py` | Smarty address autocomplete and verification proxy. |
| `backend/routes/admin.py` | Admin dashboard, leads, partners, logs, settings. |
| `backend/scoring.py` | Lead quality tier and the price charged to a partner. |
| `backend/job_costing.py` | **Internal only.** What a job costs to run and is worth. |
| `backend/partner_delivery.py` | Hands a lead to a partner by email and/or SMS. |
| `backend/smarty_client.py` | Smarty US Autocomplete Pro + US Street. Server-side only. |

## Testing

```bash
cd backend && python -m pytest tests/ -q
```

Sixteen tests cover lead intake in both the legacy and current form shapes,
phone-verification enforcement, the cost model's realism and its containment,
the service-area confirmation gate, and partner delivery over both channels.

## Required customer information

Customers must provide:

- Service type
- Pickup street address, city and ZIP code
- Destination street address and ZIP code for moving requests
- Job or load size
- A short list of the main items
- Full name
- Mobile phone number
- Successful text-message verification
- Consent to share the request with a matched provider

Everything else in the form is optional.

# Fastest way to run on Windows

Requirements:

1. Python 3.11 or newer
2. Internet access on the first launch so Python can install the packages

Steps:

1. Extract the ZIP.
2. Double-click `RUN_WINDOWS.bat`.
3. Keep both command windows open.
4. Open `http://localhost:8080` if it does not open automatically.

Admin dashboard:

- URL: `http://localhost:5002/admin`
- Username: `admin`
- Password: `haulchime123`

Change the password before putting the site online.

## Important private-environment warning

This package contains `backend/.env` because you asked to reuse PestChime's
working environment. It includes private credentials such as the Bird API key
and phone-verification secret.

Do not upload the ZIP publicly, commit `.env` to GitHub, or send it to anyone.
Rotate those credentials if the ZIP is ever shared outside your control.

# Testing phone verification locally

Phone verification is required.

For a free local test without sending a real text message, use a fictional U.S.
555 number such as:

```text
(206) 555-0142
```

Enter the development code stored as `DEV_OTP_CODE` in `backend/.env`.

For an actual mobile number, HaulChime uses the Bird credentials copied from the
PestChime environment. A real verification request may send a paid SMS through
your Bird account.

# Manual setup — Windows PowerShell

From the extracted project folder:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd backend
python seed.py
python -m flask --app app run --port 5002
```

Open a second PowerShell window:

```powershell
cd path\to\HaulChime\frontend\dist
..\..\backend\.venv\Scripts\python.exe -m http.server 8080
```

Open:

- Customer site: `http://localhost:8080`
- Admin: `http://localhost:5002/admin`

# macOS or Linux

Run:

```bash
chmod +x RUN_MAC_LINUX.sh
./RUN_MAC_LINUX.sh
```

# Rebuilding the frontend

The generated frontend is already included in `frontend/dist`, so Node.js is not
needed just to run the project.

After editing the frontend source, rebuild it with:

```bash
cd frontend
node build.js
```

# Internal lead tiers

Customers never see lead pricing. The backend still classifies submitted
opportunities internally as Standard, High Value or Premium using:

- Job scope
- Access and handling difficulty
- Information completeness
- Verified contact information
- Timing and intent

These tiers control the price paid by the service partner for the lead. They are
not estimates of the moving or hauling job price.

# Automated tests

```bash
cd backend
python -m pytest -q
```

The test suite covers:

- Lead submission
- Optional questionnaire fields
- Admin pages and partner credit handling
- Internal three-tier pricing
- Mandatory phone verification
- Fictional-number OTP completion

# Before public launch

- Replace the demo partner.
- Change the admin password.
- Confirm the Bird OTP template uses the HaulChime name.
- Configure the live HaulChime frontend and API domains.
- Use PostgreSQL instead of local SQLite.
- Use private object storage for uploaded customer photos.
- Add the final partner login and payment checkout.
- Review the consent, privacy policy, terms and partner agreement with a lawyer.
- Set `SESSION_COOKIE_SECURE=true` when the site uses HTTPS.
