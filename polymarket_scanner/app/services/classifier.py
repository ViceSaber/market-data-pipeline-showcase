"""Market classifier — categorize Polymarket markets by topic and liquidity.

Categories: crypto, sports, geopolitics, politics, macro, ipo, energy, entertainment, other
Liquidity tiers: hot (>$100K), warm ($10K-$100K), cold (<$10K)
"""


def classify_category(slug: str, underlying_entity: str = "", group_template: str = "") -> str:
    """Classify a market into a category.

    Uses underlying_entity first (more reliable), falls back to slug keywords.
    """
    entity = (underlying_entity or "").lower()
    slug_l = (slug or "").lower()
    template = (group_template or "").lower()

    # Crypto — from underlying_entity
    if any(k in entity for k in ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype", "bitcoin", "ethereum", "crypto"]):
        return "crypto"

    # Sports — from underlying_entity or slug
    if any(k in entity for k in ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "hockey",
                                   "tennis", "golf", "ufc", "boxing", "mma", "premier league", "la liga",
                                   "champions league", "world cup", "olymp", "ppa tour", "cricket",
                                   "rugby", "formula", "f1", "motogp"]):
        return "sports"
    if any(k in slug_l for k in ["nba-", "nfl-", "mlb-", "nhl-", "soccer-", "basketball-",
                                   "tennis-", "golf-", "ufc-", "boxing-", "mma-", "premier-league",
                                   "world-cup-", "olymp-", "points-in-their",
                                   "league-championship", "super-bowl"]):
        return "sports"

    # Geopolitics
    if any(k in entity for k in ["russia", "ukraine", "iran", "israel", "gaza", "china", "taiwan",
                                   "war", "ceasefire", "military", "nato", "sanction"]):
        return "geopolitics"
    if any(k in slug_l for k in ["russia-", "ukraine-", "iran-", "israel-", "gaza-", "china-", "taiwan-",
                                   "war-", "ceasefire", "military-", "nato-", "sanction",
                                   "geopolit", "nuclear", "invasion", "troops"]):
        return "geopolitics"

    # Politics / elections — must come BEFORE sports (to catch "trump-win-the-2028-election")
    if any(k in entity for k in ["election", "president", "prime minister", "congress", "senate",
                                   "parliament", "vote", "political", "trump", "biden"]):
        return "politics"
    if any(k in slug_l for k in ["election", "president-", "prime-minister", "congress-", "senate-",
                                   "parliament", "vote-", "political", "trump-", "biden-",
                                   "democrat", "republican", "2028-us", "who-will-be-the-next",
                                   "who-will-be-president", "next-president"]):
        return "politics"

    # IPO / company
    if any(k in entity for k in ["ipo", "public offering", "spac", "listing", "company"]):
        return "ipo"
    if any(k in slug_l for k in ["ipo-", "public-offering", "spac-", "listing-", "direct-listing",
                                   "go-public"]):
        return "ipo"

    # Macro / economy
    if any(k in entity for k in ["fed", "rate", "inflation", "gdp", "recession", "economy",
                                   "treasury", "bond", "stock", "s&p", "nasdaq"]):
        return "macro"
    if any(k in slug_l for k in ["fed-", "rate-", "inflation", "gdp-", "recession", "economy-",
                                   "treasury-", "bond-", "stock-market", "s&p-", "nasdaq-",
                                   "interest-rate", "fomc"]):
        return "macro"

    # Energy / commodities
    if any(k in entity for k in ["oil", "crude", "gas", "energy", "opec", "gold", "silver"]):
        return "energy"
    if any(k in slug_l for k in ["oil-", "crude-", "gas-", "energy-", "opec-", "gold-", "silver-",
                                   "barrel-", "pipeline"]):
        return "energy"

    # Entertainment
    if any(k in slug_l for k in ["oscar", "grammy", "emmy", "movie", "film", "celebrity",
                                   "elon-musk", "tweet", "big-brother", "reality"]):
        return "entertainment"

    # Crypto price predictions (from slug, not underlying_entity)
    if any(k in slug_l for k in ["bitcoin", "ethereum", "btc", "eth", "crypto", "solana",
                                   "token", "defi", "memecoin", "fdv", "mcap", "market-cap",
                                   "price-above", "price-below"]):
        return "crypto"

    return "other"


def classify_liquidity(volume_24h: float | None, liquidity: float | None) -> str:
    """Classify market into liquidity tier."""
    vol = volume_24h or 0
    liq = liquidity or 0

    # Use whichever metric is higher as the classification hint
    effective = max(vol, liq)

    if effective >= 100_000:
        return "hot"
    elif effective >= 10_000:
        return "warm"
    else:
        return "cold"
