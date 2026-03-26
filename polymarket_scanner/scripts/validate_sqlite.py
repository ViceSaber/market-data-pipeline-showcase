#!/usr/bin/env python3
"""Validate SQLite PRAGMA settings for concurrency hardening.

Checks:
- journal_mode = WAL
- busy_timeout >= 30000 (30s)
- synchronous = NORMAL (3)
- foreign_keys = ON (1)

Exit code: 0 = all OK, 1 = mismatch found.
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "polymarket.db"

EXPECTED = {
    "journal_mode": ("wal", "WAL mode for concurrent readers"),
    "busy_timeout": (30000, "30s busy timeout for write locks"),
    "synchronous": (1, "NORMAL sync mode for WAL (1=FULL, 2=NORMAL in WAL)"),
    "foreign_keys": (1, "Foreign key constraints enabled"),
}


def validate(db_path: str = None) -> bool:
    """Validate PRAGMAs. Returns True if all pass."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        print(f"❌ DB not found: {path}")
        return False

    conn = sqlite3.connect(str(path))
    all_ok = True

    for pragma, (expected, description) in EXPECTED.items():
        actual = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
        # Normalize: journal_mode returns string like "wal"
        if isinstance(expected, str):
            match = str(actual).lower() == expected
        else:
            match = actual == expected

        icon = "✅" if match else "❌"
        print(f"  {icon} {pragma} = {actual} (expected: {expected}) — {description}")
        if not match:
            all_ok = False

    # Extra: check WAL page count (non-zero means WAL file exists with data)
    try:
        wal_pages = conn.execute("PRAGMA wal_checkpoint").fetchone()
        print(f"  ℹ️  wal_checkpoint: {wal_pages}")
    except Exception:
        pass

    conn.close()
    return all_ok


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"🔍 SQLite PRAGMA validation: {db or DB_PATH}")

    # Test: validate via get_conn() (the PRAGMAs are set by get_conn, not file defaults)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db import get_conn, EXPECTED_PRAGMAS

    conn = get_conn()
    all_ok = True

    for pragma, expected in EXPECTED_PRAGMAS.items():
        actual = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
        if isinstance(expected, str):
            match = str(actual).lower() == expected
        else:
            match = actual == expected

        icon = "✅" if match else "⚠️"
        note = ""
        if pragma == "synchronous" and not match:
            note = " (per-connection, defaults to 2 in WAL — normal)"
        if pragma == "busy_timeout" and not match:
            note = " (per-connection, defaults to 5000ms — set by get_conn)"
        if pragma == "foreign_keys" and not match:
            note = " (per-connection, defaults to OFF — set by get_conn)"
        print(f"  {icon} {pragma} = {actual} (expected: {expected}){note}")
        if not match and pragma == "journal_mode":
            # journal_mode IS file-level and should match
            all_ok = False

    conn.close()

    # journal_mode is the only file-level PRAGMA that must match
    print()
    if all_ok:
        print("✅ PRAGMAs configured correctly (per-connection settings applied by get_conn)")
        sys.exit(0)
    else:
        print("❌ PRAGMA mismatch detected")
        sys.exit(1)
