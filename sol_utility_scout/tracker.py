"""Orchestration: discover new launches, rate them, and track the keepers.

One ``run_cycle`` does two things:

1. **update** every token on the watchlist -- re-observe it, detect what
   changed since the last look (events), re-rate it, move its conviction,
   and drop it if it trips a hard filter. Every 7 days of watching it is
   reviewed: kept on the radar for another 7 days if its tech still reads
   as utility and its numbers improved, otherwise closed out as completed;
2. **scan** DexScreener's discovery feeds for Solana tokens launched in
   the last ``max_launch_age_hours``, fully enrich and rate each new one,
   and add those rated >= ``watch_rating_min`` to the watchlist.

Run it on a schedule (cron, ``python -m sol_utility_scout.cli loop``, or
the Telegram bot's built-in job) -- state lives in the JSON store, so a
fresh process each time works fine.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import conviction as conv
from .config import DEFAULT_CONFIG, ScoutConfig
from .events import Event, detect
from .models import Rating, Snapshot
from .rating import rate
from .sources.dexscreener import DexScreenerClient, launch_time, main_pair, parse_socials
from .sources.rugcheck import RugCheckClient
from .sources.web import WebProbe
from .store import COMPLETED, DROPPED, WATCHING, Store, now_iso

logger = logging.getLogger(__name__)

REJECT_RECHECK_HOURS = 3  # pump.fun tokens show $0 liquidity until they graduate
ENRICH_WORKERS = 6


@dataclass
class CycleReport:
    discovered: int = 0
    evaluated: int = 0
    rejected: int = 0
    added: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[dict[str, Any], Event]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    completed: list[dict[str, Any]] = field(default_factory=list)
    extended: list[dict[str, Any]] = field(default_factory=list)
    updated: int = 0


class Tracker:
    def __init__(
        self,
        store: Store | None = None,
        config: ScoutConfig = DEFAULT_CONFIG,
        dex: DexScreenerClient | None = None,
        rugcheck: RugCheckClient | None = None,
        web: WebProbe | None = None,
    ) -> None:
        self.store = store or Store()
        self.config = config
        self.dex = dex or DexScreenerClient(chain_id=config.chain_id)
        self.rugcheck = rugcheck or RugCheckClient()
        self.web = web or WebProbe()

    # ------------------------------------------------------------------ build
    def observe(self, address: str, profile: dict[str, Any], pairs: list[dict[str, Any]]) -> tuple[Snapshot, str]:
        """Builds a fully enriched Snapshot (market + safety + web) for one token."""
        pair = main_pair(pairs) or {}
        launched = launch_time(pairs)
        base = pair.get("baseToken") or {}
        info = pair.get("info") or {}
        # Prefer the pair's live links (so removals are visible); fall back to profile links.
        if info.get("websites") or info.get("socials"):
            socials = parse_socials([], pair)
        else:
            socials = parse_socials(profile.get("links") or [], None)

        txns = (pair.get("txns") or {}).get("h24") or {}
        volume = pair.get("volume") or {}
        snap = Snapshot(
            token_address=address,
            observed_at=now_iso(),
            name=base.get("name") or profile.get("name") or "",
            symbol=base.get("symbol") or profile.get("symbol") or "",
            description=profile.get("description") or "",
            pair_address=pair.get("pairAddress"),
            dex_id=pair.get("dexId"),
            launched_at=launched.isoformat(timespec="seconds") if launched else None,
            price_usd=float(pair.get("priceUsd") or 0),
            market_cap=float(pair.get("marketCap") or pair.get("fdv") or 0),
            liquidity_usd=sum(float((p.get("liquidity") or {}).get("usd") or 0) for p in pairs),
            volume_h24=sum(float((p.get("volume") or {}).get("h24") or 0) for p in pairs),
            volume_h1=float(volume.get("h1") or 0),
            buys_h24=int(txns.get("buys") or 0),
            sells_h24=int(txns.get("sells") or 0),
            price_change_h24=float((pair.get("priceChange") or {}).get("h24") or 0),
            boosts_active=int((pair.get("boosts") or {}).get("active") or 0),
            cto=bool(profile.get("cto")),
            socials=socials,
        )

        snap.safety = self.rugcheck.report(address)
        website_text = ""
        if socials.website:
            snap.website, website_text = self.web.website(socials.website)
        if socials.telegram:
            snap.telegram_members = self.web.telegram_members(socials.telegram)
        return snap, website_text

    # ------------------------------------------------------------------ cycle
    def run_cycle(self) -> CycleReport:
        report = CycleReport()
        self.update(report)
        self.scan(report)
        self.store.prune_rejected(older_than_hours=self.config.max_launch_age_hours * 2)
        self.store.save()
        return report

    def scan(self, report: CycleReport | None = None) -> CycleReport:
        report = report or CycleReport()
        found = self.dex.discover()
        report.discovered = len(found)

        # Refresh stored profiles of tokens we already track (new description,
        # links, CTO -- picked up as events on their next update).
        for addr, profile in found.items():
            record = self.store.get(addr)
            if record:
                record["profile"] = _merge_profile(record.get("profile") or {}, profile)

        fresh = [
            a for a in found
            if not self.store.get(a) and not self.store.recently_rejected(a, REJECT_RECHECK_HOURS)
        ]
        if not fresh:
            return report
        pairs = self.dex.pairs_for_tokens(fresh)
        now = datetime.now(timezone.utc)

        candidates = []
        for addr in fresh:
            launched = launch_time(pairs[addr])
            if not pairs[addr] or launched is None:
                self.store.mark_rejected(addr, ["no trading pair yet"])
                report.rejected += 1
                continue
            age_h = (now - launched).total_seconds() / 3600
            if age_h > self.config.max_launch_age_hours:
                self.store.mark_rejected(addr, [f"launched {age_h:.0f}h ago, not new"])
                report.rejected += 1
                continue
            # Cheap pre-check before the RugCheck/website calls: most search hits are empty pools.
            liquidity = sum(float((p.get("liquidity") or {}).get("usd") or 0) for p in pairs[addr])
            if liquidity < self.config.liquidity_min_usd:
                self.store.mark_rejected(addr, [f"liquidity ${liquidity:,.0f} < ${self.config.liquidity_min_usd:,.0f} floor"])
                report.rejected += 1
                continue
            candidates.append(addr)

        def evaluate(addr: str) -> tuple[str, Snapshot, Rating]:
            snap, text = self.observe(addr, found[addr], pairs[addr])
            return addr, snap, rate(snap, self.config, text)

        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            results = list(pool.map(_safe(evaluate), candidates))

        for result in results:
            if result is None:
                continue
            addr, snap, rating = result
            report.evaluated += 1
            if rating.rejected:
                self.store.mark_rejected(addr, rating.reject_reasons)
                report.rejected += 1
            elif rating.category == "meme" or rating.utility < self.config.utility_min_ratio * self.config.weights.utility:
                self.store.mark_rejected(addr, [f"not a utility project ({rating.category}, utility {rating.utility:.0f}/{self.config.weights.utility})"])
                report.rejected += 1
            elif len(rating.tech_proof) < self.config.tech_proof_min:
                self.store.mark_rejected(addr, ["no proof of real tech (no GitHub, docs, app, API or bot link)"])
                report.rejected += 1
            elif rating.total < self.config.watch_rating_min:
                self.store.mark_rejected(addr, [f"rating {rating.total:.0f} < {self.config.watch_rating_min:.0f}"])
                report.rejected += 1
            else:
                record = self._new_record(snap, rating, found[addr])
                self.store.tokens[addr] = record
                report.added.append(record)
        return report

    def update(self, report: CycleReport | None = None) -> CycleReport:
        report = report or CycleReport()
        watching = self.store.by_status(WATCHING)
        if not watching:
            return report
        pairs = self.dex.pairs_for_tokens([r["address"] for r in watching])

        def refresh(record: dict[str, Any]) -> tuple[dict[str, Any], Snapshot, Rating] | None:
            p = pairs.get(record["address"]) or []
            if not p:
                return None
            snap, text = self.observe(record["address"], record.get("profile") or {}, p)
            return record, snap, rate(snap, self.config, text)

        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            results = list(pool.map(_safe(refresh), watching))

        now = datetime.now(timezone.utc)
        for record, result in zip(watching, results):
            if result is None:
                record["misses"] = record.get("misses", 0) + 1
                if record["misses"] >= 3:
                    self._drop(record, "no trading pair found for 3 checks (delisted / pool pulled?)", report)
                continue
            record["misses"] = 0
            _, snap, rating = result
            self._apply_update(record, snap, rating, now, report)
            report.updated += 1
        return report

    # ------------------------------------------------------------------ records
    def _new_record(self, snap: Snapshot, rating: Rating, profile: dict[str, Any]) -> dict[str, Any]:
        ts = snap.observed_at
        record = {
            "address": snap.token_address,
            "symbol": snap.symbol,
            "name": snap.name,
            "category": rating.category,
            "launched_at": snap.launched_at,
            "first_seen": ts,
            "last_checked": ts,
            "status": WATCHING,
            "status_reason": "",
            "first_rating": rating.total,
            "rating": rating.total,
            "breakdown": _breakdown(rating),
            "notes": rating.notes,
            "conviction": rating.total,
            "conviction_peak": rating.total,
            "conviction_history": [[ts, rating.total, ["initial rating"]]],
            "period_start": _period_start(ts, rating.total, snap),
            "radar_periods": 0,
            "first_mcap": snap.market_cap,
            "peak_price": snap.price_usd,
            "peak_mcap": snap.market_cap,
            "days_credited": _days_since(snap.launched_at, datetime.now(timezone.utc)),
            "profile": {k: profile.get(k) for k in ("description", "links", "cto", "feeds")},
            "last_snapshot": snap.to_dict(),
            "series": [],
            "events": [[ts, "added", f"added to watchlist at rating {rating.total:.0f} ({rating.category})", 0]],
        }
        self.store.append_series(record, _series_point(snap, rating.total, rating.total))
        return record

    def _apply_update(self, record: dict[str, Any], snap: Snapshot, rating: Rating, now: datetime, report: CycleReport) -> None:
        prev = Snapshot.from_dict(record["last_snapshot"])
        events = detect(prev, snap, first_mcap=record["first_mcap"], peak_price=record["peak_price"])
        for ev in events:
            record["events"].append(ev.to_list(snap.observed_at))
            report.events.append((record, ev))

        days = _days_since(record.get("launched_at") or snap.launched_at, now)
        new_days = max(0, days - record.get("days_credited", 0))
        prices = [pt[1] for pt in record["series"]] + [snap.price_usd]
        delta, reasons = conv.step(
            rating_prev=record["rating"],
            rating_now=rating.total if not rating.rejected else record["rating"],
            event_weights=[e.weight for e in events],
            holders_prev=prev.safety.holders,
            holders_now=snap.safety.holders,
            days_survived_new=new_days,
            prices=prices,
            config=self.config,
        )
        record["days_credited"] = days
        before = record["conviction"]
        record["conviction"] = round(max(0.0, min(100.0, before + delta)), 1)
        record["conviction_peak"] = max(record["conviction_peak"], record["conviction"])
        if reasons or record["conviction"] != before:  # the trend arrow reads the last two entries
            record["conviction_history"].append([snap.observed_at, record["conviction"], reasons or ["minor drift"]])

        record.update(
            symbol=snap.symbol or record["symbol"],
            name=snap.name or record["name"],
            last_checked=snap.observed_at,
            rating=rating.total,
            breakdown=_breakdown(rating),
            notes=rating.notes,
            category=rating.category if rating.category != "unknown" else record["category"],
            last_snapshot=snap.to_dict(),
            peak_price=max(record["peak_price"], snap.price_usd),
            peak_mcap=max(record["peak_mcap"], snap.market_cap),
        )
        self.store.append_series(record, _series_point(snap, rating.total, record["conviction"]))

        if rating.rejected:
            self._drop(record, "; ".join(rating.reject_reasons), report)
            return
        if record["peak_price"] > 0 and snap.price_usd > 0:
            dd = (1 - snap.price_usd / record["peak_price"]) * 100
            if dd >= self.config.drawdown_kill_pct:
                self._drop(record, f"price down {dd:.0f}% from tracked high", report)
                return
        reason = self._flush_reason(record, snap, rating)
        if reason:
            self._drop(record, f"flushed: {reason}", report)
            return
        start = record.setdefault("period_start", _period_start_from_series(record))
        if (now - datetime.fromisoformat(start["at"])).total_seconds() >= self.config.tracking_days * 86_400:
            self._review_period(record, snap, rating, report)

    def _flush_reason(self, record: dict[str, Any], snap: Snapshot, rating: Rating) -> str | None:
        """Why a watched token is no longer worth holding, or None if it still is."""
        c = self.config
        # The watchlist entry gates apply on every check, not just the first:
        # a re-read website can reveal a meme, or a product link can vanish.
        if rating.category == "meme":
            return "re-classified as a meme"
        site_down = snap.website is not None and not snap.website.reachable  # an outage is its own event, not lost proof
        if not site_down and len(rating.tech_proof) < c.tech_proof_min:
            return "no proof of real tech any more (no GitHub, docs, app, API or bot link)"
        if len(record["series"]) < c.flush_min_checks:
            return None
        conviction, first = record["conviction"], record["series"][0]
        first_liq = first[3] or 0
        if conviction < c.flush_conviction_below:
            return f"conviction fell to {conviction:.1f} (LOW, < {c.flush_conviction_below:.0f})"
        if (record["conviction_peak"] - conviction >= c.flush_fade_from_peak
                and record["first_mcap"] and snap.market_cap < record["first_mcap"] * c.flush_fade_mcap_ratio):
            return (f"fading: conviction {record['conviction_peak']:.0f} -> {conviction:.0f}, "
                    f"market cap {snap.market_cap / record['first_mcap']:.1f}x of first seen")
        if first_liq and snap.liquidity_usd < first_liq * c.flush_liquidity_ratio:
            return f"liquidity bleeding: ${first_liq:,.0f} -> ${snap.liquidity_usd:,.0f}"
        return None

    def flush(self, query: str, reason: str) -> dict[str, Any] | None:
        """Manually drop a watched token (CLI/bot /flush). Returns the record, or None if not watched."""
        record = self.store.find(query)
        if not record or record["status"] != WATCHING:
            return None
        self._drop(record, f"flushed manually: {reason}", CycleReport())
        self.store.save()
        return record

    def _review_period(self, record: dict[str, Any], snap: Snapshot, rating: Rating, report: CycleReport) -> None:
        """End of a 7-day watch period: keep improving utility projects on the radar, close out the rest."""
        c, start = self.config, record["period_start"]
        holders = snap.safety.holders or 0
        checks = {
            "tech still reads as utility": rating.category != "meme" and rating.utility >= c.utility_min_ratio * c.weights.utility,
            f"conviction {start['conviction']:.0f} -> {record['conviction']:.0f}": record["conviction"] >= max(start["conviction"], c.conviction_medium),
            f"holders {start['holders'] or 0:,} -> {holders:,}": holders > (start["holders"] or 0),
            f"liquidity ${start['liquidity']:,.0f} -> ${snap.liquidity_usd:,.0f}": snap.liquidity_usd >= start["liquidity"] * c.radar_liquidity_tolerance,
        }
        summary = ", ".join(f"{'✓' if ok else '✗'} {name}" for name, ok in checks.items())
        label = conv.label(record["conviction"], c)
        if all(checks.values()):
            record["radar_periods"] = record.get("radar_periods", 0) + 1
            record["period_start"] = _period_start(snap.observed_at, record["conviction"], snap)
            message = f"kept on radar for another {c.tracking_days:.0f} days (conviction {record['conviction']:.0f} {label}): {summary}"
            record["events"].append([snap.observed_at, "radar_extended", message, 0])
            report.extended.append(record)
        else:
            record["status"] = COMPLETED
            record["status_reason"] = f"watch period finished, final conviction {record['conviction']:.0f} ({label}): {summary}"
            record["events"].append([snap.observed_at, "completed", record["status_reason"], 0])
            report.completed.append(record)

    def _drop(self, record: dict[str, Any], reason: str, report: CycleReport) -> None:
        record["status"] = DROPPED
        record["status_reason"] = reason
        record["events"].append([now_iso(), "dropped", reason, 0])
        report.dropped.append(record)


def _safe(fn):
    """Wraps a worker so one token's unexpected error can't sink the whole cycle."""
    def wrapper(arg):
        try:
            return fn(arg)
        except Exception:  # noqa: BLE001 -- logged, the token is just retried next cycle
            logger.exception("enrichment failed")
            return None
    return wrapper


def _merge_profile(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(old)
    if new.get("description"):
        merged["description"] = new["description"]
    if new.get("links"):
        merged["links"] = new["links"]
    merged["cto"] = bool(old.get("cto") or new.get("cto"))
    merged["feeds"] = sorted(set(old.get("feeds") or []) | set(new.get("feeds") or []))
    return merged


def _days_since(iso: str | None, now: datetime) -> int:
    if not iso:
        return 0
    return int((now - datetime.fromisoformat(iso)).total_seconds() // 86_400)


def _period_start(ts: str, conviction: float, snap: Snapshot) -> dict[str, Any]:
    return {"at": ts, "conviction": conviction, "holders": snap.safety.holders, "liquidity": snap.liquidity_usd, "mcap": snap.market_cap}


def _period_start_from_series(record: dict[str, Any]) -> dict[str, Any]:
    """Backfills period_start for records stored before radar extensions existed."""
    first = record["series"][0] if record.get("series") else [record["first_seen"], 0, record.get("first_mcap", 0), 0, None]
    return {"at": record["first_seen"], "conviction": record["first_rating"], "holders": first[4], "liquidity": first[3] or 0, "mcap": first[2]}


def _breakdown(r: Rating) -> dict[str, float]:
    return {"utility": r.utility, "socials": r.socials, "safety": r.safety, "market": r.market, "traction": r.traction}


def _series_point(snap: Snapshot, rating: float, conviction: float) -> list[Any]:
    return [snap.observed_at, snap.price_usd, snap.market_cap, snap.liquidity_usd, snap.safety.holders, snap.volume_h24, rating, conviction]
