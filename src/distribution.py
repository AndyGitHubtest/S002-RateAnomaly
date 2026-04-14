"""S002 RateAnomaly - 8分布构建器
每币独立8分布，增量小时级更新。
分布列表:
  1. decline_rate   - 跌幅速率(4h/12h/24h/72h四个尺度)
  2. decline_amp    - 跌幅幅度(4h/12h/24h/72h四个尺度)
  3. bottom_feature - 底部特征(缩量比/盘整K线数/下影线比)
  4. depth_cluster  - 深度聚类(K-means对最大回撤聚类)
  5. profit         - 反弹最终幅度百分位
  6. rebound_speed  - 反弹速率百分位
  7. rebound_amp    - 反弹幅度百分位
  8. drawdown       - 反弹中回撤百分位
"""
import time
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class DistributionBuilder:
    def __init__(self, db: Database):
        self.db = db
        self.pctls = Config.PERCENTILES  # [0.01, 0.05, ..., 0.99]

    # ── 公开接口 ──

    def build_all_for_symbol(self, symbol: str, force: bool = False):
        """构建某币全部8分布"""
        klines = self.db.get_klines(symbol, limit=20000)
        if len(klines) < 100:
            log.warning("Skip %s: only %d klines", symbol, len(klines))
            return

        # 按时间正序排列
        klines.sort(key=lambda x: x["ts"])
        closes = np.array([k["close"] for k in klines], dtype=np.float64)
        volumes = np.array([k["volume"] for k in klines], dtype=np.float64)
        highs = np.array([k["high"] for k in klines], dtype=np.float64)
        lows = np.array([k["low"] for k in klines], dtype=np.float64)

        now_ms = int(time.time() * 1000)

        # 1-2. 跌幅速率+幅度(4个尺度)
        for scale_name, hours in [("4h", 4), ("12h", 12), ("24h", 24), ("72h", 72)]:
            rates, amps = self._calc_decline_stats(closes, hours)
            if len(rates) < 10:
                continue
            for window_name, window_days in [("2y", 730), ("90d", 90)]:
                n = min(len(rates), window_days * 24 // hours)
                r_slice = rates[-n:]
                a_slice = amps[-n:]
                self._save_percentiles(symbol, Config.DIST_DECLINE_RATE,
                                       scale_name, window_name, r_slice, now_ms)
                self._save_percentiles(symbol, Config.DIST_DECLINE_AMP,
                                       scale_name, window_name, a_slice, now_ms)

        # 3. 底部特征
        bottom_feats = self._calc_bottom_features(closes, volumes, highs, lows)
        if len(bottom_feats) > 10:
            for key in ["volume_shrink", "consolidation", "lower_shadow"]:
                vals = np.array([b[key] for b in bottom_feats], dtype=np.float64)
                for window_name, window_days in [("2y", 730), ("90d", 90)]:
                    n = min(len(vals), window_days * 24)
                    self._save_percentiles(symbol, Config.DIST_BOTTOM_FEATURE,
                                           key, window_name, vals[-n:], now_ms)

        # 4. 深度聚类(每日更新)
        max_drawdowns = self._calc_max_drawdowns(closes)
        if len(max_drawdowns) > 20:
            clusters = self._cluster_depths(max_drawdowns)
            self.db.save_distribution(
                symbol, Config.DIST_DEPTH_CLUSTER, "", "2y",
                percentiles=self._to_percentiles(max_drawdowns),
                params={"centers": clusters.tolist()},
                sample_count=len(max_drawdowns), updated_at=now_ms
            )

        # 5-8. 反弹相关分布
        rebounds = self._calc_rebound_stats(closes, volumes)
        if len(rebounds) > 10:
            for key, dist_type in [
                ("profit", Config.DIST_PROFIT),
                ("speed", Config.DIST_REBOUND_SPEED),
                ("amp", Config.DIST_REBOUND_AMP),
                ("drawdown", Config.DIST_DRAWDOWN),
            ]:
                vals = np.array([r[key] for r in rebounds], dtype=np.float64)
                vals = vals[np.isfinite(vals)]
                if len(vals) < 5:
                    continue
                for window_name, window_days in [("2y", 730), ("90d", 90)]:
                    n = min(len(vals), window_days)
                    self._save_percentiles(symbol, dist_type, "",
                                           window_name, vals[-n:], now_ms)

        # 更新币种元数据
        data_days = len(klines) // 24
        cold_mult = self._calc_cold_multiplier(data_days)
        self.db.upsert_coin_meta(symbol, data_start_ts=klines[0]["ts"],
                                  data_days=data_days,
                                  cold_start_multiplier=cold_mult)
        log.info("Built distributions for %s (%d days, cold=%.1fx)",
                 symbol, data_days, cold_mult)

    # ── 跌幅统计 ──

    def _calc_decline_stats(self, closes: np.ndarray,
                            hours: int) -> tuple[np.ndarray, np.ndarray]:
        """计算指定尺度的跌幅速率和幅度
        速率 = (close[i-hours] - close[i]) / close[i] / hours
        幅度 = (close[i-hours] - close[i]) / close[i]
        """
        n = len(closes)
        if n <= hours:
            return np.array([]), np.array([])
        # 滚动窗口最大跌幅: 每个时刻看过去hours根K线的最大回撤
        rates = []
        amps = []
        for i in range(hours, n):
            window = closes[i - hours:i + 1]
            peak_idx = np.argmax(window)
            if peak_idx < len(window) - 1:
                # 从峰到当前谷的跌幅
                decline = (window[peak_idx] - window[-1]) / window[peak_idx]
                decline_hours = len(window) - 1 - peak_idx
                rate = decline / max(decline_hours, 1)
                rates.append(rate)
                amps.append(decline)
        return np.array(rates, dtype=np.float64), np.array(amps, dtype=np.float64)

    # ── 底部特征 ──

    def _calc_bottom_features(self, closes: np.ndarray, volumes: np.ndarray,
                              highs: np.ndarray, lows: np.ndarray) -> list[dict]:
        """识别局部底部，计算底部特征"""
        results = []
        n = len(closes)
        if n < 20:
            return results

        # 找局部低点: close[i] < close[i-5:i] and close[i] < close[i+1:i+6]
        for i in range(5, n - 6):
            is_local_low = True
            for j in range(max(0, i - 5), i):
                if closes[j] <= closes[i]:
                    is_local_low = False
                    break
            if not is_local_low:
                continue
            for j in range(i + 1, min(n, i + 6)):
                if closes[j] <= closes[i]:
                    is_local_low = False
                    break
            if not is_local_low:
                continue

            # 缩量比: 低点成交量 vs 前5根平均
            vol_before = np.mean(volumes[max(0, i - 5):i])
            volume_shrink = volumes[i] / max(vol_before, 1e-10)

            # 盘整K线数: 低点附近close波动<1%的连续K线
            consolidation = 0
            for k in range(i, min(n, i + 12)):
                if abs(closes[k] - closes[i]) / closes[i] < 0.01:
                    consolidation += 1
                else:
                    break

            # 下影线比: (low-close)/(high-low)
            candle_range = highs[i] - lows[i]
            lower_shadow = (closes[i] - lows[i]) / max(candle_range, 1e-10)

            results.append({
                "volume_shrink": volume_shrink,
                "consolidation": float(consolidation),
                "lower_shadow": lower_shadow,
            })

        return results

    # ── 深度聚类 ──

    def _calc_max_drawdowns(self, closes: np.ndarray) -> np.ndarray:
        """计算滚动24h窗口的最大回撤"""
        n = len(closes)
        if n < 24:
            return np.array([])
        drawdowns = []
        for i in range(24, n, 24):  # 每天一个样本
            window = closes[i - 24:i + 1]
            peak = np.maximum.accumulate(window)
            dd = (peak - window) / peak
            drawdowns.append(np.max(dd))
        return np.array(drawdowns, dtype=np.float64)

    def _cluster_depths(self, drawdowns: np.ndarray, k: int = 3) -> np.ndarray:
        """K-means聚类深度，返回聚类中心"""
        from sklearn.cluster import KMeans
        if len(drawdowns) < k:
            return np.array([np.mean(drawdowns)])
        X = drawdowns.reshape(-1, 1)
        km = KMeans(n_clusters=min(k, len(set(np.round(X.flatten(), 4)))),
                    n_init=10, random_state=42)
        km.fit(X)
        centers = np.sort(km.cluster_centers_.flatten())
        return centers

    # ── 反弹统计 ──

    def _calc_rebound_stats(self, closes: np.ndarray,
                            volumes: np.ndarray) -> list[dict]:
        """从局部低点出发，跟踪反弹过程
        profit: 反弹最终幅度(%)
        speed: 反弹速率(每小时%)
        amp: 反弹最大幅度(%)
        drawdown: 反弹过程中最大回撤(%)
        """
        results = []
        n = len(closes)
        if n < 48:
            return results

        # 找局部低点(跌幅>5%的谷)
        local_lows = []
        for i in range(24, n - 48):
            window_before = closes[max(0, i - 24):i + 1]
            decline = (window_before[0] - closes[i]) / window_before[0]
            if decline < 0.05:
                continue
            # 确认是低点: 后5根不创新低
            is_low = all(closes[i] <= closes[j] for j in range(i + 1, min(n, i + 6)))
            if is_low:
                local_lows.append(i)

        for low_i in local_lows:
            low_price = closes[low_i]
            # 跟踪反弹: 最多看72根(72小时)
            end_i = min(n, low_i + 72)
            rebound_closes = closes[low_i:end_i]

            # 反弹结束判定: 从高点回撤超过反弹幅度的50%
            peak_price = low_price
            peak_idx = 0
            final_idx = len(rebound_closes) - 1
            for j in range(1, len(rebound_closes)):
                if rebound_closes[j] > peak_price:
                    peak_price = rebound_closes[j]
                    peak_idx = j
                # 从峰值回撤超过反弹幅度的50% → 反弹结束
                rebound_amp = (peak_price - low_price) / low_price
                pullback = (peak_price - rebound_closes[j]) / low_price
                if rebound_amp > 0.02 and pullback > rebound_amp * 0.5:
                    final_idx = j
                    break

            rebound_slice = rebound_closes[:final_idx + 1]
            if len(rebound_slice) < 3:
                continue

            peak_in_slice = np.max(rebound_slice)
            final_price = rebound_slice[-1]

            profit = (final_price - low_price) / low_price * 100
            amp = (peak_in_slice - low_price) / low_price * 100
            speed = profit / max(len(rebound_slice), 1)

            # 反弹中最大回撤
            running_peak = np.maximum.accumulate(rebound_slice)
            running_dd = (running_peak - rebound_slice) / running_peak
            max_dd = np.max(running_dd) * 100

            if np.isfinite(profit) and np.isfinite(speed):
                results.append({
                    "profit": profit,
                    "speed": speed,
                    "amp": amp,
                    "drawdown": max_dd,
                })

        return results

    # ── 工具方法 ──

    def _to_percentiles(self, data: np.ndarray) -> dict:
        """计算百分位值"""
        result = {}
        for p in self.pctls:
            key = f"p{int(p * 100):02d}" if p < 1 else f"p{int(p * 100)}"
            result[key] = float(np.percentile(data, p * 100))
        return result

    def _save_percentiles(self, symbol: str, dist_type: str,
                          scale: str, window: str,
                          data: np.ndarray, now_ms: int):
        """计算并保存百分位分布"""
        clean = data[np.isfinite(data)]
        if len(clean) < 5:
            return
        pctls = self._to_percentiles(clean)
        self.db.save_distribution(
            symbol, dist_type, scale, window,
            percentiles=pctls, sample_count=len(clean), updated_at=now_ms
        )

    def _calc_cold_multiplier(self, data_days: int) -> float:
        """冷启动宽度倍率"""
        if data_days < Config.COLD_SKIP_DAYS:
            return 0.0  # 跳过
        elif data_days < Config.COLD_MONITOR_DAYS:
            return 0.0  # 只监控
        elif data_days < Config.COLD_TRADE_DAYS:
            return Config.COLD_MULTIPLIER
        else:
            return 1.0
