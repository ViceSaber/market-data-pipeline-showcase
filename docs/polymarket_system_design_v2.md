# SQLite DDL 完整脚本 + Python 调度器目录结构

下面这版是按你前一版系统设计直接落地的。

## 1. SQLite DDL 完整脚本

```sql
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
  result_id              INTEGER NOT NULL,
  sent_at                TEXT NOT NULL,
  channel                TEXT,
  status                 TEXT NOT NULL,
  error_message          TEXT,
  FOREIGN KEY (result_id) REFERENCES scan_result(result_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_log_result
ON alert_log(result_id);

CREATE INDEX IF NOT EXISTS idx_alert_log_sent
ON alert_log(sent_at DESC);

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
```

## 2. 推荐的 SQLite 初始化脚本文件名

建议落成：

```text
sql/001_init_schema.sql
```

如果后面要演进，按这种迁移编号继续：

```text
sql/002_add_parse_version.sql
sql/003_add_watchlist_market.sql
sql/004_add_scheduler_state.sql
```

## 3. Python 调度器目录结构

```text
polymarket_scanner/
├─ README.md
├─ requirements.txt
├─ .env.example
├─ pyproject.toml
├─ sql/
│  ├─ 001_init_schema.sql
│  ├─ 002_views.sql
│  └─ 003_seed.sql
├─ data/
│  └─ polymarket.db
├─ logs/
│  └─ app.log
├─ config/
│  ├─ settings.py
│  └─ logging.yaml
├─ scripts/
│  ├─ init_db.py
│  ├─ run_event_indexer.py
│  ├─ run_hot_refresher.py
│  ├─ run_warm_refresher.py
│  ├─ run_cold_refresher.py
│  ├─ run_stale_rechecker.py
│  ├─ run_family_builder.py
│  ├─ run_scanner.py
│  ├─ run_candidate_confirmer.py
│  └─ run_scheduler.py
├─ app/
│  ├─ __init__.py
│  ├─ constants.py
│  ├─ db.py
│  ├─ models.py
│  ├─ utils/
│  │  ├─ __init__.py
│  │  ├─ timeutil.py
│  │  ├─ slugutil.py
│  │  ├─ jsonutil.py
│  │  └─ retry.py
│  ├─ clients/
│  │  ├─ __init__.py
│  │  ├─ gamma_client.py
│  │  └─ rate_limiter.py
│  ├─ repositories/
│  │  ├─ __init__.py
│  │  ├─ event_repo.py
│  │  ├─ market_repo.py
│  │  ├─ snapshot_repo.py
│  │  ├─ family_repo.py
│  │  ├─ queue_repo.py
│  │  └─ result_repo.py
│  ├─ parsers/
│  │  ├─ __init__.py
│  │  ├─ market_parser.py
│  │  ├─ question_classifier.py
│  │  └─ family_key_builder.py
│  ├─ services/
│  │  ├─ __init__.py
│  │  ├─ event_indexer.py
│  │  ├─ refresh_selector.py
│  │  ├─ hot_refresher.py
│  │  ├─ warm_refresher.py
│  │  ├─ cold_refresher.py
│  │  ├─ stale_rechecker.py
│  │  ├─ family_builder.py
│  │  ├─ scanner.py
│  │  ├─ candidate_confirmer.py
│  │  └─ alert_service.py
│  └─ scheduler/
│     ├─ __init__.py
│     ├─ jobs.py
│     └─ main.py
└─ tests/
   ├─ test_market_parser.py
   ├─ test_family_builder.py
   ├─ test_scanner_mutual_exclusion.py
   ├─ test_scanner_inclusion.py
   └─ test_stale_rechecker.py
```

## 4. 每个目录干什么

### `sql/`
放 DDL 和迁移脚本。

### `scripts/`
放可直接执行的入口脚本。你可以单独跑某个 worker。

### `app/clients/`
只负责访问外部 API，例如：
- `GET /events`
- `GET /markets/slug/{slug}`

### `app/repositories/`
只负责 SQL 读写，不写业务逻辑。

