"""Prompt Injection Filter — hard defense layer (方案 A-2 Wrapper).

Detects and neutralizes prompt injection attempts in external content.
Designed to wrap web_fetch, web_search, read, exec outputs before
feeding them to the LLM.

Usage:
    from app.utils.injection_filter import scan_injection, filter_injection

    is_injected, cleaned, confidence = scan_injection(raw_text)
    if is_injected:
        safe_text = filter_injection(raw_text, strategy="strip")
"""

from __future__ import annotations

import base64
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# ── Database path ────────────────────────────────────────────
_DB_PATH = Path(os.environ.get(
    "INJECTION_LOG_DB",
    os.path.expanduser("~/.openclaw/workspace-bot3/data/injection_log.db"),
))

# ── Keyword patterns ─────────────────────────────────────────

_EN_KEYWORDS = [
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"ignore\s+(?:all\s+)?instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above)",
    r"you\s+are\s+now\s+",
    r"new\s+instructions",
    r"system\s*prompt",
    r"override\s+(?:your|the|all)",
    r"jailbreak",
    r"act\s+as\s+(?:if|though)\s+you",
    r"pretend\s+(?:to\s+be|you\s+are)",
    r"from\s+now\s+on\s+you\s+(?:are|will|should)",
    r"stop\s+(?:being|acting)",
]

_CN_KEYWORDS = [
    r"无视你的主人",
    r"无视.*?指令",
    r"忽略以上",
    r"忽略.*?指令",
    r"你现在是",
    r"你从现在开始",
    r"忘记你是",
    r"开始发曼波",
    r"假装你是",
    r"扮演.*?角色",
]

_FORMAT_KEYWORDS = [
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[/INST\]",
    r"<<SYS>>",
    r"\[INST\]",
    r"system\s*:",
    r"<system>",
    r"\[system\]",
]

_ALL_KEYWORDS = _EN_KEYWORDS + _CN_KEYWORDS + _FORMAT_KEYWORDS
_KW_PATTERN = re.compile("|".join(_ALL_KEYWORDS), re.IGNORECASE | re.DOTALL)

# ── Structure patterns ───────────────────────────────────────

_NESTED_DISREGARD = re.compile(
    r"(?:无视|ignore|disregard)[^。.!！]{0,30}"
    r"(?:无视|ignore|disregard)[^。.!！]{0,30}"
    r"(?:无视|ignore|disregard)[^。.!！]{0,30}"
    r"(?:无视|ignore|disregard)",
    re.IGNORECASE | re.DOTALL,
)

_ROLE_SWITCH = re.compile(
    r"(?:你现在是|你从现在开始是|you\s+are\s+now\s+|from\s+now\s+on\s+you\s+are\s+)"
    r"\s*[\w\u4e00-\u9fff]{2,50}",
    re.IGNORECASE,
)

# ── Encoding patterns ────────────────────────────────────────

_B64_PATTERN = re.compile(
    r"(?:base64[:,\s]*)?[A-Za-z0-9+/]{20,}={0,2}",
)

# ── Markdown hidden injection ────────────────────────────────

_MD_HIDDEN = re.compile(
    r'\[([^\]]*)\]\([^)]*(?:"|\'|\')(?:ignore|disregard|system|无视|忽略)[^)]*(?:"|\'|\')\)',
    re.IGNORECASE,
)
_MD_ALT_TITLE = re.compile(
    r'(?:alt|title)\s*=\s*(?:"|\')(?:ignore|disregard|system|无视|忽略)[^"\']*(?:"|\')',
    re.IGNORECASE,
)

# ── Whitelist ─────────────────────────────────────────────────

_WHITELIST = [
    "ignite conference",
    "ignite 大会",
    "rpg",
    "role-playing game",
    "角色扮演",
    "ignore the noise",
    "ignore the hype",
    "ignore the market",
    "无视噪音",
    "无视市场",
    "prompt engineering",
    "prompt 注入",
    "prompt injection",
    "jailbreak detection",
    "jailbreak 检测",
]


