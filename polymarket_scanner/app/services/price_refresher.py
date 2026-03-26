"""Price Refresher — tiered price snapshot collection.

Hot  (volume_24h >= 50K or liquidity >= 10K): refresh every 5 min
Warm (tag in WARM_TAGS or volume_24h >= 5K): refresh every 15 min
Cold (everything else):                       refresh every 60 min

Writes snapshots to market_snapshot table.
Cleans up snapshots older than SNAPSHOT_RETENTION_HOURS.
"""

import json
import logging
import time
from datetime import datetime, timezone

from config.settings import (
    BATCH_SIZE,
    HOT_MIN_LIQUIDITY,
    HOT_MIN_VOLUME_24H,
    SNAPSHOT_RETENTION_HOURS,
    TIER_DEMOTION_THRESHOLD,
    TIER_PROMOTION_THRESHOLD,
    WARM_MIN_LIQUIDITY,
    WARM_MIN_VOLUME_24H,
    WARM_TAGS,
)
from app.clients.gamma_client import fetch_by_slug_batch

log = logging.getLogger(__name__)

TIER_HOT = "hot"
TIER_WARM = "warm"
TIER_COLD = "cold"

# ── Tier classification ──────────────────────────────────────


def classify_tier(market_row: dict) -> str:
    """Classify a market into hot/warm/cold based on latest snapshot data.

    Args:
        market_row: dict with keys volume_24h_num, liquidity_num, tags_json
                    (from market_snapshot_latest or market_registry)
    """
    vol = market_row.get("volume_24h_num") or 0
    liq = market_row.get("liquidity_num") or 0
    tags_raw = market_row.get("tags_json") or "[]"

    if isinstance(tags_raw, str):
        try:
            tags = set(json.loads(tags_raw))
        except (json.JSONDecodeError, TypeError):
            tags = set()
    else:
        tags = set(tags_raw)

    # Hot: high volume or high liquidity
    if vol >= HOT_MIN_VOLUME_24H or liq >= HOT_MIN_LIQUIDITY:
        return TIER_HOT

    # Warm: moderate volume, or relevant tags
    if vol >= WARM_MIN_VOLUME_24H or liq >= WARM_MIN_LIQUIDITY:
        return TIER_WARM
    if tags & WARM_TAGS:
        return TIER_WARM

    return TIER_COLD


# ── Tier tracking (DB-based with hysteresis) ─────────────────


def _read_tier_state(conn, market_id: str) -> tuple[str, int, str | None]:
    """Read current tier state from scheduler_state.

    Returns:
        (current_tier, stable_count, pending_target)
    """
    job_name = f"price_refresh_{market_id}"
    row = conn.execute(
        "SELECT notes FROM scheduler_state WHERE job_name = ?",
        (job_name,),
    ).fetchone()

    notes = (row["notes"] if row and row["notes"] is not None else "") if row else ""
    current = TIER_COLD
    stable_count = 0
    pending_target = None

    for part in notes.split(";"):
        if part.startswith("tier="):
            current = part.split("=", 1)[1]
        elif part.startswith("target="):
            pending_target = part.split("=", 1)[1]
        elif part.startswith("stable="):
            try:
                stable_count = int(part.split("=", 1)[1])
            except ValueError:
                stable_count = 0

    return current, stable_count, pending_target


