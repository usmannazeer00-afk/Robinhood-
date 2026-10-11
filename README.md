# Robinhood Chain Sniper Bot

A meme-coin scanner/scorer for newly launched pairs on Robinhood Chain,
implementing the filter and 100-point scoring rubric below. It's a
**scanner and alerting bot, not an auto-trade executor** — it surfaces
candidates and lets you decide; it never places trades.

Ask it for a scan (`/newpair` in Telegram, or `scan` on the CLI) and it
returns every candidate under the age cap, scored and ranked, with a 🟢
entry / 🟡 watch / 🔴 ignore verdict and a line-by-line breakdown of why.

## Strategy implemented

| Factor | Points |
|---|---|
| Age 5-45m | 10 |
| $75K-$400K market cap | 10 |
| $40K+ liquidity | 15 |
| MC/Liquidity < 8x | 5 |
| Accelerating volume (1m/5m/15m rate + rising 5m buckets) | 20 |
| Buy/sell ratio >= 1.5x | 10 |
| Accelerating unique buyers | 10 |
| Higher-low chart structure | 10 |
| Healthy holder distribution (top10 <= 35%) | 5 |
| Clean deployer / healthy LP | 5 |
| **Total** | **100** |

Score bands: **80-100 = 🟢 potential entry**, **65-79 = 🟡 watch**, **<65 =
🔴 ignore**. A dirty deployer (owns a large share, has already sold,
multiple failed prior tokens, linked to launch bundles, or routes to
fresh wallets before selling) is a **hard filter** — score is forced to 0
regardless of everything else, matching the spec.

All thresholds live in `robinhood_sniper/config.py` (`SniperConfig`) if
you want to retune them.

## Data sources

Three providers, in increasing order of fidelity:

- **`MockProvider`** (default) — synthetic candidates spanning the full
  quality range (a clean accelerating setup, a wash-trading look-alike, a
  rug-flagged deployer, a too-young launch). `/newpair` works out of the
  box for demoing the scoring logic with this.

- **`DexScreenerProvider`** — a real HTTP client against DexScreener's
  public **search** API, filtered by chain id. Only finds pools whose
  token name/symbol matches the search text (default: `"robinhood"`), so
  it will **not** catch a freshly launched token with a random name — it's
  useful for looking up a specific known token, not for chain-wide new-pair
  discovery. Gives market cap, liquidity, and m5/h1 volume/buy-sell counts.

