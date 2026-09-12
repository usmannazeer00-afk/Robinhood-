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

## Data source — important caveat

There isn't yet a standard public API for Robinhood Chain DEX data. This
repo ships:

- **`MockProvider`** — synthetic candidates spanning the full quality
  range (a clean accelerating setup, a wash-trading look-alike, a
  rug-flagged deployer, a too-young launch). This is the default, so
  `/newpair` works out of the box for demoing the scoring logic.
- **`DexScreenerProvider`** — a real HTTP client against DexScreener's
  public search API, filtered by chain id. DexScreener gives market cap,
  liquidity, and m5/h1 volume/buy-sell counts, which is enough to score
  age/mcap/liquidity/MC-liquidity-ratio/an approximate volume-acceleration
  signal. It does **not** give unique-buyer time series, holder
  distribution, deployer history, or a price-history array — those
  factors gracefully degrade (partial/neutral score with a note) rather
  than crash when the data isn't available.

To get full-fidelity scoring you'll want to pair `DexScreenerProvider`
with a Robinhood Chain block-explorer API (most Arbitrum-Orbit chains run
a Blockscout-compatible one) for holder distribution and deployer
history, and a price-history source for the higher-low check. The
`ExplorerEnrichment` protocol in `providers/dexscreener.py` is the seam
to plug that in — implement `.enrich(token) -> token` against whatever
explorer/indexer Robinhood Chain ends up exposing, and pass it to
`DexScreenerProvider(enrichment=...)`.

Set `DEX_CHAIN_ID` / `DEX_SEARCH_QUERY` / `DEX_API_BASE` once you know
the confirmed chain id DexScreener uses for Robinhood Chain.

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
  by default; set `DEX_LIVE=true` for the real DexScreener provider)
- `/newpair 80` — only show candidates scoring 80+
- `/params` — show the active filter thresholds

**CLI** (no Telegram needed, good for testing):

```bash
python -m robinhood_sniper.cli scan            # mock data
python -m robinhood_sniper.cli scan --live     # DexScreener-backed
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