### `app/parsers/`
只负责把 question / slug / outcome 解析成结构字段：
- `resolution_basis`
- `group_template`
- `underlying_entity`
- `line_value`
- `side_label`

### `app/services/`
业务层：
- event indexer
- stale recheck
- family build
- scanner
- candidate confirm

### `app/scheduler/`
调度器层，把多个 services 编排起来。

## 5. 推荐的 worker 拆分

### `event_indexer.py`
职责：
- 调 `events?active=true&closed=false`
- 分页
- upsert `event_registry`
- upsert `market_registry`
- 写 `last_seen_at`

### `hot_refresher.py`
职责：
- 选 hot 市场
- 批量按 slug 刷最新价格
- 写 `market_snapshot`

### `warm_refresher.py`
职责：
- 刷 watchlist 和高质量 family 成员

### `cold_refresher.py`
职责：
- 低频补长尾

### `stale_rechecker.py`
职责：
- 找 `unseen / stale_pending`
- 按 slug 回查状态
- 更新 `market_registry.active/closed/stale_status`

### `family_builder.py`
职责：
- 从 `market_registry` 生成 `market_family`
- 写 `market_family_member`

### `scanner.py`
职责：
- 扫 `market_family`
- 找：
  - 互斥套利
  - 包含套利
  - 递进/阈值链
- 写 `scanner_candidate_queue`

### `candidate_confirmer.py`
职责：
- 对候选组实时回查
- 重新确认价格、closed、active
- 通过才写 `scan_result`

## 6. 推荐的调度频率

```text
event_indexer         每 10 分钟
hot_refresher         每 30 秒
warm_refresher        每 5 分钟
cold_refresher        每 1 小时
stale_rechecker       每 15 分钟
family_builder        每 10 分钟（在 event_indexer 之后）
scanner               每 30 秒
candidate_confirmer   每 5 秒
```

## 7. 一个最小可用的 `run_scheduler.py`

```python
from apscheduler.schedulers.blocking import BlockingScheduler

from app.services.event_indexer import run_event_indexer
from app.services.hot_refresher import run_hot_refresher
from app.services.warm_refresher import run_warm_refresher
from app.services.cold_refresher import run_cold_refresher
from app.services.stale_rechecker import run_stale_rechecker
from app.services.family_builder import run_family_builder
from app.services.scanner import run_scanner
from app.services.candidate_confirmer import run_candidate_confirmer


def main():
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")

    scheduler.add_job(run_event_indexer, "interval", minutes=10, id="event_indexer", coalesce=True, max_instances=1)
    scheduler.add_job(run_hot_refresher, "interval", seconds=30, id="hot_refresher", coalesce=True, max_instances=1)
    scheduler.add_job(run_warm_refresher, "interval", minutes=5, id="warm_refresher", coalesce=True, max_instances=1)
    scheduler.add_job(run_cold_refresher, "interval", hours=1, id="cold_refresher", coalesce=True, max_instances=1)
    scheduler.add_job(run_stale_rechecker, "interval", minutes=15, id="stale_rechecker", coalesce=True, max_instances=1)
    scheduler.add_job(run_family_builder, "interval", minutes=10, id="family_builder", coalesce=True, max_instances=1)
    scheduler.add_job(run_scanner, "interval", seconds=30, id="scanner", coalesce=True, max_instances=1)
    scheduler.add_job(run_candidate_confirmer, "interval", seconds=5, id="candidate_confirmer", coalesce=True, max_instances=1)

    scheduler.start()


if __name__ == "__main__":
    main()
```

## 8. 推荐的 `db.py`

```python
import sqlite3
from pathlib import Path

DB_PATH = Path("data/polymarket.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn
```

## 9. 初始化数据库脚本 `scripts/init_db.py`

