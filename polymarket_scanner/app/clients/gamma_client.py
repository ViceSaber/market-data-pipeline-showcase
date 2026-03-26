"""Gamma API client — all external API calls go through here."""

import requests

from config.settings import GAMMA_API_URL
from app.clients.rate_limiter import get_limiter


def fetch_events(active: bool = True, closed: bool = False,
                 limit: int = 100, offset: int = 0) -> list[dict]:
    """Fetch a page of events from the Gamma API."""
    get_limiter().acquire()
    params = {"limit": limit, "offset": offset}
    if active is not None:
        params["active"] = str(active).lower()
    if closed is not None:
        params["closed"] = str(closed).lower()
    resp = requests.get(f"{GAMMA_API_URL}/events", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_by_slug_batch(slugs: list[str]) -> list[dict]:
    """Fetch markets by slug list (Gamma supports multiple slug params)."""
    if not slugs:
        return []
    get_limiter().acquire()
    resp = requests.get(
        f"{GAMMA_API_URL}/markets",
        params=[("slug", s) for s in slugs],
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
