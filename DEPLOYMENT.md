# Deploying HaulChime

Two pieces go live: a **Flask API + admin** (Railway) and a **static site**
(Railway, Cloudflare Pages or Netlify). They talk over HTTPS, so the API has to
allow the site's origin explicitly.

Work through this in order. Skipping step 0 will publish your Smarty and Bird
keys to the internet.

---

## 0. The repo is already initialised

The zip contains a working git repository: `main` branch, one commit, tagged
`v1.0.0`. `backend/.env` is **not** in it — verified before the commit was
made. Confirm for yourself:

```bash
cd HaulChime-MVP-v0.2.0
git log --oneline          # ee8c50c HaulChime v1.0.0
git ls-files | grep .env   # prints nothing
```

That file holds your live Smarty pair, the Bird SMS key, the admin password
hash and the Flask secret. Keeping it out of git is the whole reason the
`.gitignore` exists.

If a secret ever does reach a remote, deleting it in a later commit is **not
enough** — it stays in the history and in every clone and fork. You'd have to
rotate all of it: regenerate the Smarty secret pair, roll the Bird key, change
the admin password, generate a new `SECRET_KEY`. Bots watch GitHub's public
event firehose for exactly this and find keys within minutes.

The CI workflow fails the build if `.env` is ever tracked or a key-shaped
string appears in a tracked file. It's a backstop, not a substitute for
looking.

---

## 1. Push to GitHub

Create an **empty private** repository at github.com/new — no README, no
`.gitignore`, no licence. You already have all three, and adding them creates
a conflict on the first push.

Then, from the project folder:

```bash
git remote add origin https://github.com/YOUR-USERNAME/haulchime.git
git push -u origin main
git push origin v1.0.0
```

If GitHub asks for a password, it wants a **personal access token**, not your
account password: github.com → Settings → Developer settings → Personal access
tokens → Tokens (classic) → Generate, with the `repo` scope. Paste the token
where it asks for the password.

**Private, not public.** The repo contains your admin interface, your
lead-scoring logic and your internal cost model. None of that helps anyone but
a competitor.

The bundled `backend/.venv/` is a Windows virtualenv — 127 MB of
machine-specific binaries that would only break on Linux. It's gitignored, so
it stays on your machine and out of the repo. Your `RUN_WINDOWS.bat` still
uses it locally.

---

## 2. Deploy the API on Railway

1. **New Project → Deploy from GitHub repo →** pick your repo.
2. Railway reads `railway.json` at the root and builds the backend.
3. **Add a database:** in the project, **New → Database → PostgreSQL**.
   Then on the API service, add a variable:

   ```
   DATABASE_URL = ${{Postgres.DATABASE_URL}}
   ```

   That `${{...}}` reference syntax is Railway's — it wires the two services
   together and survives credential rotation. SQLite would be wiped on every
   deploy, so this is not optional in production.

4. **Set the variables.** Copy them from your local `backend/.env`, but change
   these four:

   | Variable | Value |
   |---|---|
   | `SECRET_KEY` | a **new** random value — `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
   | `APP_ENV` | `production` |
   | `SESSION_COOKIE_SECURE` | `true` (you're on HTTPS now) |
   | `SITE_URL` | `https://yourdomain.com` |
   | `ALLOWED_ORIGINS` | `https://yourdomain.com,https://www.yourdomain.com` |

   `ALLOWED_ORIGINS` is the CORS allowlist. If the site can't submit quotes
   after going live, this is nearly always why — check for a trailing slash or
   a missing `www`.

   Everything else (`BIRD_API_KEY`, `SMARTY_AUTH_ID`, `SMARTY_AUTH_TOKEN`,
   `PHONE_VERIFICATION_HMAC_SECRET`, `ADMIN_PASSWORD_HASH`, mail settings)
   carries over as-is.

   > Do not change `PHONE_VERIFICATION_HMAC_SECRET` after go-live. It keys the
   > stored OTP digests; changing it invalidates every verification in flight.

