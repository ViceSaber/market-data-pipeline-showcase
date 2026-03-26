"""
test_market_parser — 验证 parser 对真实 Polymarket slug 的分类正确性
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parsers.market_parser import parse_slug, parse_number, parse_date_from_slug, parse_threshold_market


# ─── Number Parsing Tests ──────────────────────────────────────

def test_number_parsing():
    assert parse_number("150k") == 150_000
    assert parse_number("hit-150k-by") == 150_000
    assert parse_number("1m") == 1_000_000
    assert parse_number("6b") == 6_000_000_000
    assert parse_number("800m") == 800_000_000
    assert parse_number("1pt5b") == 1_500_000_000
    assert parse_number("52.5") == 52.5
    assert parse_number("$150,000") == 150_000
    assert parse_number("10-gwei") == 10.0
    print("  ✅ number parsing (7/7)")


# ─── Date Parsing Tests ───────────────────────────────────────

def test_date_parsing():
    assert parse_date_from_slug("will-bitcoin-hit-150k-by-march-31-2026") == "2026-03-31"
    assert parse_date_from_slug("starmer-out-by-june-30-2026") == "2026-06-30"
    assert parse_date_from_slug("will-3-fed-rate-cuts-happen-in-2026") == "2026"
    assert parse_date_from_slug("taylor-swift-pregnant-before-2027") == "2027"
    assert parse_date_from_slug("kraken-ipo-in-2025") == "2025"
    assert parse_date_from_slug("will-player-l-be-the-top-goal-scorer-in-the-202526-english-premier-league-season") == "2025-26"
    assert parse_date_from_slug("will-solana-reach-100-in-march-2026") == "2026-03"
    assert parse_date_from_slug("projectbeta-fdv-above-10b-one-day-after-launch-249") == "one-day-after-launch"
    print("  ✅ date parsing (8/8)")


# ─── NBA Finals (Mutual Exclusion) ─────────────────────────────

def test_nba_finals():
    r = parse_slug("will-the-boston-celtics-win-the-2026-nba-finals")
    assert r.resolution_basis == "win_outright", f"got {r.resolution_basis}"
    assert r.side_label == "Boston Celtics", f"got {r.side_label}"
    assert "nba" in r.group_template.lower() or "finals" in r.group_template.lower()
    assert r.date_scope == "2026"

    r = parse_slug("will-the-oklahoma-city-thunder-win-the-2026-nba-finals")
    assert r.side_label == "Oklahoma City Thunder"
    assert r.resolution_basis == "win_outright"

    print("  ✅ NBA Finals (2/2)")


# ─── Fed Rate Cuts (Threshold Chain) ──────────────────────────

def test_fed_rate_cuts():
    r = parse_slug("will-3-fed-rate-cuts-happen-in-2026")
    assert r.resolution_basis == "over_under"
    assert r.line_value == 3.0
    assert r.side_label == "3"
    assert r.underlying_entity == "Fed"

    r = parse_slug("will-no-fed-rate-cuts-happen-in-2026")
    assert r.line_value == 0.0
    assert r.side_label == "0"

    r = parse_slug("will-12-or-more-fed-rate-cuts-happen-in-2026")
    assert r.side_label == "12+"

    r = parse_slug("fed-rate-cut-by-april-2026-meeting")
    assert r.resolution_basis == "completed_by"

    print("  ✅ Fed Rate Cuts (4/4)")


# ─── Top Goal Scorer ──────────────────────────────────────────

def test_top_scorer():
    r = parse_slug("will-erling-haaland-be-the-top-goal-scorer-in-the-202526-english-premier-league-season")
    assert r.resolution_basis == "top_scorer"
    assert r.side_label == "Erling Haaland"
    assert r.underlying_entity == "EPL"

    r = parse_slug("will-player-l-be-the-top-goal-scorer-in-the-202526-english-premier-league-season")
    assert r.side_label == "Player L"

    print("  ✅ Top Scorer (2/2)")


# ─── Bitcoin Price Threshold ──────────────────────────────────

def test_btc_price():
    r = parse_slug("will-bitcoin-hit-150k-by-march-31-2026")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "BTC"
    assert r.line_value == 150_000.0
    assert r.date_scope == "2026-03-31"

    r = parse_slug("will-bitcoin-hit-150k-by-june-30-2026")
    assert r.line_value == 150_000.0
    assert r.date_scope == "2026-06-30"

    r = parse_slug("will-bitcoin-hit-1m-before-gta-vi-872")
    assert r.line_value == 1_000_000.0

    r = parse_slug("will-btc-hit-high-100-by-end-of-march-111")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "BTC"
    assert r.line_value == 100.0

    r = parse_slug("will-xrp-dip-to-1pt2-in-march-2026")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "XRP"
    assert r.line_value == 1.2
    assert r.date_scope == "2026-03"

    print("  ✅ BTC/threshold Price (5/5)")


# ─── Completed By (Politics) ─────────────────────────────────

def test_completed_by():
    r = parse_slug("starmer-out-by-june-30-2026-862-594-548")
    assert r.resolution_basis == "completed_by"
    assert r.side_label == "yes"
    assert r.date_scope == "2026-06-30"
    assert "Starmer" in r.underlying_entity

    r = parse_slug("examplecorp-completes-major-asset-sale-by-march-31-2026")
    assert r.underlying_entity == "Examplecorp"
    assert r.date_scope == "2026-03-31"

    r = parse_slug("taylor-swift-pregnant-before-2027")
    assert r.resolution_basis == "completed_by"
    assert "Taylor" in r.underlying_entity

    print("  ✅ Completed By (3/3)")


# ─── Projectx Market Cap ──────────────────────────────────────

def test_megaeth():
    r = parse_slug("projectx-market-cap-fdv-6b-one-day-after-launch-365-559-334-815-776-488-224-766")
    assert r.underlying_entity == "Projectx"
    assert r.line_value == 6_000_000_000.0

    r = parse_slug("projectx-market-cap-fdv-800m-one-day-after-launch-987-114-655")
    assert r.line_value == 800_000_000.0

    r = parse_slug("projectbeta-fdv-above-10b-one-day-after-launch-249-891-857")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "Projectbeta"
    assert r.group_template == "projectbeta_fdv"
    assert r.line_value == 10_000_000_000.0
    assert r.date_scope == "one-day-after-launch"

    print("  ✅ Projectx/FDV (3/3)")


# ─── First To ────────────────────────────────────────────────

def test_first_to():
    r = parse_slug("will-bitcoin-hit-60k-or-80k-first-965")
    assert r.resolution_basis == "first_to"
    assert r.underlying_entity == "BTC"

    r = parse_slug("will-ethereum-hit-1k-or-3k-first")
    assert r.resolution_basis == "first_to"
    assert r.underlying_entity == "ETH"

    print("  ✅ First To (2/2)")


def test_parse_threshold_market_semantics():
    r = parse_threshold_market("will-solana-reach-100-in-march-2026")
    assert r is not None
    assert r.underlying_entity == "SOL"
    assert r.group_template == "sol_price_usd"
    assert r.orientation == "above"
    assert r.line_value == 100.0
    assert r.date_scope == "2026-03"

    r = parse_threshold_market("projectbeta-fdv-above-10b-one-day-after-launch-249")
    assert r is not None
    assert r.underlying_entity == "Projectbeta"
    assert r.group_template == "projectbeta_fdv"
    assert r.orientation == "above"
    assert r.line_value == 10_000_000_000.0


# ─── Gas Price ───────────────────────────────────────────────

def test_gas_price():
    r = parse_slug("will-the-average-monthly-ethereum-gas-price-hit-10-gwei-before-2027")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "ETH"
    assert r.line_value == 10.0
    assert r.group_template == "eth_gas_price"

    print("  ✅ Gas Price (1/1)")


# ─── Volatility Index ────────────────────────────────────────

def test_volatility():
    r = parse_slug("will-the-bitcoin-volatility-index-hit-50-in-2026")
    assert r.resolution_basis == "over_under"
    assert r.underlying_entity == "BTC"
    assert r.line_value == 50.0
    assert r.group_template == "btc_vol_index"

    print("  ✅ Volatility (1/1)")


# ─── Standalone (Catch-all) ──────────────────────────────────

def test_standalone():
    r = parse_slug("kraken-ipo-in-2025")
    assert r.resolution_basis in ("yes_no", "completed_by"), f"got {r.resolution_basis}"
    assert r.date_scope == "2025"

    r = parse_slug("will-no-country-leave-nato-by-june-30-2026")
    assert r.resolution_basis in ("yes_no", "completed_by"), f"got {r.resolution_basis}"

    print("  ✅ Standalone (2/2)")


# ─── Run All ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running market_parser tests...\n")
    test_number_parsing()
    test_date_parsing()
    test_nba_finals()
    test_fed_rate_cuts()
    test_top_scorer()
    test_btc_price()
    test_completed_by()
    test_megaeth()
    test_first_to()
    test_gas_price()
    test_volatility()
    test_standalone()
    print("\n🎉 All tests passed!")
