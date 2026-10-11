from datetime import datetime, timedelta, timezone

from sol_utility_scout.config import DEFAULT_CONFIG
from sol_utility_scout.conviction import step, structure_signal
from sol_utility_scout.store import COMPLETED, DROPPED, WATCHING, Store
from sol_utility_scout.tracker import Tracker

from .fakes import UTILITY_DESC, FakeDex, FakeRugCheck, FakeWeb, good_safety, make_pair

ADDR = "LdgrMint111"


def _tracker(tmp_path):
    dex, rc, web = FakeDex(), FakeRugCheck(), FakeWeb()
    dex.profiles[ADDR] = {"tokenAddress": ADDR, "description": UTILITY_DESC, "links": [], "cto": False, "feeds": ["token-profiles"]}
    dex.pairs[ADDR] = [make_pair(ADDR)]
    rc.reports[ADDR] = good_safety(600)
    t = Tracker(store=Store(str(tmp_path / "s.json")), dex=dex, rugcheck=rc, web=web)
    return t, dex, rc, web


def _age_record(t, days: float):
    rec = t.store.get(ADDR)
    launched = datetime.now(timezone.utc) - timedelta(days=days)
    rec["launched_at"] = launched.isoformat(timespec="seconds")


def test_new_utility_launch_is_added_and_persisted(tmp_path):
    t, *_ = _tracker(tmp_path)
    report = t.run_cycle()
    assert [r["address"] for r in report.added] == [ADDR]
    rec = Store(str(tmp_path / "s.json")).get(ADDR)  # reloaded from disk
    assert rec["status"] == WATCHING and rec["conviction"] == rec["first_rating"]


def test_old_tokens_and_memes_are_not_added(tmp_path):
    t, dex, rc, _ = _tracker(tmp_path)
    dex.pairs[ADDR] = [make_pair(ADDR, hours_old=24 * 10)]
    assert not t.run_cycle().added
    meme = "MemeMint"
    dex.profiles[meme] = {"tokenAddress": meme, "description": "doge pepe meme, moon 1000x", "links": []}
    dex.pairs[meme] = [make_pair(meme)]
    rc.reports[meme] = good_safety(5_000)
    assert not t.run_cycle().added
    assert "not a utility project" in t.store.data["rejected"][meme]["reasons"][0]


def test_conviction_grows_with_good_activity_and_events_are_logged(tmp_path):
    t, dex, rc, web = _tracker(tmp_path)
    t.run_cycle()
    start = t.store.get(ADDR)["conviction"]

    # Day 1: holders double, liquidity +60%, a Discord link appears, price up
    _age_record(t, 1.1)
    pair = make_pair(ADDR, price=0.002, mcap=650_000, liq=96_000)
    pair["info"]["socials"].append({"type": "discord", "url": "https://discord.gg/ledgerly"})
    dex.pairs[ADDR] = [pair]
    rc.reports[ADDR] = good_safety(1_300)
    web.members = 900
    report = t.run_cycle()
    kinds = {ev.kind for _, ev in report.events}
    assert {"social_added", "liquidity_up", "holders_milestone", "mcap_multiple", "telegram_growth", "new_high"} <= kinds
    rec = t.store.get(ADDR)
    assert rec["conviction"] > start
    assert rec["conviction"] - start <= DEFAULT_CONFIG.conviction_max_step  # bounded step
    assert any(e[1] == "social_added" for e in rec["events"])


def test_red_flags_lower_conviction_and_hard_filter_drops(tmp_path):
    t, dex, rc, web = _tracker(tmp_path)
    t.run_cycle()
    start = t.store.get(ADDR)["conviction"]
    web.up = False
    dex.pairs[ADDR] = [make_pair(ADDR, liq=30_000)]
    report = t.run_cycle()
    assert {"website_down", "liquidity_drop"} <= {ev.kind for _, ev in report.events}
    assert t.store.get(ADDR)["conviction"] < start

    s = good_safety(600)
    s.mint_revoked = False
    rc.reports[ADDR] = s
    report = t.run_cycle()
    rec = t.store.get(ADDR)
    assert rec["status"] == DROPPED and "mint authority" in rec["status_reason"]
    assert report.dropped


def test_tracking_completes_after_seven_days(tmp_path):
    t, *_ = _tracker(tmp_path)
    t.run_cycle()
    _age_record(t, 7.01)
    report = t.run_cycle()
    rec = t.store.get(ADDR)
    assert rec["status"] == COMPLETED and report.completed
    assert "7-day" in rec["status_reason"]
    # Completed tokens are no longer refreshed
    assert t.run_cycle().updated == 0


def test_drawdown_from_high_kills_token(tmp_path):
    t, dex, *_ = _tracker(tmp_path)
    t.run_cycle()
    dex.pairs[ADDR] = [make_pair(ADDR, price=0.0001)]
    t.run_cycle()
    assert t.store.get(ADDR)["status"] == DROPPED


def test_rediscovery_updates_profile_and_logs_description_change(tmp_path):
    t, dex, *_ = _tracker(tmp_path)
    t.run_cycle()
    dex.profiles[ADDR]["description"] = UTILITY_DESC + " V2 mainnet is live."
    t.run_cycle()  # scan stores the new profile
    report = t.run_cycle()  # next update sees it
    assert "description_updated" in {ev.kind for _, ev in report.events}


def test_structure_signal_and_step_bounds():
    assert structure_signal([1, 0.8, 1.2, 1.0, 1.3, 1.4]) == 1.0
    assert structure_signal([1.4, 1.3, 1.2, 1.0, 0.9, 0.8]) == -1.0
    assert structure_signal([1, 2]) == 0.0
    delta, _ = step(rating_prev=60, rating_now=60, event_weights=[50], holders_prev=100, holders_now=100,
                    days_survived_new=0, prices=[], config=DEFAULT_CONFIG)
    assert delta == DEFAULT_CONFIG.conviction_max_step