5. **Health check** is already set to `/api/config`. If deploys hang there,
   open the deploy logs — the startup report prints a plain-English readiness
   check for each integration.

---

## 3. Photo storage — pick one

Railway's container filesystem is **ephemeral**. Anything written to
`backend/uploads/` disappears on the next deploy. Customer photos need real
storage.

### Option A — Railway Volume (simplest)

1. On the API service: **Settings → Volumes → New Volume**, mount path `/data`.
2. Add variables:
   ```
   STORAGE_BACKEND=local
   UPLOAD_DIR=/data/uploads
   ```

Done. Good for a single service. The catch: a volume attaches to one service,
so you can't scale to multiple instances, and backups are yours to arrange.

### Option B — Cloudflare R2 (recommended once you have real volume)

R2 has no egress fees, which matters when partners are opening photos all day.

1. Cloudflare dashboard → **R2 → Create bucket** (e.g. `haulchime-photos`).
   **Leave it private.** Do not enable public access.
2. **Manage R2 API Tokens → Create** with Object Read & Write on that bucket.
3. Add variables on Railway:
   ```
   STORAGE_BACKEND=s3
   S3_BUCKET=haulchime-photos
   S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   S3_REGION=auto
   S3_ACCESS_KEY_ID=...
   S3_SECRET_ACCESS_KEY=...
   ```

The code never makes an object public. `/api/photos/<key>` issues a pre-signed
URL that expires after 15 minutes (`S3_URL_EXPIRY_SECONDS`). These photos show
the inside of customers' homes next to their addresses — a public bucket here
is a privacy incident waiting to happen, and the same settings work unchanged
for AWS S3 or Backblaze B2 if you'd rather use those.

---

## 4. Deploy the static site

The frontend is plain HTML/CSS/JS built by `node build.js`. Any static host
works. Two good options:

### Cloudflare Pages
- Connect the repo.
- **Build command:** `cd frontend && node build.js`
- **Output directory:** `frontend/dist`
- **Environment variable:** none needed, but see the API URL note below.

### Railway (second service, same project)
- **New → GitHub Repo →** same repo, then **Settings → Root Directory:**
  `frontend`, **Start command:** `npm start`.

### Pointing the site at the API

