"""Tests for Price Refresher — tier classification and hysteresis."""

import sqlite3
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.price_refresher as price_refresher
from app.services.price_refresher import (
    classify_tier, update_tier_state, refresh_tier,
    TIER_HOT, TIER_WARM, TIER_COLD,
)


@pytest.fixture
def conn():
    """In-memory SQLite with scheduler_state table."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE scheduler_state (
            job_name TEXT PRIMARY KEY,
            last_run_at TEXT,
            last_success_at TEXT,
            notes TEXT
        );
    """)
    yield c
    c.close()


class TestClassifyTier:
    def test_hot_by_volume(self):
        row = {"volume_24h_num": 100_000, "liquidity_num": 0, "tags_json": "[]"}
        assert classify_tier(row) == TIER_HOT

    def test_hot_by_liquidity(self):
        row = {"volume_24h_num": 0, "liquidity_num": 50_000, "tags_json": "[]"}
        assert classify_tier(row) == TIER_HOT

    def test_warm_by_volume(self):
        row = {"volume_24h_num": 10_000, "liquidity_num": 0, "tags_json": "[]"}
        assert classify_tier(row) == TIER_WARM

    def test_cold_default(self):
        row = {"volume_24h_num": 0, "liquidity_num": 0, "tags_json": "[]"}
        assert classify_tier(row) == TIER_COLD

    def test_none_values(self):
        row = {"volume_24h_num": None, "liquidity_num": None, "tags_json": None}
        assert classify_tier(row) == TIER_COLD


class TestRefreshTier:
    def test_refresh_tier_commits_each_batch(self, monkeypatch):
        class FakeConn:
            def __init__(self):
                self.commits = 0
                self.rollbacks = 0

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

        conn = FakeConn()
        batch_updates = []

        monkeypatch.setattr(price_refresher, "BATCH_SIZE", 2)
        monkeypatch.setattr(
            price_refresher,
            "_get_markets_for_tier",
            lambda conn, tier: [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}],
        )
        monkeypatch.setattr(
            price_refresher,
            "fetch_by_slug_batch",
            lambda slugs: [{"id": slug, "slug": slug, "volume24hr": 100000, "liquidity": 0, "active": True, "closed": False} for slug in slugs],
        )
        monkeypatch.setattr(
            price_refresher,
            "_parse_snapshot",
            lambda raw: {"market_id": raw["id"], "slug": raw["slug"]},
        )
        monkeypatch.setattr(
            price_refresher,
            "_batch_insert_snapshots",
            lambda conn, snapshots, now: len(snapshots),
        )
        monkeypatch.setattr(
            price_refresher,
            "classify_tier",
            lambda raw: TIER_HOT,
        )
        monkeypatch.setattr(
            price_refresher,
            "_batch_update_tier_states",
            lambda conn, changes: batch_updates.append(list(changes)),
        )
        monkeypatch.setattr(price_refresher.time, "sleep", lambda _: None)

        inserted = refresh_tier(conn, TIER_HOT)

        assert inserted == 3
        assert conn.commits == 2
        assert conn.rollbacks == 0
        assert batch_updates == [
            [("a", TIER_HOT), ("b", TIER_HOT)],
            [("c", TIER_HOT)],
        ]


class TestUpdateTierState:
    def test_first_time_cold_to_hot_promotion(self, conn):
        """First call: no existing state. Promotes to hot after threshold."""
        market_id = "mkt-1"
        # First call → stable_count=1, below threshold (3)
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is False

        # Second call → stable_count=2
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is False

        # Third call → stable_count=3 >= threshold → promote!
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is True

    def test_same_tier_resets(self, conn):
        """If tier stays same, state resets."""
        market_id = "mkt-2"
        # Build up to hot first (3 consecutive hot calls → promote)
        update_tier_state(conn, market_id, TIER_HOT)
        update_tier_state(conn, market_id, TIER_HOT)
        update_tier_state(conn, market_id, TIER_HOT)

        # Now cold → back to cold. Demotion threshold = 3.
        # Call 1: cold, stable=1
        result = update_tier_state(conn, market_id, TIER_COLD)
        assert result is False
        # Call 2: cold, stable=2
        result = update_tier_state(conn, market_id, TIER_COLD)
        assert result is False
        # Call 3: cold, stable=3 >= threshold → demote!
        result = update_tier_state(conn, market_id, TIER_COLD)
        assert result is True

    def test_reads_current_from_db(self, conn):
        """update_tier_state should read current tier from DB, not from external dict."""
        market_id = "mkt-3"
        # First: set to warm, stable=3 → promote to warm
        update_tier_state(conn, market_id, TIER_WARM)
        update_tier_state(conn, market_id, TIER_WARM)
        update_tier_state(conn, market_id, TIER_WARM)
        # Now warm is the current tier in DB

        # New call with hot → should see warm as current, not cold
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is False  # stable_count=1 for promotion

        # After 3 stable hot calls → promote from warm to hot
        update_tier_state(conn, market_id, TIER_HOT)
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is True

    def test_demotion_needs_more_stable(self, conn):
        """Both promotion and demotion thresholds are 3."""
        market_id = "mkt-4"
        # Promote to hot first
        for _ in range(3):
            update_tier_state(conn, market_id, TIER_HOT)

        # Now start demoting — cold stable count builds up
        for i in range(2):
            result = update_tier_state(conn, market_id, TIER_COLD)
            assert result is False, f"Call {i+1}: should not demote yet (stable={i+1})"

        # 3rd consecutive cold → demote
        result = update_tier_state(conn, market_id, TIER_COLD)
        assert result is True

    def test_noop_when_same_tier(self, conn):
        """If already at target tier with stable=0, returns False (no change)."""
        market_id = "mkt-5"
        # Promote to hot
        for _ in range(3):
            update_tier_state(conn, market_id, TIER_HOT)

        # Already hot, calling hot again → no change
        result = update_tier_state(conn, market_id, TIER_HOT)
        assert result is False
