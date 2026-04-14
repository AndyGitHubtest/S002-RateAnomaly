#!/usr/bin/env python3
"""T6: 止盈逻辑模拟出场测试
从T5创建的持仓 → 模拟不同价格场景 → 测试TP1/TP2/Trailing触发
"""
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import Database
from src.profit import ProfitManager
from src.config import Config


def simulate_exit(db: Database, profit: ProfitManager):
    """模拟止盈场景"""
    positions = db.get_open_positions()
    if not positions:
        print("无持仓，先跑T5")
        return

    print("=" * 60)
    print("T6 止盈逻辑模拟出场报告")
    print("=" * 60)
    print(f"持仓数: {len(positions)}")
    print()

    for pos in positions:
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        notional = pos["notional"]

        print(f"\n--- {symbol} ---")
        print(f"  entry_price={entry_price:.6f}  notional={notional:.2f}")

        # 读取profit分布
        profit_dist = db.get_distribution(symbol, Config.DIST_PROFIT, "", "2y")
        if profit_dist is None:
            print(f"  ⚠️ 无profit分布，跳过")
            continue

        pcts = profit_dist.get("percentiles", {})
        tp1_threshold = pcts.get("p50", 5.0) / 100
        tp2_normal = pcts.get("p75", 10.0) / 100
        tp2_strong = pcts.get("p90", 15.0) / 100

        print(f"  profit分布: p50={pcts.get('p50', 'N/A')}% p75={pcts.get('p75', 'N/A')}% p90={pcts.get('p90', 'N/A')}%")
        print(f"  TP1阈值={tp1_threshold*100:.2f}%  TP2(正常)={tp2_normal*100:.2f}%  TP2(强反弹)={tp2_strong*100:.2f}%")

        # 读取drawdown分布
        dd_dist = db.get_distribution(symbol, Config.DIST_DRAWDOWN, "", "2y")
        dd_pcts = dd_dist.get("percentiles", {}) if dd_dist else {}
        trailing_normal = dd_pcts.get("p75", 5.0) / 100 if dd_pcts else 0.05
        trailing_weak = dd_pcts.get("p50", 3.0) / 100 if dd_pcts else 0.03

        print(f"  drawdown分布: p50={dd_pcts.get('p50', 'N/A')}% p75={dd_pcts.get('p75', 'N/A')}%")
        print(f"  Trailing(正常)={trailing_normal*100:.2f}%  Trailing(弱反弹)={trailing_weak*100:.2f}%")

        # 读取rebound_speed分布
        speed_dist = db.get_distribution(symbol, Config.DIST_REBOUND_SPEED, "", "2y")
        if speed_dist:
            sp = speed_dist.get("percentiles", {})
            print(f"  rebound_speed: p25={sp.get('p25', 'N/A')} p75={sp.get('p75', 'N/A')}")

        # 模拟3个价格场景
        scenarios = [
            ("TP1触发", entry_price * (1 + tp1_threshold)),
            ("TP2触发(正常)", entry_price * (1 + tp2_normal)),
            ("TP2触发(强反弹)", entry_price * (1 + tp2_strong)),
        ]

        print(f"\n  模拟场景:")
        for name, sim_price in scenarios:
            profit_pct = (sim_price - entry_price) / entry_price * 100
            profit_usd = (sim_price - entry_price) * pos["total_qty"]
            print(f"    {name}: price={sim_price:.6f} profit={profit_pct:.2f}% (${profit_usd:.2f})")

        # 计算手续费
        fee_pct = 0.001 * 2  # 开仓+平仓
        breakeven_pct = fee_pct * 100
        print(f"\n  手续费: {breakeven_pct:.2f}% (开+平)")

        # 检查TP1是否覆盖手续费
        if tp1_threshold * 100 < breakeven_pct:
            print(f"  ⚠️ TP1阈值({tp1_threshold*100:.2f}%) < 手续费({breakeven_pct:.2f}%) — TP1会亏钱!")
        else:
            print(f"  ✅ TP1阈值({tp1_threshold*100:.2f}%) > 手续费({breakeven_pct:.2f}%) — TP1能覆盖成本")

    # 汇总
    print("\n" + "=" * 60)
    print("T6 汇总")
    print("=" * 60)

    # 统计所有持仓的TP阈值分布
    tp1_list = []
    tp2_list = []
    for pos in positions:
        symbol = pos["symbol"]
        profit_dist = db.get_distribution(symbol, Config.DIST_PROFIT, "", "2y")
        if profit_dist:
            pcts = profit_dist.get("percentiles", {})
            tp1_list.append(pcts.get("p50", 5.0))
            tp2_list.append(pcts.get("p75", 10.0))

    if tp1_list:
        print(f"  TP1(p50)范围: {min(tp1_list):.2f}% ~ {max(tp1_list):.2f}%  平均={sum(tp1_list)/len(tp1_list):.2f}%")
        print(f"  TP2(p75)范围: {min(tp2_list):.2f}% ~ {max(tp2_list):.2f}%  平均={sum(tp2_list)/len(tp2_list):.2f}%")

    # trailing状态
    print(f"\n  ⚠️ Trailing逻辑未实现 (_check_trailing返回False)")
    print(f"  需要添加: peak_pnl持久化到positions表")

    # 检查take_profits表是否存在
    tp_table = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='take_profits'"
    ).fetchall()
    print(f"  take_profits表: {'存在' if tp_table else '不存在'}")


if __name__ == "__main__":
    db = Database()
    profit = ProfitManager(db)
    simulate_exit(db, profit)
