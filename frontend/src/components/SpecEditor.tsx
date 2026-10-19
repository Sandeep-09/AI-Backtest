/* Editable controls for every rule. Changes are sent as explicit path/value edits and create a new version. */
import { ReactNode, useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { Json } from "../api";

const PATHS = ["instrument", "timeframe", "session", "display_timezone", "directions", "data", "indicators", "entry.long", "entry.short",
  "entry.order", "entry.time_window", "stop", "target", "management", "sizing", "costs.commission_per_side", "costs.slippage_ticks",
  "fills.same_bar_policy", "fills.limit_fill"];

const get = (o: Json, p: string) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
const clone = (x: Json) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="block"><span className="label">{label}</span>{children}{hint && <span className="mt-0.5 block text-[11px] text-ink-500">{hint}</span>}</label>;
}

function Sel({ value, onChange, options, allowNull = true }: { value: Json; onChange: (v: Json) => void; options: [Json, string][]; allowNull?: boolean }) {
  return (
    <select className="input" value={value === null || value === undefined ? "" : JSON.stringify(value)}
      onChange={(e) => onChange(e.target.value === "" ? null : JSON.parse(e.target.value))}>
      {allowNull && <option value="">— not set —</option>}
      {options.map(([v, l]) => <option key={JSON.stringify(v)} value={JSON.stringify(v)}>{l}</option>)}
    </select>
  );
}

function Num({ value, onChange, step = 1, placeholder }: { value: Json; onChange: (v: number | null) => void; step?: number; placeholder?: string }) {
  return <input className="input" type="number" step={step} placeholder={placeholder ?? "not set"} value={value ?? ""}
    onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
}

