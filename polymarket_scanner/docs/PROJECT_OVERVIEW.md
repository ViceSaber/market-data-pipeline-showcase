# Polymarket Scanner — 项目概述

> 面向团队成员的快速上手文档。
> 更详细的策略研究手记见 `docs/AGENT_HANDOFF_2026-03-25.md`。

---

## 一句话

**自动扫描 Polymarket 的 2800+ 个预测市场，找套利 / 异常价格信号，推送到 Telegram。**

---

## 项目结构

```
polymarket_scanner/
├── app/
│   ├── clients/          # 外部 API 客户端
│   │   ├── gamma_client.py     # Polymarket Gamma API 封装
│   │   └── rate_limiter.py     # 限速器（200 req / 10s）
│   ├── parsers/
│   │   └── market_parser.py    # 语义解析 slug → 结构化数据
│   ├── services/         # 核心业务逻辑
│   │   ├── alert_engine.py     # 价格异常检测 + Telegram 推送
│   │   ├── candidate_confirmer.py  # 候选信号确认
│   │   ├── classifier.py       # 市场分类
│   │   ├── event_indexer.py    # 事件索引
│   │   ├── family_builder.py   # 市场家族构建
│   │   ├── portfolio_arbitrage.py  # 组合套利检测
│   │   ├── price_refresher.py  # 价格快照刷新（hot/warm/cold）
│   │   ├── scanner.py          # 信号扫描
│   │   └── stale_rechecker.py  # 陈旧市场重检
│   ├── scheduler/
│   │   └── __init__.py         # 调度器（APScheduler）
│   └── db.py                   # 数据库连接管理
├── config/
│   └── settings.py             # 所有阈值 / 常量
├── scripts/                    # 独立运行脚本
│   ├── run_scheduler.py        # 主入口：启动所有定时任务
│   ├── health_check.py         # 健康检查
│   ├── backtest_portfolio_arb.py   # 历史回测
│   ├── evaluate_conditional_*.py   # 条件策略评估
│   ├── volume_spike.py         # 成交量异常检测
│   └── ...
├── tests/                      # 测试（pytest）
├── data/
│   └── polymarket.db           # SQLite 数据库
└── docs/
    ├── PROJECT_OVERVIEW.md     # ← 你在这里
    └── AGENT_HANDOFF_2026-03-25.md   # 策略研究手记
```

---

## 核心流程

```
┌─────────────────────────────────────────────────────────────┐
│  Scheduler (APScheduler, blocking)                          │
│  启动入口: scripts/run_scheduler.py                         │
└───────────┬─────────────────────────────────────────────────┘
            │
            ├── event_indexer (6h)   ──→ 从 Polymarket 拉事件列表
            ├── stale_rechecker (12h)──→ 重检陈旧市场
            ├── family_builder (4h)  ──→ 把同一事件下的市场组成"家族"
            ├── price_refresh_hot (5m) ─→ 高量市场快照
            ├── price_refresh_warm (15m)→ 中量市场快照
            ├── price_refresh_cold (1h) → 低量市场快照
            ├── scanner (10m)        ──→ 扫描套利信号
            ├── candidate_confirmer (5m)──→ 确认候选信号
            ├── confirmer (5m)       ──→ 确认信号
            ├── portfolio_arb (10m)  ──→ 组合套利检测
            ├── alert_check (5m)     ──→ 价格异常检测 + 推送
            ├── volume_spike (1h)    ──→ 成交量异常检测
            └── conditional_edgex_dry_run (10m)──→ 研究层 dry-run
```

---

## 三类套利 / 信号

### A. Exclusive（排他型）
- **含义**：同一事件下，多个市场互相排斥，只有一个会赢
- **策略**：买 NO on all（保证收益 = n-1）
- **例子**：哪个候选人在某个州胜选
- **当前状态**：已实现

