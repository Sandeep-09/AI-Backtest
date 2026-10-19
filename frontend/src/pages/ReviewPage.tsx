import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronUp, Edit3, MessageSquare, Pencil, RefreshCw, Send, SlidersHorizontal, X } from "lucide-react";
import { api, Json, shortHash, StrategyState } from "../api";
import PriceChart from "../components/PriceChart";
import SpecEditor from "../components/SpecEditor";
import TradeExplainer from "../components/TradeExplainer";
import { ErrorBox, Panel, Spinner } from "../components/ui";

const CHART_KEYS: Record<string, string> = { stop: "stop", target: "target", entry: "entry", signal_long: "signal", signal_short: "signal",
  indicators: "indicators", management: "management", costs: "costs" };

export default function ReviewPage({ st, setSt, goApprove }: { st: StrategyState; setSt: (s: StrategyState) => void; goApprove: () => void }) {
  const sid = st.strategy.id, v = st.version.version;
  const [modify, setModify] = useState<"none" | "chat" | "controls">("none");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [dir, setDir] = useState<string>("long");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const autoRan = useRef<string | null>(null);

  const act = async (label: string, fn: () => Promise<StrategyState>) => {
    setBusy(label); setErr(null);
    try { setSt(await fn()); } catch (e) { setErr(String(e).replace(/^Error: /, "")); }
    setBusy(null);
  };

  // Keep the chart in step with the rules: re-run the preview for every new version once it is previewable.
  useEffect(() => {
    if (!st.preview && st.preview_requirements.length === 0 && st.is_latest && autoRan.current !== st.version.spec_hash) {
      autoRan.current = st.version.spec_hash;
      act("preview", () => api.preview(sid, v));
    }
  }, [st.version.spec_hash]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirs = st.spec.directions === "both" ? ["long", "short"] : st.spec.directions ? [st.spec.directions] : [];
  useEffect(() => { if (dirs.length && !dirs.includes(dir)) setDir(dirs[0]); }, [st.spec.directions]); // eslint-disable-line react-hooks/exhaustive-deps
  const changedKeys = st.summary.filter((s) => s.changed).map((s) => s.key);
  const highlight = useMemo(() => changedKeys.map((k) => CHART_KEYS[k]).filter(Boolean), [changedKeys.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const ex = st.preview?.examples?.[dir] ?? null;
  const required = st.questions.filter((q) => q.severity === "required");
  const interp = st.interpretation;
  const lastMsgs = st.messages.slice(-12);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button className={`btn ${modify !== "none" ? "bg-warn text-ink-950" : "bg-warn/90 text-ink-950 hover:bg-warn"} px-4 py-2 text-base`}
          onClick={() => setModify(modify === "none" ? "chat" : "none")}>
          <Edit3 size={16} /> Modify strategy {modify !== "none" ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
        <span className="chip bg-ink-800 text-ink-200">version {v}</span>
        <span className="mono text-xs text-ink-400">spec {shortHash(st.version.spec_hash)}</span>
        {st.version.parent_version && <span className="chip bg-amber-500/15 text-amber-200">{st.changes.length} change(s) vs v{st.version.parent_version}</span>}
        {!st.is_latest && <span className="chip bg-red-500/15 text-red-200">viewing an older version</span>}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-ink-400">{required.length ? `${required.length} rule(s) undecided` : "All rules decided"}</span>
          <button className="btn-go" onClick={goApprove}><Check size={14} /> Review assumptions & approve</button>
        </div>
      </div>
      <ErrorBox error={err} />

      {modify !== "none" && (
        <Panel title={<div className="flex gap-1">
          <button className={`btn ${modify === "chat" ? "bg-ink-700" : ""}`} onClick={() => setModify("chat")}><MessageSquare size={14} /> Tell the AI</button>
          <button className={`btn ${modify === "controls" ? "bg-ink-700" : ""}`} onClick={() => setModify("controls")}><SlidersHorizontal size={14} /> Edit controls</button>
        </div>} right={<button className="text-ink-400 hover:text-ink-100" onClick={() => setModify("none")}><X size={16} /></button>}>
          {modify === "chat" ? (
            <div className="space-y-3">
              <div className="max-h-64 space-y-2 overflow-auto">
                {lastMsgs.map((m, k) => (
                  <div key={k} className={`rounded-lg px-3 py-2 text-sm ${m.role === "user" ? "ml-10 bg-accent/15 text-ink-100" : "mr-10 bg-ink-850 text-ink-300"}`}>
                    <div className="mb-0.5 text-[10px] uppercase tracking-wide text-ink-500">{m.role === "user" ? "you" : "interpreter"} · v{m.version}</div>{m.content}
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <input className="input" value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="e.g. Only take longs when the 10 EMA is above the 20 EMA, and stop 2 ticks below the signal bar."
                  onKeyDown={(e) => { if (e.key === "Enter" && msg.trim()) { act("chat", () => api.message(sid, msg)); setMsg(""); } }} />
                <button className="btn-primary" disabled={!msg.trim() || !!busy} onClick={() => { act("chat", () => api.message(sid, msg)); setMsg(""); }}>
                  {busy === "chat" ? <Spinner /> : <Send size={14} />} Send</button>
              </div>
            </div>
          ) : (
            <SpecEditor spec={st.spec} busy={!!busy} onApply={(ops) => act("patch", () => api.patch(sid, ops))} />
          )}
        </Panel>
      )}

      <div className="grid gap-4 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-5">
          {interp && (interp.proposals.length > 0 || interp.unsupported.length > 0 || interp.notes.length > 0 || interp.error) && (
            <Panel title="What the AI read" right={<span className="text-xs text-ink-400">{interp.engine === "openrouter" ? interp.model : interp.engine === "offline-parser" ? "offline parser" : `${interp.model ?? interp.engine} · imported`}</span>}>
              <ErrorBox error={interp.error} />
              <ul className="space-y-1.5 text-sm">
                {interp.proposals.map((p, k) => (
                  <li key={k} className={`rounded-lg border px-2.5 py-1.5 ${p.status === "applied" ? "border-green-500/30" : p.status === "needs_confirmation" ? "border-amber-500/50 bg-amber-500/5" : "border-red-500/30"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink-100">{p.label}</span>
                      <span className={`chip ${p.status === "applied" ? "bg-green-500/15 text-green-300" : p.status === "needs_confirmation" ? "bg-amber-500/20 text-amber-200" : "bg-red-500/15 text-red-300"}`}>{p.status.replace("_", " ")}</span>
                    </div>
                    {p.quote && <div className="text-xs text-ink-400">from your words: “{p.quote}”</div>}
                    {p.status !== "applied" && <pre className="mono mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[11px] text-ink-300">{JSON.stringify(p.value)}</pre>}
                    {p.reason && <div className="text-xs text-ink-400">{p.reason}</div>}
                    {p.status === "needs_confirmation" && st.is_latest && (
                      <button className="btn-ghost mt-1" onClick={() => act("patch", () => api.patch(sid, [{ path: p.path, value: p.value }], `confirmed AI suggestion for ${p.path}`))}><Check size={13} /> Accept this</button>
                    )}
                  </li>
                ))}
                {interp.notes.map((n, k) => <li key={`n${k}`} className="text-xs text-ink-400">Note: {n}</li>)}
              </ul>
            </Panel>
          )}
          {st.gate.open_unsupported.length > 0 && (
            <Panel title="Requests the rules can't express">
              <ul className="space-y-2 text-sm">
                {st.gate.open_unsupported.map((u: Json, k: number) => (
                  <li key={k} className="rounded-lg border border-red-500/40 bg-red-500/5 p-2">
                    <div className="text-ink-100">{u.text}</div><div className="text-xs text-ink-400">{u.reason}</div>
                    <button className="btn-ghost mt-1" onClick={() => act("ack", () => api.ack(sid, u.version, u.index))}>Proceed without this rule</button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          {(st.questions.length > 0 || (interp?.questions.length ?? 0) > 0) && (
            <Panel title={`Rules still to decide (${required.length})`}>
              <ul className="space-y-2.5">
                {interp?.questions.map((q, k) => <li key={`ai${k}`} className="rounded-lg bg-ink-850 px-3 py-2 text-sm text-ink-200"><span className="chip mr-2 bg-accent/20 text-accent">AI asks</span>{q}</li>)}
                {st.questions.map((q) => (
                  <li key={q.id} className="rounded-lg border border-ink-700 px-3 py-2">
                    <div className="text-sm text-ink-100">{q.question}</div>
                    {q.options.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {q.options.map((o, k) => (
                        <button key={k} disabled={!!busy || !st.is_latest} className="rounded-lg border border-ink-600 px-2 py-1 text-left text-xs text-ink-200 hover:border-accent hover:bg-accent/10"
                          onClick={() => act("patch", () => api.patch(sid, [{ path: o.path, value: o.value }], `answered: ${q.question}`))}>
                          {o.label}{o.note && <span className="block text-[10px] text-ink-400">{o.note}</span>}
                        </button>
                      ))}
                    </div>}
                    {q.free_text_hint && <div className="mt-1 text-[11px] text-ink-500">{q.free_text_hint} — or use Modify strategy.</div>}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          <Panel title="The rules that will be tested" right={<span className="text-xs text-ink-400">generated from the rules, not by the AI</span>}>
            <div className="space-y-3">
              {st.summary.map((s) => (
                <div key={s.key} className={`rounded-lg border p-3 ${s.changed ? "border-warn bg-amber-500/5" : s.missing ? "border-amber-500/30" : "border-ink-700"}`}>
                  <div className="mb-1 flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-ink-100">{s.title}{s.changed && <span className="chip ml-2 bg-warn text-ink-950">changed in v{v}</span>}</h4>
                    {st.is_latest && <button className="text-ink-400 hover:text-ink-100" title="Correct this section" onClick={() => { setEditing(s.key); setEditText(s.lines.join("\n")); }}><Pencil size={13} /></button>}
                  </div>
                  {editing === s.key ? (
                    <div className="space-y-2">
                      <textarea className="input min-h-[110px] text-xs" value={editText} onChange={(e) => setEditText(e.target.value)} />
                      <div className="flex gap-2">
                        <button className="btn-primary" disabled={!!busy} onClick={() => { act("chat", () => api.message(sid, `Correction to "${s.title}": ${editText}`)); setEditing(null); }}>Send correction to the AI</button>
                        <button className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                      <div className="text-[11px] text-ink-500">Your edited text is interpreted like a chat message; the summary is then regenerated from the updated rules.</div>
                    </div>
                  ) : (
                    <ul className="space-y-0.5 text-sm text-ink-300">{s.lines.map((l, k) => <li key={k} className={`whitespace-pre-wrap ${l.includes("⚠") ? "text-amber-300" : ""}`}>{l}</li>)}</ul>
                  )}
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="space-y-4 xl:col-span-7">
          <Panel title="How the strategy behaves" right={<>
            {dirs.map((d) => <button key={d} className={`btn ${dir === d ? (d === "long" ? "bg-long/20 text-long" : "bg-short/20 text-short") : "text-ink-400"}`} onClick={() => setDir(d)}>{d} example</button>)}
            <button className="btn-ghost" disabled={st.preview_requirements.length > 0 || !!busy} onClick={() => act("preview", () => api.preview(sid, v))}>
              {busy === "preview" ? <Spinner /> : <RefreshCw size={14} />} {st.preview ? "Refresh" : "Preview"}</button>
          </>}>
            {st.preview_requirements.length > 0 && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-100">
                To draw examples, decide: <span className="mono">{st.preview_requirements.join(", ")}</span>
              </div>
            )}
            {busy === "preview" && <div className="flex items-center gap-2 text-sm text-ink-400"><Spinner /> Running the approved-rules engine on the first sessions…</div>}
            {st.preview && !st.preview.ok && <ErrorBox error={st.preview.message ?? "Preview failed"} />}
            {st.preview?.ok && (
              <div className="space-y-3">
                <div className={`rounded-lg px-3 py-2 text-sm ${st.preview.source === "data" ? "bg-ink-850 text-ink-200" : "border border-amber-500/50 bg-amber-500/10 font-medium text-amber-200"}`}>
                  {ex ? ex.label : `No ${dir} trade found in the scanned sessions.`}
                  {st.preview.scanned && <span className="block text-xs font-normal text-ink-400">
                    Scanned {st.preview.scanned.trade_dates} session(s) {st.preview.scanned.from} → {st.preview.scanned.to}: {st.preview.scanned.signals_long} long / {st.preview.scanned.signals_short} short signal bars.
                    {st.preview.source === "data" && " These examples are produced by the same engine and rules the backtest will use."}
                  </span>}
                </div>
                {st.preview.notes?.length ? <ul className="text-xs text-amber-300">{st.preview.notes.map((n, k) => <li key={k}>• {n}</li>)}</ul> : null}
                {ex && <>
                  <PriceChart data={ex.chart} overlay={ex.overlay} highlight={highlight} height={440} />
                  <Legend />
                  <TradeExplainer t={ex.trade} highlight={highlight} showCosts={false} />
                </>}
                {!ex && st.preview.message && <div className="text-sm text-ink-400">{st.preview.message}</div>}
                {!ex && (st.preview.skipped_sample?.length ?? 0) > 0 && (
                  <div className="text-xs text-ink-400">Signals not taken: {st.preview.skipped_sample!.slice(0, 6).map((s: Json) => `${s.time.slice(0, 16)} ${s.direction}: ${s.reason}`).join(" · ")}</div>
                )}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Legend() {
  const I = ({ c, dash, label }: { c: string; dash?: boolean; label: string }) => (
    <span className="flex items-center gap-1.5"><span className="inline-block w-6 border-t-2" style={{ borderColor: c, borderStyle: dash ? "dashed" : "solid" }} />{label}</span>
  );
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-400">
      <I c="#93c5fd" dash label="planned entry" /><I c="#f87171" dash label="planned stop" /><I c="#4ade80" dash label="planned target" />
      <I c="#e5e7eb" label="actual fill" /><I c="#fbbf24" label="actual exit" />
      <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-4 rounded-sm bg-red-500/30" />risk</span>
      <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-4 rounded-sm bg-green-500/30" />reward</span>
      <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-sm bg-[#3a4663]" />outside session</span>
    </div>
  );
}