def update_tier_state(conn, market_id: str, new_tier: str) -> bool:
    """Update one market's tier state with hysteresis.

    Returns True only when the persisted current tier actually changes.
    This helper exists for tests / one-off updates; batch refresh paths should
    continue using `_batch_update_tier_states()` for efficiency.
    """
    current, stable_count, pending_target = _read_tier_state(conn, market_id)
    job_name = f"price_refresh_{market_id}"

    if current == new_tier:
        notes = f"tier={new_tier};stable=0"
        conn.execute(
            """INSERT INTO scheduler_state (job_name, notes)
               VALUES (?, ?)
               ON CONFLICT(job_name) DO UPDATE SET notes = excluded.notes""",
            (job_name, notes),
        )
        conn.commit()
        return False

    tier_order = {TIER_COLD: 0, TIER_WARM: 1, TIER_HOT: 2}
    is_promotion = tier_order.get(new_tier, 0) > tier_order.get(current, 0)
    threshold = TIER_PROMOTION_THRESHOLD if is_promotion else TIER_DEMOTION_THRESHOLD

    if pending_target != new_tier:
        stable_count = 0

    stable_count += 1

    if stable_count >= threshold:
        notes = f"tier={new_tier};stable=0"
        changed = True
    else:
        notes = f"tier={current};target={new_tier};stable={stable_count}"
        changed = False

    conn.execute(
        """INSERT INTO scheduler_state (job_name, notes)
           VALUES (?, ?)
           ON CONFLICT(job_name) DO UPDATE SET notes = excluded.notes""",
        (job_name, notes),
    )
    conn.commit()
    return changed


def _batch_update_tier_states(conn, tier_changes: list[tuple[str, str]]):
    """Batch update tier states. Replaces per-market individual queries.

    Args:
        tier_changes: list of (market_id, new_tier) tuples
    """
    if not tier_changes:
        return

    # Read current states in one query
    job_names = [f"price_refresh_{mid}" for mid, _ in tier_changes]
    placeholders = ",".join("?" * len(job_names))
    existing = conn.execute(
        f"SELECT job_name, notes FROM scheduler_state WHERE job_name IN ({placeholders})",
        job_names,
    ).fetchall()
    existing_map = {row["job_name"]: row["notes"] or "" for row in existing}

    updates = []
    tier_order = {TIER_COLD: 0, TIER_WARM: 1, TIER_HOT: 2}

    for market_id, new_tier in tier_changes:
        job_name = f"price_refresh_{market_id}"
        notes = existing_map.get(job_name, "")

        current = TIER_COLD
        stable_count = 0
        pending_target = None
        for part in notes.split(";"):
            if part.startswith("tier="):
                current = part.split("=", 1)[1]
            elif part.startswith("target="):
                pending_target = part.split("=", 1)[1]
            elif part.startswith("stable="):
                try:
                    stable_count = int(part.split("=", 1)[1])
                except ValueError:
                    stable_count = 0

        if current == new_tier:
            updates.append((job_name, f"tier={new_tier};stable=0"))
            continue

        is_promotion = tier_order.get(new_tier, 0) > tier_order.get(current, 0)
        threshold = TIER_PROMOTION_THRESHOLD if is_promotion else TIER_DEMOTION_THRESHOLD

        if pending_target != new_tier:
            stable_count = 0

        stable_count += 1
        if stable_count >= threshold:
            updates.append((job_name, f"tier={new_tier};stable=0"))
            log.info("Tier change: %s %s → %s (after %d consecutive)",
                     market_id, current, new_tier, stable_count)
        else:
            updates.append((job_name,
                            f"tier={current};target={new_tier};stable={stable_count}"))

    # Batch write
    conn.executemany(
        """INSERT INTO scheduler_state (job_name, notes)
           VALUES (?, ?)
           ON CONFLICT(job_name) DO UPDATE SET notes = excluded.notes""",
        updates,
    )


# ── Core refresh logic ───────────────────────────────────────


