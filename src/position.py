"""S002 RateAnomaly - 仓位构建器
聚类分层(2-5层)，等权10%/层。
单币名义值 = equity × 10% × 5x leverage
"""
import json
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class PositionBuilder:
    def __init__(self, db: Database):
        self.db = db

    def build(self, confirmation: dict) -> Optional[int]:
        """根据止跌确认结果构建仓位
        返回position_id或None
        """
        symbol = confirmation["symbol"]
        position_pct = confirmation["position_pct"]
        current_price = confirmation["current_price"]

        # 检查持仓上限
        open_positions = self.db.get_open_positions()
        if len(open_positions) >= Config.MAX_COINS:
            log.warning("SKIP %s: max positions reached (%d)",
                        symbol, len(open_positions))
            return None

        # 检查同板块上限
        symbol_count = sum(1 for p in open_positions if p["symbol"] == symbol)
        if symbol_count > 0:
            log.warning("SKIP %s: already have position", symbol)
            return None

        # 同板块检查
        meta = self.db.get_coin_meta(symbol)
        if meta and meta.get("sector"):
            sector_count = sum(
                1 for p in open_positions
                if self.db.get_coin_meta(p["symbol"])
                and self.db.get_coin_meta(p["symbol"]).get("sector") == meta["sector"]
            )
            if sector_count >= Config.MAX_SAME_SECTOR:
                log.warning("SKIP %s: sector %s limit reached",
                            symbol, meta["sector"])
                return None

        # 聚类分层: 从depth_cluster分布获取层数
        n_layers = self._determine_layers(symbol)

        # 计算名义值
        notional = Config.per_coin_notional() * position_pct
        per_layer_notional = notional / n_layers

        # 构建分层入场价格
        # 从当前价往下，每层间距由decline_rate分布的聚类中心决定
        layers = self._build_layer_prices(symbol, current_price,
                                           n_layers, per_layer_notional)

        # 硬止损价 = 入场价 × (1 - HARD_STOP_PCT/LEVERAGE)
        # 5x杠杆，10%名义止损 = 价格跌50%止损
        avg_entry = np.mean([l["price"] for l in layers])
        stop_price = avg_entry * (1 - Config.HARD_STOP_PCT * Config.LEVERAGE)

        # 计算总数量
        total_qty = sum(l["qty"] for l in layers)

        # 保存持仓
        position_id = self.db.save_position(
            symbol=symbol,
            side="long",
            entry_price=avg_entry,
            layers=layers,
            total_qty=total_qty,
            notional=notional,
            anomaly_id=confirmation["anomaly_id"],
            stop_price=stop_price
        )

        log.info("POSITION OPENED %s: id=%d layers=%d notional=%.2f "
                 "stop=%.2f qty=%.4f",
                 symbol, position_id, n_layers, notional, stop_price, total_qty)

        return position_id

    def _determine_layers(self, symbol: str) -> int:
        """从depth_cluster分布确定层数(2-5)
        聚类中心越多 = 历史回撤层次越丰富 = 可以分更多层
        """
        dist = self.db.get_distribution(symbol, Config.DIST_DEPTH_CLUSTER,
                                         "", "2y")
        if dist and dist.get("params") and dist["params"].get("centers"):
            n_centers = len(dist["params"]["centers"])
            return max(2, min(5, n_centers))

        # 默认3层
        return 3

    def _build_layer_prices(self, symbol: str, current_price: float,
                            n_layers: int,
                            per_layer_notional: float) -> list[dict]:
        """构建分层入场价格和数量
        层间距: 从decline_rate分布的聚类中心推导
        每层等权10%
        """
        # 从分布获取典型跌幅间距
        dist = self.db.get_distribution(symbol, Config.DIST_DECLINE_AMP,
                                         "24h", "2y")
        step_pct = 0.03  # 默认3%间距
        if dist:
            p25 = dist["percentiles"].get("p25", 0.03)
            step_pct = max(0.02, p25 * 0.5)  # 间距 = p25的一半

        layers = []
        for i in range(n_layers):
            # 每层价格 = 当前价 × (1 - i × 间距)
            price = current_price * (1 - i * step_pct)
            qty = per_layer_notional / price if price > 0 else 0
            layers.append({
                "layer": i + 1,
                "price": round(price, 6),
                "qty": round(qty, 6),
                "pct": 1.0 / n_layers,
                "filled": False,
                "fill_ts": None,
            })

        return layers

    def mark_layer_filled(self, position_id: int, layer_idx: int,
                          fill_price: float, fill_ts: int):
        """标记某层已成交"""
        positions = self.db.get_open_positions()
        for p in positions:
            if p["id"] == position_id:
                layers = p["layers"]
                if 0 <= layer_idx < len(layers):
                    layers[layer_idx]["filled"] = True
                    layers[layer_idx]["fill_price"] = fill_price
                    layers[layer_idx]["fill_ts"] = fill_ts
                    self.db.conn.execute(
                        "UPDATE positions SET layers=? WHERE id=?",
                        (json.dumps(layers, ensure_ascii=False), position_id)
                    )
                    self.db.conn.commit()
                    log.info("Layer %d filled for position %d at %.4f",
                             layer_idx + 1, position_id, fill_price)
                break
