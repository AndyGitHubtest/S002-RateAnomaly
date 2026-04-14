#!/usr/bin/env python3
"""T6: 止盈逻辑完整测试
1. 静态报告: TP1/TP2/Trailing阈值 + 手续费覆盖检查
2. 动态模拟: 模拟价格变化 → 调用profit.check_all() → 验证止盈触发
"""
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import Database
from src.profit import ProfitManager
from src.config import Config


def static_report(db: Database, profit: ProfitManager):
    """静态报告: TP1/TP2/Trailing阈值"""
    positions = db.get_open_positions()
    if not positions:
        print("无持仓，先跑T5")
        return []

    print("=" * 60)
    print("T6-A 静止盈阈值报告")
    print("=" * 60)
    print(f"持仓数: {len(positions)}")

    tp1_list = []
    tp2_list = []

    for pos in positions:
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        notional = pos["notional"]

        print(f"\n--- {symbol} ---")
        print(f"  entry_price={entry_price:.6f}  notional={notional:.2f}")
        print(f"  peak_pnl_pct={pos.get('peak_pnl_pct', 0)*100:.2f}%")

        # 读取profit分布
        profit_dist = db.get_distribution(symbol, Config.DIST_PROFIT, "", "2y")
        if profit_dist is None:
            print(f"  ⚠️ 无profit分布，跳过")
            continue

        pcts = profit_dist.get("percentiles", {})
        tp1_threshold = pcts.get("p50", 5.0) / 100
        tp2_normal = pcts.get("p75", 10.0) / 100
        tp2_strong = pcts.get("p90", 15.0) / 100

        tp1_list.append(pcts.get("p50", 5.0))
        tp2_list.append(pcts.get("p75", 10.0))

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

        # 手续费覆盖检查
        fee_pct = 0.001 * 2
        min_profit_usd = max(0.5, notional * 0.0015)
        min_profit_pct = fee_pct + min_profit_usd / notional
        print(f"\n  手续费: {fee_pct*100:.2f}%  最低利润门槛: {min_profit_pct*100:.2f}%")
        if tp1_threshold * 100 < min_profit_pct * 100:
            print(f"  ⚠️ TP1阈值({tp1_threshold*100:.2f}%) < 最低利润门槛({min_profit_pct*100:.2f}%) — TP1会亏钱!")
        else:
            print(f"  ✅ TP1阈值({tp1_threshold*100:.2f}%) > 最低利润门槛({min_profit_pct*100:.2f}%) — TP1能覆盖成本")

    # 汇总
    print(f"\n{'=' * 60}")
    print("T6-A 汇总")
    print("=" * 60)
    if tp1_list:
        print(f"  TP1(p50)范围: {min(tp1_list):.2f}% ~ {max(tp1_list):.2f}%  平均={sum(tp1_list)/len(tp1_list):.2f}%")
        print(f"  TP2(p75)范围: {min(tp2_list):.2f}% ~ {max(tp2_list):.2f}%  平均={sum(tp2_list)/len(tp2_list):.2f}%")

    return positions


