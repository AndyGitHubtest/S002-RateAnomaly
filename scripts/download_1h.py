"""S002 1H数据下载脚本 - 独立运行，不依赖主程序
用法: python3 download_1h.py [--workers 5] [--years 2]
"""
import asyncio
import sqlite3
import time
import json
import argparse
import aiohttp
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "klines_1h.db"
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
    return symbols


async def download_symbol(session, symbol, sem, conn, years):
    async with sem:
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - years * 365 * 24 * 3600 * 1000

            # 检查已有数据
            row = conn.execute(
                "SELECT MAX(ts) as max_ts, COUNT(*) as cnt FROM klines_1h WHERE symbol=?",
                (symbol,)
            ).fetchone()
            if row and row[0] and row[1] >= years * 365 * 24 * 0.9:
                start_ms = row[0] + 3600 * 1000  # 增量

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

            if all_rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO klines_1h "
                    "(ts, symbol, open, high, low, close, volume, quote_volume, trades) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    all_rows
                )
                conn.commit()
            return len(all_rows)
        except Exception as e:
            print(f"  ERROR {symbol}: {e}")
            return 0


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    conn = init_db()
    sem = asyncio.Semaphore(args.workers)

    async with aiohttp.ClientSession() as session:
        symbols = await fetch_perp_symbols(session)
        print(f"Found {len(symbols)} USDT perpetual symbols")

        start = time.time()
        total_candles = 0
        done = 0

        # 分批下载，每批20个
        batch_size = 20
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [download_symbol(session, s, sem, conn, args.years)
                     for s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            batch_candles = sum(r for r in results if isinstance(r, int))
            total_candles += batch_candles
            done += len(batch)
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - done) / rate if rate > 0 else 0
            print(f"  [{done}/{len(symbols)}] +{batch_candles} candles | "
                  f"{elapsed:.0f}s elapsed, ETA {eta:.0f}s")

    elapsed = time.time() - start
    print(f"\nDone: {len(symbols)} symbols, {total_candles} candles, {elapsed:.0f}s")

    # 统计
    row = conn.execute("SELECT COUNT(DISTINCT symbol) as n, COUNT(*) as c FROM klines_1h").fetchone()
    print(f"DB: {row[0]} symbols, {row[1]} total rows")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