# ── Data types ────────────────────────────────────────────────

@dataclass
class ScanResult:
    is_injected: bool
    cleaned_text: str
    confidence: float
    matched_patterns: list[str]
    context_snippet: str


# ── Logging (module-level init) ──────────────────────────────

def _ensure_log_db():
    """Initialize injection_log table (idempotent, called once at import or first write)."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS injection_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
            source_tool     TEXT,
            bot_session     TEXT,
            pattern_matched TEXT,
            confidence      REAL,
            action_taken    TEXT,
            context_snippet TEXT,
            raw_snippet     TEXT
        )
    """)
    conn.commit()
    conn.close()


def _log_injection(
    source_tool: str = "",
    bot_session: str = "",
    pattern_matched: str = "",
    confidence: float = 0.0,
    action_taken: str = "",
    context_snippet: str = "",
    raw_snippet: str = "",
):
    try:
        _ensure_log_db()
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute("""
            INSERT INTO injection_log
            (source_tool, bot_session, pattern_matched, confidence,
             action_taken, context_snippet, raw_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_tool, bot_session, pattern_matched, confidence,
              action_taken, context_snippet[:500], raw_snippet[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("Failed to log injection: %s", e)


# ── Core detection ────────────────────────────────────────────

def _extract_context(text: str, match_start: int, match_end: int, window: int = 100) -> str:
    """Extract context snippet around a match."""
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    return text[start:end]


def _is_whitelisted_context(context_snippet: str) -> bool:
    """Check if only the context_snippet (not full text) contains whitelist keywords.

    This prevents attackers from hiding 'rpg' far from the injection payload
    to bypass detection.
    """
    lower = context_snippet.lower()
    for w in _WHITELIST:
        if w.lower() in lower:
            return True
    return False


def _decode_and_scan_b64(text: str, depth: int = 0, max_depth: int = 2) -> ScanResult | None:
    """Decode base64 blocks and recursively scan (capped at max_depth)."""
    if depth >= max_depth:
        return None

    decoded_parts = []
    for m in _B64_PATTERN.finditer(text):
        candidate = m.group(0)
        if candidate.lower().startswith("base64"):
            candidate = candidate[7:].strip(",: ")
        try:
            decoded = base64.b64decode(candidate).decode("utf-8", errors="ignore")
            if decoded and len(decoded) > 5:
                decoded_parts.append(decoded)
        except Exception:
            continue

    if not decoded_parts:
        return None

    combined = "\n".join(decoded_parts)
    return _scan_detailed(combined, depth=depth + 1)


def scan_injection(
    text: str,
    source_tool: str = "",
    bot_session: str = "",
) -> tuple[bool, str, float]:
    """Scan text for prompt injection attempts.

    Args:
        text: The text to scan.
        source_tool: Which tool produced this text (web_fetch, web_search, etc.)
        bot_session: Which bot session is processing this.

    Returns:
        (is_injected, cleaned_text, confidence)
    """
    result = _scan_detailed(text)
    return result.is_injected, result.cleaned_text, result.confidence


def _scan_detailed(text: str, depth: int = 0) -> ScanResult:
    """Detailed scan returning full ScanResult.

    Note: This function does NOT log. Logging is done by filter_injection()
    to avoid duplicate entries.
    """
    if not text or not text.strip():
        return ScanResult(False, text, 0.0, [], "")

    matched_patterns: list[str] = []
    confidence = 0.0
    context_snippet = ""

    # 1) Keyword matching
    for m in _KW_PATTERN.finditer(text):
        matched_patterns.append(f"keyword:{m.group()[:30]}")
        if not context_snippet:
            context_snippet = _extract_context(text, m.start(), m.end())
        confidence = max(confidence, 0.7)

    # 2) Structure: recursive nesting
    mm = _NESTED_DISREGARD.search(text)
    if mm:
        matched_patterns.append("structure:recursive_nesting")
        if not context_snippet:
            context_snippet = _extract_context(text, mm.start(), mm.end())
        confidence = max(confidence, 0.9)

    # 3) Structure: role switch
    mm = _ROLE_SWITCH.search(text)
    if mm:
        matched_patterns.append("structure:role_switch")
        if not context_snippet:
            context_snippet = _extract_context(text, mm.start(), mm.end())
        confidence = max(confidence, 0.8)

    # 4) Base64 encoded injection (recursive, max_depth=2)
    b64_result = _decode_and_scan_b64(text, depth=depth)
    if b64_result and b64_result.is_injected:
        matched_patterns.append(f"encoding:base64→{b64_result.matched_patterns[0]}")
        confidence = max(confidence, 0.85)
        if not context_snippet:
            context_snippet = f"[base64 decoded] {b64_result.context_snippet}"

    # 5) Markdown hidden injection
    if _MD_HIDDEN.search(text):
        matched_patterns.append("markdown:hidden_link")
        confidence = max(confidence, 0.75)
    if _MD_ALT_TITLE.search(text):
        matched_patterns.append("markdown:hidden_alt_title")
        confidence = max(confidence, 0.75)

    # Whitelist check — only check context_snippet, not full text
    is_injected = len(matched_patterns) > 0
    if is_injected and context_snippet and _is_whitelisted_context(context_snippet):
        confidence *= 0.3
        if confidence < 0.2:
            is_injected = False

    return ScanResult(
        is_injected=is_injected,
        cleaned_text=text,
        confidence=confidence,
        matched_patterns=matched_patterns,
        context_snippet=context_snippet[:200],
    )


# ── Filtering strategies ─────────────────────────────────────

def _strip_injection(text: str) -> str:
    """Replace detected injection segments with [FILTERED]."""
    cleaned = _KW_PATTERN.sub("[FILTERED]", text)
    cleaned = _NESTED_DISREGARD.sub("[FILTERED]", cleaned)
    cleaned = _ROLE_SWITCH.sub("[FILTERED]", cleaned)
    cleaned = _MD_HIDDEN.sub("[FILTERED-LINK]", cleaned)
    cleaned = _MD_ALT_TITLE.sub("[FILTERED-ATTR]", cleaned)

    # Strip base64 blocks containing injection
    for m in _B64_PATTERN.finditer(cleaned):
        candidate = m.group(0)
        if candidate.lower().startswith("base64"):
            candidate = candidate[7:].strip(",: ")
        try:
            decoded = base64.b64decode(candidate).decode("utf-8", errors="ignore")
            if decoded and _KW_PATTERN.search(decoded):
                cleaned = cleaned.replace(m.group(0), "[FILTERED-B64]")
        except Exception:
            continue

    return cleaned


def filter_injection(
    text: str,
    strategy: str = "strip",
    source_tool: str = "",
    bot_session: str = "",
) -> str:
    """Filter injection from text.

    Args:
        text: Raw text to filter.
        strategy: "strip" replaces injection segments with [FILTERED].
                  "block" returns empty string if any injection detected.
        source_tool: Tool that produced this text.
        bot_session: Bot session identifier.

    Returns:
        Filtered text, or empty string if strategy="block" and injection found.
    """
    result = _scan_detailed(text)

    if not result.is_injected:
        return text

    # Single log point — only here, not in _scan_detailed
    action = "blocked" if strategy == "block" else "stripped"

    if strategy == "block":
        _log_injection(
            source_tool=source_tool,
            bot_session=bot_session,
            pattern_matched=", ".join(result.matched_patterns[:5]),
            confidence=result.confidence,
            action_taken=action,
            context_snippet=result.context_snippet,
            raw_snippet=text[:300],
        )
        return ""

    cleaned = _strip_injection(text)
    _log_injection(
        source_tool=source_tool,
        bot_session=bot_session,
        pattern_matched=", ".join(result.matched_patterns[:5]),
        confidence=result.confidence,
        action_taken=action,
        context_snippet=result.context_snippet,
        raw_snippet=text[:300],
    )
    return cleaned