function Block({ title, changed, children }: { title: string; changed: boolean; children: ReactNode }) {
  return (
    <fieldset className={`rounded-lg border p-3 ${changed ? "border-warn/70 bg-amber-500/5" : "border-ink-700"}`}>
      <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-ink-300">{title}{changed && <span className="ml-2 text-warn">edited</span>}</legend>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function OperandEd({ op, onChange, ids }: { op: Json; onChange: (o: Json) => void; ids: string[] }) {
  const set = (k: string, v: Json) => onChange({ ...op, [k]: v });
  return (
    <div className="flex flex-wrap items-center gap-1">
      <select className="input w-24" value={op.kind} onChange={(e) => onChange({ kind: e.target.value, bars_ago: 0, offset_ticks: 0,
        ...(e.target.value === "price" ? { field: "close" } : e.target.value === "indicator" ? { ref: ids[0] ?? null } : { value: 0 }) })}>
        <option value="indicator">indicator</option><option value="price">price</option><option value="const">number</option>
      </select>
      {op.kind === "indicator" && <select className="input w-28" value={op.ref ?? ""} onChange={(e) => set("ref", e.target.value)}>{ids.map((i) => <option key={i}>{i}</option>)}</select>}
      {op.kind === "price" && <select className="input w-24" value={op.field ?? "close"} onChange={(e) => set("field", e.target.value)}>{["open", "high", "low", "close"].map((f) => <option key={f}>{f}</option>)}</select>}
      {op.kind === "const" && <input className="input w-24" type="number" value={op.value ?? 0} onChange={(e) => set("value", Number(e.target.value))} />}
      <input className="input w-16" type="number" min={0} title="bars ago" value={op.bars_ago ?? 0} onChange={(e) => set("bars_ago", Number(e.target.value))} />
      <span className="text-[11px] text-ink-500">bars ago</span>
      <input className="input w-16" type="number" step={0.1} min={0.1} title="multiplier" value={op.multiplier ?? 1} onChange={(e) => set("multiplier", Number(e.target.value) || 1)} />
      <span className="text-[11px] text-ink-500">× mult</span>
      <input className="input w-16" type="number" title="offset ticks" value={op.offset_ticks ?? 0} onChange={(e) => set("offset_ticks", Number(e.target.value))} />
      <span className="text-[11px] text-ink-500">± ticks</span>
    </div>
  );
}

const OPS: [string, string][] = [["crosses_above", "crosses above"], ["crosses_below", "crosses below"], ["gt", "is above"], ["lt", "is below"],
  ["gte", "is at or above"], ["lte", "is at or below"], ["touches", "touches"]];

function GroupEd({ g, onChange, ids, depth = 0 }: { g: Json | null; onChange: (g: Json | null) => void; ids: string[]; depth?: number }) {
  if (!g) return <button className="btn-ghost" onClick={() => onChange({ type: "all", conditions: [] })}><Plus size={13} /> Add condition group</button>;
  const setC = (k: number, c: Json) => onChange({ ...g, conditions: g.conditions.map((x: Json, j: number) => (j === k ? c : x)) });
  const blankCmp = { type: "compare", op: "gt", left: { kind: "price", field: "close", bars_ago: 0, offset_ticks: 0 }, right: { kind: "indicator", ref: ids[0] ?? null, bars_ago: 0, offset_ticks: 0 } };
  return (
    <div className={`space-y-2 rounded-lg border border-ink-700 p-2 ${depth ? "ml-4" : ""}`}>
      <div className="flex items-center gap-2 text-xs">
        <select className="input w-32" value={g.type} onChange={(e) => onChange({ ...g, type: e.target.value })}>
          <option value="all">ALL of these</option><option value="any">ANY of these</option>
        </select>
        <button className="btn-ghost" onClick={() => onChange({ ...g, conditions: [...g.conditions, blankCmp] })}><Plus size={13} /> condition</button>
        <button className="btn-ghost" onClick={() => onChange({ ...g, conditions: [...g.conditions, { type: "all", conditions: [] }] })}><Plus size={13} /> sub-group</button>
        {depth === 0 && <button className="btn-ghost" onClick={() => onChange(null)}><Trash2 size={13} /> remove</button>}
      </div>
      {g.conditions.map((c: Json, k: number) => c.type === "compare" ? (
        <div key={k} className="flex flex-wrap items-center gap-2 rounded-md bg-ink-850 p-2">
          <OperandEd op={c.left} ids={ids} onChange={(o) => setC(k, { ...c, left: o })} />
          <select className="input w-36" value={c.op} onChange={(e) => setC(k, { ...c, op: e.target.value })}>{OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <OperandEd op={c.right} ids={ids} onChange={(o) => setC(k, { ...c, right: o })} />
          <button className="ml-auto text-ink-400 hover:text-short" onClick={() => onChange({ ...g, conditions: g.conditions.filter((_: Json, j: number) => j !== k) })}><Trash2 size={14} /></button>
        </div>
      ) : (
        <div key={k} className="flex items-start gap-1">
          <GroupEd g={c} ids={ids} depth={depth + 1} onChange={(x) => x ? setC(k, x) : onChange({ ...g, conditions: g.conditions.filter((_: Json, j: number) => j !== k) })} />
          <button className="text-ink-400 hover:text-short" onClick={() => onChange({ ...g, conditions: g.conditions.filter((_: Json, j: number) => j !== k) })}><Trash2 size={14} /></button>
        </div>
      ))}
    </div>
  );
}

export default function SpecEditor({ spec, onApply, busy }: { spec: Json; onApply: (ops: { path: string; value: Json }[]) => void; busy: boolean }) {
  const initial = useMemo(() => Object.fromEntries(PATHS.map((p) => [p, clone(get(spec, p)) ?? null])), [spec]);
  const [d, setD] = useState<Record<string, Json>>(initial);
  useEffect(() => setD(initial), [initial]);
  const set = (p: string, v: Json) => setD((x) => ({ ...x, [p]: v }));
  const sub = (p: string, k: string, v: Json) => setD((x) => ({ ...x, [p]: { ...(x[p] || {}), [k]: v } }));
  const changed = PATHS.filter((p) => JSON.stringify(d[p]) !== JSON.stringify(initial[p]));
  const ch = (...ps: string[]) => ps.some((p) => changed.includes(p));
  const ids: string[] = (d.indicators || []).map((i: Json) => i.id);
  const dirs = d.directions === "both" ? ["long", "short"] : d.directions ? [d.directions] : [];

  return (
    <div className="space-y-3">
      <Block title="Market & data" changed={ch("instrument", "timeframe", "session", "display_timezone", "directions", "data")}>
        <Field label="Instrument"><Sel value={d.instrument} onChange={(v) => set("instrument", v)} options={[["NQ", "NQ"], ["MNQ", "MNQ"], ["GC", "GC"], ["MGC", "MGC"]]} /></Field>
        <Field label="Timeframe"><Sel value={d.timeframe} onChange={(v) => set("timeframe", v)} options={["1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h"].map((t) => [t, t])} /></Field>
        <Field label="Session"><Sel value={d.session?.type ?? null} onChange={(v) => set("session", v ? { ...(d.session || {}), type: v, tz: d.session?.tz || "America/New_York" } : null)}
          options={[["rth", "RTH (regular hours)"], ["eth", "ETH (full Globex)"], ["custom", "Custom window"]]} /></Field>
        {d.session?.type === "custom" && <Field label="Window (HH:MM, session tz)"><div className="flex gap-2">
          <input className="input" placeholder="09:30" value={d.session.start ?? ""} onChange={(e) => sub("session", "start", e.target.value || null)} />
          <input className="input" placeholder="11:30" value={d.session.end ?? ""} onChange={(e) => sub("session", "end", e.target.value || null)} /></div></Field>}
        {d.session && d.session.type !== "eth" && <Field label="Indicators use"><Sel value={d.session.indicators_use ?? null} onChange={(v) => sub("session", "indicators_use", v)}
          options={[["session_bars_only", "Session bars only"], ["all_hours", "All hours"]]} /></Field>}
        <Field label="Display timezone"><Sel value={d.display_timezone} onChange={(v) => set("display_timezone", v)} options={[["America/New_York", "New York"], ["America/Chicago", "Chicago"], ["UTC", "UTC"], ["Asia/Kolkata", "India"]]} /></Field>
        <Field label="Directions"><Sel value={d.directions} onChange={(v) => set("directions", v)} options={[["both", "Long and short"], ["long", "Long only"], ["short", "Short only"]]} /></Field>
        <Field label="Contract rule"><Sel value={d.data?.contract ?? null} onChange={(v) => sub("data", "contract", v)}
          options={[["most_active_same_day", "Most active (same-day volume)"], ["front_by_prior_volume", "Front by prior-session volume"], ["specific", "Specific contract"]]} /></Field>
        {d.data?.contract === "specific" && <Field label="Symbol"><input className="input" value={d.data?.symbol ?? ""} onChange={(e) => sub("data", "symbol", e.target.value || null)} /></Field>}
        <Field label="Start date"><input className="input" type="date" value={d.data?.start ?? ""} onChange={(e) => sub("data", "start", e.target.value || null)} /></Field>
        <Field label="End date"><input className="input" type="date" value={d.data?.end ?? ""} onChange={(e) => sub("data", "end", e.target.value || null)} /></Field>
      </Block>

      <Block title="Indicators" changed={ch("indicators")}>
        <div className="space-y-2 sm:col-span-2">
          {(d.indicators || []).map((ind: Json, k: number) => (
            <div key={k} className="flex flex-wrap items-center gap-2">
              <input className="input w-28" value={ind.id} onChange={(e) => set("indicators", d.indicators.map((x: Json, j: number) => j === k ? { ...x, id: e.target.value } : x))} />
              <select className="input w-40" value={ind.type} onChange={(e) => set("indicators", d.indicators.map((x: Json, j: number) => j === k ? { ...x, type: e.target.value } : x))}>
                {["ema", "sma", "atr", "rsi", "vwap", "highest", "lowest", "prior_session_high", "prior_session_low", "candle_body", "candle_range"].map((t) => <option key={t}>{t}</option>)}
              </select>
              {["ema", "sma", "atr", "rsi", "highest", "lowest"].includes(ind.type) && <input className="input w-20" type="number" placeholder="period" value={ind.period ?? ""}
                onChange={(e) => set("indicators", d.indicators.map((x: Json, j: number) => j === k ? { ...x, period: e.target.value ? Number(e.target.value) : null } : x))} />}
              {["ema", "sma", "rsi", "highest", "lowest"].includes(ind.type) && <select className="input w-28" value={ind.source ?? ""}
                onChange={(e) => set("indicators", d.indicators.map((x: Json, j: number) => j === k ? { ...x, source: e.target.value || null } : x))}>
                <option value="">source?</option>{["close", "open", "high", "low", "hl2", "hlc3"].map((s) => <option key={s}>{s}</option>)}</select>}
              <button className="text-ink-400 hover:text-short" onClick={() => set("indicators", d.indicators.filter((_: Json, j: number) => j !== k))}><Trash2 size={14} /></button>
            </div>
          ))}
          <button className="btn-ghost" onClick={() => set("indicators", [...(d.indicators || []), { id: `ind${(d.indicators || []).length + 1}`, type: "ema", period: null, source: null }])}><Plus size={13} /> Add indicator</button>
        </div>
      </Block>

      {dirs.map((dir) => (
        <Block key={dir} title={`${dir} signal & rule exit`} changed={ch(`entry.${dir}`)}>
          <div className="space-y-2 sm:col-span-2">
            <div className="label">Signal (evaluated at the bar close)</div>
            <GroupEd g={d[`entry.${dir}`]?.when ?? null} ids={ids} onChange={(g) => set(`entry.${dir}`, { ...(d[`entry.${dir}`] || {}), when: g })} />
            <div className="label mt-2">Optional rule-based exit</div>
            <GroupEd g={d[`entry.${dir}`]?.exit_when ?? null} ids={ids} onChange={(g) => set(`entry.${dir}`, { ...(d[`entry.${dir}`] || {}), exit_when: g })} />
          </div>
        </Block>
      ))}

      <Block title="Entry order" changed={ch("entry.order", "entry.time_window")}>
        <Field label="Order type"><Sel value={d["entry.order"]?.type ?? null} onChange={(v) => set("entry.order", v ? { type: v, offset_ticks: 1, valid_bars: 1 } : null)}
          options={[["market_next_open", "Market at next bar's open"], ["stop_beyond_signal_bar", "Stop beyond signal bar"], ["limit_at_level", "Limit at a level"]]} /></Field>
        {d["entry.order"]?.type === "stop_beyond_signal_bar" && <Field label="Ticks beyond high/low"><Num value={d["entry.order"].offset_ticks} onChange={(v) => sub("entry.order", "offset_ticks", v ?? 0)} /></Field>}
        {d["entry.order"] && d["entry.order"].type !== "market_next_open" && <Field label="Valid for bars"><Num value={d["entry.order"].valid_bars} onChange={(v) => sub("entry.order", "valid_bars", v ?? 1)} /></Field>}
        {d["entry.order"]?.type === "limit_at_level" && <Field label="Limit level"><OperandEd op={d["entry.order"].level || { kind: "indicator", ref: ids[0] }} ids={ids} onChange={(o) => sub("entry.order", "level", o)} /></Field>}
        <Field label="Signal time window (optional)" hint="HH:MM in the session timezone"><div className="flex gap-2">
          <input className="input" placeholder="start" value={d["entry.time_window"]?.start ?? ""} onChange={(e) => set("entry.time_window", e.target.value ? { start: e.target.value, end: d["entry.time_window"]?.end ?? "16:00" } : null)} />
          <input className="input" placeholder="end" value={d["entry.time_window"]?.end ?? ""} onChange={(e) => d["entry.time_window"] && sub("entry.time_window", "end", e.target.value)} /></div></Field>
      </Block>

      <Block title="Stop loss" changed={ch("stop")}>
        <Field label="Type"><Sel value={d.stop?.type ?? null} onChange={(v) => set("stop", v ? { type: v, offset_ticks: 0 } : null)}
          options={[["signal_bar_extreme", "Signal bar low/high"], ["fixed_ticks", "Fixed ticks from fill"], ["atr_multiple", "ATR multiple"], ["swing_extreme", "Swing low/high"], ["indicator", "At an indicator"], ["furthest_of", "Lower (long) / higher (short) of several levels"]]} /></Field>
        {d.stop?.type === "furthest_of" && <div className="space-y-2 sm:col-span-2"><span className="label">Levels read at the signal bar</span>
          {(d.stop.levels || []).map((op: Json, k: number) => (
            <div key={k} className="flex items-center gap-2"><OperandEd op={op} ids={ids} onChange={(o) => sub("stop", "levels", d.stop.levels.map((x: Json, j: number) => j === k ? o : x))} />
              <button className="text-ink-400 hover:text-short" onClick={() => sub("stop", "levels", d.stop.levels.filter((_: Json, j: number) => j !== k))}><Trash2 size={14} /></button></div>))}
          <button className="btn-ghost" onClick={() => sub("stop", "levels", [...(d.stop.levels || []), { kind: "price", field: "low", bars_ago: 0, offset_ticks: 0 }])}><Plus size={13} /> Add level</button>
        </div>}
        {d.stop?.type === "fixed_ticks" && <Field label="Ticks"><Num value={d.stop.ticks} onChange={(v) => sub("stop", "ticks", v)} /></Field>}
        {d.stop?.type === "atr_multiple" && <><Field label="ATR indicator"><Sel value={d.stop.atr_ref ?? null} onChange={(v) => sub("stop", "atr_ref", v)} options={(d.indicators || []).filter((i: Json) => i.type === "atr").map((i: Json) => [i.id, i.id])} /></Field>
          <Field label="Multiple"><Num step={0.1} value={d.stop.multiple} onChange={(v) => sub("stop", "multiple", v)} /></Field></>}
        {d.stop?.type === "swing_extreme" && <Field label="Lookback bars"><Num value={d.stop.lookback} onChange={(v) => sub("stop", "lookback", v)} /></Field>}
        {d.stop?.type === "indicator" && <Field label="Indicator"><Sel value={d.stop.ref ?? null} onChange={(v) => sub("stop", "ref", v)} options={ids.map((i) => [i, i])} /></Field>}
        {d.stop && d.stop.type !== "fixed_ticks" && d.stop.type !== "atr_multiple" && <Field label="Extra ticks beyond"><Num value={d.stop.offset_ticks} onChange={(v) => sub("stop", "offset_ticks", v ?? 0)} /></Field>}
      </Block>

      <Block title="Profit target" changed={ch("target", "fills.limit_fill")}>
        <Field label="Type"><Sel value={d.target?.type ?? null} onChange={(v) => set("target", v ? { type: v } : null)}
          options={[["r_multiple", "Multiple of risk (R)"], ["fixed_ticks", "Fixed ticks"], ["indicator", "At an indicator"], ["none", "No target"]]} /></Field>
        {d.target?.type === "r_multiple" && <Field label="Reward : risk"><Num step={0.25} value={d.target.r} onChange={(v) => sub("target", "r", v)} /></Field>}
        {d.target?.type === "fixed_ticks" && <Field label="Ticks"><Num value={d.target.ticks} onChange={(v) => sub("target", "ticks", v)} /></Field>}
        {d.target?.type === "indicator" && <Field label="Indicator"><Sel value={d.target.ref ?? null} onChange={(v) => sub("target", "ref", v)} options={ids.map((i) => [i, i])} /></Field>}
        <Field label="Target counts as filled when"><Sel value={d["fills.limit_fill"]} onChange={(v) => set("fills.limit_fill", v)} options={[["trade_through", "Price trades 1 tick through"], ["touch", "Price touches"]]} /></Field>
      </Block>

      <Block title="Trade management" changed={ch("management")}>
        <Field label="Flat at session end"><Sel value={d.management?.flat_at_session_end ?? null} onChange={(v) => sub("management", "flat_at_session_end", v)} options={[[true, "Yes"], [false, "No, may hold"]]} /></Field>
        <Field label="Breakeven at R (optional)"><Num step={0.25} value={d.management?.breakeven_at_r} onChange={(v) => sub("management", "breakeven_at_r", v)} placeholder="off" /></Field>
        <Field label="Time exit after N bars (optional)"><Num value={d.management?.time_exit_bars} onChange={(v) => sub("management", "time_exit_bars", v)} placeholder="off" /></Field>
        <Field label="Max trades per session (optional)"><Num value={d.management?.max_trades_per_day} onChange={(v) => sub("management", "max_trades_per_day", v)} placeholder="no limit" /></Field>
        <Field label="Trailing stop (optional)"><Sel value={d.management?.trail?.type ?? null} onChange={(v) => sub("management", "trail", v ? { type: v } : null)} options={[["indicator", "Follow an indicator"], ["ticks", "Ticks behind best close"]]} /></Field>
        {d.management?.trail?.type === "indicator" && <Field label="Trail indicator"><Sel value={d.management.trail.ref ?? null} onChange={(v) => sub("management", "trail", { ...d.management.trail, ref: v })} options={ids.map((i) => [i, i])} /></Field>}
        {d.management?.trail?.type === "ticks" && <Field label="Trail ticks"><Num value={d.management.trail.ticks} onChange={(v) => sub("management", "trail", { ...d.management.trail, ticks: v })} /></Field>}
        <Field label="Exit on opposite signal"><Sel allowNull={false} value={!!d.management?.exit_on_opposite_signal} onChange={(v) => sub("management", "exit_on_opposite_signal", v)} options={[[false, "No"], [true, "Yes"]]} /></Field>
        <Field label="Cooldown bars after exit"><Num value={d.management?.cooldown_bars ?? 0} onChange={(v) => sub("management", "cooldown_bars", v ?? 0)} /></Field>
      </Block>

      <Block title="Sizing, costs & fills" changed={ch("sizing", "costs.commission_per_side", "costs.slippage_ticks", "fills.same_bar_policy")}>
        <Field label="Sizing"><Sel value={d.sizing?.type ?? null} onChange={(v) => set("sizing", v ? { type: v, below_one: "skip" } : null)} options={[["fixed_contracts", "Fixed contracts"], ["risk_usd", "Risk $ per trade"]]} /></Field>
        {d.sizing?.type === "fixed_contracts" && <Field label="Contracts"><Num value={d.sizing.contracts} onChange={(v) => sub("sizing", "contracts", v)} /></Field>}
        {d.sizing?.type === "risk_usd" && <><Field label="Risk $"><Num value={d.sizing.risk_usd} onChange={(v) => sub("sizing", "risk_usd", v)} /></Field>
          <Field label="Max contracts"><Num value={d.sizing.max_contracts} onChange={(v) => sub("sizing", "max_contracts", v)} /></Field></>}
        <Field label="Commission $ per side"><Num step={0.01} value={d["costs.commission_per_side"]} onChange={(v) => set("costs.commission_per_side", v)} /></Field>
        <Field label="Slippage ticks (market & stop fills)"><Num step={0.5} value={d["costs.slippage_ticks"]} onChange={(v) => set("costs.slippage_ticks", v)} /></Field>
        <Field label="Same-bar stop & target"><Sel value={d["fills.same_bar_policy"]} onChange={(v) => set("fills.same_bar_policy", v)} options={[
          ["intrabar_then_stop_first", "Replay ticks, else stop first"], ["intrabar_then_target_first", "Replay ticks, else target first"],
          ["stop_first", "Always stop first"], ["target_first", "Always target first"]]} /></Field>
      </Block>

      <div className="sticky bottom-0 flex items-center gap-3 border-t border-ink-700 bg-ink-900/95 py-3">
        <button className="btn-primary" disabled={!changed.length || busy} onClick={() => onApply(changed.map((p) => ({ path: p, value: d[p] })))}>
          Apply {changed.length || ""} change{changed.length === 1 ? "" : "s"} (new version)
        </button>
        <button className="btn-ghost" disabled={!changed.length} onClick={() => setD(initial)}>Discard</button>
        {changed.length > 0 && <span className="text-xs text-ink-400">Edited: {changed.join(", ")}</span>}
      </div>
    </div>
  );
}
