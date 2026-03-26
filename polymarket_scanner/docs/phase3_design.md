# Phase 3 设计文档 — 价格刷新 + Alert + Scheduler

> 状态：**DRAFT** — 等 Sakura 审核
> 日期：2026-03-21
> 作者：光酱 Hikari

---

## 1. 概述

Phase 3 将已有的单次扫描能力变成 **持续运行的自动化系统**，三个核心组件：

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Scheduler   │────▶│ Price        │────▶│ Alert       │
│  (调度中心)   │     │ Refresher    │     │ Engine      │
│              │◀────│ (分层刷新)    │     │ (异动通知)   │
└──────┬──────┘     └──────┬───────┘     └──────┬──────┘
       │                   │                     │
       ▼                   ▼                     ▼
  scheduler_state     market_snapshot         alert_log
                                         → Telegram 通知
```

**依赖关系**：
- Price Refresher → Gamma Client（已有）+ Rate Limiter（已有）
- Alert Engine → market_snapshot（Price Refresher 产出）+ scan_result（Scanner 产出）
- Scheduler → 所有 Phase 1-4 服务

---

## 2. Price Refresher（分层价格刷新）

### 2.1 设计目标

不同市场用不同频率刷新，避免浪费 API quota 同时保证高活跃市场数据新鲜。

### 2.2 分层逻辑

| 层级 | 刷新频率 | 判定条件 | 预估数量 |
|------|---------|---------|---------|
| **Hot** | 5 分钟 | `volume_24h >= 50,000` OR `liquidity >= 10,000` | ~100-200 |
| **Warm** | 15 分钟 | `tag IN ('crypto','politics','sports')` OR `volume_24h >= 5,000` | ~300-500 |
| **Cold** | 60 分钟 | 其他所有活跃 market | ~2,000+ |

**判定来源**：从 `market_snapshot_latest` 视图取最近一次快照的 `volume_24h_num` 和 `liquidity_num`。

**冷启动**：系统首次运行时，所有 market 默认 Cold。第一次刷新后根据实际数据重新分层。

**升级/降级**：每次分层刷新时重新评估。连续 3 次 Hot 判定才升级为 Hot，连续 3 次非 Hot 判定降级。

### 2.3 实现方案

**文件**: `app/services/price_refresher.py`（~200 行）

```python
class PriceRefresher:
    TIER_HOT = "hot"
    TIER_WARM = "warm"
    TIER_COLD = "cold"

    # 从 settings.py 读取阈值（已有）
    HOT_MIN_VOLUME = settings.HOT_MIN_VOLUME_24H      # 50_000
    HOT_MIN_LIQUIDITY = settings.HOT_MIN_LIQUIDITY     # 10_000
    WARM_MIN_VOLUME = settings.WARM_MIN_VOLUME_24H     # 5_000
    WARM_MIN_LIQUIDITY = settings.WARM_MIN_LIQUIDITY   # 1_000
    WARM_TAGS = settings.WARM_TAGS                      # {"crypto","politics","sports"}

    def classify_tier(self, market: dict) -> str:
        """根据 volume/liquidity/tags 判定层级"""

    def refresh_tier(self, tier: str) -> int:
        """刷新指定层级的所有 market，返回成功数"""

    def refresh_all(self, tier_filter: str = None) -> dict:
        """入口方法，按层级刷新，返回统计"""
```

**批量拉取策略**：

```
每个 tier 内，按 slug 批量调用 fetch_by_slug_batch()
每批最多 100 个 slug（Gamma API 限制）
批次间 sleep 100ms（让 rate limiter 有机会释放）
每批次写入 market_snapshot 用 executemany()（单事务）
```

**写入 market_snapshot**：

```python
conn.executemany(
    """INSERT INTO market_snapshot
       (market_id, slug, fetched_at, source,
        best_yes_bid, best_yes_ask, best_no_bid, best_no_ask,
        last_price_yes, last_price_no, midpoint_yes, midpoint_no,
        volume_num, volume_24h_num, liquidity_num, open_interest_num,
        active, closed)
       VALUES (?, ?, ?, 'gamma', ...)""",
    batch_data
)
```

**数据清理**（附带功能）：
- 每次 refresh_all() 结束后，清理 >72h 的旧快照（保留最近 72h 做分析）
- 用单条 DELETE：`DELETE FROM market_snapshot WHERE fetched_at < datetime('now', '-72 hours')`

### 2.4 数据流

```
Gamma API
    │
    ▼
