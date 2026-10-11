"""Keyword classifier: does this token describe a real product, or is it a meme?

Works on the token's DexScreener description, its name/symbol and whatever
text the project website serves. It's a heuristic -- a determined team can
write "AI DeFi protocol" on a meme -- which is why the rating also checks
for evidence a product exists (docs, GitHub, an app link, a live site) and
the conviction tracker keeps watching for 7 days.
"""

from __future__ import annotations

import re

UTILITY_CATEGORIES: dict[str, list[str]] = {
    "AI": ["ai", "ai agent", "agents", "llm", "machine learning", "inference", "gpt", "neural", "ai-powered", "artificial intelligence"],
    "DeFi": ["defi", "staking", "yield", "lending", "borrow", "liquidity protocol", "vault", "perps", "perpetual", "swap", "dex", "amm", "derivatives", "launchpad"],
    "Infra": ["infrastructure", "rpc", "oracle", "indexer", "sdk", "api", "protocol", "layer", "bridge", "validator", "node", "zk", "privacy"],
    "DePIN": ["depin", "compute", "gpu", "storage", "bandwidth", "sensor", "physical infrastructure", "wireless"],
    "Gaming": ["game", "gaming", "play-to-earn", "p2e", "metaverse", "esports", "nft game"],
    "Tools": ["trading bot", "telegram bot", "analytics", "dashboard", "terminal", "wallet", "tracker", "sniper", "scanner", "extension", "tool"],
    "Payments": ["payments", "payment", "merchant", "remittance", "stablecoin", "invoice", "card", "checkout"],
    "RWA": ["real world asset", "rwa", "tokenized", "real estate", "treasury", "commodities"],
    "Social": ["socialfi", "creator", "social platform", "dao", "governance", "community platform"],
}

# Signals of an actual product rather than a pitch.
PRODUCT_EVIDENCE = ["whitepaper", "litepaper", "docs", "documentation", "roadmap", "tokenomics", "audit", "github", "beta", "testnet", "mainnet", "launch app", "dapp", "app.", "dashboard", "revenue", "buyback"]

MEME_MARKERS = ["meme", "memecoin", "moon", "pepe", "doge", "shib", "shiba", "inu", "wojak", "frog", "cat coin", "100x", "1000x", "lfg", "send it", "pump it", "ape in", "no utility", "just a meme", "community token", "fair launch only", "elon", "trump", "bonk"]

_word_cache: dict[str, re.Pattern[str]] = {}


def _matches(text: str, phrase: str) -> bool:
    pattern = _word_cache.get(phrase)
    if pattern is None:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")
        _word_cache[phrase] = pattern
    return bool(pattern.search(text))


def classify(*texts: str) -> tuple[str, dict[str, int], list[str], list[str]]:
    """Returns (best_category, category_hit_counts, evidence_hits, meme_hits).

    best_category is "meme" when meme markers outweigh utility hits, and
    "unknown" when nothing matched either way.
    """
    blob = " ".join(t for t in texts if t).lower()
    counts = {cat: sum(1 for kw in kws if _matches(blob, kw)) for cat, kws in UTILITY_CATEGORIES.items()}
    counts = {cat: n for cat, n in counts.items() if n}
    evidence = [kw for kw in PRODUCT_EVIDENCE if kw in blob]
    # Meme markers only count in what the team says about itself (description,
    # name). Website text is full of incidental hits -- a "send it" button, a
    # launchpad called Bonk -- that say nothing about the project.
    own_words = " ".join(t for t in texts[:2] if t).lower()
    memes = [kw for kw in MEME_MARKERS if _matches(own_words, kw)]

    utility_hits = sum(counts.values())
    if not utility_hits and not memes:
        return "unknown", counts, evidence, memes
    # The team's own description calling it a meme outweighs keyword hits
    # elsewhere (launchpad-hosted pages carry generic "docs/audit" boilerplate).
    if texts and any(_matches(texts[0].lower(), kw) for kw in ("meme", "memecoin")):
        return "meme", counts, evidence, memes
    # ...and so does a meme marker in the token's own name ("Super Market Inu").
    if len(texts) > 1 and texts[1] and any(_matches(texts[1].lower(), kw) for kw in MEME_MARKERS):
        return "meme", counts, evidence, memes
    if len(memes) >= utility_hits:
        return "meme", counts, evidence, memes
    best = max(counts, key=counts.get) if counts else "unknown"
    return best, counts, evidence, memes


def utility_points(description: str, name: str, website_text: str, max_points: float, proof: list[str] | None = None) -> tuple[float, str, list[str]]:
    """Scores 0..max_points for how strongly the project reads as a utility play.

    ``proof`` (github/docs/app links found on the project) counts as product
    evidence on top of whatever the text claims."""
    category, counts, evidence, memes = classify(description, name, website_text)
    evidence = evidence + [f"{p} link" for p in proof or [] if f"{p} link" not in evidence]
    notes: list[str] = []
    utility_hits = sum(counts.values())

    points = 0.0
    # Up to 50% for describing a concrete use case (diminishing past 4 hits)
    points += min(utility_hits, 4) / 4 * 0.5 * max_points
    # Up to 40% for evidence the product exists
    points += min(len(evidence), 4) / 4 * 0.4 * max_points
    # 10% for a non-trivial description at all
    if len(description.strip()) >= 80:
        points += 0.1 * max_points
    # Meme markers eat into it
    points -= min(len(memes), 3) / 3 * 0.5 * max_points
    points = max(0.0, min(max_points, points))

    if counts:
        notes.append("use case: " + ", ".join(f"{c}({n})" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])))
    if evidence:
        notes.append("product evidence: " + ", ".join(evidence[:6]))
    if memes:
        notes.append("meme markers: " + ", ".join(memes[:5]))
    if not counts and not evidence:
        notes.append("no utility described")
    return points, category, notes
