"""S002 RateAnomaly - 止跌确认器
4条件评分:
  1. 速率衰减: 当前速率回到p30以上
  2. 缩量: 当前成交量 < 跌势中成交量的50%
  3. 不创新低: 连续N根K线未创新低(N从分布读取)
  4. 阳线确认: 出现阳线或下影线>2倍实体

评分规则: 每条件2500分，满分10000
  ≥7000 → 100%仓位
  ≥5000 → 70%仓位
  <5000 → 不入场
"""
import time
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class BottomConfirmer:
    def __init__(self, db: Database):
        self.db = db

    def confirm(self, anomaly: dict) -> Optional[dict]:
        """对异常事件做止跌确认，返回确认结果或None"""
        symbol = anomaly["symbol"]
        klines = self.db.get_klines(symbol, limit=50)
        if len(klines) < 10:
            return None

        klines.sort(key=lambda x: x["ts"])
        closes = np.array([k["close"] for k in klines], dtype=np.float64)
        volumes = np.array([k["volume"] for k in klines], dtype=np.float64)
        highs = np.array([k["high"] for k in klines], dtype=np.float64)
        lows = np.array([k["low"] for k in klines], dtype=np.float64)

        score = 0.0
        details = {}

        # 条件1: 速率衰减 (2500分)
        c1_score = self._check_rate_decay(symbol, closes, anomaly)
        score += c1_score
        details["rate_decay"] = c1_score

        # 条件2: 缩量 (2500分)
        c2_score = self._check_volume_shrink(closes, volumes)
        score += c2_score
        details["volume_shrink"] = c2_score

        # 条件3: 不创新低 (2500分)
        c3_score = self._check_no_new_low(symbol, closes)
        score += c3_score
        details["no_new_low"] = c3_score

        # 条件4: 阳线/下影线确认 (2500分)
        c4_score = self._check_bullish_candle(closes, highs, lows)
        score += c4_score
        details["bullish_candle"] = c4_score

        # 判定
        if score >= Config.CONFIRM_SCORE_100:
            position_pct = 1.0
        elif score >= Config.CONFIRM_SCORE_70:
            position_pct = 0.7
        else:
            log.info("BOTTOM REJECTED %s: score=%.0f < %d",
                     symbol, score, Config.CONFIRM_SCORE_70)
            return None

        # 更新异常状态
        self.db.update_anomaly_confirmed(anomaly["anomaly_id"], score)

        log.info("BOTTOM CONFIRMED %s: score=%.0f position=%.0f%% details=%s",
                 symbol, score, position_pct * 100, details)

        return {
            "anomaly_id": anomaly["anomaly_id"],
            "symbol": symbol,
            "confirmation_score": score,
            "position_pct": position_pct,
            "current_price": float(closes[-1]),
            "details": details,
        }

    def _check_rate_decay(self, symbol: str, closes: np.ndarray,
                          anomaly: dict) -> float:
        """条件1: 当前速率衰减到p30以上 → 2500分"""
        # 计算最近4h速率
        if len(closes) < 5:
            return 0.0
        recent_rate = 0.0
        for hours in [4, 12]:
            if len(closes) > hours:
                window = closes[-(hours + 1):]
                peak_idx = np.argmax(window)
                if peak_idx < len(window) - 1:
                    decline = (window[peak_idx] - window[-1]) / window[peak_idx]
                    decline_hours = len(window) - 1 - peak_idx
                    recent_rate = max(recent_rate, decline / max(decline_hours, 1))

        # 从分布读p30
        dist = self.db.get_distribution(symbol, Config.DIST_DECLINE_RATE,
                                         anomaly.get("scale", "24h"), "2y")
        if dist is None:
            return 0.0

        p30 = dist["percentiles"].get("p30", 0.005)
        # 速率已衰减到p30以下 = 跌势放缓
        if recent_rate < p30:
            log.debug("  rate_decay: rate=%.5f < p30=%.5f → PASS", recent_rate, p30)
            return 2500.0
        # 部分衰减
        if anomaly["decline_rate"] > 0:
            decay_ratio = 1.0 - (recent_rate / anomaly["decline_rate"])
            if decay_ratio > 0.5:
                log.debug("  rate_decay: partial %.0f%% → 1250", decay_ratio * 100)
                return 1250.0
        return 0.0

    def _check_volume_shrink(self, closes: np.ndarray,
                             volumes: np.ndarray) -> float:
        """条件2: 当前成交量 < 跌势成交量50% → 2500分"""
        if len(volumes) < 10:
            return 0.0

        # 找下跌段(最近close连续下降的区域)
        decline_volumes = []
        for i in range(1, len(closes)):
            if closes[i] < closes[i - 1]:
                decline_volumes.append(volumes[i])

        if not decline_volumes:
            return 1250.0  # 没有下跌 = 已止跌

        avg_decline_vol = np.mean(decline_volumes[-5:])
        recent_vol = np.mean(volumes[-3:])
        ratio = recent_vol / max(avg_decline_vol, 1e-10)

        if ratio < 0.5:
            log.debug("  volume_shrink: ratio=%.2f < 0.5 → PASS", ratio)
            return 2500.0
        elif ratio < 0.75:
            log.debug("  volume_shrink: ratio=%.2f < 0.75 → 1250", ratio)
            return 1250.0
        return 0.0

    def _check_no_new_low(self, symbol: str, closes: np.ndarray) -> float:
        """条件3: 连续N根不创新低 → 2500分
        N从底部特征分布的consolidation中位数读取
        """
        if len(closes) < 6:
            return 0.0

        # 从分布读取盘整K线数的中位数
        dist = self.db.get_distribution(symbol, Config.DIST_BOTTOM_FEATURE,
                                         "consolidation", "2y")
        n_required = 3  # 默认3根
        if dist:
            n_required = max(3, int(dist["percentiles"].get("p50", 3)))

        # 检查最近N根是否创新低
        recent_low = np.min(closes[-n_required:])
        prior_low = np.min(closes[-(n_required + 5):-n_required])

        if recent_low > prior_low:
            log.debug("  no_new_low: %d bars no new low → PASS", n_required)
            return 2500.0
        elif recent_low >= prior_low * 0.998:  # 接近但不破
            log.debug("  no_new_low: near-support → 1250")
            return 1250.0
        return 0.0

    def _check_bullish_candle(self, closes: np.ndarray,
                              highs: np.ndarray, lows: np.ndarray) -> float:
        """条件4: 阳线或下影线>2倍实体 → 2500分"""
        if len(closes) < 2:
            return 0.0

        # 检查最近3根K线
        for i in range(-3, 0):
            body = abs(closes[i] - (closes[i - 1] if i > -len(closes) else closes[i]))
            lower_shadow = min(closes[i], closes[i - 1] if i > -len(closes) else closes[i]) - lows[i]
            candle_range = highs[i] - lows[i]

            # 阳线
            if closes[i] > closes[i - 1] if i > -len(closes) else False:
                log.debug("  bullish_candle: bullish bar → PASS")
                return 2500.0

            # 下影线 > 2倍实体
            if body > 0 and lower_shadow > 2 * body:
                log.debug("  bullish_candle: lower shadow 2x body → PASS")
                return 2500.0

            # 下影线占比 > 60%
            if candle_range > 0 and lower_shadow / candle_range > 0.6:
                log.debug("  bullish_candle: lower shadow 60%% → 1250")
                return 1250.0

        return 0.0