fetch_by_slug_batch(slugs) → raw market dicts
    │
    ▼
normalize → (market_id, slug, prices, volumes, ...)
    │
    ▼
INSERT INTO market_snapshot
    │
    ▼
market_snapshot_latest VIEW (自动更新)
    │
    ▼
Alert Engine / Scanner 读取
```

### 2.5 配置（新增到 settings.py）

```python
# ── Refresh tiers ──
REFRESH_INTERVALS = {
    "hot":  300,    # 5 分钟
    "warm": 900,    # 15 分钟
    "cold": 3600,   # 60 分钟
}
SNAPSHOT_RETENTION_HOURS = 72
BATCH_SIZE = 100  # 每批最大 slug 数
TIER_PROMOTION_THRESHOLD = 3  # 连续 N 次才升级
TIER_DEMOTION_THRESHOLD = 3   # 连续 N 次才降级
```

---

## 3. Alert Engine（价格异动通知）

### 3.1 设计目标

检测价格异常变动，通过 Telegram 推送通知，不漏报也不刷屏。

### 3.2 触发条件

| Alert 类型 | 条件 | 优先级 |
|-----------|------|--------|
| **spike** | 价格 5 分钟内变动 >15% | 🔴 HIGH |
| **trend** | 价格 1 小时内累计变动 >25% | 🟡 MEDIUM |
| **volume_surge** | 24h 成交量突增 >300% | 🟡 MEDIUM |
| **spread_widen** | bid-ask spread >20% | 🟢 LOW |
| **arbitrage** | Scanner 发现套利机会 | 🔴 HIGH |

### 3.3 实现方案

**文件**: `app/services/alert_engine.py`（~180 行）

```python
class AlertEngine:
    def __init__(self, db_conn):
        self.conn = db_conn

    def check_all(self) -> list[dict]:
        """检查所有活跃 market，返回新 alerts 列表"""

    def check_spike(self, slug: str) -> dict | None:
        """对比最新快照 vs 5 分钟前快照"""

    def check_trend(self, slug: str) -> dict | None:
        """对比最新快照 vs 1 小时前快照"""

    def check_volume_surge(self, slug: str) -> dict | None:
        """对比最新 24h volume vs 24h 前 volume"""

    def check_spread(self, snapshot: dict) -> dict | None:
        """计算 bid-ask spread"""

    def send_alert(self, alert: dict) -> bool:
        """发送到 Telegram + 写入 alert_log"""

    def is_suppressed(self, slug: str, alert_type: str) -> bool:
        """去重：同 slug+type 30 分钟内不重复发"""
```

**去重逻辑**（防刷屏）：
```sql
-- 检查是否在 suppression window 内已有同类 alert
SELECT COUNT(*) FROM alert_log
JOIN scan_result ON alert_log.result_id = scan_result.result_id
WHERE scan_result.family_key = ?
  AND alert_log.sent_at > datetime('now', '-30 minutes')
  AND alert_log.status = 'sent'
```

### 3.4 Telegram 通知格式

```
🚨 SPIKE ALERT — Will Bitcoin reach $105K?
价格变动: $0.35 → $0.52 (+48.6%) in 5min
当前 spread: bid $0.50 / ask $0.54
Volume 24h: $127,340
→ 检查: https://polymarket.com/event/...
```

**去重规则**：
- 同 market + 同 alert_type：30 分钟内不重复
- 同 market + 不同 alert_type：可以发（spike 和 volume_surge 可以同时报）
- 每日上限：同一市场最多 8 条 alert
- 全局每日上限：100 条

### 3.5 Scanner 集成

Scanner 发现套利机会时，**不再只写 scan_result**，也触发 Alert：

```python
# 在 scanner.py 的 scan_family() 末尾
if result.edge_pct >= MIN_EDGE_PCT:
    alert_engine.send_alert({
        "type": "arbitrage",
        "slug": family_key,
        "message": f"套利机会: {result.edge_pct:.1f}% edge",
        "priority": "high"
    })
