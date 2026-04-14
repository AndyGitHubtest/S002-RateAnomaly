"""S002 RateAnomaly - 扫描调度器
3层漏斗:
  1H扫描: 全量200+币 → 异常检测
  候选确认: 异常币 → 止跌确认
  入场执行: 确认币 → 仓位构建
"""
import time

from src.config import Config
from src.database import Database
from src.logger import log
from src.distribution import DistributionBuilder
from src.anomaly import AnomalyDetector
from src.bottom import BottomConfirmer
from src.position import PositionBuilder
from src.profit import ProfitManager
from src.risk import RiskManager


class Scanner:
    def __init__(self, db: Database):
        self.db = db
        self.dist_builder = DistributionBuilder(db)
        self.anomaly_detector = AnomalyDetector(db)
        self.bottom_confirmer = BottomConfirmer(db)
        self.position_builder = PositionBuilder(db)
        self.profit_manager = ProfitManager(db)
        self.risk_manager = RiskManager(db)

    def run_hourly_scan(self):
        """每小时扫描: 增量更新分布 + 异常检测 + 确认 + 入场"""
        start_ms = int(time.time() * 1000)
        log.info("=" * 60)
        log.info("HOURLY SCAN START")
        log.info("=" * 60)

        # Phase 0: 增量更新分布
        symbols = self.db.get_symbols()
        log.info("Phase 0: Updating distributions for %d symbols...", len(symbols))
        for i, symbol in enumerate(symbols):
            self.dist_builder.build_all_for_symbol(symbol)
            if (i + 1) % 50 == 0:
                log.info("  distributions: %d/%d", i + 1, len(symbols))

        # Phase 1: 异常检测
        log.info("Phase 1: Anomaly detection...")
        anomalies = self.anomaly_detector.scan_all()

        if not anomalies:
            log.info("No anomalies found, scan complete")
            duration = int(time.time() * 1000) - start_ms
            self.db.save_scan_log("hourly", len(symbols), 0, 0, 0, duration)
            return

        # Phase 1.5: 系统性崩溃过滤
        anomalies = self.anomaly_detector.check_systemic_crash(anomalies)

        # Phase 2: 止跌确认
        log.info("Phase 2: Bottom confirmation for %d anomalies...", len(anomalies))
        confirmed = []
        for anomaly in anomalies:
            result = self.bottom_confirmer.confirm(anomaly)
            if result is not None:
                confirmed.append(result)

        if not confirmed:
            log.info("No bottom confirmed, scan complete")
            duration = int(time.time() * 1000) - start_ms
            self.db.save_scan_log("hourly", len(symbols),
                                   len(anomalies), 0, 0, duration)
            return

        # Phase 3: 仓位构建
        log.info("Phase 3: Building positions for %d confirmed...", len(confirmed))
        entered = 0
        for conf in confirmed:
            can_open, reason = self.risk_manager.can_open(conf["symbol"])
            if not can_open:
                log.warning("SKIP %s: %s", conf["symbol"], reason)
                continue
            pos_id = self.position_builder.build(conf)
            if pos_id is not None:
                entered += 1

        # Phase 4: 检查已有持仓止盈
        log.info("Phase 4: Checking take-profit conditions...")
        self.profit_manager.check_all()

        # Phase 5: 风控检查
        log.info("Phase 5: Risk checks...")
        # 检查每个持仓的风控
        open_positions = self.db.get_open_positions()
        for pos in open_positions:
            reason = self.risk_manager.check_position_risk(pos)
            if reason:
                self.db.close_position(pos["id"], reason,
                                        pos.get("unrealized_pnl", 0))

        # 组合风控
        forced_close = self.risk_manager.check_portfolio_risk()
        for symbol in forced_close:
            for pos in open_positions:
                if pos["symbol"] == symbol:
                    self.db.close_position(pos["id"], "forced_risk",
                                            pos.get("unrealized_pnl", 0))

        duration = int(time.time() * 1000) - start_ms
        self.db.save_scan_log("hourly", len(symbols),
                               len(anomalies), len(confirmed),
                               entered, duration)

        log.info("=" * 60)
        log.info("HOURLY SCAN COMPLETE: %d scanned, %d anomalies, "
                 "%d confirmed, %d entered, %dms",
                 len(symbols), len(anomalies), len(confirmed),
                 entered, duration)
        log.info("=" * 60)

    def run_profit_check(self):
        """独立止盈检查(可更频繁调用)"""
        self.profit_manager.check_all()

        open_positions = self.db.get_open_positions()
        for pos in open_positions:
            reason = self.risk_manager.check_position_risk(pos)
            if reason:
                self.db.close_position(pos["id"], reason,
                                        pos.get("unrealized_pnl", 0))
