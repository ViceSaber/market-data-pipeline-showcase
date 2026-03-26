"""Scheduler — sanitized public showcase version.

This public export keeps infrastructure-oriented orchestration only:
- event indexing
- stale market recheck
- family building
- price refresh tiers
- snapshot cleanup

Proprietary signal / strategy / alert jobs are intentionally excluded.
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import TIMEZONE, REFRESH_INTERVALS

log = logging.getLogger(__name__)


def _recover_stale_running_jobs(job_meta: dict[str, dict]) -> int:
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


class JobRegistry:
    def __init__(self, scheduler: BlockingScheduler):
        self.scheduler = scheduler
        self._jobs: dict[str, dict] = {}

    def register(self, name: str, fn, interval_seconds: int,
                 description: str = "", coalesce: bool = True,
                 max_instances: int = 1, start_date=None):
        kwargs = dict(seconds=interval_seconds, id=name,
                      coalesce=coalesce, max_instances=max_instances,
                      misfire_grace_time=max(30, interval_seconds // 2))
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
        return dict(self._jobs)

    def start(self):
        recovered = _recover_stale_running_jobs(self._jobs)
        log.info("Scheduler starting (timezone=%s)", TIMEZONE)
        if recovered:
            log.info("Recovered %d stale running job rows before startup", recovered)
        log.info("Registered %d jobs:", len(self._jobs))
        for name, meta in self._jobs.items():
            log.info("  - %s: every %ds (%s)", name, meta["interval"], meta["description"])
        self.scheduler.start()


class TrackedJob:
    def __init__(self, name: str, fn, description: str = ""):
        self.name = name
        self.fn = fn
        self.description = description

    def _write_state(self, **fields):
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
        finally:
            conn.close()

    def _mark_running(self):
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
        finally:
            conn.close()

    def __call__(self):
        now = datetime.now(timezone.utc).isoformat()
        self._mark_running()
        try:
            self.fn()
            self._write_state(last_success_at=now, notes="ok")
            log.info("Job %s completed OK", self.name)
        except Exception as e:
            self._write_state(notes=f"error: {str(e)[:200]}")
            log.error("Job %s failed: %s", self.name, e)
            raise


def register_all_jobs(registry: JobRegistry):
    from app.services.event_indexer import run_event_indexer
    from app.services.stale_rechecker import run_stale_rechecker
    from app.services.family_builder import run_family_builder
    from app.services.price_refresher import refresh_tier, cleanup_old_snapshots

    tracked_indexer = TrackedJob("event_indexer", run_event_indexer, "Full event→market index")
    tracked_stale = TrackedJob("stale_rechecker", run_stale_rechecker, "Mark expired markets as stale")
    tracked_family = TrackedJob("family_builder", run_family_builder, "Build market families")

    registry.register("event_indexer", tracked_indexer, interval_seconds=6 * 3600, description="Event indexer (6h)")
    registry.register("stale_rechecker", tracked_stale, interval_seconds=24 * 3600, description="Stale rechecker (24h)")
    registry.register("family_builder", tracked_family, interval_seconds=4 * 3600, description="Family builder (4h)")

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
        "hot": timedelta(seconds=210),
        "warm": timedelta(seconds=390),
        "cold": timedelta(seconds=570),
    }
    scheduler_start = datetime.now()

    for tier in ["hot", "warm", "cold"]:
        interval = REFRESH_INTERVALS[tier]
        fn = make_refresh_fn(tier)
        tracked = TrackedJob(f"price_refresh_{tier}", fn, f"Price refresh ({tier})")
        registry.register(
            f"price_refresh_{tier}", tracked,
            interval_seconds=interval,
            start_date=scheduler_start + refresh_staggers[tier],
            description=f"Price refresh {tier} ({interval}s, stagger +{int(refresh_staggers[tier].total_seconds())}s)",
        )

    def run_cleanup():
        from app.db import get_conn
        c = get_conn()
        try:
            deleted = cleanup_old_snapshots(c)
            if deleted > 0:
                log.info("Cleaned %d old snapshots", deleted)
        finally:
            c.close()

    tracked_cleanup = TrackedJob("snapshot_cleaner", run_cleanup, "Old snapshot cleanup")
    registry.register("snapshot_cleaner", tracked_cleanup, interval_seconds=24 * 3600, description="Snapshot cleanup (24h)")
