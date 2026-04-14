#!/usr/bin/env python3
import sqlite3, os

# Find DB
db_path = None
for root, dirs, files in os.walk(os.path.expanduser('~/S002-RateAnomaly')):
    for f in files:
        if f == 'klines_1h.db':
            db_path = os.path.join(root, f)
            break

if not db_path:
    print("DB not found!")
    exit(1)

print(f"DB: {db_path}")
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Schema
schema = c.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
print("Schema:")
for s in schema:
    print(f"  {s[0]}")

# Column names
cols = c.execute("PRAGMA table_info(klines_1h)").fetchall()
print(f"\nColumns: {[col[1] for col in cols]}")

# Stats
symbols = c.execute('SELECT COUNT(DISTINCT symbol) FROM klines_1h').fetchone()[0]
total = c.execute('SELECT COUNT(*) FROM klines_1h').fetchone()[0]
print(f'\nSymbols: {symbols}, Total rows: {total}')

# Use actual column names
ts_col = cols[1][1]  # second column is likely timestamp/ts
syms = c.execute(
    f'SELECT symbol, COUNT(*) as cnt, MIN({ts_col}), MAX({ts_col}) '
    f'FROM klines_1h GROUP BY symbol ORDER BY cnt DESC'
).fetchall()
print(f'\nAll {len(syms)} symbols:')
for s in syms:
    print(f'  {s[0]}: {s[1]} rows, {s[2]} - {s[3]}')
conn.close()
