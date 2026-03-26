PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS event_registry (
  event_id               TEXT PRIMARY KEY,
  event_slug             TEXT UNIQUE,
  title                  TEXT,
  category               TEXT,
  subcategory            TEXT,
  tags_json              TEXT,
  start_time             TEXT,
  end_time               TEXT,
  active                 INTEGER NOT NULL DEFAULT 1,
  closed                 INTEGER NOT NULL DEFAULT 0,
  archived               INTEGER NOT NULL DEFAULT 0,
  liquidity_num          REAL,
  volume_num             REAL,
  open_interest_num      REAL,
  first_seen_at          TEXT NOT NULL,
  last_seen_at           TEXT NOT NULL,
  last_full_refresh_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_registry_active_closed
ON event_registry(active, closed);

CREATE INDEX IF NOT EXISTS idx_event_registry_last_seen
ON event_registry(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_event_registry_volume
ON event_registry(volume_num DESC);

CREATE TABLE IF NOT EXISTS market_registry (
  market_id              TEXT PRIMARY KEY,
  slug                   TEXT UNIQUE NOT NULL,
  question               TEXT NOT NULL,
  description            TEXT,
  event_id               TEXT NOT NULL,
  outcome_type           TEXT,
  resolution_basis       TEXT,
  underlying_entity      TEXT,
  line_value             REAL,
  side_label             TEXT,
  group_template         TEXT,
  start_time             TEXT,
  end_time               TEXT,
  active                 INTEGER NOT NULL DEFAULT 1,
  closed                 INTEGER NOT NULL DEFAULT 0,
  archived               INTEGER NOT NULL DEFAULT 0,
  first_seen_at          TEXT NOT NULL,
  last_seen_at           TEXT NOT NULL,
  last_meta_refresh_at   TEXT,
  stale_status           TEXT NOT NULL DEFAULT 'fresh',
  parse_version          TEXT,
  date_scope             TEXT,
  FOREIGN KEY (event_id) REFERENCES event_registry(event_id)
);

CREATE INDEX IF NOT EXISTS idx_market_registry_event
ON market_registry(event_id);

CREATE INDEX IF NOT EXISTS idx_market_registry_active_closed
ON market_registry(active, closed);

CREATE INDEX IF NOT EXISTS idx_market_registry_basis
ON market_registry(event_id, resolution_basis, group_template);

CREATE INDEX IF NOT EXISTS idx_market_registry_stale
ON market_registry(stale_status, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_market_registry_end_time
ON market_registry(end_time);

CREATE INDEX IF NOT EXISTS idx_market_registry_underlying
ON market_registry(underlying_entity);

CREATE TABLE IF NOT EXISTS market_snapshot (
  snapshot_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id              TEXT NOT NULL,
  slug                   TEXT NOT NULL,
  fetched_at             TEXT NOT NULL,
  source                 TEXT NOT NULL,
  best_yes_bid           REAL,
  best_yes_ask           REAL,
  best_no_bid            REAL,
  best_no_ask            REAL,
  last_price_yes         REAL,
  last_price_no          REAL,
  midpoint_yes           REAL,
  midpoint_no            REAL,
  volume_num             REAL,
  volume_24h_num         REAL,
  liquidity_num          REAL,
  open_interest_num      REAL,
  active                 INTEGER,
  closed                 INTEGER,
  FOREIGN KEY (market_id) REFERENCES market_registry(market_id)
);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_market_time
ON market_snapshot(market_id, fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_slug_time
ON market_snapshot(slug, fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_source_time
ON market_snapshot(source, fetched_at DESC);

DROP VIEW IF EXISTS market_snapshot_latest;

CREATE VIEW market_snapshot_latest AS
SELECT s.*
FROM market_snapshot s
JOIN (
  SELECT market_id, MAX(fetched_at) AS max_fetched_at
  FROM market_snapshot
  GROUP BY market_id
) t
ON s.market_id = t.market_id
AND s.fetched_at = t.max_fetched_at;

CREATE TABLE IF NOT EXISTS market_family (
  family_key             TEXT PRIMARY KEY,
  event_id               TEXT NOT NULL,
  family_type            TEXT NOT NULL,
  resolution_basis       TEXT NOT NULL,
  group_template         TEXT,
  underlying_entity      TEXT,
  date_scope             TEXT,
  member_count           INTEGER NOT NULL,
  completeness_score     REAL NOT NULL DEFAULT 0,
  quality_score          REAL NOT NULL DEFAULT 0,
  last_rebuilt_at        TEXT NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'active',
  FOREIGN KEY (event_id) REFERENCES event_registry(event_id)
);

CREATE INDEX IF NOT EXISTS idx_market_family_event
ON market_family(event_id);

CREATE INDEX IF NOT EXISTS idx_market_family_type_quality
ON market_family(family_type, quality_score DESC);

CREATE INDEX IF NOT EXISTS idx_market_family_status
ON market_family(status);

CREATE TABLE IF NOT EXISTS market_family_member (
  family_key             TEXT NOT NULL,
  market_id              TEXT NOT NULL,
  slug                   TEXT NOT NULL,
  role_in_family         TEXT,
  ordinal_in_chain       INTEGER,
  PRIMARY KEY (family_key, market_id),
  FOREIGN KEY (family_key) REFERENCES market_family(family_key),
  FOREIGN KEY (market_id) REFERENCES market_registry(market_id)
);

CREATE INDEX IF NOT EXISTS idx_market_family_member_slug
ON market_family_member(slug);

CREATE TABLE IF NOT EXISTS stale_check_queue (
  slug                   TEXT PRIMARY KEY,
  market_id              TEXT NOT NULL,
  reason                 TEXT NOT NULL,
  priority               INTEGER NOT NULL DEFAULT 100,
  enqueued_at            TEXT NOT NULL,
  next_attempt_at        TEXT NOT NULL,
  attempt_count          INTEGER NOT NULL DEFAULT 0,
  status                 TEXT NOT NULL DEFAULT 'pending',
  last_error             TEXT,
  FOREIGN KEY (market_id) REFERENCES market_registry(market_id)
);

CREATE INDEX IF NOT EXISTS idx_stale_check_queue_sched
ON stale_check_queue(status, next_attempt_at, priority DESC);

CREATE TABLE IF NOT EXISTS scanner_candidate_queue (
  candidate_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  family_key             TEXT NOT NULL,
  candidate_type         TEXT NOT NULL,
  edge_estimate          REAL,
  enqueued_at            TEXT NOT NULL,
  priority               INTEGER NOT NULL DEFAULT 100,
  status                 TEXT NOT NULL DEFAULT 'pending',
  FOREIGN KEY (family_key) REFERENCES market_family(family_key)
);

CREATE INDEX IF NOT EXISTS idx_scanner_candidate_queue_sched
ON scanner_candidate_queue(status, priority DESC, enqueued_at);

CREATE TABLE IF NOT EXISTS scan_result (
  result_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  family_key             TEXT NOT NULL,
  result_type            TEXT NOT NULL,
  edge_pct               REAL,
  expected_profit        REAL,
  legs_json              TEXT NOT NULL,
  validated_realtime     INTEGER NOT NULL DEFAULT 0,
  reject_reason          TEXT,
  created_at             TEXT NOT NULL,
  FOREIGN KEY (family_key) REFERENCES market_family(family_key)
);

CREATE INDEX IF NOT EXISTS idx_scan_result_created
ON scan_result(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_scan_result_family
ON scan_result(family_key, created_at DESC);

CREATE TABLE IF NOT EXISTS alert_log (
  alert_id               INTEGER PRIMARY KEY AUTOINCREMENT,
  result_id              INTEGER,
  slug                   TEXT,
  sent_at                TEXT NOT NULL,
  channel                TEXT,
  status                 TEXT NOT NULL,
  error_message          TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_log_result
ON alert_log(result_id);

CREATE INDEX IF NOT EXISTS idx_alert_log_sent
ON alert_log(sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_log_slug
ON alert_log(slug, sent_at DESC);

CREATE TABLE IF NOT EXISTS scheduler_state (
  job_name               TEXT PRIMARY KEY,
  last_run_at            TEXT,
  last_success_at        TEXT,
  last_cursor            TEXT,
  notes                  TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_market (
  slug                   TEXT PRIMARY KEY,
  reason                 TEXT,
  tier                   TEXT NOT NULL DEFAULT 'warm',
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_watchlist_market_tier
ON watchlist_market(tier);
