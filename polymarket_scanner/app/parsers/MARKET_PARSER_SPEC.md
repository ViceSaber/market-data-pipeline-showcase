# Market Parser Spec

## 概述

`market_parser` 负责从 slug/question 中提取结构化字段，用于后续的 family 分组和结构化比较。

## 输出字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `resolution_basis` | str | 决算依据类型 | `"win_outright"`, `"over_under"`, `"yes_no"`, `"threshold_chain"`, `"top_scorer"`, `"first_to"` |
| `group_template` | str | 分组模板（同 event 内相同 template 的 markets 应归为一组） | `"nba_winner"`, `"fed_rate_cuts"`, `"top_goal_scorer_epl"`, `"btc_price_threshold"` |
| `underlying_entity` | str | 标的实体 | `"BTC"`, `"Fed"`, `"NBA-2026"`, `"EPL-202526"` |
| `line_value` | float/None | 阈值/数值线 | `150000.0`, `52.5`, `3.0` |
| `side_label` | str | 方向标签 | `"yes"`, `"no"`, `"over"`, `"under"`, `"team-name"`, `"0"`, `"1"`, `"12+"` |
| `date_scope` | str/None | 日期范围 | `"2026-03-31"`, `"2026"`, `"by-june-30-2026"` |

## 分类规则

### 1. `win_outright` — 直接竞猜谁赢

**特征：** slug 包含 `win-the-*` 或 question 包含 `Will X win Y`

- NBA Finals winner: `will-{team}-win-the-2026-nba-finals`
- EPL winner: `will-{team}-win-the-2025-26-english-premier-league`
- Presidential election: `will-{person}-win-the-{year}-{country}-presidential-election`
- Conference/division: `will-{team}-win-the-{conference}-conference`

**输出：**
```
resolution_basis: "win_outright"
group_template: "{event_key}_winner"  # e.g. "nba-finals-2026_winner"
underlying_entity: "{event_name}"     # e.g. "NBA Finals 2026"
side_label: "{team_or_person}"
date_scope: "{year}" if present
```

### 2. `over_under` — 超过/不超过阈值

**特征：** slug 包含价格/数值 + 日期

- Bitcoin hit 150k: `will-bitcoin-hit-150k-by-march-31-2026`
- Fed rate cuts count: `will-3-fed-rate-cuts-happen-in-2026`
- Ethereum gas price: `will-the-average-monthly-ethereum-gas-price-hit-10-gwei-before-2027`

**输出：**
```
resolution_basis: "over_under" (单一阈值) 或 "threshold_chain" (多阈值链)
group_template: "{entity}_price_{unit}"  # e.g. "btc_price_usd"
underlying_entity: "{asset}"             # e.g. "BTC"
line_value: {extracted_number}
side_label: "yes"
date_scope: "{date}"
```

### 3. `yes_no` — 二元是非

**特征：** slug 是简单的 yes/no 问题

- `examplecorp-completes-major-asset-sale-by-march-31-2026`
- `kraken-ipo-in-2025`
- `trump-out-as-president-before-gta-vi`

**输出：**
```
resolution_basis: "yes_no"
group_template: "standalone"
underlying_entity: "{extracted_entity}"
side_label: "yes"
date_scope: "{date}" if present
```

### 4. `top_scorer` — 谁是最佳射手/得分手

**特征：** slug 包含 `top-goal-scorer`, `top-scorer`, `top-batter`, `most-*`

- `will-{player}-be-the-top-goal-scorer-in-the-202526-english-premier-league-season`
- `will-{player}-be-the-top-batter-in-the-ipl-2026`

**输出：**
```
resolution_basis: "top_scorer"
group_template: "{league}_{season}_top_scorer"
underlying_entity: "{league}"  # e.g. "EPL 2025-26"
side_label: "{player}"
date_scope: "{season}"
```

### 5. `first_to` — 谁先达到

**特征：** slug 包含 `first-to`, `or-*-first`

- `will-bitcoin-hit-60k-or-80k-first-965`
- `will-ethereum-hit-1k-or-3k-first`

**输出：**
```
resolution_basis: "first_to"
group_template: "{entity}_first_to"
underlying_entity: "{asset}"
side_label: "{option}"  # e.g. "60k", "80k"
```

### 6. `completed_by` — 某日期前是否完成

**特征：** slug 包含 `by-{date}` 或 `before-{date}`

- `starmer-out-by-june-30-2026`
- `taylor-swift-pregnant-before-2027`

**输出：**
```
resolution_basis: "completed_by"
group_template: "{entity}_by_date"
underlying_entity: "{entity}"
side_label: "yes"
date_scope: "{date}"
```

## 假信号排除规则

以下 slug 模式应被标记为 `resolution_basis: "ignore"` 或 `group_template: "standalone"`，不参与套利：

1. **单一事件**（没有配对的其他市场）：`kraken-ipo-in-2025`
2. ** meme 市场**：`will-jesus-christ-return-before-gta-vi`
3. **不可比市场**：不同实体、不同维度的市场不应混组

## 日期解析

从 slug 中提取日期的正则模式：
- `by-{month}-{day}-{year}` → `YYYY-MM-DD`
- `by-{month}-{day}` → `YYYY-MM-DD`（当年）
- `in-{year}` → `YYYY`
- `before-{year}` → `YYYY`
- `by-december-31` → 当年 12-31
- `by-june-30` → 当年 06-30

## 数值解析

从 slug/question 中提取数值：
- `150k` → `150000`
- `1m` → `1000000`
- `6b` → `6000000000`
- `800m` → `800000000`
- `1pt5b` → `1500000000`
- `52.5` → `52.5`
- `10-gwei` → `10.0`
