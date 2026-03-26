"""Event Indexer — Discovery layer.

Full-paginated fetch of events from Gamma API, upsert into
event_registry and market_registry tables.
"""

import json
from datetime import datetime, timezone

from app.clients.gamma_client import fetch_events
from app.db import get_conn
from app.services.stale_rechecker import mark_seen_loop

EVENT_FIELDS_MAP = {
    "id": "event_id",
    "slug": "event_slug",
    "title": "title",
    "category": "category",
    "subcategory": "subcategory",
    "startDate": "start_time",
    "endDate": "end_time",
    "liquidity": "liquidity_num",
    "volume": "volume_num",
    "openInterest": "open_interest_num",
}

EVENT_BOOL_FIELDS = {
    "active": "active",
    "closed": "closed",
    "archived": "archived",
}

MARKET_FIELDS_MAP = {
    "id": "market_id",
    "slug": "slug",
    "question": "question",
    "description": "description",
    "startDate": "start_time",
    "endDate": "end_time",
}

MARKET_BOOL_FIELDS = {
    "active": "active",
    "closed": "closed",
    "archived": "archived",
}


def _extract_tags_json(tags: list) -> str | None:
    """Convert event.tags list to JSON string."""
    if not tags:
        return None
    return json.dumps(tags)


def _upsert_event(conn, event: dict, now: str):
    """Upsert a single event into event_registry."""
    row = {}
    for api_field, db_field in EVENT_FIELDS_MAP.items():
        row[db_field] = event.get(api_field)
    for api_field, db_field in EVENT_BOOL_FIELDS.items():
        row[db_field] = 1 if event.get(api_field) else 0

    row["tags_json"] = _extract_tags_json(event.get("tags"))
    row["first_seen_at"] = now
    row["last_seen_at"] = now

    conn.execute("""
        INSERT INTO event_registry (
            event_id, event_slug, title, category, subcategory, tags_json,
            start_time, end_time, active, closed, archived,
            liquidity_num, volume_num, open_interest_num,
            first_seen_at, last_seen_at
        ) VALUES (
            :event_id, :event_slug, :title, :category, :subcategory, :tags_json,
            :start_time, :end_time, :active, :closed, :archived,
            :liquidity_num, :volume_num, :open_interest_num,
            :first_seen_at, :last_seen_at
        )
        ON CONFLICT(event_id) DO UPDATE SET
            event_slug = excluded.event_slug,
            title = excluded.title,
            category = excluded.category,
            subcategory = excluded.subcategory,
            tags_json = excluded.tags_json,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            active = excluded.active,
            closed = excluded.closed,
            archived = excluded.archived,
            liquidity_num = excluded.liquidity_num,
            volume_num = excluded.volume_num,
            open_interest_num = excluded.open_interest_num,
            last_seen_at = excluded.last_seen_at
    """, row)


def _upsert_market(conn, market: dict, event_id: str, now: str):
    """Upsert a single market into market_registry."""
    row = {}
    for api_field, db_field in MARKET_FIELDS_MAP.items():
        row[db_field] = market.get(api_field)
    for api_field, db_field in MARKET_BOOL_FIELDS.items():
        row[db_field] = 1 if market.get(api_field) else 0

    row["event_id"] = event_id
    row["first_seen_at"] = now
    row["last_seen_at"] = now

    conn.execute("""
        INSERT INTO market_registry (
            market_id, slug, question, description, event_id,
            start_time, end_time, active, closed, archived,
            outcome_type, stale_status,
            first_seen_at, last_seen_at
        ) VALUES (
            :market_id, :slug, :question, :description, :event_id,
            :start_time, :end_time, :active, :closed, :archived,
            'unknown', 'fresh',
            :first_seen_at, :last_seen_at
        )
        ON CONFLICT(market_id) DO UPDATE SET
            slug = excluded.slug,
            question = excluded.question,
            description = excluded.description,
            event_id = excluded.event_id,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            active = excluded.active,
            closed = excluded.closed,
            archived = excluded.archived,
            last_seen_at = excluded.last_seen_at
    """, row)


def run_event_indexer():
    """Full paginated fetch of events → upsert into registries."""
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    offset = 0
    limit = 100
    total_events = 0
    total_markets = 0

    seen_slugs = []

    try:
        while True:
            events = fetch_events(active=True, closed=False, limit=limit, offset=offset)
            if not events:
                break

            for event in events:
                event_id = event.get("id")
                if not event_id:
                    continue

                _upsert_event(conn, event, now)
                total_events += 1

                markets = event.get("markets", [])
                for market in markets:
                    market_id = market.get("id")
                    if not market_id:
                        continue
                    _upsert_market(conn, market, event_id, now)
                    total_markets += 1
                    slug = market.get("slug")
                    if slug:
                        seen_slugs.append(slug)

            conn.commit()
            offset += limit

        # Re-seen loop: unseen/stale_pending → fresh
        mark_seen_loop(conn, seen_slugs, now)

        # Update scheduler_state
        conn.execute("""
            INSERT INTO scheduler_state (job_name, last_run_at, last_success_at, notes)
            VALUES ('event_indexer', ?, ?, ?)
            ON CONFLICT(job_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_success_at = excluded.last_success_at,
                notes = excluded.notes
        """, (now, now, f"indexed {total_events} events, {total_markets} markets"))
        conn.commit()

        print(f"Event indexer done: {total_events} events, {total_markets} markets")
    finally:
        conn.close()
