# HaulChime — start the whole website, step by step

Two halves:

- **API + admin** → Railway (`api.haulchime.com`) — already deployed
- **Website** → Firebase Hosting (`haulchime.com` + `www`) — this guide

Follow in order. Copy-paste each command exactly. Anything in `CAPITALS` is
something you replace.

---

## Step 1 — Fix the failed Railway build (2 minutes)

The build failed on this line:

```
ERROR: No matching distribution found for psycopg-binary==3.2.1
```

Railway picked the newest Python (3.13+), and neither `psycopg[binary] 3.2.1`
nor `Pillow 10.4` ship a prebuilt wheel for it. Three files fix it, and they're
already changed in the zip:

- `.python-version` → `3.12` (new file at the repo root — this is the real fix)
- `backend/requirements.txt` → `psycopg[binary]==3.2.9`, `Pillow==11.0.0`

Push them:

```powershell
cd C:\Users\caano\Desktop\HaulChime
git add .python-version backend/requirements.txt
git commit -m "Pin Python 3.12 and bump psycopg/Pillow for wheel availability"
git push
```

Railway redeploys automatically. Watch **Deployments** until it goes green.
Then check it's alive:

```powershell
curl.exe https://api.haulchime.com/api/config
```

You should get JSON. If you get an error, open **View logs** and read the last
20 lines — the app prints a plain-English readiness report on startup.

---

## Step 2 — Confirm the volume is wired up

You added a volume at `/data`. It only does anything if the app is told to use
it. Railway → **HaulChime service → Variables**, confirm both exist:

```
STORAGE_BACKEND=local
UPLOAD_DIR=/data/uploads
```

Without `UPLOAD_DIR`, photos still write to the container and still vanish on
the next deploy. The volume being attached is not enough on its own.

While you're in Variables, confirm these too:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `APP_ENV` | `production` |
| `SESSION_COOKIE_SECURE` | `true` |
| `SECRET_KEY` | a **new** random value, not the dev one |
| `SITE_URL` | `https://haulchime.com` |
| `ALLOWED_ORIGINS` | `https://haulchime.com,https://www.haulchime.com` |
| `MAIL_BACKEND` | `smtp` or `resend` — **not** `console` |

`console` only prints emails to the log. Nothing is sent to anyone.

---

## Step 3 — Install the Firebase CLI (once)

In PowerShell:

```powershell
npm install -g firebase-tools
firebase --version
```

If `npm` isn't recognised, install Node.js from <https://nodejs.org> (LTS),
close PowerShell, reopen it, and try again.

---

## Step 4 — Create the Firebase project

1. Go to <https://console.firebase.google.com> → **Create a project**
2. Name it `haulchime` (or anything — you'll see the real project ID on the
   next screen, e.g. `haulchime-4f21c`)
3. Google Analytics: **off**. You don't need it and it adds a consent burden.
4. When it's created, note the **Project ID** exactly. Not the display name —
   the ID.

---

## Files that belong to you, not to a zip

These hold your own settings. If you ever copy files in from an archive,
leave these alone or you will overwrite live configuration:

| File | What it holds |
|---|---|
| `.firebaserc` | your Firebase project ID (`haulchime`) |
| `backend/.env` | live Smarty, Bird and admin credentials |
| `frontend/site.local.json` | your local dev URLs |
| `.git/` | your commit history and remotes |

---

## Step 5 — Log in and point the repo at your project

```powershell
cd C:\Users\caano\Desktop\HaulChime
firebase login
```

A browser opens; sign in with the Google account that owns the project.

Now open `.firebaserc` in your editor and replace the placeholder with your
real project ID:

```json
{
  "projects": {
    "default": "haulchime-4f21c"
  }
}
```

Confirm the CLI agrees:

```powershell
firebase projects:list
```

Your project should be in that list.

---

## Step 6 — Build and deploy the site

```powershell
cd C:\Users\caano\Desktop\HaulChime\frontend
node build.js --prod
cd ..
firebase deploy --only hosting
```

`--prod` makes the build read `frontend/site.prod.json`, which points the site
at `https://api.haulchime.com`. Without that flag it reads your local config
and the live site would try to call `http://localhost:5002` — the form would
fail silently for everyone.

