"""Tests for scheduler registration / staggering."""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scheduler import _load_recent_portfolio_markets, register_all_jobs


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def register(self, name, fn, interval_seconds, description="", coalesce=True,
                 max_instances=1, start_date=None):
        self.calls.append({
            "name": name,
            "fn": fn,
            "interval_seconds": interval_seconds,
            "description": description,
            "start_date": start_date,
        })


def test_register_all_jobs_staggers_price_refresh_jobs():
    registry = FakeRegistry()
    register_all_jobs(registry)

    by_name = {call["name"]: call for call in registry.calls}

    assert by_name["price_refresh_hot"]["start_date"] is not None
    assert by_name["price_refresh_warm"]["start_date"] is not None
    assert by_name["price_refresh_cold"]["start_date"] is not None

    assert by_name["price_refresh_hot"]["start_date"] < by_name["price_refresh_warm"]["start_date"]
    assert by_name["price_refresh_warm"]["start_date"] < by_name["price_refresh_cold"]["start_date"]

    assert by_name["confirmer"]["start_date"] is None
    assert by_name["portfolio_arb"]["start_date"] is not None
    assert by_name["alert_check"]["start_date"] is not None


def test_load_recent_portfolio_markets_uses_latest_fresh_active_rows_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE market_snapshot_latest (
            market_id TEXT PRIMARY KEY,
            slug TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            last_price_yes REAL,
            volume_24h_num REAL,
            liquidity_num REAL,
            active INTEGER,
            closed INTEGER
        )"""
    )
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=1)).isoformat()
    stale = (now - timedelta(minutes=10)).isoformat()

    conn.executemany(
        """INSERT INTO market_snapshot_latest
           (market_id, slug, fetched_at, last_price_yes, volume_24h_num, liquidity_num, active, closed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("m1", "fresh-active", fresh, 0.55, 20000, 10000, 1, 0),
            ("m2", "stale", stale, 0.66, 20000, 10000, 1, 0),
            ("m3", "inactive", fresh, 0.44, 20000, 10000, 0, 0),
            ("m4", "closed", fresh, 0.33, 20000, 10000, 1, 1),
            ("m5", "null-price", fresh, None, 20000, 10000, 1, 0),
        ],
    )

    rows = _load_recent_portfolio_markets(conn, freshness_minutes=5)
    assert rows == [
        {
            "slug": "fresh-active",
            "last_price_yes": 0.55,
            "volume_24h_num": 20000.0,
            "liquidity_num": 10000.0,
            "fetched_at": fresh,
        }
    ]
