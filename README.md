# AI-Backtest

AI-assisted backtesting dashboard for NQ and GC futures on your own Databento data.

**Workflow:** describe a strategy → inspect the AI's interpretation on a chart → modify it until it's right → approve it → run the backtest → inspect every trade.

## What it guarantees

- **Nothing is assumed.** The AI (DeepSeek through OpenRouter) only *proposes* rule values, and each one must quote your own words. Anything without a quote, or anything missing, becomes an explicit question. "10/20 EMA" adds two indicators; it does not create a crossover.
- **One spec, everywhere.** Rules are stored as a versioned, hashed spec. The plain-English summary is generated from the spec (not by the AI), and the chart preview and the backtest run the same engine code on the same spec.
- **Preview = backtest.** Preview examples are the first trades of the date range from the real engine. Because nothing looks ahead, the backtest reproduces them exactly.
- **Explicit approval.** "Approve strategy and run backtest" stays disabled until every rule is decided, a preview from your data exists for this exact version, and there are no blocking data issues. Any change creates a new version that needs its own preview and approval. A run is refused unless its spec hash equals the approved hash.
- **Every trade is traceable.** Each trade shows the indicator values at the signal, the rule evidence, planned vs actual entry/stop/target, the exit and how it was decided (tick replay or policy), costs, and the approved rules with the hash.

## Data handling (fitted to the VPS folder)

- Reads Databento DBN files (`.dbn` / `.dbn.zst`): **MBO**, **trades**, TBBO/MBP (trade records), **OHLCV**, and **definition** files. CSV/Parquet bars or ticks are also accepted.
- Bars are built from **trade prints** (MBO `T` records / trades schema) using exchange time (`ts_event`). Trade prints are kept per session and replayed when a bar reaches both stop and target, or for fills inside a bar.
- Files are one UTC day each, while a CME session runs 18:00–17:00 ET, so sessions are merged across files.
- Continuous downloads (`NQ.v.0`, `GC.v.0`) map to contract ids. Their symbols are learned from raw-symbol files, such as your `gap_fill` downloads (e.g. 261401 = NQZ6). Otherwise they show as `NQ id42004177`.
- Roll days with two contracts (your gap-fill days) are reported, and the strategy's **contract rule** decides which one trades:
  - most active by same-day volume, which matches your gap-fill files, or
  - by prior-session volume (strict), or
  - one specific contract.
- Validation covers: ordering (ts_recv/ts_event), duplicates, missing sessions vs the CME calendar, intraday gaps, partial roll days, overlapping files, spreads, unknown instruments, and synthetic data.

## Run it on the VPS

```bash
git clone https://github.com/Sandeep-09/AI-Backtest.git && cd AI-Backtest
cp .env.example .env
#   edit .env: DATABENTO_DIR=/root/databento/raw   OPENROUTER_API_KEY=sk-or-...
docker compose up -d --build
```

The dashboard listens on `127.0.0.1:8000` on the VPS because it has **no login**. Open it from your computer through an SSH tunnel:

```bash
ssh -L 8000:localhost:8000 root@YOUR_VPS     # then browse http://localhost:8000
```

Then, on the single screen (TradingView-style: chart in the middle, strategy on the right, results at the bottom):

1. Click **Read data** (top-right button). The first read streams all MBO files; expect several minutes per GB, because your NQ MBO alone is roughly 15 GB. Results are cached in `./var`, so later starts are instant.
2. In **Chat**, describe a strategy or pick a template.
3. Answer the open questions one at a time in **Strategy**. The chart draws the preview trades as soon as the rules allow.
4. Click **Approve & run**. The numbers appear in the bottom panel and every trade is drawn on the chart.
5. Click any trade, on the chart or in **List of trades**, to see its signal, entry, stop, target, exit, costs and the approved rules.

`./var` holds the cache and the database. Delete it to start from scratch; your Databento folder is never written to.

### Run the EMA/VWAP strong-candle strategy (NQ and GC)

1. Go to **Chat → template "NQ: 10/20/30/60 EMA + VWAP strong-candle breakout"**. Answer the remaining questions, then click **Approve & run**. Do the same with the GC template.
2. Optionally print a paste-ready summary:
   ```bash
   docker compose exec backtest python -m app.report --list
   docker compose exec backtest python -m app.report --run 1
   ```

### Interpretations prepared outside the app

`strategies/*.json` holds interpretations written by Claude in chat (e.g. `ema_vwap_strong_candle_NQ.json` / `_GC.json`: 10/20/30/60 EMA + VWAP strong-candle breakout, longs only, 3R, full Globex session). They appear as templates in **Chat**; other files can be loaded with **Import file**. Your own words travel with the file, and every value goes through the same quote check as the in-app AI. Anything you didn't decide stays an open question.

### Inspect data without the app

```bash
pip install databento pandas pyarrow
python scripts/inspect_data.py /path/to/databento/folder --json data-report.json
```

## Try it with synthetic data

```bash
docker compose build
mkdir -p demo-data && docker run --rm -v "$PWD/demo-data:/out" ai-backtest:latest python -m app.data.synthetic /out
DATABENTO_DIR=$PWD/demo-data docker compose up -d
```

Synthetic files mimic the real layout (continuous daily files, MBO snapshots, a roll and a gap-fill file). They are labelled **SYNTHETIC** everywhere.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend && pytest                                   # engine, interpretation, data and API-flow tests
DATA_DIR=../demo-data VAR_DIR=../var uvicorn app.main:app --reload --port 8000
cd ../frontend && npm ci && npm run dev                # http://localhost:5173 (proxies /api)
python scripts/e2e_smoke.py --url http://localhost:8000 # full browser flow on synthetic data
```

Settings (environment): `DATA_DIR`, `VAR_DIR`, `OPENROUTER_API_KEY`, `LLM_MODEL` (default `deepseek/deepseek-chat`; check OpenRouter for current DeepSeek ids), `LLM_MOCK=1` (offline parser only).

## Known limitations

These are also shown in the app before approval.

- Fills follow trade prints, not bid/ask. There is no order-queue model for limit or target fills: the L3 book is not used for queue position yet. Slippage is a fixed number of ticks.
- Prices are not back-adjusted. Indicators restart at each roll, and positions are closed before a roll.
- One position at a time. Breakeven and trailing stops are decided at bar closes. Supported indicators: EMA, SMA, ATR, RSI, session VWAP, highest/lowest N, prior-session high/low. Requests the rules can't express are flagged and must be acknowledged; they are never approximated.
