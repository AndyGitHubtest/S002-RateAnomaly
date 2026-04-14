"""S002 1H数据下载脚本 - 串行版，避免Binance限流
用法: python3 download_1h_serial.py [--delay 0.5] [--years 2]
"""
import asyncio
import sqlite3
import time
import argparse
import ssl
import aiohttp
from pathlib import Path

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
        if resp.status != 200:
            text = await resp.text()
            print(f"ExchangeInfo HTTP {resp.status}: {text[:200]}")
            return []
        data = await resp.json()
    symbols = []
    for s in data.get("symbols", []):
        if (s["quoteAsset"] == "USDT"
                and s["contractType"] == "PERPETUAL"
                and s["status"] == "TRADING"):
            symbols.append(s["symbol"])
    return sorted(symbols)


async def download_one(session, symbol, years, delay):
    """下载单个symbol，串行调用"""
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
                if resp.status == 418 or resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    print(f"  RATE LIMITED on {symbol}, waiting {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue
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
            await asyncio.sleep(0.1)

        await asyncio.sleep(delay)
        return all_rows
    except Exception as e:
        print(f"  ERROR {symbol}: {e}")
        return []


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between symbols (s)")
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    conn = init_db()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    tcp_conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=tcp_conn) as session:
        # 获取symbol列表
        symbols = await fetch_perp_symbols(session)
        if not symbols:
            print("Failed to fetch symbols, aborting")
            conn.close()
            return
        print(f"Found {len(symbols)} USDT perpetual symbols")

        # 检查已下载
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
        failed = []

        # 串行下载，一个一个来
        for symbol in to_download:
            rows = await download_one(session, symbol, args.years, args.delay)
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO klines_1h "
                    "(ts, symbol, open, high, low, close, volume, quote_volume, trades) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    rows
                )
                conn.commit()
                total_candles += len(rows)
            else:
                failed.append(symbol)

            done += 1
            if done % 20 == 0 or done == len(to_download):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(to_download) - done) / rate if rate > 0 else 0
                print(f"  [{done}/{len(to_download)}] +{total_candles} candles | "
                      f"{elapsed:.0f}s, ETA {eta:.0f}s | failed: {len(failed)}")

    elapsed = time.time() - start
    print(f"\nDone: {len(to_download)} symbols, {total_candles} candles, {elapsed:.0f}s")
    if failed:
        print(f"Failed: {failed[:20]}")

    row = conn.execute("SELECT COUNT(DISTINCT symbol) as n, COUNT(*) as c FROM klines_1h").fetchone()
    print(f"DB total: {row[0]} symbols, {row[1]} rows")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