```python
from pathlib import Path
from app.db import get_conn

SQL_DIR = Path("sql")


def main():
    conn = get_conn()
    try:
        for path in sorted(SQL_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            print(f"applied: {path}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

## 10. 你当前最该先落地的最小目录

如果不想一上来搞太大，先建这几个就够：

```text
polymarket_scanner/
├─ sql/001_init_schema.sql
├─ data/polymarket.db
├─ scripts/init_db.py
├─ scripts/run_scheduler.py
├─ app/db.py
├─ app/clients/gamma_client.py
├─ app/repositories/market_repo.py
├─ app/services/event_indexer.py
├─ app/services/stale_rechecker.py
├─ app/services/family_builder.py
├─ app/services/scanner.py
└─ app/services/candidate_confirmer.py
```

## 11. 落地顺序

### 第一阶段
先跑通：
- `init_db.py`
- `event_indexer.py`
- `stale_rechecker.py`

### 第二阶段
加：
- `family_builder.py`
- `scanner.py`

### 第三阶段
再加：
- `hot/warm/cold_refresher.py`
- `candidate_confirmer.py`

## 12. 一句话版本

这套目录结构的核心就是：

- **clients** 负责打 API
- **repositories** 负责写库
- **parsers** 负责结构化 question
- **services** 负责业务
- **scheduler** 负责调度

别把它们混在一个脚本里，不然后面你一改 family 逻辑，整个系统就会很乱。

---

## 13. Review 补充：需要修正的问题

### 13.1 全局 Rate Limiter（P0）

所有 job 共享一个 rate limiter，避免 hot/warm/cold refresher 叠加突破 API 限额。

```python
# app/clients/rate_limiter.py

