#!/usr/bin/env python3
"""T5: 仓位+风控模拟入场测试
从T4确认的异常 → PositionBuilder.build() → RiskManager.can_open()
报告每步详情: 层数/名义值/止损价/风控检查
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.position import PositionBuilder
from src.risk import RiskManager
from src.config import Config


def main():
    db = Database()
    builder = PositionBuilder(db)
    risk = RiskManager(db)

    # 获取所有confirmed的异常
    rows = db.conn.execute(
        "SELECT * FROM anomalies WHERE confirmed=1 ORDER BY anomaly_score DESC"
    ).fetchall()
    confirmed = [dict(r) for r in rows]

    if not confirmed:
        print("无已确认异常，先跑T4")
        return

    print("=" * 60)
    print("T5 仓位+风控模拟入场报告")
    print("=" * 60)
    print(f"确认异常数: {len(confirmed)}")
    print(f"配置: equity={Config.EQUITY_USDT} leverage={Config.LEVERAGE} "
          f"position_pct={Config.POSITION_PCT} hard_stop={Config.HARD_STOP_PCT}")
    print(f"单币名义值= {Config.per_coin_notional():.2f} USDT")
    print(f"持仓上限= {Config.MAX_COINS} 同板块上限= {Config.MAX_SAME_SECTOR}")
    print()

    # 先清掉旧测试持仓
    db.conn.execute("DELETE FROM positions WHERE status='open'")
    db.conn.execute("DELETE FROM risk_events")
    db.conn.commit()

    entered = []
    rejected = []
    errors = []

    for i, anom in enumerate(confirmed):
        symbol = anom["symbol"]
        print(f"\n--- [{i+1}/{len(confirmed)}] {symbol} ---")
        print(f"  anomaly_score={anom['anomaly_score']:.0f}  "
              f"confirmation_score={anom['confirmation_score']:.0f}")

        # 1. RiskManager.can_open
        can, reason = risk.can_open(symbol)
        if not can:
            print(f"  ❌ 风控拒绝: {reason}")
            rejected.append((symbol, reason))
            continue
        print(f"  ✅ 风控通过: {reason}")

        # 2. 构建confirmation dict (模拟bottom.confirm输出)
        conf_score = anom["confirmation_score"]
        position_pct = 1.0 if conf_score >= Config.CONFIRM_SCORE_100 else 0.7

        # 获取当前价格 (最新close)
        latest = db.conn.execute(
            "SELECT close FROM klines_1h WHERE symbol=? ORDER BY ts DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        current_price = latest["close"] if latest else 0

        confirmation = {
            "symbol": symbol,
            "position_pct": position_pct,
            "current_price": current_price,
            "anomaly_id": anom["id"],
            "confirmation_score": conf_score,
        }

        # 3. PositionBuilder.build
        try:
            pos_id = builder.build(confirmation)
            if pos_id is None:
                print(f"  ❌ 仓位构建失败 (返回None)")
                rejected.append((symbol, "build_failed"))
                continue

            # 读取刚创建的持仓
            pos = db.conn.execute(
                "SELECT * FROM positions WHERE id=?", (pos_id,)
            ).fetchone()
            pos_dict = dict(pos)
            layers = eval(pos_dict["layers"]) if isinstance(pos_dict["layers"], str) else pos_dict["layers"]

            print(f"  ✅ 入场成功: id={pos_id}")
            print(f"     入场价={pos_dict['entry_price']:.6f}  "
                  f"止损价={pos_dict['stop_price']:.6f}")
            print(f"     名义值={pos_dict['notional']:.2f}  "
                  f"数量={pos_dict['total_qty']:.6f}")
            print(f"     层数={len(layers)}  仓位比例={position_pct*100:.0f}%")

            # 层详情
            for layer in layers:
                status = "⏳" if not layer.get("filled") else "✅"
                print(f"     L{layer['layer']}: price={layer['price']:.6f} "
                      f"qty={layer['qty']:.6f} pct={layer['pct']*100:.1f}% {status}")

            # 止损距离
            stop_dist = (pos_dict['entry_price'] - pos_dict['stop_price']) / pos_dict['entry_price'] * 100
            print(f"     止损距离={stop_dist:.1f}%")

            entered.append({
                "symbol": symbol,
                "pos_id": pos_id,
                "entry_price": pos_dict['entry_price'],
                "stop_price": pos_dict['stop_price'],
                "notional": pos_dict['notional'],
                "n_layers": len(layers),
                "position_pct": position_pct,
                "stop_dist_pct": stop_dist,
            })

        except Exception as e:
            print(f"  ❌ 异常: {e}")
            errors.append((symbol, str(e)))
            import traceback
            traceback.print_exc()

    # 汇总
    print("\n" + "=" * 60)
    print("T5 汇总")
    print("=" * 60)
    print(f"确认异常: {len(confirmed)}")
    print(f"成功入场: {len(entered)}")
    print(f"风控拒绝: {len(rejected)}")
    print(f"错误:     {len(errors)}")

    if entered:
        print(f"\n--- 入场汇总 ---")
        total_notional = sum(e["notional"] for e in entered)
        print(f"  总名义值: {total_notional:.2f} USDT "
              f"(占equity {total_notional/Config.EQUITY_USDT*100:.1f}%)")
        print(f"  平均层数: {sum(e['n_layers'] for e in entered)/len(entered):.1f}")
        print(f"  平均止损距离: {sum(e['stop_dist_pct'] for e in entered)/len(entered):.1f}%")

        print(f"\n  {'币种':<16} {'名义值':>8} {'层数':>4} {'仓位':>5} "
              f"{'入场价':>10} {'止损价':>10} {'止损%':>6}")
        print(f"  {'-'*16} {'-'*8} {'-'*4} {'-'*5} {'-'*10} {'-'*10} {'-'*6}")
        for e in entered:
            print(f"  {e['symbol']:<16} {e['notional']:>8.2f} {e['n_layers']:>4} "
                  f"{e['position_pct']*100:>4.0f}% "
                  f"{e['entry_price']:>10.6f} {e['stop_price']:>10.6f} "
                  f"{e['stop_dist_pct']:>5.1f}%")

    if rejected:
        print(f"\n--- 拒绝列表 ---")
        for sym, reason in rejected:
            print(f"  {sym}: {reason}")

    if errors:
        print(f"\n--- 错误列表 ---")
        for sym, err in errors:
            print(f"  {sym}: {err}")

    # 组合风控检查
    print(f"\n--- 组合风控检查 ---")
    forced = risk.check_portfolio_risk()
    if forced:
        print(f"  ⚠️ 需强制平仓: {forced}")
    else:
        print(f"  ✅ 组合风控正常")

    # 检查每个持仓的hard stop
    positions = db.get_open_positions()
    for pos in positions:
        # 用entry_price模拟current_price (实际运行时从交易所读取)
        pos["current_price"] = pos["entry_price"]
        risk_result = risk.check_position_risk(pos)
        if risk_result:
            print(f"  ⚠️ {pos['symbol']}: {risk_result}")
        else:
            print(f"  ✅ {pos['symbol']}: 风控正常")


if __name__ == "__main__":
    main()
