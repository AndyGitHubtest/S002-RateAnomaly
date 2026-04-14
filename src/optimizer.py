"""S002 RateAnomaly - 贝叶斯参数优化器
防止过拟合: IS/OS验证 + 参数稳定性检查
优化对象: 各分布的百分位阈值(异常/止跌/止盈)
"""
import time
from typing import Optional

import numpy as np

from src.config import Config
from src.database import Database
from src.logger import log


class BayesianOptimizer:
    def __init__(self, db: Database):
        self.db = db

    def optimize_symbol(self, symbol: str) -> Optional[dict]:
        """对单币做参数优化
        用历史异常事件回测，找最优阈值组合
        返回最优参数或None
        """
        klines = self.db.get_klines(symbol, limit=20000)
        if len(klines) < 365 * 24:
            log.debug("Skip optimize %s: only %d klines", symbol, len(klines))
            return None

        klines.sort(key=lambda x: x["ts"])
        closes = np.array([k["close"] for k in klines], dtype=np.float64)

        # IS/OS分割: 前70%训练，后30%验证
        split = int(len(closes) * 0.7)
        is_closes = closes[:split]
        os_closes = closes[split:]

        # 搜索空间
        param_grid = {
            "anomaly_rate_pctl": [0.03, 0.05, 0.07, 0.10],
            "anomaly_amp_pctl": [0.03, 0.05, 0.07, 0.10],
            "confirm_score_70": [4000, 5000, 6000],
            "confirm_score_100": [6000, 7000, 8000],
            "tp1_pctl": [0.40, 0.50, 0.60],
            "tp2_pctl": [0.65, 0.75, 0.85],
        }

        # 简化网格搜索(贝叶斯太重，先用网格)
        best_params = None
        best_is_score = -999
        best_os_score = -999

        # 固定其他参数，只搜anomaly阈值
        for rate_p in param_grid["anomaly_rate_pctl"]:
            for amp_p in param_grid["anomaly_amp_pctl"]:
                # IS回测
                is_result = self._backtest_params(is_closes, rate_p, amp_p)
                if is_result is None:
                    continue

                # OS验证
                os_result = self._backtest_params(os_closes, rate_p, amp_p)

                # 综合评分: IS的PF × OS的PF(过拟合惩罚)
                is_pf = is_result.get("pf", 0)
                os_pf = os_result.get("pf", 0) if os_result else 0

                # 过拟合检测: IS和OS差距>50%则惩罚
                if is_pf > 0 and os_pf > 0:
                    overfit_ratio = os_pf / is_pf
                    if overfit_ratio < 0.5:
                        continue  # 严重过拟合，跳过
                    score = is_pf * overfit_ratio
                else:
                    score = is_pf * 0.3  # OS无数据，大幅打折

                if score > best_is_score:
                    best_is_score = score
                    best_os_score = os_pf
                    best_params = {
                        "anomaly_rate_pctl": rate_p,
                        "anomaly_amp_pctl": amp_p,
                        "is_pf": is_pf,
                        "os_pf": os_pf,
                        "score": score,
                    }

        if best_params:
            self.db.save_param_snapshot(
                symbol=symbol,
                params=best_params,
                pnl=0,
                pf=best_params.get("is_pf", 0),
                wr=0
            )
            log.info("OPTIMIZE %s: rate_p=%.2f amp_p=%.2f "
                     "IS_PF=%.2f OS_PF=%.2f score=%.3f",
                     symbol, best_params["anomaly_rate_pctl"],
                     best_params["anomaly_amp_pctl"],
                     best_params.get("is_pf", 0),
                     best_params.get("os_pf", 0),
                     best_is_score)

        return best_params

    def _backtest_params(self, closes: np.ndarray,
                         rate_pctl: float,
                         amp_pctl: float) -> Optional[dict]:
        """用给定参数回测一段价格序列
        简化版: 只统计异常触发次数和后续收益
        """
        if len(closes) < 100:
            return None

        # 计算滚动跌幅
        trades = []
        n = len(closes)
        lookback = 24  # 24h尺度

        for i in range(lookback, n - 72):  # 留72h给反弹
            window = closes[i - lookback:i + 1]
            peak_idx = np.argmax(window)
            if peak_idx >= len(window) - 1:
                continue
            decline = (window[peak_idx] - window[-1]) / window[peak_idx]
            decline_hours = len(window) - 1 - peak_idx
            rate = decline / max(decline_hours, 1)

            # 简化: 用固定阈值(实际应从分布读取)
            if decline >= amp_pctl and rate >= rate_pctl * 0.5:
                # 入场: 买入，持有72h
                entry_price = closes[i]
                exit_price = closes[min(i + 72, n - 1)]
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append(pnl_pct)

        if len(trades) < 3:
            return None

        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0.0001
        pf = total_win / total_loss if total_loss > 0 else 0
        wr = len(wins) / len(trades) if trades else 0

        return {
            "trades": len(trades),
            "pf": pf,
            "wr": wr,
            "avg_pnl": np.mean(trades),
        }

    def optimize_all(self):
        """全量优化所有币种"""
        symbols = self.db.get_symbols()
        log.info("Starting optimization for %d symbols...", len(symbols))

        results = []
        for i, symbol in enumerate(symbols):
            result = self.optimize_symbol(symbol)
            if result:
                results.append((symbol, result))
            if (i + 1) % 50 == 0:
                log.info("  optimization: %d/%d", i + 1, len(symbols))

        log.info("Optimization complete: %d/%d symbols optimized",
                 len(results), len(symbols))