def _parse_snapshot(raw: dict) -> dict:
    """Normalize a Gamma API market dict into a snapshot row."""
    outcome_prices = raw.get("outcomePrices")
    yes_price = no_price = None
    if outcome_prices:
        try:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if len(prices) >= 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
            elif len(prices) == 1:
                yes_price = float(prices[0])
        except (ValueError, TypeError):
            pass

    best_bid = raw.get("bestBid")
    best_ask = raw.get("bestAsk")

    return {
        "market_id": raw.get("id", ""),
        "slug": raw.get("slug", ""),
        "best_yes_bid": _safe_float(best_bid),
        "best_yes_ask": _safe_float(best_ask),
        "best_no_bid": _safe_float(raw.get("bestBidNo")),
        "best_no_ask": _safe_float(raw.get("bestAskNo")),
        "last_price_yes": yes_price,
        "last_price_no": no_price,
        "midpoint_yes": _midpoint(best_bid, best_ask),
        "midpoint_no": None,
        "volume_num": _safe_float(raw.get("volume")),
        "volume_24h_num": _safe_float(raw.get("volume24hr")),
        "liquidity_num": _safe_float(raw.get("liquidity")),
        "open_interest_num": _safe_float(raw.get("openInterest")),
        "active": 1 if raw.get("active") else 0,
        "closed": 1 if raw.get("closed") else 0,
    }


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _midpoint(bid, ask) -> float | None:
    b = _safe_float(bid)
    a = _safe_float(ask)
    if b is not None and a is not None:
        return (b + a) / 2
    return None


