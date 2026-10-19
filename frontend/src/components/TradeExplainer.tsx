import { ReactNode } from "react";
import { Json, money, num } from "../api";

function Row({ k, v, tone }: { k: string; v: ReactNode; tone?: string }) {
  return <div className="flex justify-between gap-3 py-0.5 text-sm"><span className="text-ink-400">{k}</span><span className={`mono text-right ${tone ?? "text-ink-100"}`}>{v}</span></div>;
}

function Evidence({ items }: { items: Json[] }) {
  return (
    <ul className="space-y-1">
      {items.map((e, k) => e.group ? (
        <li key={k} className="text-xs"><span className="text-ink-400">{e.group.toUpperCase()} ({e.result ? "true" : "false"}):</span><div className="ml-3"><Evidence items={e.items} /></div></li>
      ) : (
        <li key={k} className="rounded-md bg-ink-850 px-2 py-1 text-xs">
          <div className="flex items-center gap-2"><span className={`chip ${e.result ? "bg-green-500/15 text-green-300" : "bg-red-500/15 text-red-300"}`}>{e.result ? "true" : "false"}</span><span className="text-ink-200">{e.rule}</span></div>
          <div className="mono mt-0.5 text-ink-400">
            left {num(e.left, 4)} vs right {num(e.right, 4)}
            {e.left_prev !== undefined && <> · previous bar: {num(e.left_prev, 4)} vs {num(e.right_prev, 4)}</>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function TradeExplainer({ t, highlight = [], showCosts = true }: { t: Json; highlight?: string[]; showCosts?: boolean }) {
  const hl = (k: string) => (highlight.includes(k) ? "ring-2 ring-warn/70" : "");
  const long = t.direction === "long";
  const orderTxt = { market_next_open: "Market at next bar's open", stop_beyond_signal_bar: "Stop order beyond the signal bar", limit_at_level: "Limit order" }[t.order?.type as string] ?? t.order?.type;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className={`rounded-lg border border-ink-700 p-3 ${hl("signal")}`}>
        <div className="mb-1 flex items-center justify-between"><h4 className="text-sm font-semibold text-accent">1 · Signal</h4><span className="mono text-xs text-ink-400">bar closed {t.signal_time?.slice(0, 19).replace("T", " ")}</span></div>
        <Evidence items={t.evidence || []} />
      </div>
      <div className={`rounded-lg border border-ink-700 p-3 ${hl("entry")}`}>
        <h4 className="mb-1 text-sm font-semibold" style={{ color: long ? "#22c55e" : "#ef4444" }}>2 · Entry ({t.direction})</h4>
        <Row k="Order" v={orderTxt + (t.order?.level ? ` @ ${t.order.level}` : "")} />
        <Row k="Planned entry" v={t.planned_entry} />
        <Row k="Actual fill" v={t.entry_price} />
        <Row k="Fill time" v={t.entry_time?.slice(0, 19).replace("T", " ")} />
        <div className="mt-1 text-xs text-ink-400">{t.entry_how}</div>
        <Row k="Contracts" v={t.qty} />
      </div>
      <div className={`rounded-lg border border-red-500/30 p-3 ${hl("stop")}`}>
        <h4 className="mb-1 text-sm font-semibold text-short">3 · Stop loss</h4>
        <Row k="Initial stop" v={t.initial_stop} />
        <div className="text-xs text-ink-400">= {t.stop_calc}</div>
        <Row k="Risk" v={`${num(t.risk_points, 4)} pts · ${money(t.risk_usd)}`} />
        {(t.stop_history || []).length > 1 && <div className="mt-1 text-xs text-ink-300">Stop moves: {t.stop_history.slice(1).map((s: Json) => `${s.stop} (${s.why})`).join(" → ")}</div>}
      </div>
      <div className={`rounded-lg border border-green-500/30 p-3 ${hl("target")}`}>
        <h4 className="mb-1 text-sm font-semibold text-long">4 · Target</h4>
        <Row k="Target" v={t.target ?? "none"} />
        <div className="text-xs text-ink-400">= {t.target_calc}</div>
        <Row k="Planned reward : risk" v={t.planned_rr ? `${t.planned_rr} : 1` : "—"} />
      </div>
      <div className={`rounded-lg border border-ink-700 p-3 ${hl("management")}`}>
        <h4 className="mb-1 text-sm font-semibold text-amber-300">5 · Actual exit</h4>
        <Row k="Exit price" v={t.exit_price} />
        <Row k="Reason" v={t.exit_reason} />
        <Row k="Exit time" v={t.exit_time?.slice(0, 19).replace("T", " ")} />
        <div className="mt-1 text-xs text-ink-400">How it was decided: {t.exit_resolution}</div>
        <Row k="Bars held" v={t.bars_held} />
        <Row k="MFE / MAE" v={`${num(t.mfe_r, 2)}R / ${num(t.mae_r, 2)}R`} />
      </div>
      {showCosts && (
        <div className={`rounded-lg border border-ink-700 p-3 ${hl("costs")}`}>
          <h4 className="mb-1 text-sm font-semibold text-ink-100">6 · Costs & result</h4>
          <Row k="Gross P&L (after slippage)" v={money(t.gross_pnl)} />
          <Row k="Slippage included" v={money(t.slippage_usd)} />
          <Row k="Commission" v={money(t.commission)} />
          <Row k="Net P&L" v={money(t.net_pnl)} tone={t.net_pnl > 0 ? "text-long" : "text-short"} />
          <Row k="Net R" v={num(t.net_r, 2)} />
        </div>
      )}
    </div>
  );
}
