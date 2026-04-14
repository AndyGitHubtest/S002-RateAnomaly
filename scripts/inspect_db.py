#!/usr/bin/env python3
import sqlite3, os

db_path = None
for root, dirs, files in os.walk(os.path.expanduser('~/S002-RateAnomaly')):
    for f in files:
        if f == 'klines_1h.db':
            db_path = os.path.join(root, f)
            break

conn = sqlite3.connect(db_path)
c = conn.cursor()

# Show raw data for first symbol
print("=== Sample rows (first 5) ===")
rows = c.execute("SELECT * FROM klines_1h LIMIT 5").fetchall()
for r in rows:
    print(r)

print("\n=== BTCUSDT first 3 rows ===")
rows = c.execute("SELECT * FROM klines_1h WHERE symbol='BTCUSDT' LIMIT 3").fetchall()
for r in rows:
    print(r)

print("\n=== Symbol count ===")
syms = c.execute("SELECT DISTINCT symbol FROM klines_1h ORDER BY symbol").fetchall()
print(f"Total: {len(syms)}")
print(f"Symbols: {[s[0] for s in syms]}")

print("\n=== Row count per symbol (top 5) ===")
rows = c.execute("SELECT symbol, COUNT(*) FROM klines_1h GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

conn.close()