### B. Nested Inversion（嵌套倒挂）
- **含义**：阈值嵌套市场，YES 更难条件的价格反而更低（倒挂）
- **策略**：买 YES(easier) + NO(harder)
- **例子**：BTC > $100k 比 BTC > $90k 更便宜 → 可套利
- **当前状态**：已修正方向，已去掉错误过滤

### C. Conditional Range（条件范围）
- **含义**：不是无风险套利，而是"外侧赢、中间亏"的结构化交易
- **策略**：特定模板 + 特定持有时间 + 入场条件
- **例子**：edgex_fdv，hold 30m
- **当前状态**：研究层，dry-run 中，尚未正式推送

### 重要区分
- **A 和 B 是 pure arb**（理论上无风险）
- **C 是方向性交易**（有风险，只是有 edge）

---

## Alert Engine（价格异常推送）

### 检测类型

| 类型 | 触发条件 | 频率限制 |
|------|----------|----------|
| 🚨 SPIKE | 5 分钟价格变动 ≥ 15%（penny market ≥ 45%） | 30min dedup |
| 📈 TREND | 1 小时累计变动 ≥ 25% | 30min dedup |
| 📊 VOLUME SURGE | 24h 成交量比 24h 前涨 3x+ | 30min dedup |
| ⚡ SPIKE REVERT | 涨 20%+ 后回撤 5%+（双向） | 30min dedup |
| ⚠️ WIDE SPREAD | bid-ask spread ≥ 25%（低量 ≥ 40%） | 5 条/天 |

### 限流机制
- **每轮 cap**：最多 5 条
- **全局 cap**：每天 30 条
- **每市场 cap**：每个 slug 每天 8 条
- **spread 专项**：每天 5 条
- **dedup**：同 slug + 同 type 30 分钟内不重复

### 推送格式示例
```text
⚡ SPIKE REVERT — 买 NO（押下跌）
市场: Will BTC hit $100k?
当前价格: $0.7000 (NO=$0.3000)
Spike: +75.0% from baseline
Peak: $0.8000 (Peak 回撤 12.5%)
建议仓位: 3-5% | 止损: 20% 继续涨
Confidence: 72% | 24h vol: $100,000
```

---

## 数据库

### 核心表

| 表 | 用途 |
|----|------|
| `market_registry` | 市场元数据（slug, question, event, template, ...） |
| `market_snapshot` | 历史快照（每 5/15/60 分钟） |
| `market_snapshot_latest` | 最新快照（物化视图） |
| `event_registry` | 事件元数据 |
| `market_family` | 市场家族（同一事件下的分组） |
| `scan_result` | 扫描结果 |
| `candidate` | 候选信号 |
| `alert_log` | 推送日志 |
| `portfolio_arb_snapshot` | 组合套利快照 |
| `scheduler_state` | 调度器状态 |
| `alert_dedup` | 推送去重 |

### SQLite 注意事项
- 使用 **WAL 模式**（`PRAGMA journal_mode = WAL`）
- `busy_timeout = 30000`（30 秒）
- `synchronous = NORMAL`
- 写路径有 retry backoff（`execute_with_retry`）

---

## 配置说明

### 价格刷新分级
- **Hot**（5 分钟）：24h 成交量 ≥ $50K 且流动性 ≥ $10K
- **Warm**（15 分钟）：24h 成交量 ≥ $5K 且流动性 ≥ $1K，或属于 crypto/politics/sports
- **Cold**（1 小时）：其他所有

### Alert 阈值（config/settings.py）
```python
ALERT_SPIKE_PCT = 15.0          # 5-min 价格变动阈值
ALERT_TREND_PCT = 25.0          # 1h 累计变动阈值
ALERT_VOLUME_SURGE_MULT = 3.0   # 24h 成交量暴涨倍数
ALERT_SPREAD_PCT = 25.0         # bid-ask spread 阈值
ALERT_SUPPRESS_MINUTES = 30     # 去重窗口
ALERT_DAILY_MAX_TOTAL = 30      # 全局每日上限
ALERT_DAILY_MAX_PER_MARKET = 8  # 每市场每日上限
```

