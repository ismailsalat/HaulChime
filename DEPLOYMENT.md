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

## 3. Photo storage — do this before you take real leads

Railway's container filesystem is **ephemeral**. Anything written to
`backend/uploads/` disappears on the next deploy. The Postgres service already
has `postgres-volume`, but that belongs to Postgres — the API service has no
storage of its own until you give it some.

Until this is done, customer photos are lost every time you push.

### Option A — Railway Volume (do this now, it takes a minute)

1. API service → **Settings → Volumes → New Volume**, mount path `/data`.
2. Variables → add:
   ```
   STORAGE_BACKEND=local
   UPLOAD_DIR=/data/uploads
   ```

Fine for one service. The limit is that a volume attaches to a single service,
so you can't scale horizontally later, and backups are yours to arrange.

### Option B — Cloudflare R2 (better once volume grows)

No egress fees, which matters when partners open photos all day.

1. Cloudflare → **R2 → Create bucket** (`haulchime-photos`). **Leave it
   private.** Do not enable public access.
2. **Manage R2 API Tokens → Create**, Object Read & Write on that bucket.
3. Railway variables:
   ```
   STORAGE_BACKEND=s3
   S3_BUCKET=haulchime-photos
   S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   S3_REGION=auto
   S3_ACCESS_KEY_ID=...
   S3_SECRET_ACCESS_KEY=...
   ```

The code never makes an object public. `/api/photos/<key>` redirects to a
pre-signed URL that expires in 15 minutes. These photos show the inside of
customers' homes next to their addresses; a public bucket here is a privacy
incident waiting to happen. Same settings work for AWS S3 and Backblaze B2.

---

## 4. Deploy the static site as a second Railway service

The repo is set up for two services in one project. The root `railway.json`
builds the backend; `frontend/railway.json` builds the site.

1. In the same Railway project: **New → GitHub Repo →** pick `HaulChime`
   again. You now have a second service from the same repo.
2. On that new service: **Settings → Root Directory** → `frontend`.
   Railway then reads `frontend/railway.json`, runs `node build.js`, and
   serves `dist/` with `server.js`.
3. **Variables** on the frontend service:

   ```
   API_URL=https://api.haulchime.com
   SITE_URL=https://haulchime.com
   PUBLIC_EMAIL=hello@haulchime.com
   PUBLIC_REGION=South King County, WA
   ```

   `build.js` reads these at build time and bakes `window.HAULCHIME_API` into
   every page. `site.local.json` is your local-only file and is gitignored, so
   without `API_URL` set here the deployed site would fall back to
   `http://localhost:5002` and every quote submission would fail.

4. **Settings → Networking → Custom Domain** → add `haulchime.com`, then add
   `www.haulchime.com`. Railway gives you a CNAME target for each — copy both.

> Changing `API_URL` requires a **redeploy**, not just a restart. The value is
> compiled into the HTML at build time.

---

## 5. Namecheap DNS

`api.haulchime.com` is already done and verified. Two records remain, both
pointing at the **frontend** service.

Namecheap → **Domain List → Manage → Advanced DNS**.

### First, delete the parking records

A fresh Namecheap domain ships with two records that will fight yours:

- `CNAME` on host `www` → `parkingpage.namecheap.com`
- `URL Redirect Record` on host `@`

Delete both. Leaving them is the most common reason a domain keeps showing the
parking page after everything else is correct.

### Then add

| Type | Host | Value | TTL |
|---|---|---|---|
| `ALIAS Record` | `@` | frontend target from Railway | Automatic |
| `CNAME Record` | `www` | frontend target from Railway | Automatic |

Your existing `api` CNAME stays exactly as it is.

Things that cost people an hour:

- **Host is the subdomain only.** `www`, not `www.haulchime.com` — Namecheap
  appends the domain, so the full name gives you
  `www.haulchime.com.haulchime.com`.
- **`@` needs ALIAS, not CNAME.** A plain CNAME at the apex is illegal under
  the DNS spec; Namecheap's ALIAS Record exists for exactly this.
- **No trailing dot** in the value.
- Nameservers (Domain tab) must be **Namecheap BasicDNS**. If they point at
  Cloudflare, the Advanced DNS tab is ignored and the records belong there
  instead.

If you'd rather not run the apex, replace the ALIAS with a **URL Redirect
Record** on `@` → `https://www.haulchime.com`, permanent (301), and make `www`
canonical.

### Verify

```bash
dig +short haulchime.com
dig +short www.haulchime.com
curl -sI https://haulchime.com | head -1              # HTTP/2 200
curl -s https://api.haulchime.com/api/config | head   # JSON, not an error
```

Railway issues certificates automatically once DNS resolves; the domain row
turns green. Empty `dig` after 30 minutes means the record didn't save or the
nameservers aren't Namecheap's.

---

## 5b. Point the API back at the site

Once the domains resolve, set these on the **API** service and redeploy:

```
SITE_URL=https://haulchime.com
ALLOWED_ORIGINS=https://haulchime.com,https://www.haulchime.com
```

`ALLOWED_ORIGINS` is the CORS allowlist and it must match the browser's origin
**exactly** — scheme included, no trailing slash, and `www` listed separately
because a browser treats it as a different origin. If the quote form fails
after go-live with nothing in the server logs, this is almost always why: open
the browser console and look for a CORS error.

---

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
