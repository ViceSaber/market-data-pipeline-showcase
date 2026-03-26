"""
Family Builder — Groups related markets into families for arbitrage detection.

Groups markets within an event by: resolution_basis + group_template + underlying_entity + date_scope

Family types:
- mutually_exclusive: same basis + template, multiple side_labels (NBA winners, Fed rate cuts)
- inclusion_chain: same basis + template, different line_values (BTC hit 100k/150k/200k)
- threshold_chain: same entity + basis, different line_values (price thresholds)
- ignore: standalone markets with no meaningful grouping
"""

from datetime import datetime, timezone
from app.db import get_conn
from app.parsers.market_parser import parse_slug, ParsedMarket


def _build_family_key(event_slug: str, resolution_basis: str, group_template: str,
                      underlying_entity: str, date_scope: str | None) -> str:
    """Build the 5-tuple family key."""
    ds = date_scope or "no_scope"
    return f"{event_slug}::{resolution_basis}::{group_template}::{underlying_entity}::{ds}"


def _classify_family_type(basis: str, line_values: list[float | None],
                          side_labels: list[str]) -> str:
    """Determine family type based on member characteristics."""
    if basis == "standalone" or basis == "yes_no":
        return "ignore"

    # Multiple distinct line values → inclusion/threshold chain
    numeric_lines = [v for v in line_values if v is not None]
    unique_lines = sorted(set(numeric_lines))

    if len(unique_lines) >= 2:
        if basis == "over_under":
            return "inclusion_chain"
        return "threshold_chain"

    # Multiple side labels with same basis → mutually exclusive
    if len(side_labels) >= 2:
        return "mutually_exclusive"

    # Single member or unclear grouping
    if len(side_labels) == 1:
        return "ignore"

    return "ignore"


def _compute_completeness(members: list[dict], family_type: str,
                          template_max_counts: dict[str, int] | None = None,
                          group_template: str | None = None) -> float:
    """Compute completeness score (0.0 to 1.0).

    Uses max member count of the same template across events as the expected count.
    Falls back to a minimum of 2 members for chains, 2 for mutual exclusion.
    """
    count = len(members)
    if count <= 1:
        return 0.0

    if template_max_counts and group_template and group_template in template_max_counts:
        expected = template_max_counts[group_template]
    elif family_type == "mutually_exclusive":
        expected = 2  # minimum: need at least 2 sides
    elif family_type in ("inclusion_chain", "threshold_chain"):
        expected = 2  # minimum: need at least 2 thresholds
    else:
        expected = 2

    if expected <= 0:
        return 0.0

    return min(1.0, count / expected)


def _compute_quality_score(members: list[dict], family_type: str,
                           completeness: float) -> float:
    """Compute quality score for prioritization."""
    if family_type == "ignore":
        return 0.0

    # Base: completeness
    score = completeness

    # Bonus for more members (more arbitrage opportunities)
    member_bonus = min(0.3, len(members) * 0.05)

    # Penalty for incomplete families (missing expected members)
    if completeness < 0.8:
        score *= 0.5

    return min(1.0, score + member_bonus)