`frontend/site.local.json` is gitignored (it's your local config). For the
build to bake in the production API URL, either commit a
`frontend/site.prod.json` and switch `build.js` to read it, or simpler — set
these before the build:

```json
{
  "domain": "https://yourdomain.com",
  "apiUrl": "https://api.yourdomain.com",
  "email": "hello@yourdomain.com"
}
```

Whatever `apiUrl` ends up as **must** appear in the API's `ALLOWED_ORIGINS`,
and vice versa.

---

## 5. Domain and DNS on Namecheap

Give the API its own subdomain. Serving both from one hostname means a
frontend deploy can take the API down with it.

| Host | Points at |
|---|---|
| `yourdomain.com` (apex) | static site |
| `www.yourdomain.com` | static site |
| `api.yourdomain.com` | Railway API service |

### Step 1 — get the targets from Railway

On the **API service**: **Settings → Networking → Custom Domain** → enter
`api.yourdomain.com`. Railway shows a CNAME target that looks like
`xxxxx.up.railway.app`. Copy it.

Do the same on the static-site service for `www.yourdomain.com` (and the apex,
if that host supports it).

### Step 2 — open Namecheap's Advanced DNS

Namecheap dashboard → **Domain List** → **Manage** next to your domain →
**Advanced DNS** tab.

Make sure **Nameservers** (on the Domain tab) is set to **Namecheap BasicDNS**.
If you've pointed the nameservers at Cloudflare or anywhere else, the records
below have to be created *there* instead — Namecheap's Advanced DNS tab will
be ignored.

### Step 3 — delete the parking records

A fresh Namecheap domain ships with two records that will fight yours:

- a `CNAME` on host `www` pointing at `parkingpage.namecheap.com`
- a `URL Redirect Record` on host `@`

**Delete both.** Leaving them is the single most common reason a new Namecheap
domain keeps showing the parking page after everything else is correct.

### Step 4 — add your records

Click **Add New Record** for each:

| Type | Host | Value | TTL |
|---|---|---|---|
| `ALIAS Record` | `@` | the static site's target | Automatic |
| `CNAME Record` | `www` | the static site's target | Automatic |
| `CNAME Record` | `api` | `xxxxx.up.railway.app` | Automatic |

Notes that save time:

- **Host is the subdomain only.** Type `api`, not `api.yourdomain.com` —
  Namecheap appends the domain for you. Entering the full name gives you
  `api.yourdomain.com.yourdomain.com`.
- **`@` means the apex.** A plain CNAME is illegal at the apex under the DNS
  spec, which is why Namecheap provides **ALIAS Record** — pick that type, not
  CNAME. If your host only gives you an IP address, use an `A Record` instead.
- **No trailing dot** in the value. Namecheap handles that.
- If you'd rather not run the apex at all, replace the ALIAS with a
  **URL Redirect Record** on `@` → `https://www.yourdomain.com`, permanent
  (301). Then `www` is your canonical site.

### Step 5 — wait, then verify

Namecheap usually propagates in a few minutes but says up to 30. Railway
issues the TLS certificate automatically once it can see the record — the
custom-domain row turns green.

Check from a terminal:

```bash
dig +short api.yourdomain.com          # should show the railway.app target
curl -sI https://api.yourdomain.com/api/config | head -1   # HTTP/2 200
```

If `dig` is empty after 30 minutes, the record didn't save or the nameservers
aren't Namecheap's. If `dig` resolves but `curl` fails with a certificate
error, give Railway another few minutes — it can't issue a certificate until
DNS resolves.

### Step 6 — update the app to match

On Railway (API service):

```
SITE_URL=https://yourdomain.com
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

Then rebuild the frontend with `apiUrl` set to `https://api.yourdomain.com`.

These two have to agree exactly. `ALLOWED_ORIGINS` is the CORS allowlist, and
a trailing slash or a missing `www` will make every quote submission fail with
a browser console error and nothing in the server logs. If the form breaks
right after go-live, check this first.

## 6. Go-live checklist

- [ ] `backend/.env` is **not** in the repo (`git ls-files | grep .env`)
- [ ] `SECRET_KEY` regenerated for production
- [ ] `SESSION_COOKIE_SECURE=true` and `APP_ENV=production`
- [ ] `DATABASE_URL` points at Railway Postgres, not SQLite
- [ ] Photo storage configured (volume or bucket) and a test photo survives a redeploy
- [ ] `ALLOWED_ORIGINS` exactly matches the live site origin
- [ ] Admin login works and the password is not the one from development
- [ ] Submit a real test quote end to end, including the SMS code
- [ ] On the admin lead page, use **Send now** to confirm the partner gets both
      the email and the text
- [ ] Use **Test contact** on the partners page for each partner before they
      depend on it
- [ ] `MAIL_BACKEND` is `smtp` or `resend`, not `console` — `console` only
      prints to the logs and sends nothing
- [ ] Consent wording and the privacy policy reviewed by a lawyer before you
      advertise. You are sharing personal contact details with third parties
      and sending automated texts; TCPA and state equivalents carry real
      per-message penalties, and this starter wording is not legal advice.

---

## 7. Day-to-day

**Deploying a change:** push to `main`. CI runs the tests and the build;
Railway deploys on green. If tests fail, fix them before merging — a lead lost
to a broken form is gone for good.

**Backups:** the admin has **Settings → Download backup** (JSON of leads and
partners) and Railway's Postgres has its own snapshots. Use both. Test a
restore once, before you need it.

**Watching costs:** SMS is the only per-use spend that can run away.
`PHONE_VERIFICATION_GLOBAL_DAILY_LIMIT` caps daily sends and the admin
dashboard shows the counter. Smarty lookups are capped per IP by
`ADDRESS_LOOKUP_LIMIT_PER_HOUR`.