```

### 3.6 配置

```python
# ── Alert thresholds ──
ALERT_SPIKE_PCT = 15.0          # 5 分钟变动阈值
ALERT_TREND_PCT = 25.0          # 1 小时累计阈值
ALERT_VOLUME_SURGE_MULT = 3.0   # 24h 成交量倍数
ALERT_SPREAD_PCT = 20.0         # bid-ask spread 阈值
ALERT_SUPPRESS_MINUTES = 30     # 去重窗口
ALERT_DAILY_MAX_PER_MARKET = 8  # 单市场每日上限
ALERT_DAILY_MAX_TOTAL = 100     # 全局每日上限
```

---

## 4. Scheduler（调度中心）

### 4.1 设计目标

统一管理所有定时任务，单进程运行，状态持久化到 `scheduler_state` 表。

### 4.2 任务列表

| Job | 频率 | 依赖 | Phase |
|-----|------|------|-------|
| `event_indexer` | 每 6 小时 | Gamma API | 1 ✅ |
| `stale_rechecker` | 每 24 小时 | Gamma API | 1 ✅ |
| `price_refresh_hot` | 每 5 分钟 | PriceRefresher | 3 🆕 |
| `price_refresh_warm` | 每 15 分钟 | PriceRefresher | 3 🆕 |
| `price_refresh_cold` | 每 60 分钟 | PriceRefresher | 3 🆕 |
| `alert_check` | 每 5 分钟 | AlertEngine | 3 🆕 |
| `family_builder` | 每 2 小时 | Parser + DB | 2 ✅ |
| `scanner` | 每 15 分钟 | Family + Snapshot | 2 ✅ |
| `candidate_confirmer` | 每 5 分钟 | Gamma API | 2 ✅ |
| `snapshot_cleaner` | 每 24 小时 | DB | 3 🆕 |

### 4.3 实现方案

**文件**: `app/scheduler/scheduler.py`（~250 行）

```python
class Scheduler:
    def __init__(self, db_conn):
        self.conn = db_conn
        self.jobs: dict[str, Job] = {}

    def register(self, name: str, fn: callable, interval_sec: int,
                 depends_on: list[str] = None):
        """注册一个定时 job"""

    def should_run(self, job_name: str) -> bool:
        """检查 scheduler_state 判断是否该跑"""

    def run_once(self, job_name: str) -> bool:
        """执行一次 job，更新 scheduler_state"""

    def run_loop(self):
        """主循环：每 30 秒检查一次所有 job，到期就跑"""
```

**Job 状态机**：

```
pending → running → success/failed
                      ↓
                    retry (3 次，间隔翻倍)
                      ↓
                    failed_final → 标记到 notes + Telegram 告警
```

**scheduler_state 更新**：

```python
# 任务开始
conn.execute("""
    INSERT INTO scheduler_state (job_name, last_run_at, notes)
    VALUES (?, datetime('now'), 'running')
    ON CONFLICT(job_name) DO UPDATE SET
        last_run_at = datetime('now'), notes = 'running'
""", (job_name,))

# 任务成功
conn.execute("""
    UPDATE scheduler_state
    SET last_success_at = datetime('now'), notes = 'ok'
    WHERE job_name = ?
""", (job_name,))
```

**并发控制**：
- 单进程单线程，job 串行执行
- 同一 job 不会并发跑（检查 `notes = 'running'`）
- Hot refresh（5 分钟）和 Warm refresh（15 分钟）可能在同一 tick 触发：
  - **解决**：只跑 Hot（Hot 是 Warm 的子集，Hot refresh 已经覆盖了 Warm 的市场）
  - Warm tick 只刷新 Warm-but-not-Hot 的市场

### 4.4 运行模式

**模式 A：独立进程（推荐，launchd 管理）**

```python
# scripts/run_scheduler.py
if __name__ == "__main__":
    scheduler = Scheduler(get_conn())
    register_all_jobs(scheduler)
    scheduler.run_loop()  # 阻塞循环
```

```xml
<!-- ~/Library/LaunchAgents/com.polymarket.scheduler.plist -->
<key>ProgramArguments</key>
<array>
    <string>/opt/homebrew/bin/python3</string>
    <string>scripts/run_scheduler.py</string>
