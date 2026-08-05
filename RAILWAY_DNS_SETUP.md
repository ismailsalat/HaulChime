# HaulChime Railway + DNS setup

This patch fixes the `pip: command not found` build by making Railway detect Python at the repository root and moving the project to Railway's current `RAILPACK` builder. It also adds a dependency-free frontend server and production environment variables for the frontend build.

## 1. Commit the patch

From the HaulChime repository in PowerShell:

```powershell
git add requirements.txt railway.json backend/railway.json frontend/railway.json frontend/server.js frontend/build.js frontend/package.json
git commit -m "Fix Railway deployment and production frontend config"
git push origin main
```

Do not commit `backend/.env`.

## 2. Fastest repair for the existing backend service

In Railway, open the failed backend service and set:

- **Settings -> Build -> Root Directory:** `/backend`
- **Settings -> Build -> Railway Config File:** `/backend/railway.json`
- **Build Command override:** clear it

Redeploy the latest commit. With `/backend` as the root, Railway sees `requirements.txt` and installs Python before the start command runs.

Alternatively, after this patch is pushed, the backend can also deploy from repository root using the root `railway.json` and root `requirements.txt`.

## 3. Add PostgreSQL

In the Railway project:

1. Click **+ New -> Database -> PostgreSQL**.
2. Open the backend service -> **Variables**.
3. Add:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Use the actual database service name if Railway names it something other than `Postgres`.

## 4. Backend production variables

Copy the secret values from your local `backend/.env` into Railway's backend **Variables** tab. Do not upload or commit the `.env` file.

At minimum, set:

```text
APP_ENV=production
SESSION_COOKIE_SECURE=true
SITE_URL=https://haulchime.com
ALLOWED_ORIGINS=https://haulchime.com,https://www.haulchime.com
LOG_FORMAT=json
LOG_STACKTRACES=false
STORAGE_BACKEND=local
UPLOAD_DIR=/data/uploads
```

Also set your real secret values for:

```text
SECRET_KEY
ADMIN_PASSWORD_HASH
PHONE_VERIFICATION_HMAC_SECRET
BIRD_API_KEY
SMARTY_AUTH_ID
SMARTY_AUTH_TOKEN
```

Do not set `DEV_OTP_CODE` in production.

## 5. Persistent photo storage

On the backend service:

1. Open **Settings -> Volumes**.
2. Add a volume mounted at `/data`.
3. Keep these variables:

```text
STORAGE_BACKEND=local
UPLOAD_DIR=/data/uploads
```

Without a volume, uploaded photos disappear when Railway redeploys the container.

## 6. Test the backend before custom DNS

On the backend service, open **Settings -> Networking -> Generate Domain**.

Test:

```text
https://YOUR-BACKEND.up.railway.app/api/config
```

It should return JSON. The admin page should be at:

```text
https://YOUR-BACKEND.up.railway.app/admin
```

## 7. Deploy the frontend as a second Railway service

1. In the same Railway project, click **+ New -> GitHub Repo** and choose the same HaulChime repository.
2. Name the service `frontend`.
3. Set:
   - **Root Directory:** `/frontend`
   - **Railway Config File:** `/frontend/railway.json`
4. Add frontend variables:

```text
SITE_URL=https://haulchime.com
API_URL=https://api.haulchime.com
PUBLIC_EMAIL=hello@haulchime.com
PUBLIC_REGION=South King County, WA
```

5. Deploy and generate a Railway domain.
6. Open the generated domain and make sure the home page and `/quote/` load.

## 8. Add custom domains in Railway

Add these domains:

- Frontend service: `haulchime.com`
- Frontend service: `www.haulchime.com`
- Backend service: `api.haulchime.com`

For every custom domain, Railway displays a routing record and a TXT verification record. Copy both exactly. A domain will not verify with only the routing record.

## 9. Namecheap DNS records

Namecheap -> **Domain List -> Manage -> Advanced DNS -> Host Records**.

Delete parking or redirect records that conflict with `@`, `www`, or `api`.

Create the records Railway gives you. The layout will normally be:

| Type | Host | Value |
|---|---|---|
| ALIAS | `@` | frontend Railway target |
| TXT | exact host Railway shows | exact verification value Railway shows |
| CNAME | `www` | frontend Railway target |
| TXT | exact host Railway shows | exact verification value Railway shows |
| CNAME | `api` | backend Railway target |
| TXT | exact host Railway shows | exact verification value Railway shows |

Use the exact TXT host Railway provides; do not guess it. In Namecheap, the CNAME host is only `www` or `api`, not the full domain.

Wait until Railway shows a green verified check and SSL is issued.

## 10. Final checks

Open:

```text
https://haulchime.com
https://www.haulchime.com
https://api.haulchime.com/api/config
https://api.haulchime.com/admin
```

Then submit one test request with a real phone number and confirm:

- address suggestions work
- the SMS code arrives and verifies
- the lead saves in PostgreSQL
- the admin page shows the lead
- the customer sees the thank-you page
- an uploaded test photo remains after a backend redeploy
