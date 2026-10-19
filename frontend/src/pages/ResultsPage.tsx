import { useEffect, useMemo, useRef, useState } from "react";
import { AreaSeries, ColorType, createChart, LineSeries, UTCTimestamp } from "lightweight-charts";
import { Search, X } from "lucide-react";
import { api, Json, money, num, pct, shortHash, StrategyState, Trade } from "../api";
import PriceChart from "../components/PriceChart";
import TradeExplainer from "../components/TradeExplainer";
import { ErrorBox, Panel, Progress, Spinner, Stat, useJob } from "../components/ui";

function EquityChart({ points }: { points: { ts: number; equity: number; drawdown: number; n: number }[] }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || !points.length) return;
    const chart = createChart(el.current, {
      height: 260, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#111723" }, textColor: "#b3bcd1", panes: { separatorColor: "#273149" } },
      grid: { vertLines: { color: "#1b2334" }, horzLines: { color: "#1b2334" } },
      timeScale: { timeVisible: true, borderColor: "#273149" }, rightPriceScale: { borderColor: "#273149" },
    });
    // Seconds must strictly increase: nudge trades closing in the same second.
    let last = 0;
    const t = points.map((p) => { let s = Math.floor(p.ts / 1e9); if (s <= last) s = last + 1; last = s; return s as UTCTimestamp; });
    const eq = chart.addSeries(AreaSeries, { lineColor: "#60a5fa", topColor: "rgba(96,165,250,0.3)", bottomColor: "rgba(96,165,250,0.02)", title: "equity $" });
    eq.setData([{ time: (t[0] - 1) as UTCTimestamp, value: 0 }, ...points.map((p, k) => ({ time: t[k], value: p.equity }))]);
    const dd = chart.addSeries(LineSeries, { color: "#ef4444", lineWidth: 1, title: "drawdown $" }, 1);
    dd.setData([{ time: (t[0] - 1) as UTCTimestamp, value: 0 }, ...points.map((p, k) => ({ time: t[k], value: p.drawdown }))]);
    chart.panes()[1]?.setHeight(80);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return <div ref={el} style={{ height: 260 }} />;
}

