#!/usr/bin/env python3
import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), 'data', 'klines_1h.db')
if not os.path.exists(db_path):
    # try project root data dir
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'klines_1h.db')
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    # search for it
    for root, dirs, files in os.walk(os.path.expanduser('~/S002-RateAnomaly')):
        for f in files:
            if f == 'klines_1h.db':
                db_path = os.path.join(root, f)
                print(f"Found DB at: {db_path}")
                break

conn = sqlite3.connect(db_path)
c = conn.cursor()
symbols = c.execute('SELECT COUNT(DISTINCT symbol) FROM klines_1h').fetchone()[0]
total = c.execute('SELECT COUNT(*) FROM klines_1h').fetchone()[0]
print(f'Symbols: {symbols}, Total rows: {total}')

syms = c.execute(
    'SELECT symbol, COUNT(*) as cnt, MIN(timestamp), MAX(timestamp) '
    'FROM klines_1h GROUP BY symbol ORDER BY cnt DESC'
).fetchall()
print(f'\nTop 10 by rows:')
for s in syms[:10]:
    print(f'  {s[0]}: {s[1]} rows, {s[2]} - {s[3]}')
print(f'\nBottom 5 by rows:')
for s in syms[-5:]:
    print(f'  {s[0]}: {s[1]} rows, {s[2]} - {s[3]}')
conn.close()
