"""S002 RateAnomaly - 风控模块
硬止损: 名义值10% (5x杠杆 = 价格跌50%)
持仓上限: 5币
同板块上限: 2币
系统性崩溃: 30%+币异常时只取top5
"""
import time
from typing import Optional

from src.config import Config
from src.database import Database
from src.logger import log


class RiskManager:
    def __init__(self, db: Database):
        self.db = db

    def check_position_risk(self, pos: dict) -> Optional[str]:
        """检查单个持仓风控，返回触发原因或None"""
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        current_price = pos["current_price"]
        stop_price = pos["stop_price"]

        # 1. 硬止损: 价格跌破stop_price
        if current_price <= stop_price:
            pnl = (current_price - entry_price) * pos["total_qty"]
            self.db.save_risk_event(
                event_type="hard_stop",
                ts=int(time.time() * 1000),
                symbol=symbol,
                details={
                    "entry": entry_price,
                    "current": current_price,
                    "stop": stop_price,
                    "pnl": pnl,
                }
            )
            log.warning("HARD STOP %s: price %.4f <= stop %.4f, pnl=%.4f",
                        symbol, current_price, stop_price, pnl)
            return "hard_stop"

        # 2. 持仓超时: 7天(168h)
        entry_ts = pos["entry_ts"]
        if entry_ts and (int(time.time() * 1000) - entry_ts) > 168 * 3600 * 1000:
            pnl = (current_price - entry_price) * pos["total_qty"]
            self.db.save_risk_event(
                event_type="timeout",
                ts=int(time.time() * 1000),
                symbol=symbol,
                details={"entry_ts": entry_ts, "pnl": pnl}
            )
            log.warning("TIMEOUT %s: held > 168h, pnl=%.4f", symbol, pnl)
            return "timeout"

        return None

    def check_portfolio_risk(self) -> list[str]:
        """检查组合风控，返回需要强制平仓的symbol列表"""
        positions = self.db.get_open_positions()
        forced = []

        # 1. 持仓数超限
        if len(positions) > Config.MAX_COINS:
            # 按PnL排序，平最差的
            sorted_pos = sorted(positions, key=lambda p: p.get("unrealized_pnl", 0))
            excess = len(positions) - Config.MAX_COINS
            for p in sorted_pos[:excess]:
                forced.append(p["symbol"])
                self.db.save_risk_event(
                    event_type="max_positions",
                    ts=int(time.time() * 1000),
                    symbol=p["symbol"],
                    details={"total": len(positions), "limit": Config.MAX_COINS}
                )

        # 2. 同板块超限
        sector_counts = {}
        for pos in positions:
            meta = self.db.get_coin_meta(pos["symbol"])
            sector = meta.get("sector", "unknown") if meta else "unknown"
            sector_counts.setdefault(sector, []).append(pos["symbol"])

        for sector, symbols in sector_counts.items():
            if len(symbols) > Config.MAX_SAME_SECTOR:
                # 平PnL最差的
                excess = symbols[Config.MAX_SAME_SECTOR:]
                for s in excess:
                    if s not in forced:
                        forced.append(s)
                        self.db.save_risk_event(
                            event_type="sector_limit",
                            ts=int(time.time() * 1000),
                            symbol=s,
                            details={"sector": sector, "count": len(symbols)}
                        )

        return forced

    def can_open(self, symbol: str) -> tuple[bool, str]:
        """检查是否可以开仓，返回(允许, 原因)"""
        positions = self.db.get_open_positions()

        # 持仓上限
        if len(positions) >= Config.MAX_COINS:
            return False, f"max positions {Config.MAX_COINS}"

        # 重复持仓
        if any(p["symbol"] == symbol for p in positions):
            return False, f"already holding {symbol}"

        # 同板块上限
        meta = self.db.get_coin_meta(symbol)
        if meta and meta.get("sector"):
            sector_count = sum(
                1 for p in positions
                if self.db.get_coin_meta(p["symbol"])
                and self.db.get_coin_meta(p["symbol"]).get("sector") == meta["sector"]
            )
            if sector_count >= Config.MAX_SAME_SECTOR:
                return False, f"sector {meta['sector']} limit {Config.MAX_SAME_SECTOR}"

        # 冷启动检查
        if meta and meta.get("cold_start_multiplier", 1.0) <= 0:
            return False, f"insufficient data for {symbol}"

        return True, "ok"
