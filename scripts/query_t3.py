#!/usr/bin/env python3
"""查询T3扫描结果"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.database import Database

db = Database()
c = db.conn
total = c.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
print(f"Total anomalies: {total}")

if total == 0:
    print("No anomalies found")
    sys.exit(0)

rows = c.execute("SELECT symbol, scale, decline_rate, decline_amp, rate_pctl, amp_pctl, anomaly_score FROM anomalies ORDER BY anomaly_score DESC").fetchall()
scores = [r[6] for r in rows]
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

# Scale distribution
from collections import Counter
scales = Counter(r[1] for r in rows)
print(f"\nScale distribution: {dict(scales)}")

# Top 20
print(f"\nTop 20 by score:")
print(f"{'Symbol':<15} {'Scale':<6} {'Rate':>10} {'Amp':>10} {'RateP':>7} {'AmpP':>7} {'Score':>7}")
print("-" * 70)
for r in rows[:20]:
    print(f"{r[0]:<15} {r[1]:<6} {r[2]:>10.6f} {r[3]:>10.4f} {r[4]:>7.3f} {r[5]:>7.3f} {r[6]:>7.0f}")

# Bottom 5
print(f"\nBottom 5 by score:")
for r in rows[-5:]:
    print(f"{r[0]:<15} {r[1]:<6} {r[2]:>10.6f} {r[3]:>10.4f} {r[4]:>7.3f} {r[5]:>7.3f} {r[6]:>7.0f}")
