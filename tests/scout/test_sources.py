from sol_utility_scout.sources.dexscreener import launch_time, main_pair, parse_socials
from sol_utility_scout.sources.rugcheck import parse_report
from sol_utility_scout.sources.web import parse_telegram_members, parse_website


def test_parse_socials_separates_tweet_links_and_docs():
    s = parse_socials([
        {"type": "twitter", "url": "https://x.com/elonmusk/status/123"},
        {"label": "Docs", "url": "https://proj.gitbook.io"},
        {"label": "Website", "url": "https://proj.xyz"},
        {"url": "https://github.com/proj/app"},
    ])
    assert s.twitter is None and s.other == ["https://x.com/elonmusk/status/123"]
    assert s.docs == "https://proj.gitbook.io"
    assert s.website == "https://proj.xyz"
    assert s.github == "https://github.com/proj/app"


def test_launch_time_uses_oldest_pool_and_main_pair_deepest():
    pairs = [{"pairCreatedAt": 2_000_000, "liquidity": {"usd": 5}}, {"pairCreatedAt": 1_000_000, "liquidity": {"usd": 50}}]
    assert launch_time(pairs).timestamp() == 1_000
    assert main_pair(pairs)["liquidity"]["usd"] == 50
    assert launch_time([]) is None


def test_rugcheck_excludes_amm_and_lockers_from_top10():
    data = {
        "mintAuthority": None, "freezeAuthority": "Frz", "totalHolders": 321, "rugged": False,
        "knownAccounts": {"POOL": {"type": "AMM"}, "LOCK": {"type": "LOCKER"}},
        "markets": [{"pubkey": "POOL", "liquidityA": "VAULT", "lp": {"lpLockedPct": 99.5, "quoteUSD": 10}}],
        "topHolders": [
            {"owner": "POOL", "address": "VAULT", "pct": 80},
            {"owner": "LOCK", "address": "x", "pct": 10},
            {"owner": "w1", "address": "a1", "pct": 3},
            {"owner": "w2", "address": "a2", "pct": 2},
        ],
        "risks": [{"name": "Low Liquidity", "level": "danger"}, {"name": "Few holders", "level": "warn"}],
        "graphInsidersDetected": 4,
    }
    r = parse_report(data)
    assert r.mint_revoked is True and r.freeze_revoked is False
    assert r.top10_pct == 5
    assert r.lp_locked_pct == 99.5
    assert r.holders == 321 and r.insiders_detected == 4
    assert r.danger_risks == ["Low Liquidity"] and r.warn_risks == ["Few holders"]


def test_parse_website_and_telegram():
    body = '<html><title>Proj &amp; Co</title><script>var x</script><a href="https://proj.gitbook.io">Docs</a>' \
           '<a href="https://github.com/proj">gh</a><a href="https://app.proj.xyz">Launch App</a><p>Read the whitepaper and roadmap</p></html>'
    probe, text = parse_website(body, 200)
    assert probe.reachable and probe.title == "Proj & Co"
    assert probe.has_docs_link and probe.has_github_link and probe.has_app_link
    assert "whitepaper" in probe.utility_hits and "var x" not in text
    assert parse_telegram_members('<div class="tgme_page_extra">10 444 355 subscribers</div>') == 10_444_355
    assert parse_telegram_members('<div class="tgme_page_extra">1,204 members, 33 online</div>') == 1_204
    assert parse_telegram_members("<div>nothing</div>") is None


def test_app_link_ignores_asset_paths():
    body = '<a href="assets/apple-touch-icon.png"></a><link href="assets/css/app.css?v=1">'
    assert not parse_website(body, 200)[0].has_app_link
    for href in ("https://app.proj.xyz", "/app", "https://proj.xyz/app/swap", "app?ref=1"):
        assert parse_website(f'<a href="{href}">x</a>', 200)[0].has_app_link, href


def test_api_and_bot_links_detected():
    probe, _ = parse_website('<a href="/api">API</a><a href="https://t.me/isitbundled_bot">bot</a><a href="https://t.me/community">tg</a>', 200)
    assert probe.has_api_link and probe.has_bot_link
    plain, _ = parse_website('<a href="https://t.me/community">tg</a><a href="/apiary.png">x</a>', 200)
    assert not plain.has_api_link and not plain.has_bot_link


def test_borrowed_github_and_docs_links_are_not_project_proof():
    body = ('<a href="https://github.com/pump-fun/pump-public-docs/blob/main/docs/x.md">gh</a>'
            '<a href="https://deepmind.google/research/publications/77240/">paper</a>'
            '<a href="https://other.example/docs/intro">docs</a>')
    probe, _ = parse_website(body, 200, "https://phasepunk.xyz/")
    assert not probe.has_github_link and not probe.has_docs_link
    own = ('<a href="https://github.com/phasepunk/engine">gh</a><a href="/docs">docs</a>'
           '<a href="https://docs.phasepunk.xyz">d</a><a href="https://phasepunk.gitbook.io">gb</a>')
    probe, _ = parse_website(own, 200, "https://phasepunk.xyz/")
    assert probe.has_github_link and probe.has_docs_link
    assert parse_socials([{"url": "https://github.com/pump-fun/pump-public-docs"}]).github is None