---

## 关键脚本

| 脚本 | 用途 | 用法 |
|------|------|------|
| `run_scheduler.py` | 主入口，启动所有定时任务 | `python scripts/run_scheduler.py` |
| `health_check.py` | 健康检查 | `python scripts/health_check.py` |
| `volume_spike.py` | 成交量异常检测（dry-run） | `python scripts/volume_spike.py` |
| `backtest_portfolio_arb.py` | 历史回测 | `python scripts/backtest_portfolio_arb.py --mode pure` |
| `evaluate_conditional_strategy_v1.py` | 条件策略 v1 评估 | `python scripts/evaluate_conditional_strategy_v1.py` |
| `run_conditional_dry_run.py` | edgex_fdv dry-run | `python scripts/run_conditional_dry_run.py` |

---

## 常见问题

### Q: 为什么 portfolio_arb 现在没推？
当前市场下 **pure arb = 0**，不是系统故障。`conditional_range` 有信号但目前默认不推。

### Q: volume_spike 显示 stuck 怎么办？
如果 health 显示 `volume_spike notes='running'` 但实际没有进程在跑，说明是 `scheduler_state` 残留。重启 scheduler 会自动 recovery。

### Q: Spike Revert 为什么推的是买 NO？
Spike Revert 是双向的。如果价格暴涨 20%+ 后回撤 → 买 NO（押继续跌）。如果暴跌 20%+ 后反弹 → 买 YES（押继续涨）。

### Q: alert_log 的 error_message 字段存的是什么？
**存的是 alert_type**（spike/trend/volume_surge 等）。这是历史遗留的列复用，不是 bug。新加代码时请注意。

### Q: 如何加一个新的 alert 类型？
1. 在 `alert_engine.py` 加 `check_xxx()` 方法
2. 在 `check_all()` 的 `for check_fn` 列表里加上
3. 在 `config/settings.py` 加阈值
4. 在 `ALERT_ENGINE_TYPES` tuple 里加上新类型名
5. 加测试

### Q: 如何调整推送频率？
在 `config/settings.py` 改：
- `ALERT_SUPPRESS_MINUTES` — 去重窗口
- `ALERT_DAILY_MAX_TOTAL` — 全局上限
- `ALERT_DAILY_MAX_PER_MARKET` — 每市场上限

调度频率在 `app/scheduler/__init__.py` 的 `registry.register()` 里改 `interval_seconds`。

---

## 与其他项目的关系

### News Alpha（新闻驱动信号）
- 独立项目：`projects/ai-trading-hunter/news_alpha/`
- 通过 `oracle_arb_bridge.py` 把新闻信号关联到 Polymarket 市场
- 推送到同一个 Telegram，但格式和逻辑完全独立
- 详见 `news_alpha/scripts/oracle_arb_bridge.py`

### 数据共享
- Polymarket Scanner 的 `polymarket.db` 是数据源
- News Alpha 的 `check_market()` 读取 `polymarket.db` 的 `market_snapshot_latest`
- 两个项目共享同一个 Telegram bot

---

## 团队约定

### 分工
- **枫（Researcher）**：写代码、跑回测、修 bug、产出策略
- **樱（Reviewer）**：代码审查、方法论审查
- **光（Judge）**：证据分类、策略评估
- **波（Coordinator）**：流程调度、任务路由
- **七海（Gide）**：最终拍板

### 代码规范
- 测试必须通过（`pytest`）
- 修改 alert 类型要更新 `ALERT_ENGINE_TYPES`
- SQLite 写路径必须用 `execute_with_retry` / `commit_with_retry`
- 不要绕过 `get_conn()` 直连数据库

---

## 最后更新
2026-03-25 by 枫（Researcher）
