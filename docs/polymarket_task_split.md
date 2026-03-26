# Polymarket 系统重构 — 任务分工

## 分工原则
- **枫**：写代码
- **Sakura**：写 spec、写 parser 逻辑、审查代码、跑测试
- **Gide**：决策 + 最终验收

---

## Phase 1：能跑（预计 1-2 天）

### 任务 1.1 — 建库 + 基础设施
**负责人：枫**
**Sakura 负责：验收**

```
要做的事：
1. 建项目目录结构（按 design doc）
2. sql/001_init_schema.sql — 把 DDL 拿过去直接用
3. scripts/init_db.py — 读 sql/ 目录初始化 DB
4. app/db.py — get_conn() 
5. config/settings.py — 阈值常量
6. requirements.txt — requests, apscheduler
7. pyproject.toml
```

**验收标准：**
- `python3 scripts/init_db.py` 成功建表
- `python3 -c "from app.db import get_conn; print('ok')"` 不报错

---

### 任务 1.2 — Gamma API Client + 全局 Rate Limiter
**负责人：枫**
**Sakura 负责：验收**

```
要做的事：
1. app/clients/rate_limiter.py — 全局单例，200 req/10s
2. app/clients/gamma_client.py：
   - fetch_events(active, closed, limit, offset) → 分页拉 events
   - fetch_by_slug_batch(slugs: list) → 按 slug 批量查市场
   - 两个方法都调 rate_limiter.acquire()
```

**验收标准：**
- `fetch_events(active=True, closed=False, limit=3, offset=0)` 返回 3 个 events
- `fetch_by_slug_batch(["bitcoin-above-70k-on-march-22"])` 返回正确数据
- 连续调用 200 次后被 rate limiter 阻塞

---

### 任务 1.3 — Event Indexer（Discovery 层）
**负责人：枫**
**Sakura 负责：验收 + 写 spec**

```
要做的事：
1. app/services/event_indexer.py:
   - 调 fetch_events() 全量分页（limit=100）
   - 对每个 event：upsert event_registry
   - 对 event 内每个 market：upsert market_registry
   - 写 last_seen_at = now
   - 从 event.tags 构建 tags_json
2. scripts/run_event_indexer.py — 单独可执行的入口
```

**Sakura 提供的 spec：**
- event_registry 字段映射：event.id → event_id, event.slug → event_slug, event.title → title, event.volume → volume_num 等
- market_registry 字段映射：market.id → market_id, market.slug → slug, market.question → question, market.outcomePrices → (写入 snapshot, 不写 registry)
- tags_json 格式：`[{"id": "21", "label": "Crypto", "slug": "crypto"}, ...]`
- market 的 outcome_type 初始设为 'unknown'，后续由 parser 填充

**验收标准：**
- 跑一次后 event_registry 有 500+ 条
- market_registry 有 1000+ 条
- 同一 event 跑两次不报错（upsert 语义）

---

### 任务 1.4 — Stale Rechecker（含完整状态机）
**负责人：枫**
**Sakura 负责：验收 + 写 spec**

```
要做的事：
1. app/services/stale_rechecker.py:
   - fresh → unseen（1 小时未见）
   - unseen → stale_pending（24h 未见 或 end_time 已过）
   - stale_pending → slug 批量回查
   - 回查结果：active → fresh, closed → closed_confirmed
   - unseen → fresh（再次见到时回路）
2. scripts/run_stale_rechecker.py
```

**Sakura 提供的 spec：**
- 完整 SQL（见 design doc section 13.3）
- end_time 判断：优先用 market_registry.end_time，其次从 slug 解析日期
- 批量回查用 fetch_by_slug_batch，每批 50 个

**验收标准：**
- 建一个 mock market（end_time < now），跑一遍后状态变为 stale_pending
- slug 回查确认 closed 后变为 closed_confirmed
- 重新被 event_indexer 见到后回到 fresh

---

### 任务 1.5 — Health Check
**负责人：枫**
**Sakura 负责：验收**

```
要做的事：
1. scripts/health_check.py:
   - 读 scheduler_state 表
   - 打印每个 job 的最后运行时间
   - 标记超时的 job（>5min ⚠️, >15min 🔴）
```

**验收标准：**
- 跑一次输出格式正确

---

### 任务 1.6 — Scheduler（Phase 1 最小版）
**负责人：枫**
**Sakura 负责：验收**

```
要做的事：
1. scripts/run_scheduler.py:
   - APScheduler BlockingScheduler
   - 时区 Asia/Shanghai
   - 只调度 Phase 1 的 job：
     - event_indexer: 10 min
     - stale_rechecker: 15 min
   - 每次 job 完成后更新 scheduler_state
```

**验收标准：**
- `python3 scripts/run_scheduler.py` 启动不报错
- 10 分钟后 event_registry 有数据
- 15 分钟后 stale_rechecker 运行过

---

## Phase 2：能找（预计 2-3 天）

### 任务 2.1 — Market Parser（slug → 结构字段）
**负责人：Sakura 写逻辑 + 测试，枫集成**

这是整个系统最难的部分。Sakura 负责写 parser 规则和测试用例，枫负责集成到代码里。

