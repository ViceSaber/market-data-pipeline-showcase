"""Health Check — interval-aware scheduler status with noise reduction.

Shows only registered scheduler jobs individually, aggregates per-market
`price_refresh_<market_id>` state rows, and summarizes recent DB-lock failures
from scheduler logs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn
from config.settings import REFRESH_INTERVALS


REGISTERED_JOB_INTERVALS = {
    "event_indexer": 6 * 3600,
    "stale_rechecker": 24 * 3600,
    "family_builder": 4 * 3600,
    "scanner": 15 * 60,
    "confirmer": 5 * 60,
    "alert_check": 5 * 60,
    "snapshot_cleaner": 24 * 3600,
    "volume_spike": 3600,
    "portfolio_arb": 600,
    "price_refresh_hot": REFRESH_INTERVALS["hot"],
    "price_refresh_warm": REFRESH_INTERVALS["warm"],
    "price_refresh_cold": REFRESH_INTERVALS["cold"],
}

LOCK_PATTERNS = [
    re.compile(r"Job (?P<job>[\w_]+) mark_running failed: database is locked"),
    re.compile(r"Job (?P<job>[\w_]+) state write failed: database is locked"),
    re.compile(r"Job (?P<job>[\w_]+) failed: database is locked"),
]


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_age(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}h"


def _parse_notes_kv(notes: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (notes or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def classify_registered_job(row, now: datetime) -> dict:
    name = row["job_name"]
    interval = REGISTERED_JOB_INTERVALS.get(name)
    notes = (row["notes"] or "").strip()
    last_run = _parse_ts(row["last_run_at"])
    last_ok = _parse_ts(row["last_success_at"])

    timestamps = [ts for ts in (last_run, last_ok) if ts is not None]
    latest = max(timestamps) if timestamps else None
    age_seconds = (now - latest).total_seconds() if latest else None

    if latest is None:
        return {
            "name": name,
            "interval": interval,
            "state": "never",
            "icon": "⚪",
            "summary": "never run",
        }

    if notes == "running":
        run_age = (now - last_run).total_seconds() if last_run else age_seconds
        running_grace = max(interval * 2 if interval else 0, 300)
        if run_age <= running_grace:
            state = "running"
            icon = "🏃"
            summary = f"running for {_format_age(run_age)}"
        else:
            state = "stuck"
            icon = "🟠"
            summary = f"running for {_format_age(run_age)} (possible stuck)"
    elif notes.startswith("error:"):
        state = "error"
        icon = "❌"
        summary = f"error {(_format_age(age_seconds))} ago"
    else:
        stale_after = max(int(interval * 1.5), 60) if interval else 900
        dead_after = max(interval * 3, stale_after + 1) if interval else 3600

        if age_seconds <= stale_after:
            state = "ok"
            icon = "✅"
        elif age_seconds <= dead_after:
            state = "stale"
            icon = "⚠️"
        else:
            state = "dead"
            icon = "🔴"
        summary = f"last activity {_format_age(age_seconds)} ago"

    if last_run and last_ok and last_ok > last_run:
        summary += " | last_success newer than last_run (mark_running likely missed/locked)"

    return {
        "name": name,
        "interval": interval,
        "state": state,
        "icon": icon,
        "summary": summary,
        "notes": notes,
        "last_run": last_run,
        "last_ok": last_ok,
    }


def aggregate_price_refresh_market_states(rows: list) -> dict:
    market_rows = [
        row for row in rows
        if row["job_name"].startswith("price_refresh_")
        and row["job_name"] not in REGISTERED_JOB_INTERVALS
    ]

    current_tiers = Counter()
    pending_targets = Counter()
    stable_counts = Counter()

    for row in market_rows:
        kv = _parse_notes_kv(row["notes"] or "")
        current_tiers[kv.get("tier", "unknown")] += 1
        if kv.get("target"):
            pending_targets[f"{kv.get('tier', 'unknown')}→{kv['target']}"] += 1
        if kv.get("stable"):
            stable_counts[kv["stable"]] += 1

    return {
        "total": len(market_rows),
        "current_tiers": current_tiers,
        "pending_targets": pending_targets,
        "stable_counts": stable_counts,
    }


def find_shadow_rows(rows: list) -> list:
    shadows = []
    for row in rows:
        name = row["job_name"]
        if name in REGISTERED_JOB_INTERVALS:
            continue
        if name.startswith("price_refresh_"):
            continue
        shadows.append(row)
    return shadows


def load_baseline_line(baseline_path: Path) -> tuple[int | None, str | None]:
    """Load `err_lines_from` and optional label from a baseline JSON file."""
    try:
        data = json.loads(baseline_path.read_text())
    except Exception:
        return None, None

    line = data.get("err_lines_from")
    if not isinstance(line, int):
        return None, data.get("baseline_at")
    return line, data.get("baseline_at")


def summarize_recent_lock_failures(
    log_path: Path,
    max_lines: int = 4000,
    baseline_line: int | None = None,
) -> Counter:
    if not log_path.exists():
        return Counter()

    try:
        all_lines = log_path.read_text(errors="ignore").splitlines()
    except Exception:
        return Counter()

    if baseline_line is not None:
        lines = all_lines[max(baseline_line, 0):]
    else:
        lines = all_lines[-max_lines:]

    counts = Counter()
    for line in lines:
        for pattern in LOCK_PATTERNS:
            m = pattern.search(line)
            if m:
                counts[m.group("job")] += 1
                break
    return counts


def render_health_check(
    rows: list,
    now: datetime,
    log_path: Path | None = None,
    *,
    lock_baseline_line: int | None = None,
    lock_baseline_label: str | None = None,
) -> str:
    registered = [row for row in rows if row["job_name"] in REGISTERED_JOB_INTERVALS]
    registered.sort(key=lambda row: row["job_name"])
    classified = [classify_registered_job(row, now) for row in registered]

    lines = [
        f"Health Check @ {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "=" * 70,
        "",
        "Registered scheduler jobs:",
    ]

    if not classified:
        lines.append("  ⚪ No registered jobs recorded yet")
    else:
        for job in classified:
            interval = job.get("interval")
            interval_str = f" / every {_format_age(interval)}" if interval else ""
            notes = job.get("notes", "")
            note_str = f"  [{notes}]" if notes and notes not in {"ok", "running"} else ""
            lines.append(f"  {job['icon']} {job['name']:20s} {job['summary']}{interval_str}{note_str}")

    agg = aggregate_price_refresh_market_states(rows)
    lines.extend(["", "Per-market price_refresh state (aggregated):"])
    if agg["total"] == 0:
        lines.append("  ⚪ No per-market price_refresh state rows")
    else:
        tier_summary = ", ".join(f"{tier}={count}" for tier, count in sorted(agg["current_tiers"].items()))
        lines.append(f"  📦 tracked markets: {agg['total']} ({tier_summary})")
        if agg["pending_targets"]:
            pending = ", ".join(f"{k}={v}" for k, v in sorted(agg["pending_targets"].items()))
            lines.append(f"  🔁 pending transitions: {pending}")

    shadows = find_shadow_rows(rows)
    lines.extend(["", "Legacy / shadow scheduler_state rows:"])
    if not shadows:
        lines.append("  ✅ none")
    else:
        for row in sorted(shadows, key=lambda r: r["job_name"]):
            notes = (row["notes"] or "").strip() or "(no notes)"
            lines.append(f"  🧩 {row['job_name']}: {notes}")

    if log_path is not None:
        lock_counts = summarize_recent_lock_failures(log_path, baseline_line=lock_baseline_line)
        if lock_baseline_line is not None:
            label = lock_baseline_label or f"line {lock_baseline_line}"
            section_title = f"DB-lock failures since baseline ({label}):"
            empty_msg = "  ✅ none found since baseline"
        else:
            section_title = "Recent DB-lock failures (scheduler.err.log tail):"
            empty_msg = "  ✅ none found in recent log tail"

        lines.extend(["", section_title])
        if not lock_counts:
            lines.append(empty_msg)
        else:
            for job, count in lock_counts.most_common():
                lines.append(f"  🔒 {job}: {count} lock-related failures")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Interval-aware scheduler health check")
    parser.add_argument("--baseline", type=str, help="Path to baseline JSON containing err_lines_from")
    parser.add_argument("--baseline-line", type=int, help="Only count DB-lock failures after this line number in scheduler.err.log")
    args = parser.parse_args(argv)

    conn = get_conn()
    now = datetime.now(timezone.utc)
    rows = conn.execute("SELECT * FROM scheduler_state ORDER BY job_name").fetchall()
    conn.close()

    log_path = Path(__file__).resolve().parent.parent / "logs" / "scheduler.err.log"
    baseline_line = args.baseline_line
    baseline_label = None

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_absolute():
            baseline_path = (Path(__file__).resolve().parent.parent / baseline_path).resolve()
        baseline_line, baseline_label = load_baseline_line(baseline_path)
        if baseline_line is None:
            baseline_label = str(baseline_path)

    print(
        render_health_check(
            rows,
            now,
            log_path=log_path,
            lock_baseline_line=baseline_line,
            lock_baseline_label=baseline_label,
        )
    )


if __name__ == "__main__":
    main()
