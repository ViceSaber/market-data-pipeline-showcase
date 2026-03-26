"""Scheduler entry point — runs all Phase 1-3 jobs.

Usage:
    python scripts/run_scheduler.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler

from app.scheduler import JobRegistry, register_all_jobs
from config.settings import TIMEZONE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def main():
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    registry = JobRegistry(scheduler)

    register_all_jobs(registry)

    jobs = registry.list_jobs()
    log.info("=" * 50)
    log.info("Polymarket Scheduler — %d jobs registered", len(jobs))
    for name, meta in jobs.items():
        log.info("  %-25s every %5ds  %s",
                 name, meta["interval"], meta["description"])
    log.info("=" * 50)
    log.info("Starting... (Ctrl+C to stop)")

    try:
        registry.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
