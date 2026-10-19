/* Strategy-tester style panel: Overview · List of trades (+ detail) · Assumptions & data. */
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { AreaSeries, ColorType, createChart, LineSeries, UTCTimestamp } from "lightweight-charts";
import { CheckCircle2, ChevronDown, ChevronUp, Search, XCircle } from "lucide-react";
import { api, Json, money, num, pct, plain, shortHash, StrategyState, Trade, TradeMark } from "../api";
import { NoteList, Progress } from "./ui";

export type Tab = "overview" | "trades" | "assumptions";

function Equity({ points }: { points: { ts: number; equity: number; drawdown: number }[] }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || !points.length) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#1e222d" }, textColor: "#787b86", fontSize: 11, panes: { separatorColor: "#2a2e39" } },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(42,46,57,0.6)" } },
      timeScale: { borderColor: "#2a2e39", timeVisible: true }, rightPriceScale: { borderColor: "#2a2e39" },
    });
    let last = 0;
    const t = points.map((p) => { let s = Math.floor(p.ts / 1e9); if (s <= last) s = last + 1; last = s; return s as UTCTimestamp; });
    const up = points[points.length - 1].equity >= 0;
    const eq = c.addSeries(AreaSeries, { lineColor: up ? "#089981" : "#f23645", topColor: up ? "rgba(8,153,129,0.35)" : "rgba(242,54,69,0.3)",
                                        bottomColor: "rgba(0,0,0,0)", lineWidth: 2, priceLineVisible: false });
    eq.setData([{ time: (t[0] - 1) as UTCTimestamp, value: 0 }, ...points.map((p, k) => ({ time: t[k], value: p.equity }))]);
    const dd = c.addSeries(LineSeries, { color: "#f23645", lineWidth: 1, priceLineVisible: false }, 1);
    dd.setData([{ time: (t[0] - 1) as UTCTimestamp, value: 0 }, ...points.map((p, k) => ({ time: t[k], value: p.drawdown }))]);
    c.panes()[1]?.setHeight(45);
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points]);
  return <div ref={el} className="h-full w-full" />;
}

function Big({ label, value, tone, sub }: { label: string; value: ReactNode; tone?: "up" | "down"; sub?: string }) {
  return (
    <div className="min-w-[120px]">
      <div className="text-[11px] text-tv-muted">{label}</div>
      <div className={`text-[20px] font-semibold leading-tight ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-tv-strong"}`}>{value}</div>
      {sub && <div className="text-[11px] text-tv-muted">{sub}</div>}
    </div>
  );
}