import time
import threading
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Polymarket Gamma API 全局节流器。

    官方限额：
    - /events: 500 req/10s
    - /markets: 300 req/10s
    - listing 合并: 900 req/10s

    实际保守设置：
    - 全局: 200 req/10s（留余量给 Cloudflare throttle）
    """

    max_per_window: int = 200
    window_seconds: float = 10.0
    _timestamps: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self):
        """阻塞直到可以发请求。"""
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [
                    t for t in self._timestamps
                    if now - t < self.window_seconds
                ]
                if len(self._timestamps) < self.max_per_window:
                    self._timestamps.append(now)
                    return
            time.sleep(0.1)


# 全局单例
_limiter = RateLimiter()


def get_limiter() -> RateLimiter:
    return _limiter
```

在 `gamma_client.py` 中统一调用：

```python
from app.clients.rate_limiter import get_limiter

def fetch_events(active=True, closed=False, limit=100, offset=0):
    get_limiter().acquire()
    resp = requests.get(API_URL + "/events", params={...})
    resp.raise_for_status()
    return resp.json()

def fetch_by_slug_batch(slugs: list[str]):
    get_limiter().acquire()
    resp = requests.get(API_URL + "/markets", params={"slug": ",".join(slugs)})
    resp.raise_for_status()
    return resp.json()
```

### 13.2 market_snapshot 清理策略（P0）

热层 30s 刷一次，220 个市场 × 每天 2880 次 = 63 万行/天。必须清理。

**方案 A：定时清理旧快照**

在 scheduler 中加一个 daily cleanup job：

```python
# app/services/snapshot_cleaner.py

from app.db import get_conn


def run_snapshot_cleaner():
    """保留最近 7 天的快照，清理更早的。"""
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM market_snapshot WHERE fetched_at < datetime('now', '-7 days')"
        )
        conn.commit()
    finally:
        conn.close()
```

调度：

```python
scheduler.add_job(
    run_snapshot_cleaner,
    "cron",
    hour=4,
    minute=0,
    id="snapshot_cleaner",
    coalesce=True,
    max_instances=1,
)
```

**方案 B（推荐）：不保留历史，只保留最新**

如果不需要价格时间序列分析，直接把 `market_snapshot` 改成 UPSERT 模式：

```sql
-- 替换原来的 market_snapshot
CREATE TABLE IF NOT EXISTS market_snapshot (
    market_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL,
    best_yes_bid REAL,
    best_yes_ask REAL,
    best_no_bid REAL,
    best_no_ask REAL,
    last_price_yes REAL,
    last_price_no REAL,
    midpoint_yes REAL,
    midpoint_no REAL,
    volume_num REAL,
    volume_24h_num REAL,
    liquidity_num REAL,
    open_interest_num REAL,
    active INTEGER,
    closed INTEGER,
    FOREIGN KEY (market_id) REFERENCES market_registry(market_id)
);
```

写入时用 `INSERT ... ON CONFLICT DO UPDATE`，表永远只有一行 per market。零膨胀。

如果以后需要时间序列，单独开一张 `market_snapshot_history` 表，由 snapshot_cleaner 从 snapshot 复制过去。

### 13.3 stale_status 状态机完善（P0）

原设计缺少回路。完整状态机：

```text
market_registry.stale_status:

    fresh
     └─(本轮 active scan 未见到)→ unseen
     └─(再次见到)→ fresh（回路）

    unseen
     └─(超过 24h 未见到)→ stale_pending
     └─(再次见到)→ fresh（回路）

    stale_pending
     └─(slug 回查确认 active=1)→ fresh（回路）
     └─(slug 回查确认 closed=1)→ closed_confirmed

    closed_confirmed（终态）
```

实现：

```python
# app/services/stale_rechecker.py

HOURS_UNSEEN_TO_STALE = 24


def run_stale_rechecker():
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # 1. fresh → unseen：本轮 discovery 没看到的
        conn.execute("""
            UPDATE market_registry
            SET stale_status = 'unseen'
            WHERE stale_status = 'fresh'
              AND last_seen_at < datetime(?, '-' || ? || ' hours')
        """, (now, 1))  # 超过 1 小时未见

        # 2. unseen → stale_pending：超过 24h 未见，或 end_time 已过
        conn.execute("""
            UPDATE market_registry
            SET stale_status = 'stale_pending'
            WHERE stale_status = 'unseen'
              AND (
                  last_seen_at < datetime(?, '-24 hours')
                  OR (end_time IS NOT NULL AND end_time < ?)
              )
        """, (now, now))

        # 3. stale_pending → slug 回查
        pending = conn.execute("""
            SELECT slug, market_id FROM market_registry
            WHERE stale_status = 'stale_pending'
            ORDER BY last_seen_at ASC
            LIMIT 50
        """).fetchall()

        for batch in chunks(pending, 50):
            slugs = [r["slug"] for r in batch]
            fresh_data = fetch_by_slug_batch(slugs)
            for m in fresh_data:
                if m.get("closed"):
                    conn.execute("""
                        UPDATE market_registry
                        SET stale_status = 'closed_confirmed', active = 0, closed = 1
                        WHERE slug = ?
                    """, (m["slug"],))
                elif m.get("active"):
                    conn.execute("""
                        UPDATE market_registry
                        SET stale_status = 'fresh', last_seen_at = ?
                        WHERE slug = ?
                    """, (now, m["slug"]))

        conn.commit()
    finally:
        conn.close()
```

### 13.4 candidate_confirmer 频率调整（P1）

5 秒太激进，改成 30 秒：

```python
scheduler.add_job(
    run_candidate_confirmer,
    "interval",
    seconds=30,  # 原来是 5 秒
    id="candidate_confirmer",
    coalesce=True,
    max_instances=1,
)
```

### 13.5 Scheduler 时区修正（P1）

```python
scheduler = BlockingScheduler(timezone="Asia/Shanghai")  # 原来是 Asia/Tokyo
```

### 13.6 热层阈值可配置（P2）

```python
# config/settings.py

# ── Refresh tier thresholds ──────────────────────────────────
HOT_MIN_VOLUME_24H = 50_000
HOT_MIN_LIQUIDITY = 10_000

WARM_MIN_VOLUME_24H = 5_000
WARM_MIN_LIQUIDITY = 1_000

# 温层额外条件：指定 tag 内的市场
WARM_TAGS = {"crypto", "politics", "sports"}

# 冷层：所有其他活跃市场
```

在 `refresh_selector.py` 中使用：

```python
def classify_tier(market: dict) -> str:
    vol = market.get("volume_24h_num", 0) or 0
    liq = market.get("liquidity_num", 0) or 0

    if vol >= HOT_MIN_VOLUME_24H and liq >= HOT_MIN_LIQUIDITY:
        return "hot"
    elif vol >= WARM_MIN_VOLUME_24H and liq >= WARM_MIN_LIQUIDITY:
        return "warm"
    else:
        return "cold"
```

### 13.7 Health Check（P2）

```python
# scripts/health_check.py

from app.db import get_conn
from datetime import datetime, timezone


def main():
    conn = get_conn()
    now = datetime.now(timezone.utc)
    jobs = conn.execute("SELECT * FROM scheduler_state").fetchall()

    print(f"Health Check @ {now.isoformat()}")
    print("=" * 60)

    for job in jobs:
        last_run = job["last_run_at"]
        last_ok = job["last_success_at"]
        if last_run:
            delta = (now - datetime.fromisoformat(last_run)).total_seconds()
            status = "✅" if delta < 300 else "⚠️ STALE" if delta < 900 else "🔴 DEAD"
            print(f"  {status} {job['job_name']:25s} last_run: {last_run} ({delta:.0f}s ago)")
        else:
            print(f"  ⚪ {job['job_name']:25s} never run")

    conn.close()


if __name__ == "__main__":
    main()
```

### 13.8 修正后的调度频率汇总

| Job | 频率 | 说明 |
|-----|------|------|
| event_indexer | 10 min | 全量 events 分页 |
| hot_refresher | 30s | 高流动性市场价格 |
| warm_refresher | 5 min | 候选白名单价格 |
| cold_refresher | 1 hour | 长尾市场价格 |
| stale_rechecker | 15 min | 过期/关闭确认 |
| family_builder | 10 min | 在 event_indexer 之后 |
| scanner | 30s | 扫描套利机会 |
| candidate_confirmer | **30s**（修正） | 候选实时确认 |
| snapshot_cleaner | 每天 4:00 | 清理旧快照 |

### 13.9 修正后的最小可用目录

```text
polymarket_scanner/
├─ sql/001_init_schema.sql
├─ data/polymarket.db
├─ config/settings.py              # 阈值配置
├─ scripts/init_db.py
├─ scripts/health_check.py         # 新增
├─ scripts/run_scheduler.py
├─ app/db.py
├─ app/clients/gamma_client.py
├─ app/clients/rate_limiter.py     # 新增：全局节流
├─ app/repositories/market_repo.py
├─ app/parsers/
│  └─ market_parser.py             # slug → 结构字段（最难的部分）
├─ app/services/
│  ├─ event_indexer.py
│  ├─ stale_rechecker.py           # 含完整状态机
│  ├─ family_builder.py
│  ├─ scanner.py
│  ├─ candidate_confirmer.py
│  ├─ snapshot_cleaner.py          # 新增
│  └─ refresh_selector.py          # 新增：热/温/冷分层
└─ tests/
   ├─ test_market_parser.py        # 最先写
   ├─ test_family_builder.py
   ├─ test_scanner_mutual_exclusion.py
   └─ test_scanner_inclusion.py
```

---

## 14. 落地优先级（修正版）

### Phase 1：能跑
- `init_db.py` → 建表
- `gamma_client.py` + `rate_limiter.py` → API + 节流
- `event_indexer.py` → 全量 events 索引
- `stale_rechecker.py` → 过期检测（含完整状态机）
- `settings.py` → 阈值配置
- `health_check.py` → 运维可见性

**验收：1 小时内覆盖 1000+ 市场，过期市场自动标记。**

### Phase 2：能找
- `market_parser.py` + `test_market_parser.py` → slug 解析（先写测试）
- `family_builder.py` + `test_family_builder.py` → 分组
- `scanner.py` → 扫描套利
- `candidate_confirmer.py` → 候选确认

**验收：8 个已知假信号 test case 全部正确分类，无假信号。**

### Phase 3：能交易
- `hot/warm/cold_refresher.py` → 分层价格刷新
- `refresh_selector.py` → 分层选择器
- `snapshot_cleaner.py` → 快照清理
- `alert_service.py` → 通知

**验收：BTC 市场价格 < 1 分钟新鲜度，套利信号可下单。**
