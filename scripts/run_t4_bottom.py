"""T4: 止跌确认测试
对T3扫描出的46个异常币逐一做止跌确认，输出4条件评分明细。
"""
import sys, os, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import Database
from src.bottom import BottomConfirmer
from src.logger import log

def main():
    db = Database()
    confirmer = BottomConfirmer(db)

    # 获取所有pending异常
    since_ts = int((time.time() - 72 * 3600) * 1000)  # 最近72h
    anomalies = db.get_pending_anomalies(since_ts)
    print(f"Pending anomalies: {len(anomalies)}")

    confirmed_100 = []
    confirmed_70 = []
    rejected = []
    errors = []

    for a in anomalies:
        symbol = a["symbol"]
        score_raw = a.get("anomaly_score", 0)
        try:
            # 先手动跑4条件看明细
            klines = db.get_klines(symbol, limit=50)
            if len(klines) < 10:
                rejected.append((symbol, score_raw))
                continue
            klines.sort(key=lambda x: x["ts"])
            import numpy as np
            closes = np.array([k["close"] for k in klines], dtype=np.float64)
            volumes = np.array([k["volume"] for k in klines], dtype=np.float64)
            highs = np.array([k["high"] for k in klines], dtype=np.float64)
            lows = np.array([k["low"] for k in klines], dtype=np.float64)

            c1 = confirmer._check_rate_decay(symbol, closes, a)
            c2 = confirmer._check_volume_shrink(closes, volumes)
            c3 = confirmer._check_no_new_low(symbol, closes)
            c4 = confirmer._check_bullish_candle(closes, highs, lows)
            total = c1 + c2 + c3 + c4
            detail_line = f"c1(rate)={c1:.0f} c2(vol)={c2:.0f} c3(nolow)={c3:.0f} c4(bull)={c4:.0f} total={total:.0f}"

            result = confirmer.confirm(a)
            if result is None:
                rejected.append((symbol, score_raw, detail_line))
            else:
                cs = result["confirmation_score"]
                pp = result["position_pct"]
                details = result["details"]
                entry = {
                    "symbol": symbol,
                    "anomaly_score": score_raw,
                    "confirm_score": cs,
                    "position_pct": pp,
                    "price": result["current_price"],
                    "details": details,
                }
                if pp >= 1.0:
                    confirmed_100.append(entry)
                else:
                    confirmed_70.append(entry)
        except Exception as e:
            errors.append((symbol, str(e), traceback.format_exc()))

    # ── 报告 ──
    print(f"\n{'='*60}")
    print(f"T4 止跌确认报告")
    print(f"{'='*60}")
    print(f"总异常: {len(anomalies)}")
    print(f"确认100%: {len(confirmed_100)}")
    print(f"确认 70%: {len(confirmed_70)}")
    print(f"拒绝:     {len(rejected)}")
    print(f"错误:     {len(errors)}")

    if confirmed_100:
        print(f"\n--- 确认100%仓位 (score>=7000) ---")
        for e in sorted(confirmed_100, key=lambda x: -x["confirm_score"]):
            d = e["details"]
            print(f"  {e['symbol']:16s} anom={e['anomaly_score']:5.0f} "
                  f"confirm={e['confirm_score']:5.0f} "
                  f"price={e['price']:.6f} "
                  f"rate_decay={d.get('rate_decay',0):.0f} "
                  f"vol_shrink={d.get('volume_shrink',0):.0f} "
                  f"no_new_low={d.get('no_new_low',0):.0f} "
                  f"bullish={d.get('bullish_candle',0):.0f}")

    if confirmed_70:
        print(f"\n--- 确认70%仓位 (5000<=score<7000) ---")
        for e in sorted(confirmed_70, key=lambda x: -x["confirm_score"]):
            d = e["details"]
            print(f"  {e['symbol']:16s} anom={e['anomaly_score']:5.0f} "
                  f"confirm={e['confirm_score']:5.0f} "
                  f"price={e['price']:.6f} "
                  f"rate_decay={d.get('rate_decay',0):.0f} "
                  f"vol_shrink={d.get('volume_shrink',0):.0f} "
                  f"no_new_low={d.get('no_new_low',0):.0f} "
                  f"bullish={d.get('bullish_candle',0):.0f}")

    if rejected:
        print(f"\n--- 拒绝 (score<5000) ---")
        for item in sorted(rejected, key=lambda x: -x[1])[:15]:
            sym, sc = item[0], item[1]
            detail = item[2] if len(item) > 2 else ""
            print(f"  {sym:16s} anomaly={sc:.0f} {detail}")

    if errors:
        print(f"\n--- 错误 ---")
        for sym, err in errors:
            print(f"  {sym}: {err[:100]}")

    # 条件通过率统计
    all_confirmed = confirmed_100 + confirmed_70
    if all_confirmed:
        c1_pass = sum(1 for e in all_confirmed if e["details"].get("rate_decay", 0) >= 2500)
        c2_pass = sum(1 for e in all_confirmed if e["details"].get("volume_shrink", 0) >= 2500)
        c3_pass = sum(1 for e in all_confirmed if e["details"].get("no_new_low", 0) >= 2500)
        c4_pass = sum(1 for e in all_confirmed if e["details"].get("bullish_candle", 0) >= 2500)
        n = len(all_confirmed)
        print(f"\n--- 条件通过率 (基于{n}个确认) ---")
        print(f"  速率衰减: {c1_pass}/{n} ({c1_pass/n*100:.0f}%)")
        print(f"  缩量:     {c2_pass}/{n} ({c2_pass/n*100:.0f}%)")
        print(f"  不创新低: {c3_pass}/{n} ({c3_pass/n*100:.0f}%)")
        print(f"  阳线确认: {c4_pass}/{n} ({c4_pass/n*100:.0f}%)")

if __name__ == "__main__":
    main()
