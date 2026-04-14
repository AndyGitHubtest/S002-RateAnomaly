"""S002 RateAnomaly - 1H数据下载器
从Binance API独立下载1H K线，2年历史。
支持断点续传、并发下载。
"""
import asyncio
import time
from typing import Optional

import aiohttp

from src.config import Config
from src.database import Database
from src.logger import log


class DataDownloader:
    BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(self, db: Database):
        self.db = db
        self.semaphore = asyncio.Semaphore(Config.DOWNLOAD_WORKERS)

    async def download_all(self, symbols: Optional[list[str]] = None):
        """下载所有币种的1H数据"""
        if symbols is None:
            symbols = await self._fetch_usdt_perp_symbols()

        log.info("Starting 1H download for %d symbols", len(symbols))
        start = time.time()

        tasks = [self._download_symbol(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if isinstance(r, int) and r > 0)
        failed = sum(1 for r in results if isinstance(r, Exception))
        total_candles = sum(r for r in results if isinstance(r, int))

        elapsed = time.time() - start
        log.info("Download complete: %d success, %d failed, "
                 "%d total candles, %.1fs",
                 success, failed, total_candles, elapsed)

    async def _fetch_usdt_perp_symbols(self) -> list[str]:
        """获取币安USDT永续合约所有交易对"""
        async with aiohttp.ClientSession() as session:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
            async with session.get(url) as resp:
                data = await resp.json()
            symbols = []
            for s in data.get("symbols", []):
                if (s["quoteAsset"] == "USDT"
                        and s["contractType"] == "PERPETUAL"
                        and s["status"] == "TRADING"):
                    symbols.append(s["symbol"])
            log.info("Found %d USDT perpetual symbols", len(symbols))
            return symbols

    async def _download_symbol(self, symbol: str) -> int:
        """下载单币1H数据，返回K线数量"""
        async with self.semaphore:
            try:
                # 检查已有数据量
                existing = self.db.get_kline_count(symbol)
                now_ms = int(time.time() * 1000)
                two_years_ms = Config.DATA_HOURS * 3600 * 1000
                start_ms = now_ms - two_years_ms

                # 如果已有足够数据，只下载增量
                if existing >= Config.DATA_HOURS * 0.9:
                    # 增量: 从最后一条开始
                    klines = self.db.get_klines(symbol, limit=1)
                    if klines:
                        last_ts = klines[0].get("ts", 0)
                        if last_ts > start_ms:
                            start_ms = last_ts + 3600 * 1000  # 跳过已有

                all_rows = []
                current_start = start_ms
                batch_size = 1500  # Binance最大1500

                async with aiohttp.ClientSession() as session:
                    while current_start < now_ms:
                        params = {
                            "symbol": symbol,
                            "interval": "1h",
                            "startTime": current_start,
                            "limit": batch_size,
                        }
                        async with session.get(self.BINANCE_KLINE_URL,
                                               params=params) as resp:
                            if resp.status != 200:
                                log.error("Download %s failed: HTTP %d",
                                          symbol, resp.status)
                                break
                            data = await resp.json()

                        if not data:
                            break

                        for k in data:
                            all_rows.append((
                                int(k[0]),        # ts
                                symbol,           # symbol
                                float(k[1]),      # open
                                float(k[2]),      # high
                                float(k[3]),      # low
                                float(k[4]),      # close
                                float(k[5]),      # volume
                                float(k[7]),      # quote_volume
                                int(k[8]),        # trades
                            ))

                        # 下一批从最后一条的下一个小时开始
                        current_start = int(data[-1][0]) + 3600 * 1000

                        # 限速
                        await asyncio.sleep(0.1)

                if all_rows:
                    self.db.insert_klines(all_rows)

                log.debug("Downloaded %s: %d candles (existing: %d)",
                          symbol, len(all_rows), existing)
                return len(all_rows)

            except Exception as e:
                log.error("Download %s error: %s", symbol, str(e))
                return 0

    async def incremental_update(self):
        """增量更新: 只下载最新数据"""
        symbols = self.db.get_symbols()
        log.info("Incremental update for %d symbols", len(symbols))
        await self.download_all(symbols)
