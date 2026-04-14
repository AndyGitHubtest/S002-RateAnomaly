#!/usr/bin/env python3
"""清理 positions 和 risk_events 表，用于重跑测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.database import Database

db = Database()
p = db.conn.execute("DELETE FROM positions").rowcount
r = db.conn.execute("DELETE FROM risk_events").rowcount
db.conn.commit()
print(f"cleaned: positions={p}, risk_events={r}")
