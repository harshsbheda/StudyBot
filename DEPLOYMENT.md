# StudyBot Deployment Notes

## Keep These Secret

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `SECRET_KEY`
- `JWT_SECRET`
- `GOOGLE_CLIENT_SECRET`
- `FIREBASE_SERVICE_ACCOUNT_JSON`
- `FIREBASE_SERVICE_ACCOUNT_JSON_RAW`
- `SMTP_PASS`
- `DB_PASSWORD`
- `BOOTSTRAP_ADMIN_PASSWORD`

## Safe To Expose Publicly

- `GOOGLE_CLIENT_ID`
- Your frontend URL / domain
- Firebase web app config values if you later add the Firebase browser SDK

## Recommended Deploy Shape

- Serve the existing `frontend/` from this Flask backend on the same domain.
- Keep all AI keys, Firebase admin credentials, SMTP credentials, and JWT secrets only in host environment variables.
- Use `/api` on the same origin so the browser never needs your server secrets.

## Alternative Deploy Shape: Vercel Frontend + Railway Backend

Use this when you want the static site on Vercel and the Flask API on Railway.

1. Deploy the backend on Railway from the repo root `/`.
2. Add all backend secrets in Railway Variables.
3. Create a Railway volume mounted at `/app/backend/uploads`.
4. Generate a Railway public domain and test `/api/health`.
5. In `frontend/config.js`, set `window.STUDYBOT_API_BASE` to your Railway backend URL plus `/api`.
6. Deploy the `frontend/` directory to Vercel as a static site.
7. Add your Vercel frontend origin to Railway `CORS_ALLOWED_ORIGINS`.
8. Add the same Vercel frontend origin to `GOOGLE_ALLOWED_ORIGINS`.
9. Add `https://your-vercel-domain/auth/google/callback` to `GOOGLE_ALLOWED_REDIRECTS`.
10. Add the same Google OAuth values in Google Cloud Console.

Example `frontend/config.js`:

```js
window.STUDYBOT_API_BASE = "https://your-backend.up.railway.app/api";
```

Example backend env values for split domains:

```env
CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app
GOOGLE_ALLOWED_ORIGINS=https://your-frontend.vercel.app
GOOGLE_ALLOWED_REDIRECTS=https://your-frontend.vercel.app/auth/google/callback
```

## Before You Push Publicly

1. Rotate any secrets that were ever committed or stored in tracked files.
2. Make sure `backend/.env`, `backend/venv`, uploads, and service-account JSON files are not pushed.
3. Replace placeholder values in [backend/.env.example](/e:/StudyBot/backend/.env.example).

If these files were already tracked by git, untrack them before your next public push:

```powershell
git rm --cached backend/.env
git rm -r --cached backend/venv
git rm -r --cached backend/uploads
```

If you ever tracked a Firebase key file, untrack that too:

```powershell
git rm --cached path\to\your-firebase-service-account.json
```

## Production Env Setup

- Set strong random values for `SECRET_KEY` and `JWT_SECRET`.
- Set `FIREBASE_PROJECT_ID`.
- Use `FIREBASE_SERVICE_ACCOUNT_JSON_RAW` on hosted platforms that only support text secrets.
- Use `FIREBASE_SERVICE_ACCOUNT_JSON` only for a local file path on your own machine/server.
- Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
- Update `GOOGLE_ALLOWED_ORIGINS` to your live site URL.
- Set `CORS_ALLOWED_ORIGINS` only if frontend and backend are on different domains.
- Set `SMTP_*` if you want signup OTP and password reset email to work.

## First Admin Account

For a brand-new deploy, set these once:

- `BOOTSTRAP_ADMIN_EMAIL`
- `BOOTSTRAP_ADMIN_PASSWORD`
- Optional: `BOOTSTRAP_ADMIN_NAME`

After the first successful startup and admin login, remove `BOOTSTRAP_ADMIN_PASSWORD` from the host secrets.

## Important

- Google OAuth client ID is public; Google OAuth client secret is not.
- Firebase Admin SDK credentials are secret; do not put them in frontend code.
- If a secret has already been committed, `.gitignore` alone is not enough. Rotate the secret and make sure it is removed from future commits.
