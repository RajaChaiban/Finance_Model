"""Tests for SQLite persistence layer."""

from __future__ import annotations

import os
import tempfile

import pytest

from src.esmm import persistence


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """Each test gets a fresh on-disk DB and persistence is enabled."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("ESMM_PERSIST", "1")
    monkeypatch.setenv("ESMM_DB_PATH", tmp.name)
    persistence.reset_for_tests()
    yield tmp.name
    persistence.reset_for_tests()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def test_save_returns_id_when_enabled():
    rec_id = persistence.save_backtest(
        symbol="SPY",
        config={"base_half_spread_bps": 5.0},
        tca={"total_pnl": 12.34},
        n_quotes=100,
        n_fills=8,
        total_pnl=12.34,
        final_inventory=50.0,
    )
    assert rec_id is not None
    assert len(rec_id) == 32  # uuid hex


def test_save_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("ESMM_PERSIST", "0")
    rec_id = persistence.save_backtest(
        symbol="SPY", config={}, tca={}, n_quotes=0, n_fills=0,
        total_pnl=0.0, final_inventory=0.0,
    )
    assert rec_id is None


def test_list_returns_records_in_reverse_chronological_order():
    ids = []
    for i in range(3):
        ids.append(
            persistence.save_backtest(
                symbol=f"S{i}",
                config={"i": i},
                tca={"total_pnl": float(i)},
                n_quotes=10 + i,
                n_fills=i,
                total_pnl=float(i),
                final_inventory=float(i * 10),
            )
        )
    records = persistence.list_backtests()
    assert len(records) == 3
    assert records[0].symbol == "S2"
    assert records[-1].symbol == "S0"


def test_list_respects_limit():
    for i in range(5):
        persistence.save_backtest(
            symbol="X", config={}, tca={}, n_quotes=i, n_fills=0,
            total_pnl=0.0, final_inventory=0.0,
        )
    assert len(persistence.list_backtests(limit=2)) == 2


def test_list_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("ESMM_PERSIST", "0")
    assert persistence.list_backtests() == []


def test_get_round_trips_full_record():
    rec_id = persistence.save_backtest(
        symbol="QQQ",
        config={"max_inventory": 500.0},
        tca={"spread_capture_pnl": 1.5, "fees_pnl": -0.2},
        n_quotes=42,
        n_fills=7,
        total_pnl=1.3,
        final_inventory=-25.0,
    )
    record = persistence.get_backtest(rec_id)
    assert record is not None
    assert record.symbol == "QQQ"
    assert record.config["max_inventory"] == 500.0
    assert record.tca["spread_capture_pnl"] == 1.5
    assert record.n_quotes == 42
    assert record.final_inventory == -25.0


def test_get_unknown_id_returns_none():
    assert persistence.get_backtest("does_not_exist") is None


def test_get_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("ESMM_PERSIST", "0")
    assert persistence.get_backtest("anything") is None