def _batch_insert_snapshots(conn, snapshots: list[dict], now: str) -> int:
    """Insert a batch of snapshots and update materialized latest table. Returns count inserted."""
    if not snapshots:
        return 0

    from app.services.classifier import classify_category, classify_liquidity

    # Pre-fetch underlying_entity for all market_ids (batch lookup)
    market_ids = list({s["market_id"] for s in snapshots if s.get("market_id")})
    entity_map = {}
    if market_ids:
        placeholders = ",".join("?" * len(market_ids))
        for row in conn.execute(
            f"SELECT market_id, underlying_entity, group_template FROM market_registry WHERE market_id IN ({placeholders})",
            market_ids,
        ).fetchall():
            entity_map[row["market_id"]] = (row["underlying_entity"] or "", row["group_template"] or "")

    rows = []
    for s in snapshots:
        if not s["market_id"] or not s["slug"]:
            continue
        slug = s["slug"]
        mid = s["market_id"]
        entity, template = entity_map.get(mid, ("", ""))
        category = classify_category(slug, entity, template)
        tier = classify_liquidity(s.get("volume_24h_num"), s.get("liquidity_num"))
        rows.append((
            mid, slug, now, "gamma",
            s["best_yes_bid"], s["best_yes_ask"],
            s["best_no_bid"], s["best_no_ask"],
            s["last_price_yes"], s["last_price_no"],
            s["midpoint_yes"], s["midpoint_no"],
            s["volume_num"], s["volume_24h_num"],
            s["liquidity_num"], s["open_interest_num"],
            s["active"], s["closed"],
            category, tier,
        ))

    conn.executemany(
        """INSERT INTO market_snapshot
           (market_id, slug, fetched_at, source,
            best_yes_bid, best_yes_ask, best_no_bid, best_no_ask,
            last_price_yes, last_price_no, midpoint_yes, midpoint_no,
            volume_num, volume_24h_num, liquidity_num, open_interest_num,
            active, closed, category, liquidity_tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )

    # Update materialized latest table (incremental upsert)
    conn.executemany(
        """INSERT INTO market_snapshot_latest
           (market_id, slug, fetched_at, source,
            best_yes_bid, best_yes_ask, best_no_bid, best_no_ask,
            last_price_yes, last_price_no, midpoint_yes, midpoint_no,
            volume_num, volume_24h_num, liquidity_num, open_interest_num,
            active, closed, category, liquidity_tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(market_id) DO UPDATE SET
               slug = excluded.slug,
               fetched_at = excluded.fetched_at,
               source = excluded.source,
               best_yes_bid = excluded.best_yes_bid,
               best_yes_ask = excluded.best_yes_ask,
               best_no_bid = excluded.best_no_bid,
               best_no_ask = excluded.best_no_ask,
               last_price_yes = excluded.last_price_yes,
               last_price_no = excluded.last_price_no,
               midpoint_yes = excluded.midpoint_yes,
               midpoint_no = excluded.midpoint_no,
               volume_num = excluded.volume_num,
               volume_24h_num = excluded.volume_24h_num,
               liquidity_num = excluded.liquidity_num,
               open_interest_num = excluded.open_interest_num,
               active = excluded.active,
               closed = excluded.closed,
               category = excluded.category,
               liquidity_tier = excluded.liquidity_tier""",
        rows,
    )

    return len(rows)


# ── Public API ───────────────────────────────────────────────


def refresh_tier(conn, tier: str) -> int:
    """Refresh all markets assigned to a tier. Returns count of snapshots written.

    Uses market_snapshot_latest to find markets and their current tier,
    then fetches fresh data from Gamma API and writes new snapshots.

    Locking note:
    Commit after each fetch batch so we do not hold SQLite's writer lock for
    the full duration of large tier refreshes (especially hot/cold tiers).
    This reduces contention with other higher-frequency jobs.
    """
    markets = _get_markets_for_tier(conn, tier)

    if not markets:
        log.info("Tier %s: no markets found", tier)
        return 0

    log.info("Tier %s: refreshing %d markets", tier, len(markets))

    slugs = [m["slug"] for m in markets]
    now = datetime.now(timezone.utc).isoformat()
    total_inserted = 0

    for i in range(0, len(slugs), BATCH_SIZE):
        batch_slugs = slugs[i : i + BATCH_SIZE]
        try:
            raw_markets = fetch_by_slug_batch(batch_slugs)
            snapshots = [_parse_snapshot(m) for m in raw_markets]
            inserted = _batch_insert_snapshots(conn, snapshots, now)
            total_inserted += inserted

            tier_changes = []
            for raw, snap in zip(raw_markets, snapshots):
                new_tier = classify_tier(raw)
                tier_changes.append((snap["market_id"], new_tier))

            if tier_changes:
                _batch_update_tier_states(conn, tier_changes)

            # Important: release the write lock between batches.
            conn.commit()

        except Exception as e:
            log.error("Tier %s batch %d failed: %s", tier, i // BATCH_SIZE, e)
            conn.rollback()

        if i + BATCH_SIZE < len(slugs):
            time.sleep(0.1)

    log.info("Tier %s: wrote %d snapshots", tier, total_inserted)
    return total_inserted


def _get_markets_for_tier(conn, tier: str) -> list[dict]:
    """Get markets that should be refreshed at this tier.

    Uses materialized market_snapshot_latest table for fast SQL filtering.
    Tier is classified directly in SQL — no Python loop over all markets.
    """
    if tier == TIER_HOT:
        rows = conn.execute("""
            SELECT s.market_id, s.slug, s.volume_24h_num, s.liquidity_num,
                   COALESCE(e.tags_json, '[]') as tags_json
            FROM market_snapshot_latest s
            LEFT JOIN market_registry r ON s.market_id = r.market_id
            LEFT JOIN event_registry e ON r.event_id = e.event_id
            WHERE s.active = 1 AND s.closed = 0
              AND (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
        """, (HOT_MIN_VOLUME_24H, HOT_MIN_LIQUIDITY)).fetchall()

    elif tier == TIER_WARM:
        rows = conn.execute("""
            SELECT s.market_id, s.slug, s.volume_24h_num, s.liquidity_num,
                   COALESCE(e.tags_json, '[]') as tags_json
            FROM market_snapshot_latest s
            LEFT JOIN market_registry r ON s.market_id = r.market_id
            LEFT JOIN event_registry e ON r.event_id = e.event_id
            WHERE s.active = 1 AND s.closed = 0
              AND (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
              AND NOT (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
        """, (WARM_MIN_VOLUME_24H, WARM_MIN_LIQUIDITY,
              HOT_MIN_VOLUME_24H, HOT_MIN_LIQUIDITY)).fetchall()

    elif tier == TIER_COLD:
        rows = conn.execute("""
            SELECT s.market_id, s.slug, s.volume_24h_num, s.liquidity_num,
                   COALESCE(e.tags_json, '[]') as tags_json
            FROM market_snapshot_latest s
            LEFT JOIN market_registry r ON s.market_id = r.market_id
            LEFT JOIN event_registry e ON r.event_id = e.event_id
            WHERE s.active = 1 AND s.closed = 0
              AND NOT (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
              AND NOT (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
        """, (WARM_MIN_VOLUME_24H, WARM_MIN_LIQUIDITY,
              HOT_MIN_VOLUME_24H, HOT_MIN_LIQUIDITY)).fetchall()

        # Also include unsnapshotted markets for cold tier
        snap_count = conn.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0]
        if snap_count == 0:
            unsnapshotted = conn.execute("""
                SELECT r.market_id, r.slug, 0 as volume_24h_num, 0 as liquidity_num,
                       COALESCE(e.tags_json, '[]') as tags_json
                FROM market_registry r
                LEFT JOIN event_registry e ON r.event_id = e.event_id
                WHERE r.active = 1 AND r.closed = 0
                ORDER BY COALESCE(e.volume_num, 0) DESC
                LIMIT 200
            """).fetchall()
            log.info("Cold start bootstrap: fetching %d top markets", len(unsnapshotted))
            rows = list(rows) + [dict(r) for r in unsnapshotted]
    else:
        rows = []

    result = [dict(row) for row in rows]

    # Warm tag override: include markets with WARM_TAGS that aren't already hot
    if tier == TIER_WARM:
        warm_tag_rows = conn.execute("""
            SELECT s.market_id, s.slug, s.volume_24h_num, s.liquidity_num,
                   COALESCE(e.tags_json, '[]') as tags_json
            FROM market_snapshot_latest s
            LEFT JOIN market_registry r ON s.market_id = r.market_id
            LEFT JOIN event_registry e ON r.event_id = e.event_id
            WHERE s.active = 1 AND s.closed = 0
              AND NOT (s.volume_24h_num >= ? OR s.liquidity_num >= ?)
        """, (HOT_MIN_VOLUME_24H, HOT_MIN_LIQUIDITY)).fetchall()

        existing_ids = {r["market_id"] for r in result}
        for row in warm_tag_rows:
            row_dict = dict(row)
            if row_dict["market_id"] in existing_ids:
                continue
            tags_raw = row_dict.get("tags_json") or "[]"
            if isinstance(tags_raw, str):
                try:
                    tags = set(json.loads(tags_raw))
                except (json.JSONDecodeError, TypeError):
                    tags = set()
            else:
                tags = set(tags_raw)
            if tags & WARM_TAGS:
                result.append(row_dict)

    return result


def refresh_all(conn, tier_filter: str = None) -> dict[str, int]:
    """Refresh prices for all tiers (or a specific tier).

    Returns dict mapping tier name to snapshot count.
    """
    tiers = [tier_filter] if tier_filter else [TIER_HOT, TIER_WARM, TIER_COLD]
    results = {}

    for tier in tiers:
        count = refresh_tier(conn, tier)
        results[tier] = count

    return results


def cleanup_old_snapshots(conn) -> int:
    """Delete snapshots older than SNAPSHOT_RETENTION_HOURS. Returns count deleted."""
    cur = conn.execute(
        """DELETE FROM market_snapshot
           WHERE fetched_at < datetime('now', ? || ' hours')""",
        (-SNAPSHOT_RETENTION_HOURS,),
    )
    deleted = cur.rowcount
    if deleted > 0:
        conn.commit()
        log.info("Cleaned up %d old snapshots (>%dh)", deleted, SNAPSHOT_RETENTION_HOURS)
    return deleted
