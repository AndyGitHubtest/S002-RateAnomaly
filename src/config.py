"""S002 RateAnomaly - 配置中心
全per-coin参数，零全局硬编码。所有阈值从分布动态读取。
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── 交易所 ──
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

    # ── Telegram ──
    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "")
    TG_CHAT_ID: str = os.getenv("TG_CHAT_ID", "")

    # ── 数据库 ──
    DB_PATH: str = os.getenv("DB_PATH", "data/s002.db")

    # ── 下载 ──
    DOWNLOAD_WORKERS: int = int(os.getenv("DOWNLOAD_WORKERS", "5"))
    DOWNLOAD_BATCH_SIZE: int = int(os.getenv("DOWNLOAD_BATCH_SIZE", "10"))
    DOWNLOAD_ON_START: bool = os.getenv("DOWNLOAD_ON_START", "true").lower() == "true"

    # ── 主循环 ──
    CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "300"))  # 5分钟

    # ── 扫描 ──
    SCAN_INTERVAL_HOURS: int = int(os.getenv("SCAN_INTERVAL_HOURS", "1"))
    MAX_COINS: int = int(os.getenv("MAX_COINS", "5"))
    MAX_SAME_SECTOR: int = int(os.getenv("MAX_SAME_SECTOR", "2"))

    # ── 风控 ──
    LEVERAGE: int = int(os.getenv("LEVERAGE", "5"))
    EQUITY_USDT: float = float(os.getenv("EQUITY_USDT", "1000"))
    POSITION_PCT: float = float(os.getenv("POSITION_PCT", "0.10"))
    HARD_STOP_PCT: float = float(os.getenv("HARD_STOP_PCT", "0.10"))

    # ── 分布窗口 ──
    MAIN_WINDOW_DAYS: int = 730       # 2年主窗口
    FAST_WINDOW_DAYS: int = 90        # 90天快窗口

    # ── 异常阈值 ──
    ANOMALY_RATE_PCTL: float = 0.05   # 速率p5
    ANOMALY_AMP_PCTL: float = 0.05    # 幅度p5

    # ── 止跌确认 ──
    CONFIRM_SCORE_100: float = 7000   # 满分入场阈值
    CONFIRM_SCORE_70: float = 5000    # 70%仓位阈值

    # ── 止盈 ──
    TP1_PCTL: float = 0.50            # profit分布p50
    TP1_CLOSE_PCT: float = 0.30       # 平30%
    TP2_PCTL: float = 0.75            # profit分布p75
    TP2_CLOSE_PCT: float = 0.30       # 平30%
    TRAILING_CLOSE_PCT: float = 0.40  # trailing平40%

    # ── 冷启动 ──
    COLD_SKIP_DAYS: int = 30          # <30天跳过
    COLD_MONITOR_DAYS: int = 90       # 30-90天只监控
    COLD_TRADE_DAYS: int = 365        # 90-365天1.5x宽度
    COLD_MULTIPLIER: float = 1.5      # 冷启动宽度倍率

    # ── 系统性崩溃 ──
    SYSTEMIC_THRESHOLD: float = 0.30   # 30%币同时异常
    SYSTEMIC_TOP_N: int = 5            # 只取跌幅最深的5个

    # ── 数据 ──
    TIMEFRAME: str = "1h"
    DATA_HOURS: int = 730 * 24        # 2年小时数

    # ── 分布类型枚举 ──
    DIST_DECLINE_RATE: str = "decline_rate"
    DIST_DECLINE_AMP: str = "decline_amp"
    DIST_BOTTOM_FEATURE: str = "bottom_feature"
    DIST_DEPTH_CLUSTER: str = "depth_cluster"
    DIST_PROFIT: str = "profit"
    DIST_REBOUND_SPEED: str = "rebound_speed"
    DIST_REBOUND_AMP: str = "rebound_amp"
    DIST_DRAWDOWN: str = "drawdown"

    DIST_TYPES: list = [
        DIST_DECLINE_RATE, DIST_DECLINE_AMP, DIST_BOTTOM_FEATURE,
        DIST_DEPTH_CLUSTER, DIST_PROFIT, DIST_REBOUND_SPEED,
        DIST_REBOUND_AMP, DIST_DRAWDOWN
    ]

    # ── 跌幅尺度 ──
    DECLINE_SCALES: list = ["4h", "12h", "24h", "72h"]

    # ── 百分位列表 ──
    PERCENTILES: list = [0.01, 0.05, 0.10, 0.25, 0.30, 0.50,
                         0.75, 0.90, 0.95, 0.99]

    @classmethod
    def per_coin_notional(cls) -> float:
        """单币名义值 = equity × position_pct × leverage"""
        return cls.EQUITY_USDT * cls.POSITION_PCT * cls.LEVERAGE
