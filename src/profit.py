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

        # 最低利润门槛(覆盖手续费)
        total_cost_pct = 0.001  # 0.1% 手续费
        min_profit_pct = total_cost_pct + max(0.05, pos["notional"] * 0.001) / pos["notional"]

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

        # Trailing(全部止盈后检查)
        if "tp1" in done_levels and "trailing" not in done_levels:
            # 检查利润回撤
            if self._check_trailing(pos, trailing_trigger, min_profit_pct):
                self._execute_tp(pos, "trailing", Config.TRAILING_CLOSE_PCT,
                                 current_price)
                log.info("TRAILING %s: triggered at profit=%.2f%%",
                         symbol, profit_pct * 100)

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
        """检查trailing条件: 利润回撤超过trigger_pct"""
        # 从止盈记录获取peak profit
        tps = self.db.conn.execute(
            "SELECT MAX(pnl) as max_pnl FROM take_profits WHERE position_id=?",
            (pos["id"],)
        ).fetchone()

        current_pnl_pct = (pos["current_price"] - pos["entry_price"]) / pos["entry_price"]

        # 简化: 如果当前利润低于peak利润的50%且利润回撤>trigger
        # 需要tracking peak，这里用position的unrealized_pnl做近似
        if current_pnl_pct < min_profit_pct:
            return False

        # 利润回撤检查: 从最高利润回撤超过trigger
        # 实际需要记录peak，这里先简化
        return False  # TODO: 需要持久化peak_pnl到positions表

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

        # 如果是trailing(最后40%)，关闭整个仓位
        if tp_level == "trailing":
            remaining_pnl = (price - pos["entry_price"]) * pos["total_qty"] * (1 - Config.TP1_CLOSE_PCT - Config.TP2_CLOSE_PCT)
            total_pnl = pnl + remaining_pnl
            self.db.close_position(pos["id"], f"tp_{tp_level}", total_pnl)
            log.info("POSITION CLOSED %s via %s: pnl=%.4f",
                     pos["symbol"], tp_level, total_pnl)