- **`RobinhoodChainFactoryProvider`** — real on-chain discovery. Watches
  `UniswapV3Factory.PoolCreated` directly on Robinhood Chain mainnet via
  JSON-RPC, so it catches every new pool regardless of name, then enriches
  each one with `DexScreenerProvider.enrich_by_token` for market data.
  This is the one to use for real sniping.

  Verified live and working (2026-09-12) against Robinhood Chain mainnet:
  chain id `4663`, public RPC `https://rpc.mainnet.chain.robinhood.com`,
  `UniswapV3Factory` at `0x1f7d7550b1b028f7571e69a784071f0205fd2efa`
  (bytecode confirmed to match the standard Uniswap v3 factory), with
  `WETH`/`USDG` as the known base tokens pools are quoted against (per
  [docs.robinhood.com/chain/contracts](https://docs.robinhood.com/chain/contracts)
  and [blog.uniswap.org/robinhood-chain-is-live](https://blog.uniswap.org/robinhood-chain-is-live)).
  A live test run found real new pools in the last 45 minutes — including
  several identical-named zero-liquidity spam launches, which the scorer
  correctly zeroed out.

  Set `ROBINHOOD_RPC_URL` to point at a dedicated RPC (Alchemy, etc.)
  instead of the public rate-limited one for heavier polling.

### Persistent history (real buyer/structure scoring)

A single scan is one snapshot in time — it can't tell you whether buyers
are accelerating or whether price just made a higher low, because those
need multiple points over time. `RobinhoodChainFactoryProvider` solves
this with `providers/history.py`: a small JSON file (`TokenHistoryStore`,
default path `.robinhood_sniper_history.json`, override with
`SNIPER_HISTORY_PATH`) that persists two **real, on-chain-derived**
series per pool across scans:

- **Unique buyer wallets** — pulled from actual `Swap` events on the
  pool contract, using each swap's *transaction sender* (the real
  trading wallet) rather than the Swap event's own `sender` field (which
  is almost always just the router contract and would undercount
  distinct traders). This is genuine wallet-address tracking, not an
  estimated proxy.
- **Price points** — sampled from DexScreener's live price at each scan.

Every scan appends to both series (only re-querying swaps since the last
scanned block, so it stays cheap), and prunes pools that have aged well
past the sniping window. A cron-driven loop (`/loop` or `CronCreate`,
each fire a fresh process) shares this state automatically since it's on
disk, not in memory — so the WATCH/ENTRY calls actually improve as a
token gets rescanned, instead of permanently missing the two scoring
categories that need history (20 of the 100 points).

Holder distribution and deployer history still gracefully degrade
(partial/neutral score with a note) — Robinhood Chain doesn't have a
standard block explorer API publicly documented as of this writing. The
`ExplorerEnrichment` protocol in `providers/dexscreener.py` is the seam
to plug one in once available.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN
```

Get a Telegram bot token from [@BotFather](https://t.me/BotFather).

## Usage

**Telegram bot** (send `/newpair` any time you want a fresh scan):

```bash
export TELEGRAM_BOT_TOKEN=xxxxx
python -m robinhood_sniper.bot
```

Commands:
- `/newpair` or `/scan` — scan now, return scored candidates (mock data
  by default; set `SNIPER_SOURCE=onchain` for the real on-chain provider,
  or `SNIPER_SOURCE=dexscreener` for name-search)
- `/newpair 80` — only show candidates scoring 80+
- `/params` — show the active filter thresholds

**CLI** (no Telegram needed, good for testing):

```bash
python -m robinhood_sniper.cli scan                        # mock data
python -m robinhood_sniper.cli scan --source onchain        # real PoolCreated watcher (recommended)
python -m robinhood_sniper.cli scan --source dexscreener    # name-search, limited
python -m robinhood_sniper.cli scan --min-score 80
```

## Tests

```bash
pip install pytest
pytest
```

Covers the scoring rubric (hard filters, each factor's edge cases), the
higher-low structure detector, and the scan/rank/format pipeline against
the mock provider.

## Risk notes

Microcap meme-coin launches are adversarial: wash trading, bundled
launches, and coordinated buy/sell activity can make on-chain metrics
look better than reality (this is why buyer/deployer/holder checks exist
here, not just volume). A score of 100 is a filter passing, not a
guarantee. Keep position sizes small, decide your invalidation level
before entering, and treat slippage settings as an execution parameter,
not a safety net. This tool is for informational/research purposes only
and is not financial advice.

---

# Solana Utility Scout (`sol_utility_scout/`)

A second bot in this repo: it finds **newly launched Solana tokens that
look like real utility projects** (not pure memes) with real socials,
rates them, then **tracks each one for 7 days after launch**. Every check
logs anything that happened since the last one, and moves a **conviction
score** that rises as the project proves itself and falls on red flags.
Like the sniper, it never trades.

## How it works

1. **Discover**: polls DexScreener's Solana discovery feeds (new token
   profiles, boosts, community takeovers). A token only appears there if
   a team paid for a profile or boost, which already filters out most
   throwaway launches. It also runs DexScreener name searches for
   utility terms (AI agent, protocol, launchpad, DePIN, terminal, SDK,
   payments, oracle…) to catch older launches that never paid for a
   profile. Tokens launched in the **last 7 days** are
   evaluated, so a project that was quiet at launch but is now building
   traction still gets found.
2. **Enrich**: pulls each candidate's
   - market data from DexScreener (price, MC, liquidity, volume, buys/sells, links),
   - safety from RugCheck (mint/freeze authority, LP lock %, top-10
     holders excluding AMM pool and locker accounts, holder count,
     insider wallets, risk list),
   - liveness from the web: fetches the project website (real content?
     links to docs, GitHub or an app?) and reads Telegram member counts
     from the public t.me page.
3. **Rate** (0-100 points):

   | Factor | Pts | Earned by |
   |---|---:|---|
   | Utility | 25 | a concrete use case (AI, DeFi, Infra, DePIN, Tools, Payments, RWA, Gaming, Social) plus evidence the product exists (docs, whitepaper, roadmap, audit, GitHub, app). Meme markers subtract points |
   | Socials | 20 | a website that loads with real content, an X account (a link to a single tweet doesn't count), Telegram (scaled by member count), Discord, GitHub, docs |
   | Safety | 25 | mint and freeze revoked, LP locked/burned, holders spread out, no RugCheck dangers, few insiders |
   | Market | 15 | liquidity depth, a sensible MC/liquidity ratio |
   | Traction | 15 | holder count, 24h volume, buy pressure |

   **Hard filters** (rejected, or dropped if already tracked): rugged,
   active mint or freeze authority, liquidity under $10K, top-10 holders
   over 45%, more than 2 RugCheck "danger" risks. A token joins the
   watchlist only if it rates **≥55**, isn't classified as a meme, and
   scores at least 40% on Utility. It also needs at least one **proof
   of real tech**: a GitHub repo, docs/whitepaper, a link to a working
   app, a public API, or a Telegram bot. Keywords are cheap to write; these take real work to fake.
4. **Track for 7 days**: every cycle re-observes each watched token and
   logs **post-launch events**: socials or website added or removed,
   website down or back up, description updated, liquidity ±40-50%,
   holder milestones, Telegram growth, MC 2x/3x/5x/10x since first seen,
   new highs, drawdowns, DexScreener boosts, community takeovers, new or
   cleared RugCheck risks, and authority re-enabled or rugged.
5. **Conviction** starts at the first rating and moves each check by a
   step capped at ±8. The step is built from:
   - the change in rating,
   - the weights of the events since the last check,
   - holder growth,
   - +2 for each full day the token survives,
   - +2 for a higher-low price structure, -2 for lower highs.

   So conviction has to build up across several checks. 🟢 ≥75 HIGH,
   🟡 ≥55 MEDIUM, 🔴 LOW.
6. **Radar**: every 7 days of watching, each token gets a review. It
   **stays on the radar** for another 7 days if all of these hold:
   - its tech still reads as utility,
   - conviction held or rose and is still at least 55,
   - holders grew,
   - liquidity held within 10% of where the period started.

   The next period is then judged against the new baseline, so a token
   has to keep improving to stay. Otherwise it's marked **completed**
   with its final verdict and the checklist of which conditions failed.
   A token that falls 85% from its tracked high, loses its pair, or
   trips a hard filter is **dropped** at any point. From its third check
   on, it's also **flushed** when it stops being worth holding:
   - conviction falls under 55 (LOW),
   - it's fading: conviction 10+ points under its peak while market cap
     is under half of first seen,
   - liquidity has bled below 60% of first seen.

   You can flush a token by hand with `flush <CA|SYMBOL> "reason"` (CLI)
   or `/flush` (bot). Completed and
   dropped tokens stay in the history.

State is a JSON file (`SCOUT_STORE_PATH`, default `.sol_utility_scout.json`),
so a cron job that starts a fresh process each time works fine.

## Usage

```bash
pip install -r requirements.txt

# CLI
python -m sol_utility_scout scan                 # one cycle: update watchlist + discover new launches
python -m sol_utility_scout loop --every 10      # cycle every 10 min forever
python -m sol_utility_scout watchlist [--all]    # ranked by conviction (--all adds completed/dropped)
python -m sol_utility_scout token <CA|SYMBOL>    # full card + post-launch activity log
python -m sol_utility_scout check <CA>           # rate any Solana token right now (not added)
python -m sol_utility_scout flush <CA|SYMBOL> "reason"   # drop a watched token by hand

# Telegram bot (runs a cycle every SCOUT_INTERVAL_MIN minutes, pushes alerts)
export TELEGRAM_BOT_TOKEN=xxxxx
python -m sol_utility_scout.bot
```

Bot commands: `/scan`, `/watchlist`, `/all`, `/token <CA|SYMBOL>`,
`/check <CA>`, `/flush <CA|SYMBOL> [reason]`, `/subscribe` (push new picks, events and 7-day verdicts
to this chat), `/unsubscribe`.

All thresholds and weights live in `sol_utility_scout/config.py`.

**Limits:** X follower counts need a paid API, so X only counts as
present or absent. The utility classifier works on keywords, so a team
can dress a meme up with the right words. That's why the bot also
checks for product evidence and a live website, and keeps watching for
7 days before it trusts anything. Tests: `pytest tests/scout`. This
tool is not financial advice.
