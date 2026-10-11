"""Liveness checks for a project's off-chain presence.

* Website: fetch the homepage, strip tags, look for product language and
  links to docs / GitHub / a launch-app. A parked or template page with a
  title and a buy button reads very differently from a real product site.
* Telegram: t.me's public preview page shows member/subscriber counts,
  which is a real (if gameable) community-size number with no API key.

X/Twitter follower counts need a paid API, so X is scored on presence
only (and only if it links to an account, not a single tweet).
"""

from __future__ import annotations

import html
import logging
import re

import requests

from ..models import WebsiteProbe
from ..utility import PRODUCT_EVIDENCE
from .http import make_session

logger = logging.getLogger(__name__)

MAX_BYTES = 400_000
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_TG_EXTRA_RE = re.compile(r'tgme_page_extra">\s*([\d\s,.]+)\s*(members|subscribers)', re.I)


class WebProbe:
    def __init__(self, session: requests.Session | None = None, timeout: float = 10) -> None:
        self.session = session or make_session()
        self.timeout = timeout

    def website(self, url: str) -> tuple[WebsiteProbe, str]:
        """Returns (probe, visible_text). visible_text feeds the utility classifier."""
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True, stream=True)
            raw = resp.raw.read(MAX_BYTES, decode_content=True) if resp.raw is not None else resp.content[:MAX_BYTES]
            body = raw.decode(resp.encoding or "utf-8", errors="replace")
        except (requests.RequestException, OSError, ValueError) as exc:
            return WebsiteProbe(reachable=False, error=type(exc).__name__), ""
        return parse_website(body, resp.status_code)

    def telegram_members(self, url: str) -> int | None:
        if "t.me/" not in url or "/+" in url or "joinchat" in url:
            return None  # private invite links have no public preview
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return None
        return parse_telegram_members(resp.text) if resp.ok else None


def parse_website(body: str, status: int) -> tuple[WebsiteProbe, str]:
    title_match = _TITLE_RE.search(body)
    title = html.unescape(title_match.group(1)).strip()[:120] if title_match else ""
    hrefs = [h.lower() for h in _HREF_RE.findall(body)]
    text = html.unescape(_TAG_RE.sub(" ", body))
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    probe = WebsiteProbe(
        reachable=200 <= status < 400,
        status=status,
        title=title,
        text_length=len(text),
        utility_hits=[kw for kw in PRODUCT_EVIDENCE if kw in lowered],
        has_docs_link=any("gitbook" in h or "/docs" in h or "docs." in h or "whitepaper" in h for h in hrefs),
        has_github_link=any("github.com/" in h for h in hrefs),
        has_app_link=any(h.startswith("https://app.") or "/app" in h or "launch app" in lowered for h in hrefs),
    )
    return probe, f"{title} {text[:20_000]}"


def parse_telegram_members(body: str) -> int | None:
    match = _TG_EXTRA_RE.search(body)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None
