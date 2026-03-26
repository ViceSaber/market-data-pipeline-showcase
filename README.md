# Market Data Pipeline Showcase

A sanitized public portfolio project focused on **market-data ingestion, parsing, scheduling, and operational infrastructure**.

This repository is intentionally trimmed to showcase **engineering quality and system design** without exposing private decision logic, research artifacts, or live production secrets.

## Highlights

- **Structured market-data pipeline**
  - event / market ingestion
  - normalized parsing
  - grouped family construction
- **Scheduler-oriented architecture**
  - recurring jobs
  - state tracking
  - stale-run recovery
- **Operational reliability focus**
  - SQLite WAL usage
  - retry-aware DB access
  - health checks and maintenance scripts
- **Clean public surface**
  - private logic removed
  - local runtime artifacts removed
  - safe configuration templates only

## Repository Layout

```text
market-data-pipeline-showcase/
├── README.md
├── config.yaml.example
├── polymarket_scanner/
│   ├── app/
│   │   ├── clients/        # API client + rate limiter
│   │   ├── parsers/        # slug/question → structured fields
│   │   ├── repositories/   # persistence abstractions
│   │   ├── scheduler/      # APScheduler orchestration
│   │   ├── services/       # indexing / refresh / maintenance services
│   │   └── db.py           # SQLite connection helpers
│   ├── config/
│   ├── docs/
│   ├── scripts/
│   ├── sql/
│   └── tests/
└── requirements.txt
```

## Included in This Public Version

- market-data ingestion infrastructure
- API clients and rate limiting
- parser and structural grouping logic
- scheduler/orchestration scaffolding
- database helpers and schema
- selected tests and maintenance scripts

## Intentionally Excluded

- proprietary decision logic
- research and evaluation artifacts
- internal handoff / review documents
- secrets, private configs, and local runtime data

## Engineering Themes

This repo is mainly meant to demonstrate:

- modular Python project structure
- scheduler-driven system design
- SQLite-backed service coordination
- parsing / normalization of messy external identifiers
- operational thinking around retries, cleanup, and observability

## Stack

- **Python**
- **SQLite**
- **APScheduler**
- optional notification integration

## Notes

This is a **showcase export**, not a full production mirror.
Some business-specific modules, thresholds, and research components were deliberately removed before publication.
