"""Scheduler — unified job orchestration for all phases.

Manages Phase 1 (indexer, stale rechecker), Phase 2 (family builder,
scanner, confirmer), and Phase 3 (price refresher, alert engine) jobs.

Uses APScheduler for interval-based scheduling with coalescing and
single-instance guarantees. Persists job state to scheduler_state table.
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import TIMEZONE, REFRESH_INTERVALS

log = logging.getLogger(__name__)


def _recover_stale_running_jobs(job_meta: dict[str, dict]) -> int:
    """Mark lingering `notes='running'` rows as recovered on scheduler startup.

    If the scheduler process restarts mid-run, scheduler_state can be left in a
    stale running state forever. On a clean startup we should heal those rows so
    health_check reflects reality.
    """
    from app.db import get_conn, execute_with_retry, commit_with_retry

    conn = get_conn()
    try:
        running_rows = conn.execute(
            "SELECT job_name, last_run_at FROM scheduler_state WHERE notes = 'running'"
        ).fetchall()
        recovered = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in running_rows:
            job_name = row["job_name"]
            if job_name not in job_meta:
                continue
            interval = job_meta[job_name]["interval"]
            note = f"recovered_stale_running_on_startup interval={interval}s at {now}"
            execute_with_retry(
                conn,
                "UPDATE scheduler_state SET notes = ? WHERE job_name = ?",
                (note, job_name),
                label=f"scheduler_state[{job_name}] recover_stale_running",
            )
            recovered += 1
        if recovered:
            commit_with_retry(conn, label="scheduler_state recover_stale_running commit")
            log.warning("Recovered %d stale running scheduler_state rows on startup", recovered)
        return recovered
    finally:
        conn.close()


def _load_recent_portfolio_markets(conn, freshness_minutes: int = 5) -> list[dict]:
    """Return one fresh latest snapshot per active market for portfolio_arb.

    Uses `market_snapshot_latest` so we don't accidentally mix stale historical
    rows with `GROUP BY slug` indeterminism from `market_snapshot`.
    """
    rows = conn.execute(
        f"""SELECT slug, last_price_yes, volume_24h_num, liquidity_num, fetched_at
            FROM market_snapshot_latest
            WHERE datetime(fetched_at) > datetime('now', '-{int(freshness_minutes)} minutes')
              AND last_price_yes IS NOT NULL
              AND COALESCE(active, 1) = 1
              AND COALESCE(closed, 0) = 0"""
    ).fetchall()
    return [dict(r) for r in rows]


class JobRegistry:
    """Register and manage all scheduled jobs."""

    def __init__(self, scheduler: BlockingScheduler):
        self.scheduler = scheduler
        self._jobs: dict[str, dict] = {}

    def register(self, name: str, fn, interval_seconds: int,
                 description: str = "", coalesce: bool = True,
                 max_instances: int = 1, start_date=None):
        """Register a periodic job. Optional start_date for stagger."""
        kwargs = dict(seconds=interval_seconds, id=name,
                       coalesce=coalesce, max_instances=max_instances,
                       misfire_grace_time=interval_seconds // 2)
        if start_date:
            kwargs["start_date"] = start_date
        self.scheduler.add_job(fn, "interval", **kwargs)
        self._jobs[name] = {
            "fn": fn,
            "interval": interval_seconds,
            "description": description,
        }
        log.info("Registered job: %s (every %ds%s)", name,
                 interval_seconds,
                 f", start_date={start_date}" if start_date else "")

    def list_jobs(self) -> dict[str, dict]:
        """Return all registered job metadata."""
        return dict(self._jobs)

    def start(self):
        """Start the scheduler loop."""
        recovered = _recover_stale_running_jobs(self._jobs)
        log.info("Scheduler starting (timezone=%s)", TIMEZONE)
        if recovered:
            log.info("Recovered %d stale running job rows before startup", recovered)
        log.info("Registered %d jobs:", len(self._jobs))
        for name, meta in self._jobs.items():
            log.info("  - %s: every %ds (%s)", name,
                     meta["interval"], meta["description"])
        self.scheduler.start()


# ── Job wrapper with state tracking ──────────────────────────


class TrackedJob:
    """Wraps a job function to track execution in scheduler_state.

    Concurrency hardening:
    - Separate connection per state write (no connection sharing with job)
    - Clean state on crash (never stuck in 'running')
    - Detailed logging for state transitions
    """

    def __init__(self, name: str, fn, description: str = ""):
        self.name = name
        self.fn = fn
        self.description = description

    def _write_state(self, **fields):
        """Write scheduler_state with isolated connection. Short transaction."""
        from app.db import get_conn, execute_with_retry, commit_with_retry
        conn = get_conn()
        try:
            columns = ["job_name", *fields.keys()]
            placeholders = ", ".join("?" for _ in columns)
            updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
            values = [self.name, *fields.values()]
            sql = (
                f"INSERT INTO scheduler_state ({', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(job_name) DO UPDATE SET {updates}"
            )
            execute_with_retry(conn, sql, values, label=f"scheduler_state[{self.name}] write")
            commit_with_retry(conn, label=f"scheduler_state[{self.name}] commit")
            log.debug("Job %s state: %s", self.name, fields)
        except Exception as e:
            log.warning("Job %s state write failed: %s", self.name, e)
        finally:
            conn.close()

    def _mark_running(self):
        """Mark job as running. Isolated connection, short transaction."""
        from app.db import get_conn, execute_with_retry, commit_with_retry
        conn = get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            execute_with_retry(
                conn,
                """INSERT INTO scheduler_state (job_name, last_run_at, notes)
                   VALUES (?, ?, 'running')
                   ON CONFLICT(job_name) DO UPDATE SET
                       last_run_at = ?, notes = 'running'""",
                (self.name, now, now),
                label=f"scheduler_state[{self.name}] mark_running",
            )
            commit_with_retry(conn, label=f"scheduler_state[{self.name}] mark_running commit")
            log.debug("Job %s → running", self.name)
        except Exception as e:
            log.warning("Job %s mark_running failed: %s", self.name, e)
        finally:
            conn.close()

    def __call__(self):
        """Execute the job and record state. Creates per-call DB connection."""
        now = datetime.now(timezone.utc).isoformat()

        # 1. Mark running (isolated connection, short transaction)
        self._mark_running()

        # 2. Execute job (with its own connection)
        try:
            self.fn()
            # Mark success
            self._write_state(last_success_at=now, notes="ok")
            log.info("Job %s completed OK", self.name)
        except Exception as e:
            # Mark failure — NEVER stuck in 'running'
            self._write_state(notes=f"error: {str(e)[:200]}")
            log.error("Job %s failed: %s", self.name, e)
            raise  # Let APScheduler log the traceback


def register_all_jobs(registry: JobRegistry):
    """Register all Phase 1-3 jobs with the scheduler.

    Note: Each job receives a per-thread conn from TrackedJob.__call__().
    All job functions must accept (conn) as their first argument.
    """

    # ── Phase 1 ──────────────────────────────────────────────
    from app.services.event_indexer import run_event_indexer
    from app.services.stale_rechecker import run_stale_rechecker

    tracked_indexer = TrackedJob("event_indexer", run_event_indexer,
                                 "Full event→market index")
    tracked_stale = TrackedJob("stale_rechecker", run_stale_rechecker,
                               "Mark expired markets as stale")

    registry.register("event_indexer", tracked_indexer,
                      interval_seconds=6 * 3600,
                      description="Event indexer (6h)")
    registry.register("stale_rechecker", tracked_stale,
                      interval_seconds=24 * 3600,
                      description="Stale rechecker (24h)")

    # ── Phase 2 ──────────────────────────────────────────────
    from app.services.family_builder import run_family_builder
    from app.services.scanner import run_scanner
    from app.services.candidate_confirmer import run_candidate_confirmer

    tracked_family = TrackedJob("family_builder", run_family_builder,
                                "Build market families")
    tracked_scanner = TrackedJob("scanner", run_scanner,
                                 "Scan for arbitrage opportunities")
    tracked_confirmer = TrackedJob("confirmer", run_candidate_confirmer,
                                   "Confirm candidates with live prices")

    registry.register("family_builder", tracked_family,
                      interval_seconds=4 * 3600,
                      description="Family builder (4h)")
    registry.register("scanner", tracked_scanner,
                      interval_seconds=15 * 60,
                      description="Arbitrage scanner (15m)")
    registry.register("confirmer", tracked_confirmer,
                      interval_seconds=5 * 60,
                      description="Candidate confirmer (5m)")

    # ── Phase 3: Price Refresher ─────────────────────────────
    from app.services.price_refresher import refresh_tier, cleanup_old_snapshots

    def make_refresh_fn(tier: str):
        def _refresh():
            from app.db import get_conn
            c = get_conn()
            try:
                refresh_tier(c, tier)
            finally:
                c.close()
        _refresh.__name__ = f"refresh_{tier}"
        return _refresh

    refresh_staggers = {
        "hot": timedelta(seconds=210),   # avoid 5m confirmer / alert cadence
        "warm": timedelta(seconds=390),  # avoid scanner + hot overlap
        "cold": timedelta(seconds=570),  # low-frequency, keep well separated
    }
    scheduler_start = datetime.now()

    for tier in ["hot", "warm", "cold"]:
        interval = REFRESH_INTERVALS[tier]
        fn = make_refresh_fn(tier)
        tracked = TrackedJob(f"price_refresh_{tier}", fn,
                             f"Price refresh ({tier})")
        registry.register(
            f"price_refresh_{tier}", tracked,
            interval_seconds=interval,
            start_date=scheduler_start + refresh_staggers[tier],
            description=f"Price refresh {tier} ({interval}s, stagger +{int(refresh_staggers[tier].total_seconds())}s)",
        )

    # ── Phase 3: Alert Engine ────────────────────────────────
    from app.services.alert_engine import AlertEngine

    def run_alert_check():
        from app.db import get_conn
        c = get_conn()
        try:
            engine = AlertEngine(c)
            sent = engine.process_alerts()
            if sent > 0:
                log.info("Alerts sent: %d", sent)
        finally:
            c.close()

    tracked_alerts = TrackedJob("alert_check", run_alert_check,
                                "Price anomaly alerts")
    registry.register("alert_check", tracked_alerts,
                      interval_seconds=5 * 60,
                      start_date=datetime.now() + timedelta(minutes=2.5),
                      description="Alert check (5m, stagger +2.5m)")

    # ── Snapshot cleanup ─────────────────────────────────────
    def run_cleanup():
        from app.db import get_conn
        c = get_conn()
        try:
            deleted = cleanup_old_snapshots(c)
            if deleted > 0:
                log.info("Cleaned %d old snapshots", deleted)
        finally:
            c.close()

    tracked_cleanup = TrackedJob("snapshot_cleaner", run_cleanup,
                                 "Old snapshot cleanup")
    registry.register("snapshot_cleaner", tracked_cleanup,
                      interval_seconds=24 * 3600,
                      description="Snapshot cleanup (24h)")

    # ── Volume Spike Detection ───────────────────────────────
    from scripts.volume_spike import scan_all

    def run_volume_spike():
        from app.db import get_conn
        c = get_conn()
        try:
            spikes = scan_all(c, send=True, z_threshold=3.0)
            if spikes:
                log.info("Volume spikes detected: %d", len(spikes))
        finally:
            c.close()

    tracked_spike = TrackedJob("volume_spike", run_volume_spike,
                               "Volume spike detection (z>2σ)")
    registry.register("volume_spike", tracked_spike,
                      interval_seconds=3600,
                      description="Volume spike detection (1h)")

    # ── Portfolio Arbitrage ────────────────────────────────────
    from app.services.portfolio_arbitrage import scan_portfolio, format_opportunity

    def run_portfolio_arb():
        import json
        from app.db import get_conn
        c = get_conn()
        try:
            market_dicts = _load_recent_portfolio_markets(c, freshness_minutes=5)
            opps = scan_portfolio(market_dicts, {"min_edge": 0.02, "min_volume_usd": 10000})

            if opps:
                # Re-verify with latest prices before sending
                verified_opps = []
                for opp in opps:
                    # Get the freshest price for both slugs
                    for slug_key in ["slug_a", "slug_b"]:
                        slug = opp.get(slug_key)
                        if slug:
                            latest = c.execute(
                                "SELECT last_price_yes FROM market_snapshot_latest WHERE slug = ? LIMIT 1",
                                (slug,)
                            ).fetchone()
                            if latest:
                                opp[f"{slug_key}_latest"] = latest["last_price_yes"]
                    verified_opps.append(opp)

                log.info("Portfolio arb: %d opportunities found", len(verified_opps))
                from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
                import urllib.request

                # Dedup: skip arb if same slug_a was sent in last 30 min
                sent_recently = set(
                    row["slug"] for row in c.execute(
                        "SELECT slug FROM alert_log WHERE sent_at > datetime('now', '-30 minutes') AND status='sent'"
                    ).fetchall()
                )

                max_send = 3
                sent_count = 0
                pending_logs = []  # batch insert to avoid locking
                sent_slugs = set()  # track which were sent via Telegram

                for opp in verified_opps:
                    slug_a = opp.get("slug_a", "")
                    arb_type = opp.get("type", "unknown")
                    edge = opp.get("edge", 0)

                    # Queue all opportunities for DB log
                    pending_logs.append((slug_a, f"portfolio_{arb_type}_edge={edge:.4f}"))

                    if sent_count >= max_send:
                        continue
                    if slug_a in sent_recently:
                        log.info("Portfolio arb dedup: skipping %s (sent recently)", slug_a)
                        continue
                    text = format_opportunity(opp)
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    data = json.dumps({
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": text,
                        "parse_mode": "Markdown",
                    }).encode()
                    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        log.info("Portfolio arb alert sent: %s", opp["type"])
                        sent_count += 1
                        sent_recently.add(slug_a)
                        sent_slugs.add(slug_a)
                    except Exception as e:
                        log.error("Telegram send failed for %s: %s", slug_a, e)

                # Batch log all opportunities to alert_log
                if pending_logs:
                    try:
                        c.executemany(
                            """INSERT INTO alert_log (result_id, slug, sent_at, channel, status, error_message)
                               VALUES (NULL, ?, datetime('now'), 'db', 'logged', ?)""",
                            pending_logs
                        )
                        c.commit()

                        # Update sent ones to telegram status
                        for slug in sent_slugs:
                            c.execute(
                                """UPDATE alert_log SET channel='telegram', status='sent'
                                   WHERE slug = ? AND sent_at > datetime('now', '-1 minute') AND channel='db'""",
                                (slug,)
                            )
                        c.commit()
                    except Exception as e:
                        log.error("Batch alert_log insert/update failed: %s", e)
        finally:
            c.close()

    tracked_portfolio = TrackedJob("portfolio_arb", run_portfolio_arb,
                                   "Portfolio arbitrage scan")
    registry.register("portfolio_arb", tracked_portfolio,
                      interval_seconds=600,
                      start_date=datetime.now() + timedelta(minutes=1),
                      description="Portfolio arbitrage (10m, stagger +1m)")

    # ── Conditional dry-run recorder (research only, no push) ──
    from scripts.run_conditional_dry_run import run_edgex_fdv_dry_run

    tracked_conditional_dry = TrackedJob(
        "conditional_edgex_dry_run",
        run_edgex_fdv_dry_run,
        "Conditional edgex_fdv dry-run recorder",
    )
    registry.register(
        "conditional_edgex_dry_run",
        tracked_conditional_dry,
        interval_seconds=600,
        start_date=datetime.now() + timedelta(minutes=4),
        description="Conditional Edgex dry-run (10m, no push)",
    )
