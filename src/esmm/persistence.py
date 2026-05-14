"""SQLite persistence for backtest runs.

Stores compact summaries of every backtest the platform has run, so the UI
can show a history table and so a user can drill into a past run.

Mirrors the pattern in src/agents/persistence.py:
- Env-flag opt-in (`ESMM_PERSIST=1`) keeps test isolation clean by default.
- Default DB path under ./data/ (also configurable via env).
- Module-level connection guarded by an RLock so the API can call from
  request threads without surprises.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional


_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None


def _persist_enabled() -> bool:
    return os.getenv("ESMM_PERSIST", "0") == "1"


def _db_path() -> str:
    return os.getenv("ESMM_DB_PATH", "esmm_backtests.db")


def _conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = sqlite3.connect(_db_path(), check_same_thread=False)
            _CONN.execute(
                """
                CREATE TABLE IF NOT EXISTS backtests (
                    id TEXT PRIMARY KEY,
                    created_ts REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    n_quotes INTEGER NOT NULL,
                    n_fills INTEGER NOT NULL,
                    total_pnl REAL NOT NULL,
                    final_inventory REAL NOT NULL,
                    config_json TEXT NOT NULL,
                    tca_json TEXT NOT NULL
                )
                """
            )
            _CONN.commit()
        return _CONN


@dataclass
class BacktestRecord:
    id: str
    created_ts: float
    symbol: str
    n_quotes: int
    n_fills: int
    total_pnl: float
    final_inventory: float
    config: dict
    tca: dict


def reset_for_tests() -> None:
    """Test-only helper: drop the in-memory connection so each test starts
    from a clean state. Never call from production code."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
            _CONN = None


def save_backtest(symbol: str, config: dict, tca: dict, n_quotes: int,
                  n_fills: int, total_pnl: float, final_inventory: float) -> str | None:
    """Persist a backtest summary, returning the new row's id.

    Returns None when persistence is disabled (the caller can ignore — the
    backtest still ran, just not stored).
    """
    if not _persist_enabled():
        return None
    record_id = uuid.uuid4().hex
    with _LOCK:
        _conn().execute(
            "INSERT INTO backtests (id, created_ts, symbol, n_quotes, n_fills,"
            " total_pnl, final_inventory, config_json, tca_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                time.time(),
                symbol,
                n_quotes,
                n_fills,
                total_pnl,
                final_inventory,
                json.dumps(config, default=str),
                json.dumps(tca, default=str),
            ),
        )
        _conn().commit()
    return record_id


def list_backtests(limit: int = 100) -> list[BacktestRecord]:
    if not _persist_enabled():
        return []
    with _LOCK:
        rows = _conn().execute(
            "SELECT id, created_ts, symbol, n_quotes, n_fills, total_pnl,"
            " final_inventory, config_json, tca_json"
            " FROM backtests ORDER BY created_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        BacktestRecord(
            id=r[0],
            created_ts=r[1],
            symbol=r[2],
            n_quotes=r[3],
            n_fills=r[4],
            total_pnl=r[5],
            final_inventory=r[6],
            config=json.loads(r[7]),
            tca=json.loads(r[8]),
        )
        for r in rows
    ]


def get_backtest(record_id: str) -> Optional[BacktestRecord]:
    if not _persist_enabled():
        return None
    with _LOCK:
        row = _conn().execute(
            "SELECT id, created_ts, symbol, n_quotes, n_fills, total_pnl,"
            " final_inventory, config_json, tca_json"
            " FROM backtests WHERE id = ?",
            (record_id,),
        ).fetchone()
    if row is None:
        return None
    return BacktestRecord(
        id=row[0],
        created_ts=row[1],
        symbol=row[2],
        n_quotes=row[3],
        n_fills=row[4],
        total_pnl=row[5],
        final_inventory=row[6],
        config=json.loads(row[7]),
        tca=json.loads(row[8]),
    )
