from geopy.geocoders import Nominatim
geolocator = Nominatim(user_agent="credit_card_v2")

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import joblib
import math
import smtplib
import hashlib
import os
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_dotenv():
    """Load KEY=VALUE lines from a local .env file, if one exists."""
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

_load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY") or os.urandom(24)
if not (os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")):
    print("WARNING: No FLASK_SECRET_KEY set; using a random key. Sessions will not survive a restart.")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
model = joblib.load(os.path.join(BASE_DIR, "credit_card_model.pkl"))

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASS = os.environ.get("SENDER_PASS", "")

# ─── DB SETUP ────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "users.db"))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_number     TEXT    UNIQUE,
            email           TEXT,
            password_hash   TEXT,
            home_lat        REAL,
            home_lon        REAL,
            pincode         TEXT,
            balance         REAL    DEFAULT 100000,
            balance_pin     TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_number     TEXT,
            amount          REAL,
            txn_type        TEXT,
            status          TEXT,
            reason          TEXT,
            balance_before  REAL,
            balance_after   REAL,
            distance        REAL,
            ml_prediction   INTEGER,
            ml_probability  REAL,
            fraud_flags     TEXT,
            timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    # Migration: add balance_pin column if it doesn't exist (for existing databases)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN balance_pin TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists
    conn.close()

init_db()

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────
def hash_pw(pw):
    # Salted, deliberately slow hash (Werkzeug / scrypt). Never store plain SHA-256.
    return generate_password_hash(pw)

def check_pw(pw, stored):
    """Verify a password/PIN, accepting legacy unsalted SHA-256 hashes for migration."""
    if not stored:
        return False
    try:
        if check_password_hash(stored, pw):
            return True
    except ValueError:
        pass
    return stored == hashlib.sha256(pw.encode()).hexdigest()

def _needs_rehash(stored):
    return not (stored or "").startswith(("pbkdf2:", "scrypt:"))

def validate_password(pw):
    errs = []
    if len(pw) < 8:               errs.append("At least 8 characters")
    if not re.search(r'[A-Z]', pw): errs.append("One uppercase letter")
    if not re.search(r'[a-z]', pw): errs.append("One lowercase letter")
    if not re.search(r'\d', pw):    errs.append("One number")
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>]', pw): errs.append("One special character")
    return errs

# ─── USER DB ──────────────────────────────────────────────────────────────────
def get_lat_lon(pincode):
    try:
        loc = geolocator.geocode(pincode, timeout=10)
        if loc: return loc.latitude, loc.longitude
    except: pass
    return None, None

