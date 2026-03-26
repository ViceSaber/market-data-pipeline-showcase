"""Stale Rechecker — Market lifecycle state machine.

State machine:
  fresh → unseen (1h not seen)
  unseen → stale_pending (24h not seen or end_time passed)
  unseen → fresh (re-seen by event_indexer) [loop]
  stale_pending → slug recheck → fresh (active) [loop]
  stale_pending → slug recheck → closed_confirmed (closed)
  closed_confirmed (terminal)
"""

from datetime import datetime, timezone

from app.clients.gamma_client import fetch_by_slug_batch
from app.db import get_conn
from config.settings import HOURS_UNSEEN_TO_STALE, STALE_RECHECK_BATCH_SIZE


def run_stale_rechecker():
    """Run the stale recheck state machine."""
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Step 1: fresh → unseen (>1h since last_seen)
        cursor = conn.execute("""
            UPDATE market_registry
            SET stale_status = 'unseen'
            WHERE stale_status = 'fresh'
              AND last_seen_at < datetime(?, '-1 hours')
        """, (now,))
        step1_count = cursor.rowcount

        # Step 2: unseen → stale_pending (>24h since last_seen OR end_time passed)
        cursor = conn.execute("""
            UPDATE market_registry
            SET stale_status = 'stale_pending'
            WHERE stale_status = 'unseen'
              AND (
                  last_seen_at < datetime(?, '-24 hours')
                  OR (end_time IS NOT NULL AND end_time < ?)
              )
        """, (now, now))
        step2_count = cursor.rowcount

        # Step 3: stale_pending → slug batch recheck
        pending = conn.execute("""
            SELECT slug, market_id FROM market_registry
            WHERE stale_status = 'stale_pending'
            ORDER BY last_seen_at ASC
            LIMIT ?
        """, (STALE_RECHECK_BATCH_SIZE,)).fetchall()

        rechecked = 0
        confirmed_closed = 0
        confirmed_fresh = 0

        for i in range(0, len(pending), STALE_RECHECK_BATCH_SIZE):
            batch = pending[i:i + STALE_RECHECK_BATCH_SIZE]
            slugs = [r["slug"] for r in batch]

            try:
                fresh_data = fetch_by_slug_batch(slugs)
                slug_to_market = {m.get("slug"): m for m in fresh_data}
            except Exception as e:
                print(f"Stale rechecker API error: {e}")
                continue

            for row in batch:
                slug = row["slug"]
                market_data = slug_to_market.get(slug)

                if market_data is None:
                    # Not found in API — market may be delisted
                    conn.execute("""
                        UPDATE market_registry
                        SET stale_status = 'closed_confirmed', active = 0, closed = 1
                        WHERE slug = ?
                    """, (slug,))
                    confirmed_closed += 1
                elif market_data.get("closed"):
                    conn.execute("""
                        UPDATE market_registry
                        SET stale_status = 'closed_confirmed', active = 0, closed = 1
                        WHERE slug = ?
                    """, (slug,))
                    confirmed_closed += 1
                elif market_data.get("active"):
                    conn.execute("""
                        UPDATE market_registry
                        SET stale_status = 'fresh', last_seen_at = ?,
                            active = 1, closed = 0
                        WHERE slug = ?
                    """, (now, slug))
                    confirmed_fresh += 1

                rechecked += 1

        conn.commit()

        # Update scheduler_state
        notes = (f"step1_unseen={step1_count}, step2_stale={step2_count}, "
                 f"rechecked={rechecked}, closed={confirmed_closed}, refreshed={confirmed_fresh}")
        conn.execute("""
            INSERT INTO scheduler_state (job_name, last_run_at, last_success_at, notes)
            VALUES ('stale_rechecker', ?, ?, ?)
            ON CONFLICT(job_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_success_at = excluded.last_success_at,
                notes = excluded.notes
        """, (now, now, notes))
        conn.commit()

        print(f"Stale rechecker done: {notes}")

    finally:
        conn.close()


def mark_seen_loop(conn, slugs: list[str], now: str):
    """Called by event_indexer: unseen markets seen again → fresh (loop back).

    This is the 're-seen → fresh' path of the state machine.
    Event indexer should call this after upserting markets that were
    previously marked as unseen/stale_pending.
    """
    if not slugs:
        return
    placeholders = ",".join("?" * len(slugs))
    conn.execute(f"""
        UPDATE market_registry
        SET stale_status = 'fresh', last_seen_at = ?, active = 1
        WHERE slug IN ({placeholders})
          AND stale_status IN ('unseen', 'stale_pending')
    """, [now] + slugs)
