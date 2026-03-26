"""Migration: add slug column to alert_log (Phase 3 P0 fix).

Run: python scripts/migrate_alert_slug.py
Safe to run multiple times (ALTER TABLE IF NOT EXISTS won't work in SQLite,
so we catch the OperationalError).
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_conn


def migrate():
    conn = get_conn()
    try:
        conn.execute("ALTER TABLE alert_log ADD COLUMN slug TEXT")
        print("✅ Added slug column to alert_log")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("ℹ️  slug column already exists, skipping")
        else:
            raise

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_log_slug "
            "ON alert_log(slug, sent_at DESC)"
        )
        print("✅ Created idx_alert_log_slug")
    except sqlite3.OperationalError as e:
        print(f"ℹ️  Index creation skipped: {e}")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
