"""S002 RateAnomaly - 数据库模块
所有数据落库。SQLite WAL模式。
"""
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.config import Config
from src.logger import log


class Database:
    SCHEMA_SQL = """
    -- 1. 1H K线
    CREATE TABLE IF NOT EXISTS klines_1h (
        ts INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, quote_volume REAL, trades INTEGER,
        PRIMARY KEY (ts, symbol)
    );

    -- 2. 每币8分布
    CREATE TABLE IF NOT EXISTS distributions (
        symbol TEXT NOT NULL,
        dist_type TEXT NOT NULL,
        scale TEXT NOT NULL DEFAULT '',
        window TEXT NOT NULL DEFAULT '2y',
        percentiles TEXT NOT NULL DEFAULT '{}',
        params TEXT,
        sample_count INTEGER DEFAULT 0,
        updated_at INTEGER DEFAULT 0,
        PRIMARY KEY (symbol, dist_type, scale, window)
    );

    -- 3. 异常事件
    CREATE TABLE IF NOT EXISTS anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        ts INTEGER NOT NULL,
        decline_rate REAL,
        decline_amp REAL,
        rate_pctl REAL,
        amp_pctl REAL,
        anomaly_score REAL,
        confirmed INTEGER DEFAULT 0,
        confirmed_at INTEGER,
        confirmation_score REAL,
        status TEXT DEFAULT 'pending'
    );

    -- 4. 持仓
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL DEFAULT 'long',
        entry_price REAL,
        current_price REAL,
        entry_ts INTEGER,
        layers TEXT NOT NULL DEFAULT '[]',
        total_qty REAL DEFAULT 0,
        notional REAL DEFAULT 0,
        anomaly_id INTEGER,
        stop_price REAL,
        status TEXT DEFAULT 'open',
        close_reason TEXT,
        close_ts INTEGER,
        realized_pnl REAL DEFAULT 0,
        unrealized_pnl REAL DEFAULT 0
    );

    -- 5. 止盈
    CREATE TABLE IF NOT EXISTS take_profits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id INTEGER NOT NULL,
        tp_level TEXT NOT NULL,
        price REAL,
        qty REAL,
        pct REAL,
        pnl REAL DEFAULT 0,
        ts INTEGER NOT NULL
    );

    -- 6. 风控事件
    CREATE TABLE IF NOT EXISTS risk_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        event_type TEXT NOT NULL,
        ts INTEGER NOT NULL,
        details TEXT
    );

    -- 7. 币种元数据
    CREATE TABLE IF NOT EXISTS coin_meta (
        symbol TEXT PRIMARY KEY,
        sector TEXT,
        tier TEXT,
        data_start_ts INTEGER,
        data_days INTEGER,
        cold_start_multiplier REAL DEFAULT 1.0,
        active INTEGER DEFAULT 1,
        updated_at INTEGER
    );

    -- 8. 参数快照
    CREATE TABLE IF NOT EXISTS param_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        params TEXT NOT NULL,
        backtest_pnl REAL,
        backtest_pf REAL,
        backtest_wr REAL,
        ts INTEGER NOT NULL
    );

    -- 9. 扫描日志
    CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_type TEXT NOT NULL,
        coins_scanned INTEGER DEFAULT 0,
        anomalies_found INTEGER DEFAULT 0,
        confirmed INTEGER DEFAULT 0,
        entered INTEGER DEFAULT 0,
        duration_ms INTEGER DEFAULT 0,
        ts INTEGER NOT NULL
    );

    -- 索引
    CREATE INDEX IF NOT EXISTS idx_klines_symbol_ts ON klines_1h(symbol, ts);
    CREATE INDEX IF NOT EXISTS idx_anomalies_symbol_ts ON anomalies(symbol, ts);
    CREATE INDEX IF NOT EXISTS idx_anomalies_status ON anomalies(status);
    CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
    CREATE INDEX IF NOT EXISTS idx_distributions_type ON distributions(dist_type);
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(self.SCHEMA_SQL)
        conn.commit()
        log.info("Database initialized: %s", self.db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_conn()

    # ── K线操作 ──

    def insert_klines(self, rows: list[tuple]):
        """批量插入1h K线，忽略重复"""
        if not rows:
            return
        conn = self.conn
        conn.executemany(
            "INSERT OR IGNORE INTO klines_1h (ts, symbol, open, high, low, close, volume, quote_volume, trades) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        conn.commit()

    def get_klines(self, symbol: str, limit: int = 20000,
                   start_ts: Optional[int] = None) -> list[dict]:
        """获取某币K线，返回字典列表"""
        conn = self.conn
        if start_ts:
            rows = conn.execute(
                "SELECT * FROM klines_1h WHERE symbol=? AND ts>=? ORDER BY ts",
                (symbol, start_ts)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM klines_1h WHERE symbol=? ORDER BY ts DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_symbols(self) -> list[str]:
        """获取所有有数据的币种"""
        rows = self.conn.execute(
            "SELECT DISTINCT symbol FROM klines_1h ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def get_top_volume_symbols(self, top_n: int = 50) -> list[str]:
        """获取24h成交量Top N币种"""
        max_ts = self.conn.execute(
            "SELECT MAX(ts) FROM klines_1h"
        ).fetchone()[0]
        if max_ts is None:
            return []
        rows = self.conn.execute(
            """SELECT symbol FROM klines_1h
               WHERE ts >= ? - 86400000
               GROUP BY symbol
               ORDER BY SUM(quote_volume) DESC
               LIMIT ?""",
            (max_ts, top_n)
        ).fetchall()
        return [r["symbol"] for r in rows]

    def get_kline_count(self, symbol: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM klines_1h WHERE symbol=?", (symbol,)
        ).fetchone()
        return row["cnt"] if row else 0

    # ── 分布操作 ──

    def save_distribution(self, symbol: str, dist_type: str,
                          scale: str, window: str,
                          percentiles: dict, params: Optional[dict] = None,
                          sample_count: int = 0, updated_at: int = 0):
        conn = self.conn
        conn.execute(
            "INSERT OR REPLACE INTO distributions "
            "(symbol, dist_type, scale, window, percentiles, params, sample_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, dist_type, scale, window,
             json.dumps(percentiles, ensure_ascii=False),
             json.dumps(params, ensure_ascii=False) if params else None,
             sample_count, updated_at)
        )
        conn.commit()

    def get_distribution(self, symbol: str, dist_type: str,
                         scale: str = "", window: str = "2y") -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM distributions WHERE symbol=? AND dist_type=? AND scale=? AND window=?",
            (symbol, dist_type, scale, window)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["percentiles"] = json.loads(result["percentiles"])
        if result["params"]:
            result["params"] = json.loads(result["params"])
        return result

    # ── 异常事件操作 ──

    def save_anomaly(self, symbol: str, ts: int, decline_rate: float,
                     decline_amp: float, rate_pctl: float, amp_pctl: float,
                     anomaly_score: float) -> int:
        conn = self.conn
        cur = conn.execute(
            "INSERT INTO anomalies (symbol, ts, decline_rate, decline_amp, "
            "rate_pctl, amp_pctl, anomaly_score, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (symbol, ts, decline_rate, decline_amp, rate_pctl, amp_pctl, anomaly_score)
        )
        conn.commit()
        return cur.lastrowid

    def update_anomaly_confirmed(self, anomaly_id: int, score: float):
        import time
        conn = self.conn
        conn.execute(
            "UPDATE anomalies SET confirmed=1, confirmation_score=?, "
            "confirmed_at=?, status='confirmed' WHERE id=?",
            (score, int(time.time() * 1000), anomaly_id)
        )
        conn.commit()

    def get_pending_anomalies(self, since_ts: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM anomalies WHERE status='pending' AND ts>=? ORDER BY ts DESC",
            (since_ts,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 持仓操作 ──

    def save_position(self, symbol: str, side: str, entry_price: float,
                      layers: list, total_qty: float, notional: float,
                      anomaly_id: Optional[int], stop_price: float) -> int:
        import time
        conn = self.conn
        cur = conn.execute(
            "INSERT INTO positions (symbol, side, entry_price, current_price, "
            "entry_ts, layers, total_qty, notional, anomaly_id, stop_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (symbol, side, entry_price, entry_price,
             int(time.time() * 1000),
             json.dumps(layers, ensure_ascii=False),
             total_qty, notional, anomaly_id, stop_price)
        )
        conn.commit()
        return cur.lastrowid

    def get_open_positions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status='open'"
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["layers"] = json.loads(d["layers"])
            results.append(d)
        return results

    def close_position(self, position_id: int, reason: str, pnl: float):
        import time
        conn = self.conn
        conn.execute(
            "UPDATE positions SET status='closed', close_reason=?, "
            "close_ts=?, realized_pnl=? WHERE id=?",
            (reason, int(time.time() * 1000), pnl, position_id)
        )
        conn.commit()

    def update_position_price(self, position_id: int, current_price: float,
                              unrealized_pnl: float):
        conn = self.conn
        conn.execute(
            "UPDATE positions SET current_price=?, unrealized_pnl=? WHERE id=?",
            (current_price, unrealized_pnl, position_id)
        )
        conn.commit()

    # ── 止盈操作 ──

    def save_take_profit(self, position_id: int, tp_level: str,
                         price: float, qty: float, pct: float, pnl: float):
        import time
        conn = self.conn
        conn.execute(
            "INSERT INTO take_profits (position_id, tp_level, price, qty, pct, pnl, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (position_id, tp_level, price, qty, pct, pnl, int(time.time() * 1000))
        )
        conn.commit()

    # ── 风控事件 ──

    def save_risk_event(self, event_type: str, ts: int,
                        symbol: Optional[str] = None,
                        details: Optional[dict] = None):
        conn = self.conn
        conn.execute(
            "INSERT INTO risk_events (symbol, event_type, ts, details) "
            "VALUES (?, ?, ?, ?)",
            (symbol, event_type, ts,
             json.dumps(details, ensure_ascii=False) if details else None)
        )
        conn.commit()

    # ── 币种元数据 ──

    def upsert_coin_meta(self, symbol: str, sector: Optional[str] = None,
                         tier: Optional[str] = None,
                         data_start_ts: Optional[int] = None,
                         data_days: Optional[int] = None,
                         cold_start_multiplier: float = 1.0):
        import time
        conn = self.conn
        existing = conn.execute(
            "SELECT symbol FROM coin_meta WHERE symbol=?", (symbol,)
        ).fetchone()
        if existing:
            sets = ["updated_at=?"]
            vals = [int(time.time() * 1000)]
            if sector is not None:
                sets.append("sector=?"); vals.append(sector)
            if tier is not None:
                sets.append("tier=?"); vals.append(tier)
            if data_start_ts is not None:
                sets.append("data_start_ts=?"); vals.append(data_start_ts)
            if data_days is not None:
                sets.append("data_days=?"); vals.append(data_days)
            sets.append("cold_start_multiplier=?"); vals.append(cold_start_multiplier)
            vals.append(symbol)
            conn.execute(f"UPDATE coin_meta SET {','.join(sets)} WHERE symbol=?", vals)
        else:
            conn.execute(
                "INSERT INTO coin_meta (symbol, sector, tier, data_start_ts, "
                "data_days, cold_start_multiplier, updated_at) VALUES (?,?,?,?,?,?,?)",
                (symbol, sector, tier, data_start_ts, data_days,
                 cold_start_multiplier, int(time.time() * 1000))
            )
        conn.commit()

    def get_coin_meta(self, symbol: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM coin_meta WHERE symbol=?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    # ── 参数快照 ──

    def save_param_snapshot(self, symbol: str, params: dict,
                            pnl: float = 0, pf: float = 0, wr: float = 0):
        import time
        conn = self.conn
        conn.execute(
            "INSERT INTO param_snapshots (symbol, params, backtest_pnl, backtest_pf, backtest_wr, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, json.dumps(params, ensure_ascii=False), pnl, pf, wr,
             int(time.time() * 1000))
        )
        conn.commit()

    # ── 扫描日志 ──

    def save_scan_log(self, scan_type: str, coins_scanned: int,
                      anomalies_found: int, confirmed: int,
                      entered: int, duration_ms: int):
        import time
        conn = self.conn
        conn.execute(
            "INSERT INTO scan_log (scan_type, coins_scanned, anomalies_found, "
            "confirmed, entered, duration_ms, ts) VALUES (?,?,?,?,?,?,?)",
            (scan_type, coins_scanned, anomalies_found, confirmed,
             entered, duration_ms, int(time.time() * 1000))
        )
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
