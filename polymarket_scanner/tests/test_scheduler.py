"""Tests for scheduler registration / staggering (public export)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scheduler import register_all_jobs


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def register(self, name, fn, interval_seconds, description="", coalesce=True,
                 max_instances=1, start_date=None):
        self.calls.append({
            "name": name,
            "fn": fn,
            "interval_seconds": interval_seconds,
            "description": description,
            "start_date": start_date,
        })


def test_register_all_jobs_staggers_price_refresh_jobs():
    registry = FakeRegistry()
    register_all_jobs(registry)

    by_name = {call["name"]: call for call in registry.calls}

    assert by_name["price_refresh_hot"]["start_date"] is not None
    assert by_name["price_refresh_warm"]["start_date"] is not None
    assert by_name["price_refresh_cold"]["start_date"] is not None

    assert by_name["price_refresh_hot"]["start_date"] < by_name["price_refresh_warm"]["start_date"]
    assert by_name["price_refresh_warm"]["start_date"] < by_name["price_refresh_cold"]["start_date"]


def test_registers_only_infra_jobs():
    registry = FakeRegistry()
    register_all_jobs(registry)
    names = {call["name"] for call in registry.calls}

    assert {
        "event_indexer",
        "stale_rechecker",
        "family_builder",
        "snapshot_cleaner",
        "price_refresh_hot",
        "price_refresh_warm",
        "price_refresh_cold",
    }.issubset(names)
