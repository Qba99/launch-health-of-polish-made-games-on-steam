"""Shared helpers for Steam's public endpoints.

Everything that talks to Steam goes through `get_json`, which
- keeps at least PAUSE_SECONDS between calls (the project's rate-limit rule),
- retries with exponential back-off on HTTP 429, 5xx and network errors,
- fails fast on other errors (e.g. 400 = a bad parameter), so mistakes surface.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import requests

# --- Endpoints -------------------------------------------------------------
# Official reviews endpoint (replaces the deprecated store.steampowered.com/appreviews).
# Docs: https://partner.steamgames.com/doc/webapi/IUserReviewsService
REVIEWS_URL = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
# Deprecated endpoint, only used by test_call.py as a fallback diagnosis.
LEGACY_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
# Public store endpoints (undocumented but widely used) for app IDs and release dates.
STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

# --- Politeness settings ---------------------------------------------------
PAUSE_SECONDS = 1.0      # minimum gap between any two calls
MAX_RETRIES = 5          # attempts per call before giving up
MAX_BACKOFF_SECONDS = 120
TIMEOUT_SECONDS = 30
USER_AGENT = "steam-launch-health/1.0 (personal data-analysis portfolio project)"

log = logging.getLogger(__name__)
_last_call = 0.0


class SteamAPIError(RuntimeError):
    """Raised when Steam keeps failing or answers with a non-retryable error."""


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def _pace() -> None:
    """Sleep so that consecutive calls are at least PAUSE_SECONDS apart."""
    global _last_call
    wait = PAUSE_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def get_json(session: requests.Session, url: str, params: dict | None = None) -> dict:
    """GET a URL and return parsed JSON, with pacing and retries."""
    last_error = "no attempt made"
    for attempt in range(1, MAX_RETRIES + 1):
        _pace()
        retry_after = None
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last_error = "HTTP 200 but the body was not JSON"
            elif resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
            else:
                raise SteamAPIError(
                    f"HTTP {resp.status_code} for {resp.url} -> {resp.text[:300]}"
                )

        if attempt == MAX_RETRIES:
            break
        backoff = min(MAX_BACKOFF_SECONDS, 2 ** attempt)  # 2, 4, 8, 16 ...
        if retry_after and retry_after.isdigit():
            backoff = max(backoff, int(retry_after))
        log.warning("Attempt %d/%d failed (%s); retrying in %ds",
                    attempt, MAX_RETRIES, last_error, backoff)
        time.sleep(backoff)

    raise SteamAPIError(f"Giving up on {url} after {MAX_RETRIES} attempts: {last_error}")


# --- Store lookups ---------------------------------------------------------
def search_app(session: requests.Session, name: str) -> list[dict]:
    """Return store search hits as [{'id': 123, 'name': '...'}, ...]."""
    data = get_json(session, STORE_SEARCH_URL, {"term": name, "l": "english", "cc": "us"})
    return [{"id": item["id"], "name": item["name"]} for item in data.get("items", [])]


def find_app_id(session: requests.Session, name: str) -> tuple[int | None, list[dict]]:
    """Pick the hit whose name matches exactly (case-insensitive); else None."""
    hits = search_app(session, name)
    for hit in hits:
        if hit["name"].casefold() == name.casefold():
            return hit["id"], hits
    return None, hits


def get_app_details(session: requests.Session, app_id: int) -> dict | None:
    """Return the store 'data' block for an app (name, release_date, genres, ...)."""
    data = get_json(session, APP_DETAILS_URL, {"appids": app_id, "l": "english", "cc": "us"})
    block = data.get(str(app_id), {})
    return block.get("data") if block.get("success") else None


_DATE_FORMATS = ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%d %b %Y", "%b %d %Y")


def parse_release_date(text: str | None) -> date | None:
    """Parse Steam's release-date text ('4 Mar, 2024' or 'Mar 4, 2024')."""
    if not text:
        return None
    cleaned = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None
