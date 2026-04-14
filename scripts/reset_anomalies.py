"""Reset anomalies status back to pending for re-testing."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.database import Database

db = Database()
cur = db.conn.execute("UPDATE anomalies SET confirmed=0, confirmation_score=NULL, status='pending' WHERE 1")
db.conn.commit()
print(f"Reset {cur.rowcount} anomalies to pending")
