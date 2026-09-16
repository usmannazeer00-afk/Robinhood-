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

# Binance 15m Futures Short-Setup Scanner (`binance_shorts`)

A second, independent bot in this repo: scans Binance USDT-M perpetual
futures on the **15-minute chart** and scores how good a *short* setup
each symbol currently is. Same philosophy as the sniper bot above --
**scanner and alerting only, it never places an order** -- and it only
ever calls Binance's public, unauthenticated market-data endpoints, so
there's no API key to configure or protect.

## Strategy implemented

Rather than picking tops, the rubric rewards a **pullback-into-resistance
short inside an already-confirmed downtrend**: a higher-timeframe filter
keeps it from fighting a strong uptrend, and full points only come when
trend, structure, a price-action rejection, momentum, order flow, and
crowded-long positioning all agree.

| Factor | Points |
|---|---|
| Higher-timeframe (1h) trend below EMA20/EMA50 | 15 |
| 15m swing structure (lower highs / lower lows) | 15 |
| Rejection candle at resistance (shooting star / bearish engulfing) | 15 |
| Momentum turning down (RSI rollover, bearish divergence, or MACD cross) | 15 |
| Volume / order-flow (bearish volume spike, taker sell dominance) | 15 |
| Positioning (funding rate + open interest into the move) | 15 |
| Risk/reward (ATR-based entry/stop/target) | 10 |
| **Total** | **100** |

Score bands: **>=75 = 🟢 SHORT**, **60-74 = 🟡 WATCH**, **<60 = 🔴 AVOID**.
Hard filters (forced to 0 regardless of other factors): not enough 15m/1h
candle history yet, or 24h quote volume below the liquidity floor (thin
futures books are unreliable to trade regardless of setup quality).

All thresholds live in `binance_shorts/config.py` (`ShortScannerConfig`).

## Data source

`BinanceFuturesProvider` reads only public REST endpoints on
`fapi.binance.com` -- `exchangeInfo`, `ticker/24hr`, `klines`,
`premiumIndex`, `openInterestHist` -- none of which need an API key or
request signing, so this provider can only ever read market state, never
touch an account or place a trade. It pre-filters to USDT-M perpetuals
above a 24h quote-volume floor (avoiding thin/manipulable books), ranks
by volume, then enriches the top symbols concurrently under a fixed time
budget (mirroring the retry/backoff and time-budgeted concurrency used by
the on-chain sniper provider above).

`MockFuturesProvider` (default) generates a deterministic set of
synthetic symbols spanning the full range -- a clean downtrend pullback
that scores 90+, a strong uptrend that's correctly avoided, a choppy
symbol, and a too-thin one -- so `/shortscan` works out of the box.

## Usage

**Telegram bot:**

```bash
export TELEGRAM_BOT_TOKEN=xxxxx
export BINANCE_SOURCE=binance   # omit for mock demo data
python -m binance_shorts.bot
```

Commands:
- `/shortscan [min_score]` -- scan now, return every candidate at/above min_score
- `/bestshort` -- scan now, return only the single best short setup right now
- `/params` -- show the active scanner configuration

**CLI:**

```bash
python -m binance_shorts.cli scan                            # mock data
python -m binance_shorts.cli scan --source binance            # real Binance USDT-M futures data
python -m binance_shorts.cli scan --source binance --best     # only the single best setup
python -m binance_shorts.cli scan --min-score 60
```

## Risk notes

Shorting leveraged perpetual futures carries liquidation risk that spot
and simple meme-coin sniping don't: a stop-loss protects you against the
market, but not against an exchange-side liquidation cascade, a funding
bill, or slippage on a thin book. A 🟢 SHORT verdict is a filter passing,
not a guarantee -- confirm the setup on the chart yourself, size small,
and set your stop before entering, not after. This tool is for
informational/research purposes only and is not financial advice.
