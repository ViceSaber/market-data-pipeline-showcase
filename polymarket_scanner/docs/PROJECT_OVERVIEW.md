# Polymarket Scanner — Public Overview

This document is the sanitized public overview for the scanner subsystem.

## Summary
A prediction-market data pipeline focused on:
- event / market ingestion
- structured market parsing
- family/group construction
- price snapshot refresh tiers
- scheduler state tracking
- operational health checks

## Included public modules
- `app/clients/` — API client + rate limiting
- `app/parsers/` — slug parsing into structured metadata
- `app/repositories/` — repository abstractions
- `app/scheduler/` — APScheduler orchestration scaffold
- `app/services/event_indexer.py` — event / market indexing
- `app/services/stale_rechecker.py` — stale market recovery
- `app/services/family_builder.py` — grouping related markets
- `app/services/price_refresher.py` — hot / warm / cold snapshot refresh
- `scripts/health_check.py` — operational checks

## Intentionally excluded
This public export does **not** include:
- proprietary signal generation logic
- arbitrage / directional strategy modules
- backtest engines and reports
- internal handoff / review notes
- live alerting heuristics and production thresholds tied to trading logic

## Goal
Show engineering quality, system structure, and operational thinking without exposing private trading edge.
