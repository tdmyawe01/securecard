# Deploy SecureCard to Render

This folder is the clean, public-ready copy: fresh database, no original
demo users, and no `.env` file included.

## 1. Put this code in a GitHub repo

Render deploys from a Git repo, not from a zip directly.

- Create a new repo on GitHub (it can be private).
- Upload all files in this folder to it — easiest is GitHub's web UI:
  "Add file" > "Upload files", then drag the files in.
- Do **not** upload `.env` or `users.db` (they are not in this package).

## 2. Create the service on Render

- Sign up / log in at render.com.
- Dashboard > New > Web Service > "Build and deploy from a Git repository"
  > connect the repo you just created.
- Render should pick up `render.yaml` automatically if you use
  New > Blueprint instead, or you can set these manually:
  - Environment: Python
  - Build command: `pip install -r requirements.txt`
  - Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Environment variables (Render sets `PORT` itself):
  - `FLASK_SECRET_KEY`: let Render generate one, or paste a long random value.
    `render.yaml` already sets this to `generateValue: true`.
  - Leave `SENDER_EMAIL` / `SENDER_PASS` unset unless you want fraud emails,
    and then use a fresh Gmail app password — never the one from the
    original upload, which should be revoked.

## 3. Deploy and open it

- Click "Create Web Service" and wait for the build to finish.
- Render gives you a public URL like `https://securecard.onrender.com`.
- Open it, register a fresh account there, and test a transaction.
  Old demo accounts from the local copy will not work here — this copy
  starts with an empty database by design.

## Notes

- On Render's free tier, the service sleeps after inactivity, so the first
  visit after a pause can be slow. The filesystem is also ephemeral, so
  the SQLite database resets on redeploy/restart. That is fine for a demo;
  a persistent demo needs a Render Disk (paid) or moving to Postgres.
- If the build fails, check the Render build log for the Python version
  or a missing wheel, and tell Muse the exact error — the local copy
  was tested with Flask, scikit-learn, XGBoost, geopy, and gunicorn.
