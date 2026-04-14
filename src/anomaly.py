"""S002 RateAnomaly - 异常检测器
双维度: 速率p5 + 幅度p5。两者都超阈值才算异常。
"""
import time
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class AnomalyDetector:
    def __init__(self, db: Database):
        self.db = db

    def scan_all(self) -> list[dict]:
        """扫描成交量Top50币种，返回异常币种列表"""
        symbols = self.db.get_top_volume_symbols(top_n=50)
        now_ms = int(time.time() * 1000)
        anomalies = []

        for symbol in symbols:
            result = self.check_symbol(symbol, now_ms)
            if result is not None:
                anomalies.append(result)

        log.info("Anomaly scan: %d/%d symbols flagged (top50 volume)",
                 len(anomalies), len(symbols))
        return anomalies

    def check_symbol(self, symbol: str, now_ms: int) -> Optional[dict]:
        """检查单币是否异常
        条件: 速率 < rate_p5 AND 幅度 > amp_p5 (都在历史分布中极端)
        """
        # 获取最近K线
        klines = self.db.get_klines(symbol, limit=200)
        if len(klines) < 72:
            return None

        klines.sort(key=lambda x: x["ts"])
        closes = np.array([k["close"] for k in klines], dtype=np.float64)

        # 计算当前4个尺度的跌幅
        current_stats = {}
        for scale_name, hours in [("4h", 4), ("12h", 12), ("24h", 24), ("72h", 72)]:
            if len(closes) < hours + 1:
                continue
            window = closes[-(hours + 1):]
            peak_idx = np.argmax(window)
            if peak_idx < len(window) - 1:
                decline = (window[peak_idx] - window[-1]) / window[peak_idx]
                decline_hours = len(window) - 1 - peak_idx
                rate = decline / max(decline_hours, 1)
                current_stats[scale_name] = {"rate": rate, "amp": decline}

        if not current_stats:
            return None

        # 取最极端的尺度
        best_scale = max(current_stats, key=lambda s: current_stats[s]["amp"])
        stat = current_stats[best_scale]

        # 从分布读取阈值
        rate_dist = self.db.get_distribution(symbol, Config.DIST_DECLINE_RATE,
                                              best_scale, "2y")
        amp_dist = self.db.get_distribution(symbol, Config.DIST_DECLINE_AMP,
                                             best_scale, "2y")

        # 快窗口优先
        rate_dist_fast = self.db.get_distribution(symbol, Config.DIST_DECLINE_RATE,
                                                   best_scale, "90d")
        amp_dist_fast = self.db.get_distribution(symbol, Config.DIST_DECLINE_AMP,
                                                  best_scale, "90d")

        # 优先用快窗口，样本不够用主窗口
        rate_pctl_dist = rate_dist_fast if rate_dist_fast and rate_dist_fast["sample_count"] >= 30 else rate_dist
        amp_pctl_dist = amp_dist_fast if amp_dist_fast and amp_dist_fast["sample_count"] >= 30 else amp_dist

        if rate_pctl_dist is None or amp_pctl_dist is None:
            return None

        # 冷启动宽度倍率
        meta = self.db.get_coin_meta(symbol)
        cold_mult = meta["cold_start_multiplier"] if meta else 1.0
        if cold_mult <= 0:
            return None  # 数据不足，跳过

        # 阈值 = p5 × 冷启动倍率(倍率>1时放宽)
        rate_threshold = rate_pctl_dist["percentiles"].get("p05", 0.01) * cold_mult
        amp_threshold = amp_pctl_dist["percentiles"].get("p05", 0.01) * cold_mult

        # 计算当前值在分布中的分位
        rate_pctl = self._estimate_percentile(stat["rate"],
                                               rate_pctl_dist["percentiles"])
        amp_pctl = self._estimate_percentile(stat["amp"],
                                              amp_pctl_dist["percentiles"])

        # 双维度判断: 速率极端(跌得快) AND 幅度极端(跌得多)
        is_anomaly = (stat["rate"] >= rate_threshold and
                      stat["amp"] >= amp_threshold)

        if not is_anomaly:
            return None

        # 异常评分 = 速率分位 × 幅度分位 × 10000
        anomaly_score = rate_pctl * amp_pctl * 10000

        # 保存异常事件
        anomaly_id = self.db.save_anomaly(
            symbol=symbol,
            ts=now_ms,
            decline_rate=stat["rate"],
            decline_amp=stat["amp"],
            rate_pctl=rate_pctl,
            amp_pctl=amp_pctl,
            anomaly_score=anomaly_score
        )

        log.info("ANOMALY %s: scale=%s rate=%.4f(%.1fp) amp=%.4f(%.1fp) score=%.0f",
                 symbol, best_scale, stat["rate"], rate_pctl * 100,
                 stat["amp"], amp_pctl * 100, anomaly_score)

        return {
            "anomaly_id": anomaly_id,
            "symbol": symbol,
            "scale": best_scale,
            "decline_rate": stat["rate"],
            "decline_amp": stat["amp"],
            "rate_pctl": rate_pctl,
            "amp_pctl": amp_pctl,
            "anomaly_score": anomaly_score,
        }

    def check_systemic_crash(self, anomalies: list[dict]) -> list[dict]:
        """系统性崩溃过滤: >30%币同时异常时，只取跌幅最深的5个"""
        total_symbols = len(self.db.get_symbols())
        if total_symbols == 0:
            return anomalies

        ratio = len(anomalies) / total_symbols
        if ratio < Config.SYSTEMIC_THRESHOLD:
            return anomalies  # 非系统性，全部保留

        # 系统性崩溃: 按跌幅排序，取top N
        sorted_anomalies = sorted(anomalies, key=lambda x: x["decline_amp"],
                                   reverse=True)
        top_n = sorted_anomalies[:Config.SYSTEMIC_TOP_N]

        log.warning("SYSTEMIC CRASH: %d/%d (%.0f%%) coins anomalous, "
                     "taking top %d deepest",
                     len(anomalies), total_symbols, ratio * 100,
                     len(top_n))

        # 标记被过滤的为expired
        top_ids = {a["anomaly_id"] for a in top_n}
        for a in anomalies:
            if a["anomaly_id"] not in top_ids:
                self.db.conn.execute(
                    "UPDATE anomalies SET status='expired' WHERE id=?",
                    (a["anomaly_id"],)
                )
        self.db.conn.commit()

        return top_n

    def _estimate_percentile(self, value: float, percentiles: dict) -> float:
        """估算值在分布中的分位(插值+外推)"""
        sorted_pcts = sorted(percentiles.items(), key=lambda x: x[1])
        for i, (key, pval) in enumerate(sorted_pcts):
            if value <= pval:
                if i == 0:
                    return float(key.replace("p", "")) / 100
                prev_key, prev_val = sorted_pcts[i - 1]
                prev_pct = float(prev_key.replace("p", "")) / 100
                curr_pct = float(key.replace("p", "")) / 100
                if prev_val == pval:
                    return curr_pct
                ratio = (value - prev_val) / (pval - prev_val)
                return prev_pct + ratio * (curr_pct - prev_pct)
        # 超过最大分位(p99): 基于p95→p99斜率外推，cap 0.999
        if len(sorted_pcts) >= 2:
            last_key, last_val = sorted_pcts[-1]
            prev_key, prev_val = sorted_pcts[-2]
            last_pct = float(last_key.replace("p", "")) / 100
            prev_pct = float(prev_key.replace("p", "")) / 100
            if last_val > prev_val:
                slope = (last_pct - prev_pct) / (last_val - prev_val)
                extrapolated = last_pct + slope * (value - last_val)
                return min(extrapolated, 0.999)
        return 0.99
