"""FOMO layer: buy coins with a story, real backing, and smart money in early.

Runs on top of the sniper scan. A token only becomes a BUY when all of
these line up:

  * it passes the sniper's hard filters and scores at least WATCH on the
    100-point sniper rubric (so the chart/liquidity side isn't garbage),
  * it scores at least `buy_min_score` on the 100-point FOMO rubric below,
  * at least `smart_wallets_min` known smart wallets are among its early
    buyers, and
  * it has real backing: a website or an X account at minimum.

    FOMO rubric                                         Points
    Story     description (8), narrative theme (10),
              image (3), community takeover/boost (4)     25
    Backing   website (8), X/twitter (8), telegram or
              discord (6), paid boost (4), LP locked (4)  30
    Smart $   early smart wallets (15 each),
              later smart wallets (7.5 each)              45
    Total                                                100

"Story" and "backing" are measured from what the token publishes on
DexScreener. That proves effort, not honesty -- a polished website can
still front a rug, which is why smart money carries the most weight and
the sniper's deployer/bundle hard filters still apply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .config import DEFAULT_CONFIG, SniperConfig
from .models import TokenSnapshot
from .scanner import ScannedToken
from .scoring import FactorScore
from .smart_wallets import SmartWalletBook

# Narratives that have pulled flows on meme launches. Matched as whole
# words against name, symbol and description. Retune freely.
DEFAULT_THEMES: dict[str, tuple[str, ...]] = {
    "Robinhood / stocks": ("robinhood", "hood", "stock", "stocks", "tokenized", "wallstreet", "nasdaq", "ipo"),
    "AI / agents": ("ai", "agent", "agents", "gpt", "neural", "bot", "agi"),
    "Animal meme": ("dog", "doge", "inu", "cat", "frog", "pepe", "shiba", "bonk", "wif", "hat"),
    "Political / celebrity": ("trump", "maga", "elon", "musk", "president", "vlad"),
    "Gaming / culture": ("game", "gaming", "meme", "anime", "chad", "wojak", "based"),
}


class FomoAction(str, Enum):
    BUY = "BUY"
    WATCH = "WATCH"
    SKIP = "SKIP"

    @property
    def emoji(self) -> str:
        return {"BUY": "🚀", "WATCH": "👀", "SKIP": "⏭️"}[self.value]


@dataclass(frozen=True)
class FomoConfig:
    buy_min_score: float = 60
    watch_min_score: float = 40
    smart_wallets_min: int = 2
    # A smart wallet counts as "early" when it is within this many first buyers.
    early_rank_window: int = 30
    themes: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_THEMES))
    story_max: int = 25
    backing_max: int = 30
    smart_money_max: int = 45


DEFAULT_FOMO_CONFIG = FomoConfig()


@dataclass
class SmartBuyer:
    address: str
    rank: int  # 1 = first buyer
    description: str


@dataclass
class FomoResult:
    scanned: ScannedToken
    score: float
    action: FomoAction
    factors: list[FactorScore]
    smart_buyers: list[SmartBuyer]
    themes: list[str]
    reasons: list[str]  # why it isn't a BUY (empty when it is)

    @property
    def token(self) -> TokenSnapshot:
        return self.scanned.token


def match_themes(token: TokenSnapshot, themes: dict[str, tuple[str, ...]]) -> list[str]:
    text = f"{token.name} {token.symbol} {token.profile.description}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    return [theme for theme, keywords in themes.items() if words & set(keywords)]


def find_smart_buyers(token: TokenSnapshot, book: SmartWalletBook) -> list[SmartBuyer]:
    return [
        SmartBuyer(addr, rank, book.describe(addr))
        for rank, addr in enumerate(token.early_buyers, start=1)
        if book.is_smart(addr)
    ]


def _story(token: TokenSnapshot, themes: list[str], max_points: int) -> FactorScore:
    p = token.profile
    pts, notes = 0.0, []
    if len(p.description.strip()) >= 40:
        pts += 8
        notes.append("has a written story")
    else:
        notes.append("no real description")
    if themes:
        pts += 10
        notes.append(f"narrative: {', '.join(themes)}")
    else:
        notes.append("no hot narrative matched")
    if p.has_image:
        pts += 3
    if p.community_takeover or p.boosts_active:
        pts += 4
        notes.append("community takeover" if p.community_takeover else "boosted")
    return FactorScore("Story", min(pts, max_points), max_points, "; ".join(notes))


def _backing(token: TokenSnapshot, max_points: int) -> FactorScore:
    p = token.profile
    pts, have, missing = 0.0, [], []
    checks = [
        (bool(p.websites), 8, "website"),
        ("twitter" in p.socials, 8, "X"),
        ("telegram" in p.socials or "discord" in p.socials, 6, "TG/Discord"),
        (p.boosts_active > 0, 4, "paid boost"),
        (token.liquidity.locked_or_burned, 4, "LP locked/burned"),
    ]
    for ok, points, label in checks:
        if ok:
            pts += points
            have.append(label)
        else:
            missing.append(label)
    note = f"has {', '.join(have) or 'nothing'}"
    if missing:
        note += f"; missing {', '.join(missing)}"
    return FactorScore("Backing", min(pts, max_points), max_points, note)


def _smart_money(buyers: list[SmartBuyer], cfg: FomoConfig) -> FactorScore:
    if not buyers:
        return FactorScore("Smart money", 0, cfg.smart_money_max, "no known smart wallets in early buyers")
    early = [b for b in buyers if b.rank <= cfg.early_rank_window]
    late = len(buyers) - len(early)
    pts = min(cfg.smart_money_max, 15 * len(early) + 7.5 * late)
    ranks = ", ".join(f"#{b.rank}" for b in buyers[:6])
    return FactorScore(
        "Smart money", pts, cfg.smart_money_max,
        f"{len(buyers)} smart wallet(s), {len(early)} early (buyer {ranks})",
    )


def evaluate(
    scanned: ScannedToken,
    book: SmartWalletBook,
    cfg: FomoConfig = DEFAULT_FOMO_CONFIG,
    sniper_cfg: SniperConfig = DEFAULT_CONFIG,
) -> FomoResult:
    token, sniper = scanned.token, scanned.result
    themes = match_themes(token, cfg.themes)
    buyers = find_smart_buyers(token, book)
    factors = [
        _story(token, themes, cfg.story_max),
        _backing(token, cfg.backing_max),
        _smart_money(buyers, cfg),
    ]
    score = sum(f.points for f in factors)

    reasons = []
    if not sniper.passed_hard_filters:
        reasons.append(f"sniper hard filter: {sniper.hard_filter_failed}")
    elif sniper.total < sniper_cfg.watch_score_min:
        reasons.append(f"sniper score {sniper.total:.0f} < {sniper_cfg.watch_score_min}")
    if score < cfg.buy_min_score:
        reasons.append(f"FOMO score {score:.0f} < {cfg.buy_min_score:.0f}")
    if len(buyers) < cfg.smart_wallets_min:
        reasons.append(f"{len(buyers)} smart wallet(s), need {cfg.smart_wallets_min}")
    if not (token.profile.websites or "twitter" in token.profile.socials):
        reasons.append("no website or X account")

    if not reasons:
        action = FomoAction.BUY
    elif sniper.passed_hard_filters and score >= cfg.watch_min_score:
        action = FomoAction.WATCH
    else:
        action = FomoAction.SKIP
    return FomoResult(scanned, score, action, factors, buyers, themes, reasons)


def rank(results: list[FomoResult]) -> list[FomoResult]:
    order = {FomoAction.BUY: 0, FomoAction.WATCH: 1, FomoAction.SKIP: 2}
    return sorted(results, key=lambda r: (order[r.action], -r.score, -r.scanned.result.total))