Verify before moving on. The build prints the API it baked in:

```
Built HaulChime to ...\frontend\dist
API: https://api.haulchime.com        <- must say this, not localhost
```

When the deploy finishes, the CLI prints a **Hosting URL** like
`https://haulchime-4f21c.web.app`. Open it. The site should load and the quote
form should work end to end — including the SMS code — because the API is
already live on its own domain.

**Test the whole flow now, on the .web.app URL, before touching DNS.** If
something's broken, you want to find it here rather than after your domain is
pointing at it.

---

## Step 7 — Connect haulchime.com to Firebase

In the Firebase console: **Build → Hosting → Add custom domain**.

1. Enter `haulchime.com`. Tick **Also set up www.haulchime.com** (Firebase
   offers a redirect from `www` → apex; take it).
2. Firebase shows a **TXT record** to prove you own the domain.
3. In another tab: Namecheap → **Domain List → Manage → Advanced DNS**.

### First, delete the parking records

Namecheap ships every new domain with these two, and they will fight yours:

- `CNAME Record`, host `www`, value `parkingpage.namecheap.com`
- `URL Redirect Record`, host `@`

**Delete both.** Leaving them is the number one reason a domain keeps showing
the parking page long after everything else is right.

Leave your existing `api` CNAME alone — that's Railway and it's working.

### Add the verification record

| Type | Host | Value | TTL |
|---|---|---|---|
| `TXT Record` | `@` | the string Firebase gave you | Automatic |

Save, then go back to Firebase and click **Verify**. This usually takes a few
minutes. If it fails, wait 10 minutes and click again — DNS is not instant.

### Add the hosting records

Once verified, Firebase shows the final records — normally **two A records**
for the apex. Add exactly what your console shows:

| Type | Host | Value | TTL |
|---|---|---|---|
| `A Record` | `@` | first IP from Firebase | Automatic |
| `A Record` | `@` | second IP from Firebase | Automatic |

Yes, two records with the same host. That's correct and intentional.

If Firebase also gives you records for `www`, add those with host `www`.

> Use the values Firebase shows you, not values from a blog post. Firebase's
> hosting IPs have changed before, and a stale IP produces a site that
> resolves but never loads.

---

## Step 8 — Wait, then verify

Certificate provisioning takes anywhere from 15 minutes to a few hours.
Firebase shows the domain as **Pending** and then **Connected**.

Check from PowerShell:

```powershell
nslookup haulchime.com
curl.exe -I https://haulchime.com
curl.exe -I https://www.haulchime.com
curl.exe https://api.haulchime.com/api/config
```

You want `HTTP/2 200` from the first two and JSON from the last.

---

## Step 9 — Final go-live checks

Once `https://haulchime.com` loads:

- [ ] Submit a real test quote end to end, including receiving the SMS code
- [ ] Confirm the lead appears at `https://api.haulchime.com/admin`
- [ ] Attach a photo, then **redeploy the backend** and confirm the photo is
      still there (this proves the volume works)
- [ ] On the lead page, use **Send now** to check the partner gets both the
      email and the text
- [ ] Change the admin password from whatever you used in development
- [ ] Open the browser console on the live site — no red CORS errors

If the form fails with a CORS error, `ALLOWED_ORIGINS` doesn't match the
browser's origin exactly. It must list both `https://haulchime.com` and
`https://www.haulchime.com`, with no trailing slashes.

---

## Deploying changes later

**Website change** (anything in `frontend/`):

```powershell
cd C:\Users\caano\Desktop\HaulChime\frontend
npm run deploy
```

That runs the production build and the Firebase deploy in one go.

**API change** (anything in `backend/`): just push to GitHub. Railway
redeploys `main` automatically.

```powershell
git add -A
git commit -m "what you changed"
git push
```

---

## Why the site is on Firebase and the API is on Railway

Firebase Hosting is a CDN built for static files — it's free at your volume,
fast worldwide, and handles certificates for you. The API can't live there
because it needs a database, a filesystem and a long-running process, which is
exactly what Railway is for.

The one thing to remember: they're separate deploys. Pushing to GitHub updates
the API but **not** the website. The website only changes when you run
`npm run deploy`.