export default function ResultsPage({ st, runId, setRunId, onRunDone }: { st: StrategyState; runId: number | null; setRunId: (n: number) => void; onRunDone?: () => void }) {
  const [run, setRun] = useState<Json>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [dirF, setDirF] = useState("");
  const [outF, setOutF] = useState("");
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState<{ total: number; items: Trade[] }>({ total: 0, items: [] });
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<Json>(null);
  const PAGE = 50;
  const id = runId ?? st.runs[0]?.id ?? null;

  const loadRun = async () => { if (id) { try { setRun(await api.getRun(id)); } catch (e) { setErr(String(e)); } } };
  useEffect(() => { setRun(null); setSel(null); setDetail(null); loadRun(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const job = useJob(run && run.status !== "done" && run.status !== "error" ? run.job?.id : null, () => { loadRun(); onRunDone?.(); });
  useEffect(() => {
    if (!id || run?.status !== "done") return;
    api.trades(id, { q, direction: dirF, outcome: outF, limit: PAGE, offset: page * PAGE }).then(setRows).catch((e) => setErr(String(e)));
  }, [id, run?.status, q, dirF, outF, page]);
  useEffect(() => {
    if (!id || sel === null) return;
    setDetail(null);
    api.trade(id, sel).then(setDetail).catch((e) => setErr(String(e)));
  }, [id, sel]);

  const m = run?.result?.metrics;
  const skippedByReason = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of run?.result?.skipped || []) { const k = s.reason.replace(/\d+(\.\d+)?/g, "#"); c[k] = (c[k] || 0) + 1; }
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [run]);

  if (!id) return <Panel title="Results"><div className="text-sm text-ink-400">No backtest has been run for this strategy yet. Approve the rules first.</div></Panel>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-ink-400">Run</span>
        <select className="input w-auto" value={id} onChange={(e) => setRunId(Number(e.target.value))}>
          {st.runs.map((r) => <option key={r.id} value={r.id}>#{r.id} · v{r.version} · {r.status} · {r.created_at.slice(0, 16).replace("T", " ")}</option>)}
        </select>
        {run && <span className="mono text-xs text-ink-400">executed spec v{run.version} · {shortHash(run.spec_hash)}</span>}
        {run?.result?.data?.synthetic && <span className="chip bg-amber-500/20 text-amber-200">SYNTHETIC DATA</span>}
      </div>
      <ErrorBox error={err} />
      {run && run.status !== "done" && (
        <Panel title="Backtest progress">
          {run.status === "error" || job?.status === "error" ? <ErrorBox error={job?.message ?? run.job?.message ?? "failed"} /> :
            <Progress value={job?.progress ?? 0} label={job?.message ?? "queued"} />}
          <pre className="mono mt-2 max-h-40 overflow-auto text-[11px] text-ink-500">{(job?.log || []).slice(-8).join("\n")}</pre>
        </Panel>
      )}
      {m && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
            <Stat label="Net profit (after costs)" value={money(m.net_profit)} tone={m.net_profit > 0 ? "pos" : m.net_profit < 0 ? "neg" : "none"} />
            <Stat label="Trades" value={m.trade_count} />
            <Stat label="Win rate" value={pct(m.win_rate)} />
            <Stat label="Profit factor" value={m.profit_factor ?? (m.trade_count ? "∞ (no losses)" : "—")} />
            <Stat label="Expectancy / trade" value={money(m.expectancy)} tone={m.expectancy > 0 ? "pos" : "neg"} />
            <Stat label="Expectancy (R)" value={num(m.expectancy_r, 3)} />
            <Stat label="Max drawdown" value={money(m.max_drawdown)} tone="neg" />
            <Stat label="Costs (comm + slip)" value={money((m.total_commission ?? 0) + (m.total_slippage ?? 0))} />
          </div>
          <div className="grid gap-4 xl:grid-cols-3">
            <Panel title="Equity curve & drawdown (net, per closed trade)" className="xl:col-span-2"><EquityChart points={run.result.equity} /></Panel>
            <Panel title="How fills were decided">
              <ul className="space-y-1 text-xs text-ink-300">{Object.entries(m.exit_resolutions || {}).map(([k, v]) => <li key={k} className="flex justify-between gap-2"><span>{k}</span><span className="mono">{v as number}</span></li>)}</ul>
              <div className="mt-2 text-xs text-ink-400">Data: warm-up from {run.result.data.warmup_from}, trading {run.result.data.trade_from} → {run.result.data.trade_to}; intrabar replay: {run.result.data.intrabar ?? "none"}.
                {run.result.data.rolls.length > 0 && <> Rolls: {run.result.data.rolls.map((r: Json) => `${r.trade_date} ${r.from}→${r.to}`).join(", ")}.</>}</div>
              {skippedByReason.length > 0 && <div className="mt-2 text-xs text-ink-400">Signals not taken ({run.result.skipped_total}): {skippedByReason.slice(0, 5).map(([k, v]) => `${k} (${v})`).join("; ")}</div>}
            </Panel>
          </div>
        </>
      )}
      {run?.status === "done" && (
        <div className="grid gap-4 2xl:grid-cols-2">
          <Panel title={`Trades (${rows.total})`} right={<>
            <div className="relative"><Search size={13} className="absolute left-2 top-2.5 text-ink-500" />
              <input className="input w-48 pl-7" placeholder="search: date, stop, NQZ6…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} /></div>
            <select className="input w-auto" value={dirF} onChange={(e) => { setDirF(e.target.value); setPage(0); }}><option value="">all</option><option value="long">long</option><option value="short">short</option></select>
            <select className="input w-auto" value={outF} onChange={(e) => { setOutF(e.target.value); setPage(0); }}><option value="">wins & losses</option><option value="win">wins</option><option value="loss">losses</option></select>
          </>}>
            <div className="max-h-[560px] overflow-auto">
              <table className="tbl w-full">
                <thead className="sticky top-0 bg-ink-900"><tr><th>#</th><th>Dir</th><th>Entry</th><th className="text-right">Fill</th><th className="text-right">Stop</th><th className="text-right">Target</th><th>Exit</th><th className="text-right">Net</th></tr></thead>
                <tbody>{rows.items.map((t) => (
                  <tr key={t.n} onClick={() => setSel(t.n)} className={`cursor-pointer border-t border-ink-800 hover:bg-ink-800 ${sel === t.n ? "bg-ink-800" : ""}`}>
                    <td className="mono text-ink-400">{t.n}</td>
                    <td className={t.direction === "long" ? "text-long" : "text-short"}>{t.direction}</td>
                    <td className="mono text-xs">{t.entry_time.slice(5, 16).replace("T", " ")}<div className="text-[10px] text-ink-500">{t.symbol}</div></td>
                    <td className="mono text-right">{t.entry_price}</td><td className="mono text-right">{t.initial_stop}</td><td className="mono text-right">{t.target ?? "—"}</td>
                    <td className="text-xs">{t.exit_reason}<div className="mono text-[10px] text-ink-500">{t.exit_price}</div></td>
                    <td className={`mono text-right ${t.net_pnl > 0 ? "text-long" : "text-short"}`}>{money(t.net_pnl)}</td>
                  </tr>
                ))}</tbody>
              </table>
              {!rows.items.length && <div className="p-3 text-sm text-ink-400">No trades match.</div>}
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-ink-400">
              <button className="btn-ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Prev</button>
              <span>{page * PAGE + 1}–{Math.min(rows.total, (page + 1) * PAGE)} of {rows.total}</span>
              <button className="btn-ghost" disabled={(page + 1) * PAGE >= rows.total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </Panel>
          <Panel title={sel ? `Trade #${sel}` : "Trade detail"} right={sel && <button className="text-ink-400" onClick={() => setSel(null)}><X size={15} /></button>}>
            {!sel && <div className="text-sm text-ink-400">Click a trade to see its chart, indicators, signal, entry, stop, target, exit, costs and the exact approved rules that triggered it.</div>}
            {sel && !detail && <Spinner />}
            {detail && (
              <div className="space-y-3">
                <PriceChart data={detail.chart} overlay={detail.overlay} height={380} />
                <div className="flex flex-wrap gap-2 text-xs">
                  <span className="text-ink-400">Indicators at the signal bar:</span>
                  {Object.entries(detail.indicators_at_signal).map(([k, v]) => <span key={k} className="chip mono bg-ink-800 text-ink-200">{k} = {num(v as number, 4)}</span>)}
                </div>
                <TradeExplainer t={detail.trade} />
                <details className="rounded-lg border border-ink-700 p-3" open>
                  <summary className="cursor-pointer text-sm font-semibold text-ink-100">Approved rules that produced this trade · v{detail.version} · <span className="mono">{shortHash(detail.spec_hash)}</span>
                    {detail.hash_matches_trade ? <span className="chip ml-2 bg-green-500/15 text-green-300">hash verified</span> : <span className="chip ml-2 bg-red-500/15 text-red-300">hash mismatch</span>}</summary>
                  <div className="mt-2 space-y-2">{detail.rules.map((s: Json) => (
                    <div key={s.key}><div className="text-xs font-semibold uppercase tracking-wide text-ink-400">{s.title}</div>
                      <ul className="text-xs text-ink-300">{s.lines.map((l: string, k: number) => <li key={k} className="whitespace-pre-wrap">{l}</li>)}</ul></div>
                  ))}</div>
                </details>
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
