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
from urllib.parse import urlparse

import requests

from ..models import WebsiteProbe
from ..utility import PRODUCT_EVIDENCE
from .http import make_session

logger = logging.getLogger(__name__)

MAX_BYTES = 400_000
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
# A link to an app: an app.* subdomain or an /app path segment -- not any
# href containing "/app" (that matched assets/apple-touch-icon.png and css/app.css).
_APP_LINK_RE = re.compile(r"^https?://app\.|/app(?:[/?#]|$)|^/?app(?:[/?#]|$)")
_API_LINK_RE = re.compile(r"^https?://api\.|/api(?:[/?#]|$)|^/?api(?:[/?#]|$)")
_BOT_LINK_RE = re.compile(r"t\.me/[a-z0-9_]+bot(?:[/?#]|$)")
# Links that prove nothing about *this* project: GitHub orgs of the
# platforms tokens launch on, and docs/papers hosted by someone else.
# ("Phasepunk" linked pump.fun's own GitHub docs and a DeepMind paper.)
PLATFORM_GITHUB_ORGS = {
    "pump-fun", "solana-labs", "solana-foundation", "anza-xyz", "raydium-io", "metaplex-foundation",
    "jup-ag", "coral-xyz", "orca-so", "meteoraag", "solana-developers", "helius-labs", "openai", "anthropics",
}
DOCS_HOSTS = ("gitbook.io", "gitbook.com", "notion.site", "mintlify.app", "readme.io", "readthedocs.io")
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
        return parse_website(body, resp.status_code, resp.url or url)

    def telegram_members(self, url: str) -> int | None:
        if "t.me/" not in url or "/+" in url or "joinchat" in url:
            return None  # private invite links have no public preview
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return None
        return parse_telegram_members(resp.text) if resp.ok else None


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


def _root(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def is_project_github(href: str) -> bool:
    match = re.search(r"github\.com/([^/?#]+)", href)
    return bool(match) and match.group(1).lower() not in PLATFORM_GITHUB_ORGS


def is_project_docs(href: str, site_host: str) -> bool:
    """A docs link counts when it's relative or on the project's own domain, or on a docs host (gitbook...)."""
    if not any(k in href for k in ("gitbook", "/docs", "docs.", "whitepaper", "litepaper")):
        return False
    host = _host(href)
    if not host:  # relative link -> the project's own site
        return True
    if "github.com" in host:
        return False  # GitHub links are judged by is_project_github
    return any(host.endswith(d) for d in DOCS_HOSTS) or (bool(site_host) and _root(host) == _root(site_host))


def parse_website(body: str, status: int, url: str = "") -> tuple[WebsiteProbe, str]:
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
        has_docs_link=any(is_project_docs(h, _host(url)) for h in hrefs),
        has_github_link=any(is_project_github(h) for h in hrefs),
        has_app_link=any(_APP_LINK_RE.search(h) for h in hrefs),
        has_api_link=any(_API_LINK_RE.search(h) for h in hrefs),
        has_bot_link=any(_BOT_LINK_RE.search(h) for h in hrefs),
    )
    return probe, f"{title} {text[:20_000]}"


def parse_telegram_members(body: str) -> int | None:
    match = _TG_EXTRA_RE.search(body)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None