```
Sakura 要做的事：
1. 写 app/parsers/market_parser.py:
   - parse_slug(slug) → {
       resolution_basis: "winner" | "over_under" | "completed_match" | "top_batter" | ...,
       group_template: "team_top_batter" | "most_sixes" | "toss_match_double" | ...,
       underlying_entity: "BTC" | "Trump" | "teamA-teamB" | "MSFT" | ...,
       line_value: 52.5 | None,
       side_label: "teamA" | "draw" | "teamB" | "over" | "under" | ...,
       date_scope: "2026-03-23" | None
     }
2. 写 tests/test_market_parser.py:
   - 覆盖今天 8 个假信号 case
   - 覆盖 crypto、sports、politics、energy 各类别
   - 目标：100% case 正确分类
```

**枫要做的事：**
- 把 parser 集成到 event_indexer 里，upsert market_registry 时调 parse_slug()
- 写 parse_version 字段（v1, v2...）

---

### 任务 2.2 — Family Builder
**负责人：枫**
**Sakura 负责：写 spec + 验收**

```
要做的事：
1. app/services/family_builder.py:
   - 按 event_id 分组
   - 同 event 内按 resolution_basis + group_template + underlying_entity + date_scope 切桶
   - 生成 family_key = "{event_slug}::{resolution_basis}::{group_template}::{underlying_entity}::{date_scope}"
   - 判断 family_type:
     - mutually_exclusive：同一 event + 同 basis + 同 template + 多个 side_label
     - inclusion_chain：同一 event + 不同 line_value
     - threshold_chain：同一 event + 同 basis + 不同 line_value
     - ignore：其他
   - 计算 completeness_score（互斥组：实际成员数 / 期望成员数）
   - 写 market_family + market_family_member
2. scripts/run_family_builder.py
```

**验收标准：**
- 板球 top batter (teamA/draw/teamB) → 一个 mutually_exclusive family，member_count=3，completeness=1.0
- over 52.5 / over 83.5 / over 86.5 → threshold_chain family（不是 mutually_exclusive）
- Fannie Mae 不同时间窗口 → 不混组

---

### 任务 2.3 — Scanner
**负责人：枫**
**Sakura 负责：写 spec + 验收**

```
要做的事：
1. app/services/scanner.py:
   - 只扫 quality_score >= 阈值 的 family
   - mutually_exclusive：买全部 NO，计算 edge = 1 - sum(NO_prices)
   - inclusion_chain：买大市场 YES + 小市场 NO
   - threshold_chain：只做 progressive 检测，不做互斥
   - 结果写 scanner_candidate_queue
2. 每 30 秒调一次
```

**验收标准：**
- 今天 8 个假信号 case 全部被正确排除（不属于任何有效 family 或 edge < 阈值）
- 如果有真实套利机会，正确写入 candidate_queue

---

### 任务 2.4 — Candidate Confirmer
**负责人：枫**
**Sakura 负责：验收**

```
要做的事：
1. app/services/candidate_confirmer.py:
   - 从 scanner_candidate_queue 取 pending 候选
   - 收集相关 slug
   - 调 fetch_by_slug_batch 拿最新价格和状态
   - 验证：全部 active、价格未变、结构未变
   - 通过 → 写 scan_result + alert_log
   - 失败 → 标记 reject_reason
2. 每 30 秒调一次
```

**验收标准：**
- 候选确认通过后 scan_result 有记录
- 已关闭的市场被正确 reject

---

## Phase 3：能交易（预计 2-3 天）

### 任务 3.1 — 分层价格刷新
**负责人：枫**
**Sakura 负责：写 spec + 验收**

```
要做的事：
1. app/services/refresh_selector.py:
   - classify_tier(market) → "hot" | "warm" | "cold"
   - 阈值从 config/settings.py 读
2. app/services/hot_refresher.py (30s)
3. app/services/warm_refresher.py (5 min)
4. app/services/cold_refresher.py (1 hour)
5. app/services/snapshot_cleaner.py (每天 4:00)
```

---

### 任务 3.2 — Alert Service
**负责人：枫**
**Sakura 负责：写通知格式 spec**

```
要做的事：
1. app/services/alert_service.py:
   - 读 scan_result 中 validated_realtime=1 的记录
   - 发送通知（Telegram / stdout）
   - 写 alert_log
```

---

### 任务 3.3 — 完整 Scheduler
**负责人：枫**

```
把所有 job 加到 run_scheduler.py：
- event_indexer: 10 min
- hot_refresher: 30s
- warm_refresher: 5 min
- cold_refresher: 1 hour
- stale_rechecker: 15 min
- family_builder: 10 min
- scanner: 30s
- candidate_confirmer: 30s
- snapshot_cleaner: 每天 4:00
```

---

## 时间线

| 日期 | 阶段 | 产出 |
|------|------|------|
| Day 1 | Phase 1 | 建库 + API + Event Indexer + Stale Rechecker + Scheduler 能跑 |
| Day 2-3 | Phase 2 | Parser + Family Builder + Scanner 能找套利 |
| Day 4-5 | Phase 3 | 分层刷新 + Alert 能通知 |

## Sakura 立刻要做的事

1. ✅ 写 test case 列表（8 个假信号 case + 补充 case）
2. ✅ 写 market_parser.py 的核心逻辑
3. ✅ 准备 Phase 1 验收脚本
