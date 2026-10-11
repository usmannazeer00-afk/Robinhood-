from sol_utility_scout.config import DEFAULT_CONFIG
from sol_utility_scout.models import SafetyReport, Snapshot, Socials, WebsiteProbe
from sol_utility_scout.rating import rate
from sol_utility_scout.utility import classify

from .fakes import UTILITY_DESC, good_safety


def _snap(**kw) -> Snapshot:
    base = dict(
        token_address="T", observed_at="2026-10-11T00:00:00+00:00", name="Ledgerly", symbol="LDGR",
        description=UTILITY_DESC, price_usd=0.001, market_cap=300_000, liquidity_usd=60_000,
        volume_h24=400_000, buys_h24=900, sells_h24=600,
        socials=Socials(website="https://l.example", twitter="https://x.com/l", telegram="https://t.me/l"),
        website=WebsiteProbe(reachable=True, status=200, text_length=5_000, has_docs_link=True),
        telegram_members=1_500, safety=good_safety(1_200),
    )
    base.update(kw)
    return Snapshot(**base)


def test_strong_utility_project_rates_high():
    r = rate(_snap(), DEFAULT_CONFIG)
    assert not r.rejected
    assert r.category in {"AI", "Tools"}
    assert r.total >= 75, r


def test_meme_is_classified_as_meme():
    cat, _, _, memes = classify("Super Market Inu, the doge of memes. 1000x moon", "SUPINU")
    assert cat == "meme"
    assert memes


def test_meme_description_scores_low_utility():
    r = rate(_snap(description="Pepe frog meme coin, send it to the moon, 1000x"), DEFAULT_CONFIG)
    assert r.utility < 5
    assert r.category == "meme"


def test_hard_filters():
    cases = [
        dict(safety=SafetyReport(available=True, mint_revoked=False, freeze_revoked=True, holders=500)),
        dict(safety=SafetyReport(available=True, mint_revoked=True, freeze_revoked=False, holders=500)),
        dict(safety=SafetyReport(available=True, mint_revoked=True, freeze_revoked=True, rugged=True)),
        dict(safety=SafetyReport(available=True, mint_revoked=True, freeze_revoked=True, top10_pct=70)),
        dict(liquidity_usd=2_000),
    ]
    for kw in cases:
        r = rate(_snap(**kw), DEFAULT_CONFIG)
        assert r.rejected and r.total == 0, kw


def test_dead_website_and_no_socials_cost_points():
    good = rate(_snap(), DEFAULT_CONFIG)
    bad = rate(_snap(socials=Socials(), website=None, telegram_members=None), DEFAULT_CONFIG)
    assert bad.socials == 0
    assert good.socials > bad.socials + 10


def test_insiders_reduce_safety():
    clean = rate(_snap(), DEFAULT_CONFIG)
    s = good_safety(1_200)
    s.insiders_detected = 200
    dirty = rate(_snap(safety=s), DEFAULT_CONFIG)
    assert dirty.safety < clean.safety - 8
