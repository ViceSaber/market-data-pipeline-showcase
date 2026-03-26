"""
market_parser — Polymarket slug → 结构化字段

从 market slug 和 question 中提取：
- resolution_basis: 决算依据类型
- group_template: 分组模板
- underlying_entity: 标的实体
- line_value: 阈值/数值线
- side_label: 方向标签
- date_scope: 日期范围

详见 MARKET_PARSER_SPEC.md
"""

import re
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime


@dataclass
class ParsedMarket:
    resolution_basis: str
    group_template: str
    underlying_entity: str
    line_value: Optional[float] = None
    side_label: str = "yes"
    date_scope: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class ThresholdMarket:
    underlying_entity: str
    group_template: str
    line_value: float
    orientation: str
    date_scope: Optional[str] = None

    @property
    def semantic_group(self) -> str:
        return "|".join([
            self.group_template or "unknown_template",
            self.underlying_entity or "unknown_entity",
            self.date_scope or "unknown_date",
        ]).lower()


# ─── Number Parsing ─────────────────────────────────────────────

_SUFFIX_MAP = {
    'k': 1_000,
    'm': 1_000_000,
    'b': 1_000_000_000,
}

def parse_number(s: str) -> Optional[float]:
    """
    Extract a numeric value from a string.
    Handles: 150k, 1m, 6b, 800m, 1pt5b, 52.5, 10
    """
    # Pattern: digits with optional suffix (k/m/b) and optional decimal
    # Also handles "1pt5b" → 1.5b
    s = s.lower().strip()

    # Handle "1pt5b" style
    pt_match = re.search(r'(\d+)pt(\d+)([kmb]?)', s)
    if pt_match:
        whole = int(pt_match.group(1))
        frac = int(pt_match.group(2))
        suffix = pt_match.group(3)
        val = whole + frac / (10 ** len(str(frac)))
        if suffix in _SUFFIX_MAP:
            val *= _SUFFIX_MAP[suffix]
        return val

    # Handle "150k", "1m", "6b", "800m", or plain numbers like "52.5", "150000"
    match = re.search(r'(\d+\.?\d*)\s*([kmb])\b', s)
    if match:
        val = float(match.group(1))
        suffix = match.group(2)
        return val * _SUFFIX_MAP[suffix]

    # Plain number
    match = re.search(r'\$(\d+[\d,.]*)', s)
    if match:
        return float(match.group(1).replace(',', ''))

    match = re.search(r'(\d+\.\d+)', s)
    if match:
        return float(match.group(1))

    match = re.search(r'\b(\d+)\b', s)
    if match:
        return float(match.group(1))

    return None


# ─── Date Parsing ───────────────────────────────────────────────

_THRESHOLD_ACTION_RE = re.compile(
    r'(?:^|-)(reach|dip-to|hit-high|hit-low|above|below|hit)-(\d[\d,.]*(?:pt\d+|\.\d+)?[kmb]?)'
)

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

