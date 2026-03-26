"""Initialize the Polymarket Scanner database from SQL migration files."""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def main():
    conn = get_conn()
    try:
        for path in sorted(SQL_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            print(f"applied: {path.name}")
        conn.commit()
        print(f"Database initialized at {conn.execute('PRAGMA database_list').fetchall()}")
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
