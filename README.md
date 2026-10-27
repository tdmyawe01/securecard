# SecureCard — fixed version

A Flask demo for credit-card fraud detection. A transaction is checked by an
XGBoost model plus rule checks (distance from home, balance, velocity, large
debits, and spending jumps). Any flag blocks the transaction and leaves the
balance unchanged; otherwise the amount is deducted.

## What was fixed from the uploaded version

- **Removed the hardcoded email credential** from `app.py`. Email settings now
  come only from environment variables (`SENDER_EMAIL` / `SENDER_PASS`). If
  they are not set, fraud emails are skipped instead of crashing.
  **Important: revoke the old Gmail app password from the original upload —
  it was exposed in that file and should be treated as compromised.**
- **Stronger password storage.** Passwords and balance PINs now use salted
  Werkzeug hashes instead of plain unsalted SHA-256. Old SHA-256 hashes still
  verify once, and are automatically upgraded to the new format on a
  successful login / PIN check.
- **Persistent secret key.** `FLASK_SECRET_KEY` comes from the environment
  (or `.env`), so sessions no longer break on every restart.
- **No debug mode.** The server runs with `debug=False`.
- **Paths are relative to the app file**, not the current working directory.
- **`init_db.py` is no longer destructive by default.** It uses
  `CREATE TABLE IF NOT EXISTS` and adds the `balance_pin` column if missing.
  A full reset now needs `python init_db.py --reset` plus typing `YES`.
- **`clear_db.py` now deletes transactions as well as users**, so no orphaned
  transactions remain.
- **Stricter `/predict` validation** for amount, transaction type, and location.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set FLASK_SECRET_KEY to a long random value
python app.py
```

Then open http://127.0.0.1:5000

Register with a card number, email, pincode, strong password, and a 4–6 digit
balance PIN. The balance is hidden on the dashboard until you enter that PIN.

## Notes

- This is a demo, not a production banking system. The ML features are
  simplified, and the balance PIN is short by design.
- Existing demo users from the old database keep working, but they have no
  balance PIN until they re-register — the PIN check will fail for them.
- Do not commit `.env`, and do not reuse the old exposed email credential.
