"""S002 RateAnomaly - 主入口
定时扫描 + 实时监控循环
"""
import asyncio
import signal
import sys
import time
from pathlib import Path

from src.config import Config
from src.database import Database
from src.logger import log
from src.data_downloader import DataDownloader
from src.distribution import DistributionBuilder
from src.anomaly import AnomalyDetector
from src.bottom import BottomConfirmer
from src.position import PositionBuilder
from src.profit import ProfitManager
from src.risk import RiskManager
from src.scanner import Scanner
from src.optimizer import BayesianOptimizer
from src.notify import send_telegram, format_scan_summary


class S002Engine:
    def __init__(self):
        self.db = Database()
        self.scanner = Scanner(self.db)
        self.downloader = DataDownloader(self.db)
        self.optimizer = BayesianOptimizer(self.db)
        self._running = False
        self._last_scan_ts = 0
        self._last_download_ts = 0
        self._last_optimize_ts = 0

    async def run(self):
        """主循环"""
        self._running = True
        log.info("S002 RateAnomaly Engine starting...")
        await send_telegram("🟢 S002 RateAnomaly 引擎启动")

        # 启动时先下载数据
        if Config.DOWNLOAD_ON_START:
            log.info("Initial data download...")
            await self.downloader.download_all()
            self._last_download_ts = int(time.time() * 1000)

        # 启动时先构建分布
        log.info("Building initial distributions...")
        symbols = self.db.get_symbols()
        for i, symbol in enumerate(symbols):
            self.scanner.dist_builder.build_all_for_symbol(symbol)
            if (i + 1) % 50 == 0:
                log.info("  initial dist: %d/%d", i + 1, len(symbols))

        # 主循环
        while self._running:
            try:
                now = int(time.time() * 1000)
                hour_ms = 3600 * 1000

                # 每小时扫描
                if now - self._last_scan_ts >= hour_ms:
                    self.scanner.run_hourly_scan()
                    self._last_scan_ts = now

                # 每6小时增量下载数据
                if now - self._last_download_ts >= 6 * hour_ms:
                    await self.downloader.incremental_update()
                    self._last_download_ts = now

                # 每24小时参数优化
                if now - self._last_optimize_ts >= 24 * hour_ms:
                    self.optimizer.optimize_all()
                    self._last_optimize_ts = now

                # 每5分钟止盈+风控检查
                self.scanner.run_profit_check()

                # 等待
                await asyncio.sleep(Config.CHECK_INTERVAL)

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error("Main loop error: %s", str(e), exc_info=True)
                await asyncio.sleep(30)

        log.info("S002 Engine stopped")
        await send_telegram("🔴 S002 RateAnomaly 引擎停止")
        self.db.close()

    def stop(self):
        self._running = False


async def download_only():
    """仅下载数据模式"""
    db = Database()
    downloader = DataDownloader(db)
    await downloader.download_all()
    db.close()


async def optimize_only():
    """仅优化模式"""
    db = Database()
    optimizer = BayesianOptimizer(db)
    optimizer.optimize_all()
    db.close()


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "download":
            asyncio.run(download_only())
        elif cmd == "optimize":
            asyncio.run(optimize_only())
        elif cmd == "scan":
            db = Database()
            scanner = Scanner(db)
            scanner.run_hourly_scan()
            db.close()
        else:
            print(f"Usage: python -m src.main [download|optimize|scan]")
            sys.exit(1)
    else:
        engine = S002Engine()

        def handle_signal(signum, frame):
            log.info("Received signal %d, stopping...", signum)
            engine.stop()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        asyncio.run(engine.run())


if __name__ == "__main__":
    main()
