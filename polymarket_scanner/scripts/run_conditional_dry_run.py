#!/usr/bin/env python3
"""Dry-run recorder for conditional strategy candidates.

Current scope: edgex_fdv rule from template-specific paper strategy v1.
This script never sends alerts. It only records paper entries/exits locally.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn
from app.scheduler import _load_recent_portfolio_markets
from app.services.portfolio_arbitrage import scan_conditional_range_strategy
from scripts.backtest_portfolio_arb import _opp_key
from scripts.evaluate_conditional_strategy_v1 import STRATEGY_V1_RULES, _record_matches_rule

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "conditional_edgex_dry_run_state.json"
EVENTS_PATH = BASE_DIR / "data" / "conditional_edgex_dry_run_events.jsonl"
RULE = next(r for r in STRATEGY_V1_RULES if r.group_template == "edgex_fdv")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(dt: str) -> datetime:
    return datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(timezone.utc)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "rule": {
                "group_template": RULE.group_template,
                "hold_minutes": RULE.hold_minutes,
                "min_entry_edge": RULE.min_entry_edge,
                "min_tradable_score": RULE.min_tradable_score,
            },
            "open_positions": {},
            "recent_entries": {},
            "closed_count": 0,
            "last_run_at": None,
        }
    return json.loads(STATE_PATH.read_text())


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _append_event(event: dict) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _selected_opportunities() -> list[dict]:
    conn = get_conn()
    try:
        markets = _load_recent_portfolio_markets(conn, freshness_minutes=5)
        opps = scan_conditional_range_strategy(markets, {"min_volume_usd": 10000})
        return [opp for opp in opps if _record_matches_rule(opp, RULE)]
    finally:
        conn.close()


def run_edgex_fdv_dry_run() -> dict:
    now = _now()
    state = _load_state()
    current_opps = _selected_opportunities()
    current_by_key = {_opp_key(opp): opp for opp in current_opps}

    # Close due positions
    hold_delta = timedelta(minutes=RULE.hold_minutes)
    still_open = {}
    for key, pos in state.get("open_positions", {}).items():
        opened_at = _parse(pos["opened_at"])
        if now - opened_at < hold_delta:
            still_open[key] = pos
            continue

        current = current_by_key.get(key)
        exit_edge = current.get("edge", 0.0) if current else 0.0
        event = {
            "kind": "exit",
            "at": _iso(now),
            "key": key,
            "group_template": pos.get("group_template"),
            "entry_edge": pos.get("entry_edge"),
            "tradable_score": pos.get("tradable_score"),
            "alive": current is not None,
            "exit_edge": round(exit_edge, 6),
            "edge_decay": round(exit_edge - pos.get("entry_edge", 0.0), 6),
            "held_minutes": RULE.hold_minutes,
        }
        _append_event(event)
        state["closed_count"] = state.get("closed_count", 0) + 1
        state.setdefault("recent_entries", {})[key] = _iso(now)
    state["open_positions"] = still_open

    # Open new positions
    for key, opp in current_by_key.items():
        if key in state["open_positions"]:
            continue
        last_seen = state.get("recent_entries", {}).get(key)
        if last_seen and now - _parse(last_seen) < hold_delta:
            continue
        pos = {
            "opened_at": _iso(now),
            "slug_a": opp.get("slug_a"),
            "slug_b": opp.get("slug_b"),
            "group_template": opp.get("group_template"),
            "entry_edge": opp.get("edge"),
            "tradable_score": opp.get("tradable_score"),
            "hold_minutes": RULE.hold_minutes,
        }
        state["open_positions"][key] = pos
        _append_event({
            "kind": "entry",
            "at": _iso(now),
            "key": key,
            **pos,
        })

    state["last_run_at"] = _iso(now)
    _save_state(state)
    return {
        "timestamp": _iso(now),
        "selected_now": len(current_opps),
        "open_positions": len(state["open_positions"]),
        "closed_count": state.get("closed_count", 0),
    }


if __name__ == "__main__":
    print(json.dumps(run_edgex_fdv_dry_run(), ensure_ascii=False, indent=2))
