#!/usr/bin/env python3
"""T3: Anomaly Scan - 全量538币异常扫描"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import Database
from src.anomaly import AnomalyDetector
from src.logger import log

def main():
    db = Database()
    detector = AnomalyDetector(db)

    # Clear old anomalies
    old_count = db.conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
    db.conn.execute("DELETE FROM anomalies")
    db.conn.commit()
    log.info("Cleared %d old anomalies", old_count)

    # Run scan
    start = time.time()
    anomalies = detector.scan_all()
    elapsed = time.time() - start

    # Stats
    total = len(db.get_symbols())
    error_count = 0
    # Check for errors in log
    print(f"\n{'='*60}")
    print(f"T3 ANOMALY SCAN RESULTS")
    print(f"{'='*60}")
    print(f"Total symbols: {total}")
    print(f"Anomalies found: {len(anomalies)}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    if anomalies:
        # Sort by anomaly_score desc
        anomalies.sort(key=lambda x: x["anomaly_score"], reverse=True)
        print(f"{'Symbol':<15} {'Scale':<6} {'Rate':>10} {'Amp':>10} {'RatePctl':>10} {'AmpPctl':>10} {'Score':>8}")
        print("-" * 75)
        for a in anomalies:
            print(f"{a['symbol']:<15} {a['scale']:<6} {a['decline_rate']:>10.6f} {a['decline_amp']:>10.4f} "
                  f"{a['rate_pctl']:>10.4f} {a['amp_pctl']:>10.4f} {a['anomaly_score']:>8.0f}")

        # Score distribution
        scores = [a["anomaly_score"] for a in anomalies]
        print(f"\nScore range: {min(scores):.0f} - {max(scores):.0f}")
        print(f"Score mean: {sum(scores)/len(scores):.0f}")

        # Scales distribution
        from collections import Counter
        scales = Counter(a["scale"] for a in anomalies)
        print(f"Scales: {dict(scales)}")

    # Verify DB
    db_count = db.conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
    print(f"\nDB anomalies count: {db_count}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
