"""Tests for interval-aware health_check output."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.health_check import (
    classify_registered_job,
    aggregate_price_refresh_market_states,
    find_shadow_rows,
    load_baseline_line,
    summarize_recent_lock_failures,
    render_health_check,
)


def row(job_name, last_run_at=None, last_success_at=None, notes=""):
    return {
        "job_name": job_name,
        "last_run_at": last_run_at,
        "last_success_at": last_success_at,
        "notes": notes,
    }


def test_classify_registered_job_is_interval_aware_for_slow_jobs():
    now = datetime(2026, 3, 25, 5, 0, tzinfo=timezone.utc)
    result = classify_registered_job(
        row(
            "event_indexer",
            last_run_at="2026-03-24T19:00:00+00:00",
            last_success_at="2026-03-24T19:00:00+00:00",
            notes="ok",
        ),
        now,
    )
    assert result["state"] == "stale"


def test_classify_registered_job_detects_running_vs_stuck():
    now = datetime(2026, 3, 25, 5, 0, tzinfo=timezone.utc)

    running = classify_registered_job(
        row(
            "confirmer",
            last_run_at="2026-03-25T04:57:00+00:00",
            last_success_at="2026-03-25T04:52:00+00:00",
            notes="running",
        ),
        now,
    )
    assert running["state"] == "running"

    stuck = classify_registered_job(
        row(
            "confirmer",
            last_run_at="2026-03-25T04:30:00+00:00",
            last_success_at="2026-03-25T04:25:00+00:00",
            notes="running",
        ),
        now,
    )
    assert stuck["state"] == "stuck"


def test_aggregate_price_refresh_market_states_counts_tiers_and_pending_targets():
    rows = [
        row("price_refresh_hot", notes="ok"),
        row("price_refresh_123", notes="tier=hot;stable=0"),
        row("price_refresh_124", notes="tier=warm;target=hot;stable=1"),
        row("price_refresh_125", notes="tier=cold;target=warm;stable=2"),
    ]

    agg = aggregate_price_refresh_market_states(rows)
    assert agg["total"] == 3
    assert agg["current_tiers"]["hot"] == 1
    assert agg["current_tiers"]["warm"] == 1
    assert agg["current_tiers"]["cold"] == 1
    assert agg["pending_targets"]["warm→hot"] == 1
    assert agg["pending_targets"]["cold→warm"] == 1


def test_find_shadow_rows_excludes_registered_and_market_state_rows():
    rows = [
        row("scanner", notes="ok"),
        row("price_refresh_hot", notes="ok"),
        row("price_refresh_123", notes="tier=hot;stable=0"),
        row("candidate_confirmer", notes="2 candidates processed"),
    ]
    shadows = find_shadow_rows(rows)
    assert [r["job_name"] for r in shadows] == ["candidate_confirmer"]


def test_summarize_recent_lock_failures(tmp_path):
    log = tmp_path / "scheduler.err.log"
    log.write_text(
        "\n".join([
            "2026-03-25 ... Job confirmer failed: database is locked",
            "2026-03-25 ... Job portfolio_arb mark_running failed: database is locked",
            "2026-03-25 ... Job confirmer state write failed: database is locked",
        ])
    )
    counts = summarize_recent_lock_failures(log)
    assert counts["confirmer"] == 2
    assert counts["portfolio_arb"] == 1


def test_summarize_recent_lock_failures_supports_baseline_line(tmp_path):
    log = tmp_path / "scheduler.err.log"
    log.write_text(
        "\n".join([
            "old line",
            "2026-03-25 ... Job confirmer failed: database is locked",
            "2026-03-25 ... Job portfolio_arb mark_running failed: database is locked",
            "2026-03-25 ... Job confirmer state write failed: database is locked",
        ])
    )
    counts = summarize_recent_lock_failures(log, baseline_line=2)
    assert counts["portfolio_arb"] == 1
    assert counts["confirmer"] == 1


def test_load_baseline_line_from_json(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"baseline_at": "2026-03-25T05:59:30+08:00", "err_lines_from": 188411}')
    line, label = load_baseline_line(baseline)
    assert line == 188411
    assert label == "2026-03-25T05:59:30+08:00"


def test_render_health_check_includes_sections():
    now = datetime(2026, 3, 25, 5, 0, tzinfo=timezone.utc)
    rows = [
        row("scanner", last_run_at="2026-03-25T04:55:00+00:00", last_success_at="2026-03-25T04:55:00+00:00", notes="ok"),
        row("price_refresh_123", notes="tier=warm;target=hot;stable=1"),
        row("candidate_confirmer", notes="2 candidates processed"),
    ]
    rendered = render_health_check(rows, now)
    assert "Registered scheduler jobs:" in rendered
    assert "Per-market price_refresh state (aggregated):" in rendered
    assert "Legacy / shadow scheduler_state rows:" in rendered
    assert "candidate_confirmer" in rendered


def test_render_health_check_supports_baseline_lock_section(tmp_path):
    now = datetime(2026, 3, 25, 5, 0, tzinfo=timezone.utc)
    rows = [row("scanner", last_run_at="2026-03-25T04:55:00+00:00", last_success_at="2026-03-25T04:55:00+00:00", notes="ok")]
    log = tmp_path / "scheduler.err.log"
    log.write_text(
        "\n".join([
            "old historical line",
            "2026-03-25 ... Job confirmer failed: database is locked",
        ])
    )
    rendered = render_health_check(
        rows,
        now,
        log_path=log,
        lock_baseline_line=1,
        lock_baseline_label="2026-03-25T05:59:30+08:00",
    )
    assert "DB-lock failures since baseline (2026-03-25T05:59:30+08:00):" in rendered
    assert "🔒 confirmer: 1 lock-related failures" in rendered