export function TradeDetail({ t, rules, ruleId, verified }: { t: Trade; rules?: { title: string; lines: string[] }[]; ruleId?: string; verified?: boolean }) {
  const [showRules, setShowRules] = useState(false);
  const long = t.direction === "long";
  const flat = (ev: Json[]): Json[] => ev.flatMap((e) => (e.items ? flat(e.items) : [e]));
  const Step = ({ n, title, color, children }: { n: number; title: string; color: string; children: ReactNode }) => (
    <div className="rounded border border-tv-line bg-tv-bg p-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold" style={{ color }}><span className="flex h-4 w-4 items-center justify-center rounded-full bg-tv-hover text-[10px] text-tv-text">{n}</span>{title}</div>
      <div className="space-y-0.5 text-[12px] text-tv-text">{children}</div>
    </div>
  );
  const R = ({ k, v }: { k: string; v: ReactNode }) => <div className="flex justify-between gap-2"><span className="text-tv-muted">{k}</span><span className="mono text-right">{v}</span></div>;
  return (
    <div className="space-y-2">
      <div>
        <div className="flex items-center gap-2">
          <span className={`chip ${long ? "bg-up/20 text-up" : "bg-down/20 text-down"}`}>{long ? "LONG" : "SHORT"}</span>
          <span className="text-[15px] font-semibold text-tv-strong">Trade #{t.n}</span>
          <span className={`ml-auto whitespace-nowrap mono text-[16px] font-semibold ${t.net_pnl > 0 ? "text-up" : "text-down"}`}>{money(t.net_pnl)}</span>
        </div>
        <div className="mt-0.5 text-[12px] text-tv-muted">{t.symbol} · {t.entry_time?.slice(0, 16).replace("T", " ")} → {t.exit_time?.slice(11, 16)} · {t.exit_reason}</div>
      </div>
      <div className="grid gap-2">
        <Step n={1} title="Signal" color="#d1d4dc">
          <div className="text-tv-muted">bar closed {t.signal_time?.slice(11, 16)}</div>
          {flat(t.evidence || []).map((e, k) => (
            <div key={k} className="flex items-start gap-1">
              {e.result ? <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-up" /> : <XCircle size={12} className="mt-0.5 shrink-0 text-tv-dim" />}
              <span className={e.result ? "" : "text-tv-muted"}>{e.rule.replace(/^the bar's /, "")} <span className="mono text-tv-muted">({num(e.left, 2)} vs {num(e.right, 2)})</span></span>
            </div>
          ))}
        </Step>
        <Step n={2} title="Entry" color="#2962ff">
          <R k="Planned" v={t.planned_entry} />
          <R k="Filled" v={`${t.entry_price}${t.entry_price !== t.planned_entry ? ` (slippage ${num(Math.abs(t.entry_price - t.planned_entry), 2)})` : ""}`} />
          <R k="Time" v={t.entry_time?.slice(11, 19)} />
          <R k="Contracts" v={t.qty} />
        </Step>
        <Step n={3} title="Stop" color="#f23645">
          <R k="Stop" v={t.initial_stop} />
          <div className="text-tv-muted">{t.stop_calc}</div>
          <R k="Risk" v={`${num(t.risk_points, 2)} pts · ${money(t.risk_usd)}`} />
        </Step>
        <Step n={4} title="Target" color="#089981">
          <R k="Target" v={t.target ?? "none"} />
          <div className="text-tv-muted">{t.target_calc}</div>
          <R k="Reward : risk" v={t.planned_rr ? `${t.planned_rr} : 1` : "—"} />
        </Step>
        <Step n={5} title="Exit" color="#f7a600">
          <R k="Price" v={t.exit_price} />
          <R k="Why" v={t.exit_reason} />
          <R k="Time" v={t.exit_time?.slice(0, 19).replace("T", " ")} />
          <div className="text-tv-muted">{plain(t.exit_resolution)}</div>
        </Step>
        <Step n={6} title="Result" color="#d1d4dc">
          <R k="Gross (after slippage)" v={money(t.gross_pnl)} />
          <R k="Commission" v={money(-t.commission)} />
          <R k="Net" v={<span className={t.net_pnl > 0 ? "text-up" : "text-down"}>{money(t.net_pnl)}</span>} />
          <R k="R multiple" v={num(t.net_r, 2)} />
        </Step>
      </div>
      {ruleId && (
        <div className="flex items-center gap-2 text-[12px]">
          {verified ? <CheckCircle2 size={13} className="text-up" /> : <XCircle size={13} className="text-down" />}
          <span className="text-tv-muted">Made by the approved rules (ID <span className="mono">{shortHash(ruleId)}</span>){verified ? "" : " — MISMATCH"}</span>
          {rules && <button className="text-accent" onClick={() => setShowRules(!showRules)}>{showRules ? "hide rules" : "show rules"}</button>}
        </div>
      )}
      {showRules && rules && (
        <div className="grid gap-2 rounded border border-tv-line p-2.5 text-[12px]">
          {rules.map((s) => <div key={s.title}><div className="text-tv-muted">{s.title}</div>{s.lines.map((l, k) => <div key={k} className="whitespace-pre-wrap text-tv-text">{l}</div>)}</div>)}
        </div>
      )}
    </div>
  );
}

export default function BottomPanel({ st, tab, setTab, open, setOpen, run, runJob, previewTrades, previewDetails, selected, onSelect, onApprove }: {
  st: StrategyState | null; tab: Tab; setTab: (t: Tab) => void; open: boolean; setOpen: (b: boolean) => void;
  run: Json | null; runJob: Json | null; previewTrades: TradeMark[]; previewDetails: Record<string, Trade>;
  selected: number | null; onSelect: (n: number | null) => void; onApprove: () => void;
}) {
  const [q, setQ] = useState("");
  const [side, setSide] = useState("");
  const [outcome, setOutcome] = useState("");
  const [rows, setRows] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const isRun = run && run.status === "done";
  const m = isRun ? run.result.metrics : null;

  useEffect(() => {
    if (!isRun) return;
    api.trades(run.id, { q, direction: side, outcome, limit: 1000 }).then((r) => { setRows(r.items); setTotal(r.total); }).catch(() => {});
  }, [isRun, run?.id, q, side, outcome]); // eslint-disable-line react-hooks/exhaustive-deps
  const list: Trade[] = useMemo(() => {
    if (isRun) return rows;
    const src = previewTrades.map((t) => previewDetails[String(t.n)]).filter(Boolean);
    return src.filter((t) => (!side || t.direction === side) && (!outcome || (outcome === "win" ? t.net_pnl > 0 : t.net_pnl <= 0)) &&
      (!q || JSON.stringify([t.n, t.direction, t.exit_reason, t.entry_time, t.symbol]).toLowerCase().includes(q.toLowerCase())));
  }, [isRun, rows, previewTrades, previewDetails, q, side, outcome]);
  const cum = useMemo(() => { let c = 0; return list.map((t) => (c += t.net_pnl)); }, [list]);

  const Tabs = (
    <div className="flex items-center border-b border-tv-line pl-1">
      {([["overview", "Overview"], ["trades", isRun ? `List of trades (${total})` : `Preview trades (${previewTrades.length})`], ["assumptions", "Assumptions & data"]] as [Tab, string][]).map(([k, l]) => (
        <button key={k} className={`tab ${tab === k && open ? "tab-on" : ""}`} onClick={() => { setTab(k); setOpen(true); }}>{l}</button>
      ))}
      <div className="ml-auto flex items-center gap-2 pr-2 text-[11px]">
        {isRun && <span className="text-tv-muted">Backtest #{run.id} · rules v{run.version}</span>}
        {!isRun && st?.preview?.ok && <span className="text-warn">Preview only — not a backtest result</span>}
        {run?.result?.data?.synthetic && <span className="chip bg-warn/20 text-warn">SYNTHETIC DATA</span>}
        <button className="btn-ghost px-1.5 py-1 text-tv-muted" onClick={() => setOpen(!open)} title={open ? "Collapse" : "Expand"}>{open ? <ChevronDown size={15} /> : <ChevronUp size={15} />}</button>
      </div>
    </div>
  );

  if (!open) return Tabs;
  return (
    <div className="flex h-full flex-col">
      {Tabs}
      <div className="min-h-0 flex-1 overflow-auto scroll-thin">
        {tab === "overview" && (
          runJob && (runJob.status === "running" || runJob.status === "queued") ? (
            <div className="mx-auto max-w-md p-6"><Progress value={runJob.progress} label={runJob.message} /></div>
          ) : isRun && m ? (
            <div className="flex h-full gap-4 p-3">
              <div className="grid shrink-0 grid-cols-2 content-start gap-x-6 gap-y-3 lg:grid-cols-4">
                <Big label="Net profit" value={money(m.net_profit)} tone={m.net_profit > 0 ? "up" : m.net_profit < 0 ? "down" : undefined} sub="after commission & slippage" />
                <Big label="Total trades" value={m.trade_count} sub={m.trade_count ? `${m.long?.trade_count ?? 0} long · ${m.short?.trade_count ?? 0} short` : ""} />
                <Big label="Win rate" value={pct(m.win_rate)} sub={m.trade_count ? `${m.wins} won · ${m.losses} lost` : ""} />
                <Big label="Profit factor" value={m.profit_factor ?? (m.trade_count ? "∞" : "—")} />
                <Big label="Max drawdown" value={money(m.max_drawdown)} tone="down" />
                <Big label="Avg trade" value={money(m.expectancy)} tone={m.expectancy > 0 ? "up" : "down"} sub={m.expectancy_r !== null ? `${num(m.expectancy_r, 2)} R` : ""} />
                <Big label="Avg win / loss" value={<span className="text-[15px]">{money(m.avg_win)} / {money(m.avg_loss)}</span>} />
                <Big label="Costs paid" value={money((m.total_commission ?? 0) + (m.total_slippage ?? 0))} sub="commission + slippage" />
              </div>
              <div className="min-w-[280px] flex-1 rounded border border-tv-line">{run.result.equity?.length ? <Equity points={run.result.equity} /> : <div className="p-4 text-tv-muted">No trades.</div>}</div>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
              {st?.preview?.ok && st.preview.scanned ? (
                <>
                  <div className="text-[14px] text-tv-strong">Preview: {previewTrades.length} trade{previewTrades.length === 1 ? "" : "s"} in the first {st.preview.scanned.trade_dates} sessions ({st.preview.scanned.from} → {st.preview.scanned.to})</div>
                  <div className="max-w-xl text-tv-muted">These are drawn on the chart so you can check the rules behave as intended. They are not results. Approve the rules to backtest the full date range.</div>
                  {st.gate.can_approve && <button className="btn-go mt-1" onClick={onApprove}>Approve & run backtest</button>}
                </>
              ) : (
                <div className="text-tv-muted">{st ? "Answer the open questions on the right; the chart and preview update as you go." : "Describe a strategy on the right to begin."}</div>
              )}
            </div>
          )
        )}
        {tab === "trades" && (
          <div className="flex h-full">
            <div className="flex w-full min-w-0 flex-col">
              <div className="flex items-center gap-2 px-2 py-1.5">
                <div className="relative w-56"><Search size={13} className="absolute left-2 top-2 text-tv-dim" />
                  <input className="input py-1 pl-7" placeholder="Search trades" value={q} onChange={(e) => setQ(e.target.value)} /></div>
                <select className="input w-auto py-1" value={side} onChange={(e) => setSide(e.target.value)}><option value="">Long & short</option><option value="long">Long</option><option value="short">Short</option></select>
                <select className="input w-auto py-1" value={outcome} onChange={(e) => setOutcome(e.target.value)}><option value="">Wins & losses</option><option value="win">Wins</option><option value="loss">Losses</option></select>
              </div>
              <div className="min-h-0 flex-1 overflow-auto scroll-thin">
                <table className="tbl w-full whitespace-nowrap text-[12px]">
                  <thead><tr><th>#</th><th>Type</th><th>Entry</th><th className="text-right">Price</th><th>Exit</th><th className="text-right">Price</th><th>Why</th><th className="text-right">P&L</th><th className="text-right">Cum. P&L</th><th className="text-right">R</th></tr></thead>
                  <tbody>{list.map((t, k) => (
                    <tr key={t.n} onClick={() => onSelect(t.n)} className={`cursor-pointer border-t border-tv-line hover:bg-tv-hover ${selected === t.n ? "bg-accent/15" : ""}`}>
                      <td className="text-tv-muted">{t.n}</td>
                      <td className={t.direction === "long" ? "text-up" : "text-down"}>{t.direction === "long" ? "Long" : "Short"}</td>
                      <td className="mono">{t.entry_time?.slice(5, 16).replace("T", " ")}</td><td className="mono text-right">{t.entry_price}</td>
                      <td className="mono">{t.exit_time?.slice(5, 16).replace("T", " ")}</td><td className="mono text-right">{t.exit_price}</td>
                      <td>{t.exit_reason}</td>
                      <td className={`mono text-right ${t.net_pnl > 0 ? "text-up" : "text-down"}`}>{money(t.net_pnl)}</td>
                      <td className={`mono text-right ${cum[k] > 0 ? "text-up" : "text-down"}`}>{money(cum[k])}</td>
                      <td className="mono text-right">{num(t.net_r, 2)}</td>
                    </tr>
                  ))}</tbody>
                </table>
                {!list.length && <div className="p-3 text-tv-muted">No trades{st?.preview?.ok ? " in the preview sessions" : ""}.</div>}
              </div>
            </div>
          </div>
        )}
        {tab === "assumptions" && st && (
          <div className="grid gap-4 p-3 lg:grid-cols-3">
            <div>
              <div className="label">What the backtest assumes</div>
              <table className="w-full text-[12px]"><tbody>{st.assumptions.rows.map((r) => (
                <tr key={r.label} className="border-t border-tv-line align-top"><td className="w-36 py-1 pr-2 text-tv-muted">{r.label}</td><td className={`py-1 ${r.missing ? "text-warn" : "text-tv-text"}`}>{r.value}</td></tr>
              ))}</tbody></table>
            </div>
            <div>
              <div className="label">What it can't know</div>
              <ul className="list-disc space-y-1 pl-4 text-[12px] text-tv-text">{st.assumptions.limitations.map((l, k) => <li key={k} className={l.startsWith("SOME") ? "font-semibold text-warn" : ""}>{l}</li>)}</ul>
            </div>
            <div>
              <div className="label">Data notes for {st.spec.instrument ?? "this instrument"}</div>
              <div className="text-[12px]"><NoteList issues={st.assumptions.data_issues} empty="No data notes." /></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
