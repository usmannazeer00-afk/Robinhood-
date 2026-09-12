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

None of the three give unique-buyer time series, holder distribution, or
deployer history yet — Robinhood Chain doesn't have a standard block
explorer API publicly documented as of this writing. Those factors
gracefully degrade (partial/neutral score with a note) rather than crash
when the data isn't available. The `ExplorerEnrichment` protocol in
`providers/dexscreener.py` is the seam to plug one in once available.

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
