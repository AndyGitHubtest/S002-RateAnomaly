#!/usr/bin/env python3
"""查询T3扫描结果"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.database import Database

db = Database()
c = db.conn

# Check schema
cols = c.execute("PRAGMA table_info(anomalies)").fetchall()
print("anomalies columns:", [col[1] for col in cols])

total = c.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
print(f"Total anomalies: {total}")

if total == 0:
    sys.exit(0)

# Get all data
rows = c.execute("SELECT * FROM anomalies ORDER BY anomaly_score DESC").fetchall()
col_names = [desc[0] for desc in c.execute("SELECT * FROM anomalies LIMIT 1").description]
print(f"Columns: {col_names}")

# Find score, symbol, rate, amp columns
def get_col(name):
    for i, n in enumerate(col_names):
        if name in n.lower():
            return i
    return None

sym_i = get_col("symbol")
score_i = get_col("score")
rate_i = get_col("rate")
amp_i = get_col("amp")
rp_i = get_col("rate_pctl")
ap_i = get_col("amp_pctl")
scale_i = get_col("scale") or get_col("timeframe") or get_col("window")

print(f"Indices: sym={sym_i} score={score_i} rate={rate_i} amp={amp_i} rp={rp_i} ap={ap_i} scale={scale_i}")

scores = [r[score_i] for r in rows if score_i is not None]
print(f"Score range: {min(scores):.0f} - {max(scores):.0f}")
print(f"Score mean: {sum(scores)/len(scores):.0f}")

# Score buckets
buckets = {"9000+": 0, "7000-9000": 0, "5000-7000": 0, "3000-5000": 0, "1000-3000": 0, "<1000": 0}
for s in scores:
    if s >= 9000: buckets["9000+"] += 1
    elif s >= 7000: buckets["7000-9000"] += 1
    elif s >= 5000: buckets["5000-7000"] += 1
    elif s >= 3000: buckets["3000-5000"] += 1
    elif s >= 1000: buckets["1000-3000"] += 1
    else: buckets["<1000"] += 1
print(f"\nScore distribution:")
for k, v in buckets.items():
    print(f"  {k}: {v}")

# Top 20
print(f"\nTop 20 by score:")
for r in rows[:20]:
    sym = r[sym_i] if sym_i is not None else "?"
    sc = r[scale_i] if scale_i is not None else "?"
    rate = r[rate_i] if rate_i is not None else 0
    amp = r[amp_i] if amp_i is not None else 0
    rp = r[rp_i] if rp_i is not None else 0
    ap = r[ap_i] if ap_i is not None else 0
    score = r[score_i] if score_i is not None else 0
    print(f"  {sym:<15} {sc!s:<6} rate={rate:.5f} amp={amp:.4f} rp={rp:.3f} ap={ap:.3f} score={score:.0f}")

# Bottom 5
print(f"\nBottom 5:")
for r in rows[-5:]:
    sym = r[sym_i] if sym_i is not None else "?"
    sc = r[scale_i] if scale_i is not None else "?"
    score = r[score_i] if score_i is not None else 0
    print(f"  {sym:<15} {sc!s:<6} score={score:.0f}")
