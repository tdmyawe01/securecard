"""Create the database tables if they do not exist.

SAFE BY DEFAULT: this no longer drops existing tables.
To start completely fresh, run:  python init_db.py --reset
and confirm when prompted.
"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")


def init_db(reset=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if reset:
        c.execute("DROP TABLE IF EXISTS login_events")
        c.execute("DROP TABLE IF EXISTS transactions")
        c.execute("DROP TABLE IF EXISTS users")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        card_number     TEXT UNIQUE,
        email           TEXT,
        password_hash   TEXT,
        home_lat        REAL,
        home_lon        REAL,
        pincode         TEXT,
        balance         REAL DEFAULT 100000,
        balance_pin     TEXT,
        status          TEXT DEFAULT 'active',
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS login_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        card_number     TEXT,
        email           TEXT,
        event           TEXT,
        ip_address      TEXT,
        user_agent      TEXT,
        timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    # Migration for databases created before the balance PIN feature.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN balance_pin TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()
    print("Database initialized!")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    if reset:
        answer = input("This will DELETE all users and transactions. Type YES to confirm: ")
        if answer.strip() != "YES":
            print("Aborted.")
            sys.exit(1)
    init_db(reset=reset)
