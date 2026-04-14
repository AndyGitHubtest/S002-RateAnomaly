#!/usr/bin/env python3
from src.database import Database
db = Database()
count = db.conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
db.conn.execute("DELETE FROM anomalies")
db.conn.commit()
print(f"Cleared {count} old anomalies")