def run_family_builder():
    """Build market families from market_registry."""
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Backward-compatible lightweight migration: cache date_scope on market_registry
        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_registry)").fetchall()}
        if "date_scope" not in cols:
            conn.execute("ALTER TABLE market_registry ADD COLUMN date_scope TEXT")
            conn.commit()

        # Fetch all active, parsed markets
        markets = conn.execute("""
            SELECT market_id, slug, question, event_id, resolution_basis,
                   group_template, underlying_entity, line_value, side_label,
                   parse_version, date_scope
            FROM market_registry
            WHERE active = 1 AND closed = 0
        """).fetchall()

        # Parse markets that don't have parser fields yet (incremental)
        updated = 0
        date_scope_cache: dict[str, str | None] = {}
        for m in markets:
            # Use pre-computed date_scope if available (avoids re-parsing)
            if m["date_scope"] is not None:
                date_scope_cache[m["market_id"]] = m["date_scope"]
            if m["resolution_basis"] and m["group_template"]:
                continue  # already parsed, skip

            parsed = parse_slug(m["slug"], m["question"] or "")
            date_scope_cache[m["market_id"]] = parsed.date_scope
            conn.execute("""
                UPDATE market_registry SET
                    resolution_basis = ?, group_template = ?,
                    underlying_entity = ?, line_value = ?,
                    side_label = ?, parse_version = 'v1',
                    date_scope = ?
                WHERE market_id = ?
            """, (
                parsed.resolution_basis, parsed.group_template,
                parsed.underlying_entity, parsed.line_value,
                parsed.side_label, parsed.date_scope, m["market_id"]
            ))
            updated += 1
            if updated % 200 == 0:
                conn.commit()
        if updated:
            conn.commit()
            print(f"  Parsed {updated} new markets")

        # Re-fetch with parser fields
        markets = conn.execute("""
            SELECT market_id, slug, question, event_id, resolution_basis,
                   group_template, underlying_entity, line_value, side_label
            FROM market_registry
            WHERE active = 1 AND closed = 0
              AND resolution_basis IS NOT NULL
              AND group_template IS NOT NULL
        """).fetchall()

        # Get event slugs
        event_slugs = {}
        for row in conn.execute("SELECT event_id, event_slug FROM event_registry").fetchall():
            event_slugs[row["event_id"]] = row["event_slug"] or "unknown"

        # Group into families: event_id + basis + template + entity + date_scope
        families: dict[str, list[dict]] = {}
        for m in markets:
            event_id = m["event_id"]
            basis = m["resolution_basis"]
            template = m["group_template"]
            entity = m["underlying_entity"] or "unknown"
            ds = date_scope_cache.get(m["market_id"]) or "no_scope"

            key = f"{event_id}::{basis}::{template}::{entity}::{ds}"
            if key not in families:
                families[key] = []
            families[key].append(dict(m))

        # Compute max member count per group_template (for completeness scoring)
        template_max_counts: dict[str, int] = {}
        for members in families.values():
            if not members:
                continue
            template = members[0].get("group_template", "unknown")
            count = len(members)
            if template not in template_max_counts or count > template_max_counts[template]:
                template_max_counts[template] = count

        # Build family records
        families_written = 0
        members_written = 0
        write_ops = 0

        for family_key, members in families.items():
            parts = family_key.split("::", 4)
            event_id = parts[0]
            basis = parts[1]
            template = parts[2]
            entity = parts[3]
            ds = parts[4]
            date_scope = ds if ds != "no_scope" else None

            line_values = [m["line_value"] for m in members]
            side_labels = [m["side_label"] or "yes" for m in members]

            family_type = _classify_family_type(basis, line_values, side_labels)
            if family_type == "ignore":
                continue

            completeness = _compute_completeness(
                members, family_type, template_max_counts, template)
            quality = _compute_quality_score(members, family_type, completeness)

            event_slug = event_slugs.get(event_id, "unknown")
            display_key = _build_family_key(event_slug, basis, template, entity, date_scope)

            conn.execute("""
                INSERT INTO market_family (
                    family_key, event_id, family_type, resolution_basis,
                    group_template, underlying_entity, date_scope,
                    member_count, completeness_score, quality_score,
                    last_rebuilt_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                ON CONFLICT(family_key) DO UPDATE SET
                    event_id = excluded.event_id,
                    family_type = excluded.family_type,
                    resolution_basis = excluded.resolution_basis,
                    group_template = excluded.group_template,
                    underlying_entity = excluded.underlying_entity,
                    date_scope = excluded.date_scope,
                    member_count = excluded.member_count,
                    completeness_score = excluded.completeness_score,
                    quality_score = excluded.quality_score,
                    last_rebuilt_at = excluded.last_rebuilt_at,
                    status = excluded.status
            """, (
                display_key, event_id, family_type, basis,
                template, entity, date_scope,
                len(members), completeness, quality,
                now,
            ))
            families_written += 1
            write_ops += 1

            # Write family members
            for i, m in enumerate(members):
                conn.execute("""
                    INSERT INTO market_family_member (
                        family_key, market_id, slug, role_in_family, ordinal_in_chain
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(family_key, market_id) DO UPDATE SET
                        slug = excluded.slug,
                        role_in_family = excluded.role_in_family,
                        ordinal_in_chain = excluded.ordinal_in_chain
                """, (
                    display_key, m["market_id"], m["slug"],
                    m["side_label"] or "yes", i,
                ))
                members_written += 1
                write_ops += 1
                if write_ops % 100 == 0:
                    conn.commit()

        conn.commit()

        # Update scheduler_state
        conn.execute("""
            INSERT INTO scheduler_state (job_name, last_run_at, last_success_at, notes)
            VALUES ('family_builder', ?, ?, ?)
            ON CONFLICT(job_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_success_at = excluded.last_success_at,
                notes = excluded.notes
        """, (now, now, f"{families_written} families, {members_written} members"))
        conn.commit()

        print(f"Family builder done: {families_written} families, {members_written} members")

    finally:
        conn.close()
