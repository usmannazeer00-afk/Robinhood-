"""Shared HTTP session with a polite User-Agent and small retry on 429/5xx."""

from __future__ import annotations

import time
from typing import Any

import requests

USER_AGENT = "sol-utility-scout/0.1 (+research bot)"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def get_json(session: requests.Session, url: str, timeout: float = 10, retries: int = 2, **kwargs: Any) -> Any:
    """GET -> parsed JSON, retrying briefly on rate limits and server errors.
    Raises requests.RequestException once retries are exhausted."""
    for attempt in range(retries + 1):
        resp = session.get(url, timeout=timeout, **kwargs)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    return None  # unreachable
