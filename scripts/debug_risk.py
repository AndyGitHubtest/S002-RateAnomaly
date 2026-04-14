#!/usr/bin/env python3
"""调试: 检查风控参数和coin_meta数据"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import Config
from src.database import Database

db = Database()
print(f"MAX_COINS={Config.MAX_COINS}")
print(f"MAX_SAME_SECTOR={Config.MAX_SAME_SECTOR}")

# coin_meta表
rows = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='coin_meta'").fetchall()
print(f"coin_meta exists: {len(rows) > 0}")

if rows:
    meta = db.conn.execute("SELECT * FROM coin_meta LIMIT 5").fetchall()
    print(f"coin_meta rows: {len(meta)}")
    for r in meta:
        print(dict(r))

# 当前open positions
positions = db.get_open_positions()
print(f"\nopen positions: {len(positions)}")
for p in positions:
    print(f"  {p['symbol']} sector={p.get('sector', 'N/A')}")

# 检查sector分布
sectors = {}
for p in positions:
    meta = db.get_coin_meta(p["symbol"])
    s = meta.get("sector", "unknown") if meta else "unknown"
    sectors.setdefault(s, []).append(p["symbol"])
print(f"\nsector分布:")
for s, syms in sectors.items():
    print(f"  {s}: {syms} (count={len(syms)})")