def insert_user(card, email, password, pincode, balance_pin):
    lat, lon = get_lat_lon(pincode)
    if lat is None:
        return False, "Invalid pincode or location not found"
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO users (card_number, email, password_hash, home_lat, home_lon, pincode, balance_pin)
            VALUES (?,?,?,?,?,?,?)
        """, (card, email, hash_pw(password), lat, lon, pincode, hash_pw(balance_pin)))
        conn.commit()
        return True, "Registered successfully"
    except sqlite3.IntegrityError:
        return False, "Card number already registered"
    finally:
        conn.close()

def get_user(card):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE card_number=?", (card,)).fetchone()
    conn.close()
    return row

def update_balance(card, new_balance):
    conn = get_conn()
    conn.execute("UPDATE users SET balance=? WHERE card_number=?", (new_balance, card))
    conn.commit()
    conn.close()

# ─── TRANSACTION DB ───────────────────────────────────────────────────────────
def log_txn(card, amount, txn_type, status, reason,
            bal_before, bal_after, distance=None,
            ml_pred=None, ml_prob=None, flags=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO transactions
          (card_number, amount, txn_type, status, reason,
           balance_before, balance_after, distance, ml_prediction, ml_probability, fraud_flags)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (card, amount, txn_type, status, reason,
          bal_before, bal_after, distance, ml_pred, ml_prob,
          ",".join(flags) if flags else ""))
    conn.commit()
    conn.close()

def get_txns(card, limit=30):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM transactions WHERE card_number=?
        ORDER BY timestamp DESC LIMIT ?
    """, (card, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_recent_txns(card, minutes=60):
    """Returns transactions in the last N minutes."""
    since = (datetime.utcnow() - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM transactions
        WHERE card_number=? AND timestamp >= ?
        ORDER BY timestamp DESC
    """, (card, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─── LOCATION ─────────────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ─── EMAIL ────────────────────────────────────────────────────────────────────
def send_fraud_email(to_email, card, amount, reason, flags, bal_before, bal_after):
    if not SENDER_EMAIL or not SENDER_PASS:
        print("Fraud email skipped: SENDER_EMAIL/SENDER_PASS not configured in environment")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = to_email
        msg["Subject"] = "🚨 Fraud Alert — Transaction Blocked"

        flag_list = "\n".join(f"  ⚠ {f}" for f in flags) if flags else "  • Fraud detected"

        plain = f"""
FRAUD ALERT — SecureCard Protection

Your transaction has been BLOCKED.

  Card    : ****{card[-4:]}
  Amount  : ₹{amount:,.2f}
  Reason  : {reason}

Fraud signals detected:
{flag_list}

  Balance before : ₹{bal_before:,.2f}
  Balance after  : ₹{bal_after:,.2f}  (unchanged — transaction blocked)

If this was you, please contact support.
If this was NOT you, your card may be compromised — act immediately.
"""
        html = f"""
<html><body style="font-family:sans-serif;background:#f4f4f4;padding:20px">
<div style="max-width:520px;margin:auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)">
  <div style="background:#dc2626;padding:24px 28px">
    <h2 style="color:white;margin:0;font-size:20px">🚨 Fraud Alert — Transaction Blocked</h2>
  </div>
  <div style="padding:28px">
    <table style="width:100%;border-collapse:collapse">
      <tr><td style="padding:8px 0;color:#666;font-size:13px">Card</td>
          <td style="padding:8px 0;font-weight:700">****{card[-4:]}</td></tr>
      <tr style="background:#fef2f2"><td style="padding:8px 6px;color:#666;font-size:13px">Amount</td>
          <td style="padding:8px 6px;font-weight:700;color:#dc2626">₹{amount:,.2f}</td></tr>
      <tr><td style="padding:8px 0;color:#666;font-size:13px">Primary Reason</td>
          <td style="padding:8px 0;font-weight:600">{reason}</td></tr>
      <tr style="background:#fef2f2"><td style="padding:8px 6px;color:#666;font-size:13px">Balance Before</td>
          <td style="padding:8px 6px">₹{bal_before:,.2f}</td></tr>
      <tr><td style="padding:8px 0;color:#666;font-size:13px">Balance After</td>
          <td style="padding:8px 0;color:#16a34a;font-weight:700">₹{bal_after:,.2f} (Unchanged)</td></tr>
    </table>

    <div style="margin-top:20px;padding:16px;background:#fff7ed;border-left:4px solid #f97316;border-radius:4px">
      <strong style="font-size:13px;color:#9a3412">Fraud Signals Detected:</strong>
      <ul style="margin:8px 0 0 0;padding-left:18px;color:#7c3aed;font-size:13px">
        {"".join(f"<li>{f}</li>" for f in flags) if flags else "<li>Fraud pattern detected by ML model</li>"}
      </ul>
    </div>

    <p style="margin-top:24px;font-size:12px;color:#9ca3af">
      If this was NOT you, your card may be compromised. Contact support immediately.
    </p>
  </div>
</div>
</body></html>
"""
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        s = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        s.login(SENDER_EMAIL, SENDER_PASS)
        s.send_message(msg)
        s.quit()
        print("✅ Fraud email sent to", to_email)
    except Exception as e:
        print("❌ Email error:", e)

# ─── FRAUD DETECTION ENGINE ───────────────────────────────────────────────────
TYPE_MAP = {"TRANSFER": 1, "CASH_OUT": 2, "PAYMENT": 3, "DEBIT": 4, "CASH_IN": 5}

def run_fraud_checks(card, amount, txn_type, lat, lon, user):
    """
    Returns (blocked: bool, reason: str, flags: list, ml_pred: int, ml_prob: float)
    """
    flags = []
    balance  = user["balance"]
    home_lat = user["home_lat"]
    home_lon = user["home_lon"]
    email    = user["email"]

    # ── 1. ML MODEL ──────────────────────────────────────────────────────────
    type_encoded = TYPE_MAP.get(txn_type, 1)
    new_bal_orig = max(balance - amount, 0)
    features = [[
        1,              # step
        type_encoded,   # type (encoded)
        amount,         # amount
        balance,        # oldbalanceOrg  (sender's current balance)
        new_bal_orig,   # newbalanceOrig (sender's balance after)
        0,              # oldbalanceDest
        amount,         # newbalanceDest
        0               # isFlaggedFraud
    ]]
    ml_pred = int(model.predict(features)[0])
    ml_prob = float(model.predict_proba(features)[0][1])  # probability of fraud

    if ml_pred == 1:
        flags.append(f"ML model flagged fraud (confidence: {ml_prob*100:.1f}%)")

    # ── 2. LOCATION ──────────────────────────────────────────────────────────
    distance = None
    if lat is not None and lon is not None:
        distance = haversine(float(lat), float(lon), home_lat, home_lon)
        if distance > 900:
            flags.append(f"Transaction location too far from home ({distance:.0f} km away)")

    # ── 3. AMOUNT > BALANCE (overdraft attempt) ───────────────────────────────
    if amount > balance:
        flags.append(f"Amount (₹{amount:,.0f}) exceeds account balance (₹{balance:,.0f})")

    # ── 4. ENTIRE BALANCE DRAIN (≥95% of balance) ────────────────────────────
    if balance > 0 and (amount / balance) >= 0.95:
        flags.append(f"Transaction drains ≥95% of balance (₹{amount:,.0f} of ₹{balance:,.0f})")

    # ── 5. SINGLE HIGH AMOUNT ────────────────────────────────────────────────
    if amount > 100000:
        flags.append(f"Single transaction amount too high (₹{amount:,.0f})")

    # ── 6. RAPID REPEATED DEBITS (velocity check — last 10 min) ──────────────
    recent = get_recent_txns(card, minutes=10)
    approved_recent = [t for t in recent if t["status"] == "Approved"]
    if len(approved_recent) >= 5:
        flags.append(f"Unusual velocity: {len(approved_recent)} transactions in last 10 minutes")

    # ── 7. CONSECUTIVE LARGE DEBITS (last 30 min) ────────────────────────────
    recent_30 = get_recent_txns(card, minutes=30)
    large_recent = [t for t in recent_30 if t["status"] == "Approved" and t["amount"] >= 10000]
    if len(large_recent) >= 3:
        total_drained = sum(t["amount"] for t in large_recent)
        flags.append(f"Consecutive large debits: {len(large_recent)} transactions totalling ₹{total_drained:,.0f} in 30 min")

    # ── 8. UNUSUAL PATTERN: large jump from typical spending ─────────────────
    all_txns = get_txns(card, limit=50)
    approved_txns = [t for t in all_txns if t["status"] == "Approved" and t["amount"] > 0]
    if len(approved_txns) >= 5:
        avg = sum(t["amount"] for t in approved_txns) / len(approved_txns)
        if amount > avg * 10 and amount > 5000:
            flags.append(f"Amount (₹{amount:,.0f}) is {amount/avg:.0f}x your average transaction (₹{avg:,.0f})")

    # ── DECISION ──────────────────────────────────────────────────────────────
    blocked = len(flags) > 0

    if blocked:
        primary_reason = flags[0]  # most important flag
    else:
        dist_str = f"{distance:.1f} km from home" if distance else "location OK"
        primary_reason = f"All checks passed ({dist_str}, ML clear)"

    return blocked, primary_reason, flags, ml_pred, ml_prob, distance

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def login_page():
    if 'card' in session: return redirect(url_for('dashboard'))
    return render_template("login.html")

@app.route('/register_page')
def register_page():
    return render_template("register.html")

@app.route('/login', methods=['POST'])
def login():
    d = request.json
    card = str(d.get('card_number','')).strip()
    pw   = str(d.get('password','')).strip()
    user = get_user(card)
    if user and check_pw(pw, user["password_hash"]):
        if _needs_rehash(user["password_hash"]):
            conn = get_conn()
            conn.execute("UPDATE users SET password_hash=? WHERE card_number=?", (hash_pw(pw), card))
            conn.commit()
            conn.close()
        session['card']  = card
        session['email'] = user["email"]
        return jsonify({"status":"success"})
    return jsonify({"status":"fail","message":"Invalid card number or password"})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/dashboard')
def dashboard():
    if 'card' not in session: return redirect(url_for('login_page'))
    return render_template("dashboard.html", card=session['card'])

@app.route('/register', methods=['POST'])
def register():
    d = request.json
    pw = d.get('password','')
    errs = validate_password(pw)
    if errs:
        return jsonify({"message":"Password must have: " + " | ".join(errs), "success":False})
    if pw != d.get('confirm_password',''):
        return jsonify({"message":"Passwords do not match", "success":False})
    balance_pin = str(d.get('balance_pin','')).strip()
    if not re.match(r'^\d{4,6}$', balance_pin):
        return jsonify({"message":"Balance PIN must be 4–6 digits", "success":False})
    ok, msg = insert_user(d['card_number'], d['email'], pw, d['pincode'], balance_pin)
    return jsonify({"message": msg, "success": ok})

@app.route('/me')
def me():
    if 'card' not in session: return jsonify({}), 401
    user = get_user(session['card'])
    if not user: return jsonify({}), 404
    return jsonify({
        "card": session['card'],
        "email": user["email"]
        # balance intentionally omitted — requires PIN via /verify_balance_pin
    })

@app.route('/verify_balance_pin', methods=['POST'])
def verify_balance_pin():
    if 'card' not in session: return jsonify({"success": False, "message": "Not logged in"}), 401
    d = request.json
    entered_pin = str(d.get('pin', '')).strip()
    user = get_user(session['card'])
    if not user: return jsonify({"success": False, "message": "User not found"}), 404
    if user["balance_pin"] and check_pw(entered_pin, user["balance_pin"]):
        if _needs_rehash(user["balance_pin"]):
            conn = get_conn()
            conn.execute("UPDATE users SET balance_pin=? WHERE card_number=?", (hash_pw(entered_pin), card))
            conn.commit()
            conn.close()
        return jsonify({"success": True, "balance": user["balance"]})
    return jsonify({"success": False, "message": "Incorrect Balance PIN"})

@app.route('/transactions')
def transactions():
    if 'card' not in session: return jsonify({"error":"Unauthorized"}), 401
    rows = get_txns(session['card'])
    return jsonify(rows)

@app.route('/predict', methods=['POST'])
def predict():
    if 'card' not in session:
        return jsonify({"status":"Error","reason":"Not logged in"}), 401

    d    = request.json
    card = session['card']
    user = get_user(card)
    if not user:
        return jsonify({"status":"Error","reason":"User not found"})

    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({"status":"Error","reason":"Invalid amount"})
    txn_type = d.get('type', 'TRANSFER')
    if txn_type not in TYPE_MAP:
        return jsonify({"status":"Error","reason":"Invalid transaction type"})
    try:
        lat = float(d.get('lat')) if d.get('lat') is not None else None
        lon = float(d.get('lon')) if d.get('lon') is not None else None
    except (TypeError, ValueError):
        return jsonify({"status":"Error","reason":"Invalid location"})

    if amount <= 0:
        return jsonify({"status":"Error","reason":"Invalid amount"})

    if lat is None or lon is None:
        return jsonify({"status":"Error","reason":"Location required"})

    bal_before = user["balance"]

    blocked, reason, flags, ml_pred, ml_prob, distance = run_fraud_checks(
        card, amount, txn_type, lat, lon, user
    )

    if blocked:
        # Balance does NOT change on blocked transaction
        bal_after = bal_before
        log_txn(card, amount, txn_type, "Blocked", reason,
                bal_before, bal_after, distance, ml_pred, ml_prob, flags)
        send_fraud_email(user["email"], card, amount, reason, flags, bal_before, bal_after)
        return jsonify({
            "status":  "Blocked",
            "reason":  reason,
            "flags":   flags,
            "ml_probability": round(ml_prob*100, 1)
            # balance intentionally omitted from response
        })
    else:
        # Deduct amount from balance
        bal_after = round(bal_before - amount, 2)
        update_balance(card, bal_after)
        log_txn(card, amount, txn_type, "Approved", reason,
                bal_before, bal_after, distance, ml_pred, ml_prob, [])
        return jsonify({
            "status":  "Approved",
            "reason":  reason,
            "ml_probability": round(ml_prob*100, 1)
            # balance intentionally omitted from response
        })

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=False)
