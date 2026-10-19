# AI-Backtest

AI-assisted backtesting dashboard for NQ and GC futures on your own Databento data.

Workflow: describe a strategy → inspect the AI's interpretation on a chart → modify or approve → run the backtest → inspect every trade.

## Step 0: inspect your data (run this on the VPS first)

```bash
pip install databento pandas pyarrow
python scripts/inspect_data.py /path/to/your/databento/folder --json data-report.json
```

The script is read-only. It reports each file's schema, symbols, timestamp range, ordering problems, instruments (including spreads) and coverage. Paste the printed output into the chat so the data adapter can be fitted to what's actually in your files.

Add `--max-records 0` to scan every record (slower on multi-GB MBO files).
