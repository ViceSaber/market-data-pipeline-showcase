"""Polymarket Scanner — 阈值与常量配置"""

import os
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# ── Database ─────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "polymarket.db"

# ── Gamma API ────────────────────────────────────────────────
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# ── Rate Limiter ─────────────────────────────────────────────
RATE_LIMIT_MAX_REQUESTS = 200
RATE_LIMIT_WINDOW_SECONDS = 10.0

# ── Refresh tier thresholds ──────────────────────────────────
HOT_MIN_VOLUME_24H = 50_000
HOT_MIN_LIQUIDITY = 10_000

WARM_MIN_VOLUME_24H = 5_000
WARM_MIN_LIQUIDITY = 1_000

WARM_TAGS = {"crypto", "politics", "sports"}

# ── Stale rechecker ──────────────────────────────────────────
HOURS_UNSEEN_TO_STALE = 24
STALE_RECHECK_BATCH_SIZE = 50

# ── Generic processing thresholds ───────────────────────────
MIN_QUALITY_SCORE = 0.5
MIN_EDGE_PCT = 1.0
CANDIDATE_DEDUP_HOURS = 1

# ── Scheduler ────────────────────────────────────────────────
TIMEZONE = "Asia/Shanghai"

# ── Refresh tiers ────────────────────────────────────────────
REFRESH_INTERVALS = {
    "hot":  300,    # 5 minutes
    "warm": 900,    # 15 minutes
    "cold": 3600,   # 60 minutes
}
SNAPSHOT_RETENTION_HOURS = 72
BATCH_SIZE = 20  # max slugs per fetch_by_slug_batch call (Gamma API URL length limit)
TIER_PROMOTION_THRESHOLD = 3  # consecutive passes to promote tier
TIER_DEMOTION_THRESHOLD = 3   # consecutive passes to demote tier

# ── Optional notifications ───────────────────────────────────
NOTIFICATION_TOKEN = os.environ.get("NOTIFICATION_TOKEN", "")
NOTIFICATION_TARGET = os.environ.get("NOTIFICATION_TARGET", "")
