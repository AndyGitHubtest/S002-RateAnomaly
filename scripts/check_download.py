"""检查下载结果"""
import sqlite3
from pathlib import Path

db_path = Path(__file__).parent.parent / "data" / "klines_1h.db"
if not db_path.exists():
    print("DB not found!")
    exit(1)

conn = sqlite3.connect(str(db_path))
print("=== 总体统计 ===")
row = conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines_1h").fetchone()
print(f"Symbols: {row[0]}, Total rows: {row[1]}")

print("\n=== 每个symbol的K线数 ===")
rows = conn.execute(
    "SELECT symbol, COUNT(*) as cnt, MIN(ts), MAX(ts) "
    "FROM klines_1h GROUP BY symbol ORDER BY cnt DESC LIMIT 30"
).fetchall()
for r in rows:
    from datetime import datetime
    min_t = datetime.utcfromtimestamp(r[2]/1000).strftime('%Y-%m-%d') if r[2] else '?'
    max_t = datetime.utcfromtimestamp(r[3]/1000).strftime('%Y-%m-%d') if r[3] else '?'
    print(f"  {r[0]:20s} {r[1]:6d} rows  {min_t} ~ {max_t}")

print("\n=== K线数分布 ===")
rows = conn.execute(
    "SELECT CASE WHEN cnt<100 THEN '<100' "
    "WHEN cnt<1000 THEN '100-1k' "
    "WHEN cnt<5000 THEN '1k-5k' "
    "WHEN cnt<10000 THEN '5k-10k' "
    "WHEN cnt<17520 THEN '10k-17k' "
    "ELSE '17k+' END as bucket, COUNT(*) as n "
    "FROM (SELECT symbol, COUNT(*) as cnt FROM klines_1h GROUP BY symbol) "
    "GROUP BY bucket ORDER BY bucket"
).fetchall()
for r in rows:
    print(f"  {r[0]:12s} {r[1]} symbols")

conn.close()