</array>
<key>RunAtLoad</key>
<true/>
<key>KeepAlive</key>
<true/>
```

**模式 B：OpenClaw Cron 触发（备选，每分钟 cron 调一次 tick）**
- 优点：不用管进程生命周期
- 缺点：每分钟 spawn 一次 Python，冷启动开销，不适合 5 分钟级任务
- **不推荐**

### 4.5 健康检查

扩展现有的 `scripts/health_check.py`：

```python
def check_scheduler_health():
    """检查所有 job 的 last_run_at 是否过期"""
    for job in conn.execute("SELECT * FROM scheduler_state").fetchall():
        expected_interval = JOB_INTERVALS[job["job_name"]]
        last_run = parse(job["last_run_at"])
        if (now - last_run).seconds > expected_interval * 2:
            print(f"⚠️ {job['job_name']} overdue! Last run: {last_run}")
```

---

## 5. 数据库变更

**无 schema 变更！** 所有需要的表已在 Phase 1 建好：

| 表 | Phase 3 用途 | 状态 |
|----|-------------|------|
| `market_snapshot` | Price Refresher 写入 | ✅ 已有 |
| `market_snapshot_latest` | Alert Engine 读取 | ✅ 已有 VIEW |
| `alert_log` | Alert Engine 写入 | ✅ 已有 |
| `scheduler_state` | Scheduler 读写 | ✅ 已有 |
| `watchlist_market` | 手动追踪 market | ✅ 已有 |

---

## 6. 文件清单

```
polymarket_scanner/
├── app/
│   ├── services/
│   │   ├── price_refresher.py    🆕 ~200 行
│   │   └── alert_engine.py       🆕 ~180 行
│   └── scheduler/
│       └── __init__.py            ♻️ 改造为 Scheduler class (~250 行)
├── config/
│   └── settings.py               ♻️ 新增 Phase 3 配置项 (~20 行)
├── scripts/
│   ├── run_scheduler.py          🆕 ~30 行
│   └── health_check.py           ♻️ 扩展 scheduler 检查 (~30 行)
└── docs/
    └── phase3_design.md          📄 本文件
```

**总计新增/修改**：~710 行

---

## 7. 实施顺序

| 步骤 | 内容 | 预计时间 | 依赖 |
|------|------|---------|------|
| 1 | `settings.py` 加 Phase 3 配置 | 10 min | 无 |
| 2 | `price_refresher.py` 核心逻辑 | 2h | Step 1 |
| 3 | 手动测试 Price Refresher（单 tier） | 30 min | Step 2 |
| 4 | `alert_engine.py` 核心逻辑 | 1.5h | Step 2 |
| 5 | Alert Telegram 集成 + 去重 | 1h | Step 4 |
| 6 | `scheduler.py` 改造 | 2h | Step 2, 4 |
| 7 | `run_scheduler.py` + launchd | 30 min | Step 6 |
| 8 | `health_check.py` 扩展 | 20 min | Step 6 |
| 9 | 端到端测试（全量跑 1 小时） | 1h | Step 1-8 |
| 10 | Sakura Code Review | - | Step 9 |

**总预估**：~9 小时开发 + 测试

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Gamma API 429 限速 | Hot tier 刷新失败 | Rate Limiter 已有 200/10s 限制；Hot ~150 market = 2 次 API 调用，远低于限额 |
| 快照表膨胀 | DB 文件变大 | 72h 自动清理；5000 market × 288 snap/day × 72h ≈ 1 亿行... 需要调整 |
| Alert 刷屏 | Telegram 被淹 | 30 分钟去重 + 每市场 8 条/天 + 全局 100 条/天 |
| Scheduler 进程挂掉 | 全部定时任务停摆 | launchd KeepAlive 自动重启 + health_check 过期告警 |

**⚠️ 快照表大小估算需要重新评估**：

```
Hot: 150 markets × 288 snaps/day × 3 days = 129,600 行
Warm: 400 markets × 96 snaps/day × 3 days = 115,200 行
Cold: 2000 markets × 24 snaps/day × 3 days = 144,000 行
Total: ~390K 行，每行 ~500 bytes ≈ 195 MB
```

3 天 195MB 可接受。清理周期可以从 72h 改为 48h 如果空间紧张。

---

## 9. 后续 Phase 预留

- **Phase 4**：Snapshot Cleaner 独立优化、Dashboard（Web UI 看实时状态）
- **Phase 5**：Machine Learning 层 — 用历史 snapshot 训练价格预测模型
- **Phase 6**：实盘执行 — 自动下单（需要钱包配置 + 风控层）

---

_文档结束，等待 Sakura 审核。_