def parse_date_from_slug(slug: str) -> Optional[str]:
    """
    Extract date scope from slug.
    Returns ISO date string, month scope, season label, or special scope string.
    """
    slug_lower = slug.lower()
    current_year = datetime.now().year

    # "by-june-30-2026" or "by-december-31-2026"
    m = re.search(r'by-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(\d{4})', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"

    # "on-march-24-2026" / "on-march-24"
    m = re.search(r'on-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})(?:-(\d{4}))?', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else current_year
        return f"{year:04d}-{month:02d}-{day:02d}"

    # "in-march-2026"
    m = re.search(r'in-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{4})', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        year = int(m.group(2))
        return f"{year:04d}-{month:02d}"

    # "by-end-of-march" / "end-of-march"
    m = re.search(r'(?:by-)?end-of-(january|february|march|april|may|june|july|august|september|october|november|december)', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        return f"{current_year:04d}-{month:02d}-end"

    # Special launch-relative scope used by FDV markets
    if 'one-day-after-launch' in slug_lower:
        return 'one-day-after-launch'

    # "by-june-30" (no year, assume current year)
    m = re.search(r'by-(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})\b', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        return f"{current_year:04d}-{month:02d}-{day:02d}"

    # "in-2026" or "before-2027"
    m = re.search(r'(?:in|before|after)-(\d{4})', slug_lower)
    if m:
        return m.group(1)

    # "before-january" (just month)
    m = re.search(r'before-(january|february|march|april|may|june|july|august|september|october|november|december)(?:-(\d{4}))?', slug_lower)
    if m:
        month = MONTH_MAP[m.group(1)]
        year = int(m.group(2)) if m.group(2) else current_year
        return f"{year:04d}-{month:02d}-01"

    # "2025-26 season" or "202526"
    m = re.search(r'(\d{4})[-–](\d{2})', slug_lower)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # "202526" compact season
    m = re.search(r'\b(20\d{2})(\d{2})\b', slug_lower)
    if m:
        y1 = int(m.group(1))
        y2 = int(m.group(2))
        if y2 == y1 % 100 + 1:  # likely a season
            return f"{y1}-{m.group(2)}"

    return None


# ─── Category Detectors ────────────────────────────────────────

def _is_win_outright(slug: str, question: str) -> bool:
    """Detect: will-X-win-the-Y patterns"""
    patterns = [
        r'will-.+-win-the-',
        r'win-the-\d{4}-nba-finals',
        r'win-the-.*-premier-league',
        r'win-the-.*-conference',
        r'win-the-.*-division',
        r'win-the-\d{4}-.*-presidential-election',
        r'win-the-1st-round',
        r'win-the-\d{4}-nhl-stanley-cup',
        r'win-the-.*-election',
    ]
    slug_lower = slug.lower()
    for p in patterns:
        if re.search(p, slug_lower):
            return True
    return False


def _is_top_scorer(slug: str, question: str) -> bool:
    """Detect: top-goal-scorer, top-batter, most-sixes patterns"""
    patterns = [
        r'top-goal-scorer',
        r'top-scorer',
        r'top-batter',
        r'most-sixes',
        r'most-wickets',
        r'golden-ball',
        r'golden-boot',
    ]
    slug_lower = slug.lower()
    for p in patterns:
        if re.search(p, slug_lower):
            return True
    return False


def _is_price_threshold(slug: str, question: str) -> bool:
    """Detect threshold-style markets (price, FDV, hit/reach/above/below)."""
    patterns = [
        r'(?:reach|dip-to|hit-high|hit-low|above|below|hit)-\d[\d,.]*(?:pt\d+|\.\d+)?[kmb]?(?:-by|-in|-before|-on|-one-day)',
        r'hit-\d+\.?\d*[kmb]?(?:-by|-in|-before)',
        r'hit-\$?\d+.*(?:-by|-in|-before)',
        r'volatility-index-hit-\d+',
        r'gas-price-hit-\d+',
        r'kimchi-premium-hit-\d+',
        r'market-cap.*-hit-\d+',
        r'market-cap.*[km]b-one-day',
        r'-fdv-(?:above|below)-\d[\d,.]*(?:pt\d+|\.\d+)?[kmb]?',
        r'hit-\d+-gwei',  # gas price in gwei
    ]
    slug_lower = slug.lower()
    for p in patterns:
        if re.search(p, slug_lower):
            return True
    return False


def _is_first_to(slug: str, question: str) -> bool:
    """Detect: X-or-Y-first patterns"""
    patterns = [
        r'-or-.*-first',
        r'first-to-',
    ]
    slug_lower = slug.lower()
    for p in patterns:
        if re.search(p, slug_lower):
            return True
    return False


def _is_completed_by(slug: str, question: str) -> bool:
    """Detect: completed-by-date patterns"""
    patterns = [
        r'-out-by-',
        r'-called-by-',
        r'-held-by-',
        r'-released-by-',
        r'-sells?-any-.*-by-',
        r'-sells?-any-.*-in-',
        r'-unban.*-by-',
        r'-pregnant-before',
        r'-ceasefire-before',
        r'-released-before',
        r'-invades.*-before',
        r'-eliminates.*-before',
        r'-unban.*-before',
        r'-fights.*-in-',
        r'-clash-by-',
        r'-ipo-by-',
        r'-ipo-in-',
    ]
    slug_lower = slug.lower()
    for p in patterns:
        if re.search(p, slug_lower):
            return True
    return False


def _is_fed_rate_cuts(slug: str, question: str) -> bool:
    """Special case: Fed rate cut count markets"""
    return bool(re.search(r'fed-rate-cut(s)?-happen-in-\d{4}', slug.lower())) or \
           bool(re.search(r'\d+-fed-rate-cut', slug.lower())) or \
           bool(re.search(r'no-fed-rate-cut', slug.lower()))


def _is_fed_rate_meeting(slug: str, question: str) -> bool:
    """Special case: Fed rate cut by specific meeting date"""
    return bool(re.search(r'fed-rate-cut-by-.*-meeting', slug.lower()))


# ─── Team/Entity Extractors ────────────────────────────────────

_NBA_TEAMS = {
    'atlanta-hawks', 'boston-celtics', 'brooklyn-nets', 'charlotte-hornets',
    'chicago-bulls', 'cleveland-cavaliers', 'dallas-mavericks', 'denver-nuggets',
    'detroit-pistons', 'golden-state-warriors', 'houston-rockets', 'indiana-pacers',
    'los-angeles-clippers', 'los-angeles-lakers', 'memphis-grizzlies', 'miami-heat',
    'milwaukee-bucks', 'minnesota-timberwolves', 'new-orleans-pelicans', 'new-york-knicks',
    'oklahoma-city-thunder', 'orlando-magic', 'philadelphia-76ers', 'phoenix-suns',
    'portland-trail-blazers', 'sacramento-kings', 'san-antonio-spurs', 'toronto-raptors',
    'utah-jazz', 'washington-wizards',
}

_SPORT_LEAGUES = {
    'nba-finals': 'NBA Finals',
    'nba-western-conference': 'NBA Western Conference',
    'nba-eastern-conference': 'NBA Eastern Conference',
    'english-premier-league': 'English Premier League',
    'la-liga': 'La Liga',
    'serie-a': 'Serie A',
    'ligue-1': 'Ligue 1',
    'bundesliga': 'Bundesliga',
    'nhl-stanley-cup': 'NHL Stanley Cup',
    'pac-division': 'NBA Pacific Division',
}


def _extract_team_from_slug(slug: str) -> str:
    """Extract team name from 'will-{team}-win-the-...' slug"""
    slug_lower = slug.lower()
    for team in _NBA_TEAMS:
        if team in slug_lower:
            return team.replace('-', ' ').title()

    # Generic: will-{name}-win-the
    m = re.match(r'will-(.+?)-(?:win|finish|be-relegated)', slug_lower)
    if m:
        return m.group(1).replace('-', ' ').title()

    return "unknown"


def _extract_entity_from_completed_by(slug: str) -> str:
    """Extract entity from 'X-out-by-date' slug"""
    slug_lower = slug.lower()

    # "{name}-out-by-..." or "{name}-sells-any-..."
    m = re.match(r'(.+?)-(?:out|called|held|released|sells?-any|ipo|pregnant|ceasefire|invades|eliminates|fights|clash)-', slug_lower)
    if m:
        entity = m.group(1)
        # Remove trailing hash numbers like "-862-594-548"
        entity = re.sub(r'-\d{3,}(-\d+)*$', '', entity)
        return entity.replace('-', ' ').title()

    return "unknown"


def _extract_crypto_asset(slug: str) -> str:
    """Extract crypto asset from slug"""
    slug_lower = slug.lower()
    if 'megaeth' in slug_lower:
        return 'MegaETH'
    if 'bitcoin' in slug_lower or 'btc' in slug_lower:
        return 'BTC'
    if 'ethereum' in slug_lower or 'eth' in slug_lower:
        return 'ETH'
    if 'solana' in slug_lower or 'sol' in slug_lower:
        return 'SOL'
    if 'ripple' in slug_lower or 'xrp' in slug_lower:
        return 'XRP'
    return 'unknown'


def _extract_threshold_value(slug: str) -> Optional[float]:
    slug_lower = slug.lower()
    m = _THRESHOLD_ACTION_RE.search(slug_lower)
    if not m:
        return None
    return parse_number(m.group(2))


def _extract_threshold_subject(slug: str) -> str:
    slug_lower = slug.lower()
    m = re.search(r'^(?:will-)?(.+?)-(?:reach|dip-to|hit-high|hit-low|above|below|hit)-', slug_lower)
    if not m:
        return 'unknown'
    subject = m.group(1)
    subject = re.sub(r'-\d{3,}(-\d+)*$', '', subject)
    return subject.strip('-') or 'unknown'


def parse_threshold_market(slug: str, question: str = "") -> Optional[ThresholdMarket]:
    """Parse threshold-style markets for arb grouping/comparison.

    Returns None for non-threshold markets.
    """
    if not _is_price_threshold(slug, question):
        return None

    slug_lower = slug.lower()
    m = _THRESHOLD_ACTION_RE.search(slug_lower)
    if not m:
        return None

    action = m.group(1)
    line_value = parse_number(m.group(2))
    if line_value is None:
        return None

    orientation = 'above' if action in {'reach', 'hit-high', 'above', 'hit'} else 'below'
    date_scope = parse_date_from_slug(slug)

    if '-fdv-' in slug_lower:
        project = _extract_threshold_subject(slug)
        if project.endswith('-fdv'):
            project = project[:-4]
        entity = project.replace('-', ' ').title() or 'Unknown'
        template = f"{project.replace('-', '_')}_fdv" if project else 'unknown_fdv'
        return ThresholdMarket(
            underlying_entity=entity,
            group_template=template,
            line_value=line_value,
            orientation=orientation,
            date_scope=date_scope or 'unknown',
        )

    asset = _extract_crypto_asset(slug)
    if asset != 'unknown':
        entity = asset
        template = f"{asset.lower()}_price_usd"
    else:
        subject = _extract_threshold_subject(slug)
        entity = subject.replace('-', ' ').title() if subject != 'unknown' else 'Unknown'
        template = f"{subject.replace('-', '_')}_threshold" if subject != 'unknown' else 'unknown_threshold'
    return ThresholdMarket(
        underlying_entity=entity,
        group_template=template,
        line_value=line_value,
        orientation=orientation,
        date_scope=date_scope or 'unknown',
    )


# ─── Main Parser ────────────────────────────────────────────────


def parse_slug(slug: str, question: str = "") -> ParsedMarket:
    """
    Parse a market slug into structured fields.

    Args:
        slug: The market slug (e.g., "will-bitcoin-hit-150k-by-march-31-2026")
        question: The market question (optional, for additional context)

    Returns:
        ParsedMarket with resolution_basis, group_template, underlying_entity,
        line_value, side_label, date_scope
    """
    slug_lower = slug.lower()
    date_scope = parse_date_from_slug(slug)

    # ── Fed Rate Cuts (special case) ──
    if _is_fed_rate_cuts(slug, question):
        if 'no-fed-rate-cut' in slug_lower:
            line = 0.0
            side = '0'
        elif '12-or-more' in slug_lower:
            line = 12.0
            side = '12+'
        else:
            m = re.search(r'(\d+)-fed-rate-cut', slug_lower)
            line = float(m.group(1)) if m else None
            side = str(int(line)) if line is not None else 'yes'
        return ParsedMarket(
            resolution_basis='over_under',
            group_template='fed_rate_cuts_2026',
            underlying_entity='Fed',
            line_value=line,
            side_label=side,
            date_scope=date_scope,
        )

    if _is_fed_rate_meeting(slug, question):
        m = re.search(r'fed-rate-cut-by-(.+?)(?:-\d+)*-meeting', slug_lower)
        meeting = m.group(1) if m else 'unknown'
        return ParsedMarket(
            resolution_basis='completed_by',
            group_template='fed_meeting_2026',
            underlying_entity='Fed',
            side_label='yes',
            date_scope=meeting,
        )

    # ── Win Outright (sports/politics) ──
    if _is_win_outright(slug, question):
        team = _extract_team_from_slug(slug)
        league = 'unknown'
        for key, name in _SPORT_LEAGUES.items():
            if key in slug_lower:
                league = name
                break

        if 'presidential-election' in slug_lower:
            m = re.search(r'(?:the-)?(\d{4})-(.+?)-presidential-election', slug_lower)
            if m:
                year = m.group(1)
                country = m.group(2).replace('-', ' ').title()
                return ParsedMarket(
                    resolution_basis='win_outright',
                    group_template=f'{country.lower().replace(" ", "_")}_president_{year}',
                    underlying_entity=f'{country} Presidential {year}',
                    side_label=team,
                    date_scope=year,
                )

        event_key = re.sub(r'^will-the-', '', slug_lower)
        event_key = re.sub(r'-\d{3,}(-\d+)*$', '', event_key)
        year_match = re.search(r'(\d{4})', event_key)
        year_str = year_match.group(1) if year_match else ''

        return ParsedMarket(
            resolution_basis='win_outright',
            group_template=f'{league.lower().replace(" ", "_")}_winner',
            underlying_entity=league,
            side_label=team,
            date_scope=year_str,
        )

    # ── Top Scorer ──
    if _is_top_scorer(slug, question):
        m = re.search(r'will-(.+?)-be-the-top-goal-scorer', slug_lower)
        if not m:
            m = re.search(r'will-(.+?)-be-the-top', slug_lower)
        player = m.group(1).replace('-', ' ').title() if m else 'unknown'

        league = 'unknown'
        league_patterns = {
            'english-premier-league': 'EPL',
            'la-liga': 'La Liga',
            'serie-a': 'Serie A',
            'ligue-1': 'Ligue 1',
            'bundesliga': 'Bundesliga',
        }
        for key, name in league_patterns.items():
            if key in slug_lower:
                league = name
                break

        season = parse_date_from_slug(slug) or 'unknown'
        return ParsedMarket(
            resolution_basis='top_scorer',
            group_template=f'{league.lower().replace(" ", "_")}_top_scorer',
            underlying_entity=league,
            side_label=player,
            date_scope=season,
        )

    # ── Gas Price (must check before generic price threshold) ──
    if 'gas-price' in slug_lower:
        m = re.search(r'hit-(\d+)-gwei', slug_lower)
        line_val = float(m.group(1)) if m else parse_number(slug)
        return ParsedMarket(
            resolution_basis='over_under',
            group_template='eth_gas_price',
            underlying_entity='ETH',
            line_value=line_val,
            side_label='yes',
            date_scope=date_scope,
        )

    # ── Volatility Index ──
    if 'volatility-index' in slug_lower:
        asset = _extract_crypto_asset(slug)
        line_val = parse_number(slug)
        return ParsedMarket(
            resolution_basis='over_under',
            group_template=f'{asset.lower()}_vol_index',
            underlying_entity=asset,
            line_value=line_val,
            side_label='yes',
            date_scope=date_scope,
        )

    # ── Price / FDV Threshold ──
    if _is_price_threshold(slug, question):
        threshold = parse_threshold_market(slug, question)
        line_val = threshold.line_value if threshold else None

        if '-or-' in slug_lower and '-first' in slug_lower:
            asset = _extract_crypto_asset(slug)
            return ParsedMarket(
                resolution_basis='first_to',
                group_template=f'{asset.lower()}_first_to',
                underlying_entity=asset,
                line_value=line_val,
                side_label='yes',
                date_scope=date_scope,
            )

        if threshold:
            return ParsedMarket(
                resolution_basis='over_under',
                group_template=threshold.group_template,
                underlying_entity=threshold.underlying_entity,
                line_value=threshold.line_value,
                side_label='yes',
                date_scope=threshold.date_scope,
            )

        asset = _extract_crypto_asset(slug)
        return ParsedMarket(
            resolution_basis='over_under',
            group_template=f'{asset.lower()}_price_usd',
            underlying_entity=asset,
            line_value=line_val,
            side_label='yes',
            date_scope=date_scope,
        )

    # ── First To ──
    if _is_first_to(slug, question):
        asset = _extract_crypto_asset(slug)
        options = re.findall(r'(\d+[\d.]*[kmb]?)', slug_lower)
        return ParsedMarket(
            resolution_basis='first_to',
            group_template=f'{asset.lower()}_first_to',
            underlying_entity=asset,
            side_label=options[0] if options else 'yes',
        )

    # ── Completed By ──
    if _is_completed_by(slug, question):
        entity = _extract_entity_from_completed_by(slug)

        if 'microstrategy' in slug_lower:
            return ParsedMarket(
                resolution_basis='completed_by',
                group_template='microstrategy_sells_btc',
                underlying_entity='MicroStrategy',
                side_label='yes',
                date_scope=date_scope,
            )

        return ParsedMarket(
            resolution_basis='completed_by',
            group_template=f'{entity.lower().replace(" ", "_")}_by_date',
            underlying_entity=entity,
            side_label='yes',
            date_scope=date_scope,
        )

    # ── Market Cap (MegaETH etc.) ──
    if 'market-cap' in slug_lower and ('one-day' in slug_lower or 'hit' in slug_lower):
        asset = _extract_crypto_asset(slug)
        line_val = parse_number(slug)
        return ParsedMarket(
            resolution_basis='over_under',
            group_template=f'{asset.lower()}_market_cap',
            underlying_entity=asset,
            line_value=line_val,
            side_label='yes',
            date_scope=date_scope,
        )

    # ── Catch-all: yes_no standalone ──
    entity = slug_lower
    entity = re.sub(r'^will-?', '', entity)
    entity = re.sub(r'^the-', '', entity)
    entity = re.sub(r'-(?:in|by|before|after|on)-\d{4}.*$', '', entity)
    entity = re.sub(r'-\d{3,}(-\d+)*$', '', entity)
    entity = entity.replace('-', ' ').title()[:60]

    return ParsedMarket(
        resolution_basis='yes_no',
        group_template='standalone',
        underlying_entity=entity,
        side_label='yes',
        date_scope=date_scope,
    )


# ─── Backward-compat alias ─────────────────────────────────────
def parse_market(slug: str, question: str = "") -> dict:
    """Alias for parse_slug, returns dict instead of dataclass."""
    return parse_slug(slug, question).to_dict()
