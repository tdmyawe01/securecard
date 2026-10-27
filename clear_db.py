"""Clear demo data: users AND their transactions.

The old version deleted users only, leaving orphaned transactions behind.
This version deletes both, so no transaction is left pointing at a deleted user.
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("DELETE FROM transactions")
txn_count = cursor.rowcount
cursor.execute("DELETE FROM login_events")
event_count = cursor.rowcount
cursor.execute("DELETE FROM users")
user_count = cursor.rowcount
conn.commit()
conn.close()

print(f"Database cleared! Removed {user_count} users, {txn_count} transactions and {event_count} login events.")
