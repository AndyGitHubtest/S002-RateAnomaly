"""S002 RateAnomaly - 利润管理器
三阶段止盈:
  TP1: profit分布p50 → 平30% + 移保本
  TP2: profit分布p75 → 平30% + 移SL到TP1利润50%
  Trailing: 利润回撤触发 → 平40%
  反弹动能调整: 强反弹→提高TP2到p90+放宽trailing; 弱反弹→提前trailing
"""
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class ProfitManager:
    def __init__(self, db: Database):
        self.db = db

    def check_all(self):
        """检查所有持仓的止盈条件"""
        positions = self.db.get_open_positions()
        for pos in positions:
            self._check_position(pos)

    def _check_position(self, pos: dict):
        """检查单个持仓的止盈"""
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        current_price = pos["current_price"]
        position_id = pos["id"]

        if current_price <= 0 or entry_price <= 0:
            return

        # 当前利润率
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct <= 0:
            return  # 还在亏，不触发止盈

        # 更新peak_pnl_pct(只升不降)
        if profit_pct > pos.get("peak_pnl_pct", 0):
            self.db.update_peak_pnl(position_id, profit_pct)

        # 从分布读取阈值
        profit_dist = self.db.get_distribution(symbol, Config.DIST_PROFIT,
                                                "", "2y")
        speed_dist = self.db.get_distribution(symbol, Config.DIST_REBOUND_SPEED,
                                               "", "2y")
        dd_dist = self.db.get_distribution(symbol, Config.DIST_DRAWDOWN,
                                            "", "2y")

        if profit_dist is None:
            return

        # 反弹动能评估
        rebound_strength = self._assess_rebound_strength(symbol, speed_dist)

        # TP1阈值
        tp1_threshold = profit_dist["percentiles"].get("p50", 5.0) / 100
        # TP2阈值(根据反弹强度调整)
        if rebound_strength == "strong" and profit_dist["percentiles"].get("p90"):
            tp2_threshold = profit_dist["percentiles"]["p90"] / 100
        else:
            tp2_threshold = profit_dist["percentiles"].get("p75", 10.0) / 100

        # Trailing触发(根据反弹强度调整)
        if rebound_strength == "weak" and dd_dist:
            trailing_trigger = dd_dist["percentiles"].get("p50", 3.0) / 100
        else:
            trailing_trigger = dd_dist["percentiles"].get("p75", 5.0) / 100 if dd_dist else 0.05

        # 最低利润门槛(覆盖手续费+最低利润)
        # 手续费: 开0.1% + 平0.1% = 0.2%; 最低利润: max(0.5U, notional×0.15%)
        total_cost_pct = 0.002  # 0.2% 开平手续费
        min_profit_usd = max(0.5, pos["notional"] * 0.0015)
        min_profit_pct = total_cost_pct + min_profit_usd / pos["notional"]

        # 检查止盈层级
        # 先查已执行的止盈
        existing_tps = self.db.conn.execute(
            "SELECT tp_level FROM take_profits WHERE position_id=?",
            (position_id,)
        ).fetchall()
        done_levels = {r["tp_level"] for r in existing_tps}

        # TP1
        if "tp1" not in done_levels and profit_pct >= tp1_threshold:
            if profit_pct >= min_profit_pct:
                self._execute_tp(pos, "tp1", Config.TP1_CLOSE_PCT, current_price)
                log.info("TP1 %s: profit=%.2f%% >= threshold=%.2f%%",
                         symbol, profit_pct * 100, tp1_threshold * 100)
            else:
                log.debug("TP1 %s: profit %.2f%% below min %.2f%%, skip",
                          symbol, profit_pct * 100, min_profit_pct * 100)

        # TP2
        if "tp2" not in done_levels and profit_pct >= tp2_threshold:
            if profit_pct >= min_profit_pct:
                self._execute_tp(pos, "tp2", Config.TP2_CLOSE_PCT, current_price)
                log.info("TP2 %s: profit=%.2f%% >= threshold=%.2f%% (strength=%s)",
                         symbol, profit_pct * 100, tp2_threshold * 100,
                         rebound_strength)

        # Trailing(TP1触发后检查，平掉剩余全部仓位)
        if "tp1" in done_levels and "trailing" not in done_levels:
            if self._check_trailing(pos, trailing_trigger, min_profit_pct):
                # trailing平掉剩余全部仓位
                closed_pct = sum(
                    r[0] for r in self.db.conn.execute(
                        "SELECT pct FROM take_profits WHERE position_id=?",
                        (position_id,)
                    ).fetchall()
                )
                remaining_pct = max(0.01, 1.0 - closed_pct)
                self._execute_tp(pos, "trailing", remaining_pct, current_price)
                log.info("TRAILING %s: triggered at profit=%.2f%%, closing %.0f%% remaining",
                         symbol, profit_pct * 100, remaining_pct * 100)

    def _assess_rebound_strength(self, symbol: str,
                                  speed_dist: Optional[dict]) -> str:
        """评估反弹动能: strong/normal/weak"""
        if speed_dist is None:
            return "normal"

        # 获取最近K线计算反弹速率
        klines = self.db.get_klines(symbol, limit=24)
        if len(klines) < 6:
            return "normal"

        klines.sort(key=lambda x: x["ts"])
        closes = np.array([k["close"] for k in klines], dtype=np.float64)

        # 最近6根的反弹速率
        if len(closes) >= 6:
            recent_rate = (closes[-1] - closes[-6]) / closes[-6] / 6  # 每小时
        else:
            recent_rate = 0

        p75_speed = speed_dist["percentiles"].get("p75", 0.01)
        p25_speed = speed_dist["percentiles"].get("p25", 0.002)

        if recent_rate >= p75_speed:
            return "strong"
        elif recent_rate <= p25_speed:
            return "weak"
        return "normal"

    def _check_trailing(self, pos: dict, trigger_pct: float,
                        min_profit_pct: float) -> bool:
        """检查trailing条件: 从peak利润回撤超过trigger_pct
        peak_pnl_pct已在_check_position中实时更新到positions表
        """
        peak_pnl_pct = pos.get("peak_pnl_pct", 0)
        if peak_pnl_pct <= 0:
            return False

        current_pnl_pct = (pos["current_price"] - pos["entry_price"]) / pos["entry_price"]

        # 当前利润必须仍 > 最低利润门槛(覆盖手续费)
        if current_pnl_pct < min_profit_pct:
            return False

        # 从peak回撤幅度
        drawdown_from_peak = peak_pnl_pct - current_pnl_pct
        if drawdown_from_peak < 0:
            drawdown_from_peak = 0  # 当前创新高，无回撤

        # 回撤超过trigger → 触发trailing
        if drawdown_from_peak >= trigger_pct:
            log.info("TRAILING TRIGGER %s: peak=%.2f%% current=%.2f%% drawdown=%.2f%% >= trigger=%.2f%%",
                     pos["symbol"], peak_pnl_pct * 100, current_pnl_pct * 100,
                     drawdown_from_peak * 100, trigger_pct * 100)
            return True

        return False

    def _execute_tp(self, pos: dict, tp_level: str, close_pct: float,
                    price: float):
        """执行止盈"""
        qty_to_close = pos["total_qty"] * close_pct
        pnl = (price - pos["entry_price"]) * qty_to_close

        self.db.save_take_profit(
            position_id=pos["id"],
            tp_level=tp_level,
            price=price,
            qty=qty_to_close,
            pct=close_pct,
            pnl=pnl
        )

        # 如果是trailing(平剩余全部)，关闭整个仓位
        if tp_level == "trailing":
            # trailing的pnl已经包含了剩余全部仓位的利润
            self.db.close_position(pos["id"], f"tp_{tp_level}", pnl)
            log.info("POSITION CLOSED %s via %s: pnl=%.4f",
                     pos["symbol"], tp_level, pnl)