def dynamic_test(db: Database, profit: ProfitManager, positions: list):
    """动态模拟: 修改current_price → 调用profit.check_all() → 验证止盈触发"""
    if not positions:
        return

    print(f"\n{'=' * 60}")
    print("T6-B 动态止盈触发测试")
    print("=" * 60)

    for pos in positions:
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        position_id = pos["id"]
        notional = pos["notional"]

        # 读取profit分布
        profit_dist = db.get_distribution(symbol, Config.DIST_PROFIT, "", "2y")
        if profit_dist is None:
            continue

        pcts = profit_dist.get("percentiles", {})
        tp1_threshold = pcts.get("p50", 5.0) / 100

        print(f"\n--- {symbol} 动态测试 ---")
        print(f"  entry={entry_price:.6f}  TP1阈值={tp1_threshold*100:.2f}%")

        # 场景1: 价格涨到TP1 → 应触发TP1
        tp1_price = entry_price * (1 + tp1_threshold)
        print(f"\n  [场景1] 设置价格到TP1: {tp1_price:.6f}")
        db.conn.execute(
            "UPDATE positions SET current_price=? WHERE id=?",
            (tp1_price, position_id)
        )
        db.conn.commit()

        # 重新读取position (含peak_pnl_pct)
        updated_pos = db.conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
        pos_dict = dict(updated_pos) if updated_pos else pos

        profit._check_position(pos_dict)

        # 检查TP1是否记录
        tp1_records = db.conn.execute(
            "SELECT * FROM take_profits WHERE position_id=? AND tp_level='tp1'",
            (position_id,)
        ).fetchall()
        if tp1_records:
            r = tp1_records[0]
            print(f"  ✅ TP1已触发: price={r['price']:.6f} qty={r['qty']:.4f} pct={r['pct']:.2f} pnl={r['pnl']:.4f}")
        else:
            print(f"  ❌ TP1未触发 (预期应触发)")

        # 场景2: 价格涨到高peak(确保利润远大于trailing_trigger), 然后回撤
        dd_dist = db.get_distribution(symbol, Config.DIST_DRAWDOWN, "", "2y")
        dd_pcts = dd_dist.get("percentiles", {}) if dd_dist else {}
        trailing_trigger = dd_pcts.get("p75", 5.0) / 100 if dd_pcts else 0.05

        # 先涨到很高 (利润需远大于trailing_trigger, 否则回撤后pnl变负)
        # 目标: peak利润 = trailing_trigger * 3 (确保回撤后仍盈利)
        target_peak_profit = trailing_trigger * 3
        peak_price = entry_price * (1 + target_peak_profit)
        print(f"\n  [场景2a] 设置价格到peak: {peak_price:.6f} (利润={target_peak_profit*100:.2f}%)")
        db.conn.execute(
            "UPDATE positions SET current_price=? WHERE id=?",
            (peak_price, position_id)
        )
        db.conn.commit()

        updated_pos = db.conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
        pos_dict = dict(updated_pos) if updated_pos else pos
        profit._check_position(pos_dict)

        # 重新读peak_pnl_pct (可能被_check_position更新)
        updated_pos = db.conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
        peak_val = dict(updated_pos).get("peak_pnl_pct", 0) if updated_pos else 0
        print(f"  peak_pnl_pct={peak_val*100:.2f}%")

        # 回撤50%不够触发trailing_trigger
        drawdown_price = entry_price * (1 + target_peak_profit - trailing_trigger * 0.5)
        print(f"\n  [场景2b] 回撤50%不够: price={drawdown_price:.6f}")
        db.conn.execute(
            "UPDATE positions SET current_price=? WHERE id=?",
            (drawdown_price, position_id)
        )
        db.conn.commit()

        updated_pos = db.conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
        pos_dict = dict(updated_pos) if updated_pos else pos
        profit._check_position(pos_dict)

        trailing_records = db.conn.execute(
            "SELECT * FROM take_profits WHERE position_id=? AND tp_level='trailing'",
            (position_id,)
        ).fetchall()
        print(f"  trailing触发? {'是' if trailing_records else '否(预期:否，回撤不够)'}")

        # 回撤120%超过trailing_trigger
        drawdown_price_full = entry_price * (1 + target_peak_profit - trailing_trigger * 1.2)
        current_pnl_est = target_peak_profit - trailing_trigger * 1.2
        print(f"\n  [场景2c] 回撤120%足够: price={drawdown_price_full:.6f} (估计利润={current_pnl_est*100:.2f}%)")
        db.conn.execute(
            "UPDATE positions SET current_price=? WHERE id=?",
            (drawdown_price_full, position_id)
        )
        db.conn.commit()

        updated_pos = db.conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
        pos_dict = dict(updated_pos) if updated_pos else pos
        profit._check_position(pos_dict)

        trailing_records = db.conn.execute(
            "SELECT * FROM take_profits WHERE position_id=? AND tp_level='trailing'",
            (position_id,)
        ).fetchall()
        if trailing_records:
            r = trailing_records[0]
            print(f"  ✅ trailing已触发: price={r['price']:.6f} qty={r['qty']:.4f} pct={r['pct']:.2f} pnl={r['pnl']:.4f}")
        else:
            # 可能是TP2先触发了(利润太高), 或者TP1后trailing条件不满足
            tp2_records = db.conn.execute(
                "SELECT * FROM take_profits WHERE position_id=? AND tp_level='tp2'",
                (position_id,)
            ).fetchall()
            if tp2_records:
                print(f"  ℹ️ TP2先触发(利润太高), trailing无需再触发")
            else:
                print(f"  ❌ trailing未触发 (需排查)")

        # 检查仓位是否关闭
        final_pos = db.conn.execute(
            "SELECT status, close_reason FROM positions WHERE id=?",
            (position_id,)
        ).fetchone()
        if final_pos and final_pos["status"] == "closed":
            print(f"  ✅ 仓位已关闭: reason={final_pos['close_reason']}")
        else:
            print(f"  ⚠️ 仓位仍open (trailing应关闭仓位)")


if __name__ == "__main__":
    db = Database()
    profit = ProfitManager(db)

    positions = static_report(db, profit)
    dynamic_test(db, profit, positions)
