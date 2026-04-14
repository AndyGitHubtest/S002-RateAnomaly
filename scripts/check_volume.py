#!/usr/bin/env python3
"""查询klines_1h schema和Top50成交量"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.database import Database

db = Database()
c = db.conn

# Schema
cols = c.execute("PRAGMA table_info(klines_1h)").fetchall()
print("klines_1h columns:", [col[1] for col in cols])

# Top 50 by 24h volume
rows = c.execute("""
    SELECT symbol, SUM(volume) as vol_24h
    FROM klines_1h
    WHERE ts >= (SELECT MAX(ts) FROM klines_1h) - 86400000
    GROUP BY symbol
    ORDER BY vol_24h DESC
    LIMIT 50
""").fetchall()
print(f"\nTop 50 by 24h volume:")
for i, (sym, vol) in enumerate(rows[:20], 1):
    print(f"  {i:>3}. {sym:<15} vol={vol:>15,.0f}")
print(f"  ... (showing top 20 of 50)")

# Total symbols with volume data
total = c.execute("""
    SELECT COUNT(DISTINCT symbol) FROM klines_1h
    WHERE ts >= (SELECT MAX(ts) FROM klines_1h) - 86400000
""").fetchone()[0]
print(f"\nTotal symbols with 24h data: {total}")
