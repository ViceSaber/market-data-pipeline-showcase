"""Migration: Convert market_snapshot_latest from VIEW to TABLE.

Steps:
1. Drop the VIEW
2. Create a TABLE with the same columns
3. Populate from market_snapshot (latest per market_id)
4. Add index on (market_id) for fast lookups
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "polymarket.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # 1. Drop old VIEW
    conn.execute("DROP VIEW IF EXISTS market_snapshot_latest")
    print("✓ Dropped old VIEW")

    # 2. Create TABLE (same columns as market_snapshot)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshot_latest (
            snapshot_id     INTEGER,
            market_id       TEXT NOT NULL,
            slug            TEXT NOT NULL,
            fetched_at      TEXT NOT NULL,
            source          TEXT NOT NULL,
            best_yes_bid    REAL,
            best_yes_ask    REAL,
            best_no_bid     REAL,
            best_no_ask     REAL,
            last_price_yes  REAL,
            last_price_no   REAL,
            midpoint_yes    REAL,
            midpoint_no     REAL,
            volume_num      REAL,
            volume_24h_num  REAL,
            liquidity_num   REAL,
            open_interest_num REAL,
            active          INTEGER,
            closed          INTEGER,
            PRIMARY KEY (market_id)
        )
    """)
    print("✓ Created TABLE market_snapshot_latest")

    # 3. Populate from market_snapshot
    conn.execute("""
        INSERT OR REPLACE INTO market_snapshot_latest
        SELECT s.*
        FROM market_snapshot s
        JOIN (
            SELECT market_id, MAX(fetched_at) AS max_fetched_at
            FROM market_snapshot
            GROUP BY market_id
        ) t ON s.market_id = t.market_id AND s.fetched_at = t.max_fetched_at
    """)
    count = conn.execute("SELECT COUNT(*) FROM market_snapshot_latest").fetchone()[0]
    print(f"✓ Populated {count:,} rows")

    # 4. Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_latest_active ON market_snapshot_latest(active, closed)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_latest_tier ON market_snapshot_latest(volume_24h_num, liquidity_num)")
    print("✓ Created indexes")

    conn.commit()
    conn.close()
    print("\n✅ Migration complete!")


if __name__ == "__main__":
    migrate()
