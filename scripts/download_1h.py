"""S002 1H数据下载脚本 - 独立运行，不依赖主程序
用法: python3 download_1h.py [--workers 3] [--years 2]
"""
import asyncio
import sqlite3
import time
import argparse
import aiohttp
from pathlib import Path

# 使用项目根目录的data/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "klines_1h.db"
BINANCE_KLINE = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines_1h (
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, quote_volume REAL, trades INTEGER,
    PRIMARY KEY (ts, symbol)
);
CREATE INDEX IF NOT EXISTS idx_klines_symbol_ts ON klines_1h(symbol, ts);
"""


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


async def fetch_perp_symbols(session):
    async with session.get(BINANCE_INFO) as resp:
        data = await resp.json()
    symbols = []
    for s in data.get("symbols", []):
        if (s["quoteAsset"] == "USDT"
                and s["contractType"] == "PERPETUAL"
                and s["status"] == "TRADING"):
            symbols.append(s["symbol"])
    return sorted(symbols)


async def download_symbol(session, symbol, sem, years):
    """下载单个symbol的1H数据，返回行列表"""
    async with sem:
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - years * 365 * 24 * 3600 * 1000

            all_rows = []
            current = start_ms
            while current < now_ms:
                params = {
                    "symbol": symbol,
                    "interval": "1h",
                    "startTime": current,
                    "limit": 1500,
                }
                async with session.get(BINANCE_KLINE, params=params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        print(f"  HTTP {resp.status} for {symbol}: {text[:100]}")
                        break
                    data = await resp.json()
                if not data:
                    break
                for k in data:
                    all_rows.append((
                        int(k[0]), symbol,
                        float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                        float(k[5]), float(k[7]), int(k[8]),
                    ))
                current = int(data[-1][0]) + 3600 * 1000
                await asyncio.sleep(0.05)

            return all_rows
        except Exception as e:
            print(f"  ERROR {symbol}: {e}")
            return []


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    conn = init_db()
    sem = asyncio.Semaphore(args.workers)

    async with aiohttp.ClientSession() as session:
        symbols = await fetch_perp_symbols(session)
        print(f"Found {len(symbols)} USDT perpetual symbols")

        # 检查已下载的symbol
        existing = conn.execute(
            "SELECT symbol, COUNT(*) as cnt FROM klines_1h GROUP BY symbol"
        ).fetchall()
        existing_set = {s for s, cnt in existing if cnt >= args.years * 365 * 24 * 0.9}
        to_download = [s for s in symbols if s not in existing_set]
        print(f"Already have: {len(existing_set)}, Need to download: {len(to_download)}")

        if not to_download:
            print("All symbols already downloaded!")
            conn.close()
            return

        start = time.time()
        total_candles = 0
        done = 0

        # 分批下载，每批10个，减少并发避免API限流和SQLite锁
        batch_size = 10
        for i in range(0, len(to_download), batch_size):
            batch = to_download[i:i + batch_size]
            tasks = [download_symbol(session, s, sem, args.years)
                     for s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 批量写入DB（主线程串行写入，避免SQLite并发问题）
            batch_candles = 0
            for result in results:
                if isinstance(result, Exception):
                    print(f"  Exception: {result}")
                    continue
                if result:
                    conn.executemany(
                        "INSERT OR IGNORE INTO klines_1h "
                        "(ts, symbol, open, high, low, close, volume, quote_volume, trades) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        result
                    )
                    batch_candles += len(result)
            conn.commit()

            total_candles += batch_candles
            done += len(batch)
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(to_download) - done) / rate if rate > 0 else 0
            print(f"  [{done}/{len(to_download)}] +{batch_candles} candles | "
                  f"{elapsed:.0f}s elapsed, ETA {eta:.0f}s")

    elapsed = time.time() - start
    print(f"\nDone: {len(to_download)} symbols, {total_candles} candles, {elapsed:.0f}s")

    # 统计
    row = conn.execute("SELECT COUNT(DISTINCT symbol) as n, COUNT(*) as c FROM klines_1h").fetchone()
    print(f"DB: {row[0]} symbols, {row[1]} total rows")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
