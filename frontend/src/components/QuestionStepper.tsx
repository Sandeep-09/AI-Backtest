/* Open decisions, asked one at a time. Every answer is an explicit click or typed reply. */
import { useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Send } from "lucide-react";
import type { Json, StrategyState } from "../api";
import { Spinner } from "./ui";

type Item =
  | { kind: "q"; key: string; text: string; options: { label: string; path: string; value: Json; note?: string | null }[]; hint?: string | null }
  | { kind: "confirm"; key: string; text: string; path: string; value: Json; quote: string | null }
  | { kind: "flag"; key: string; text: string; reason: string; version: number; index: number }
  | { kind: "ai"; key: string; text: string };

export default function QuestionStepper({ st, busy, onPatch, onMessage, onAck }: {
  st: StrategyState; busy: boolean;
  onPatch: (ops: { path: string; value: Json }[], note: string) => void;
  onMessage: (text: string) => void;
  onAck: (version: number, index: number) => void;
}) {
  const [i, setI] = useState(0);
  const [typed, setTyped] = useState("");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    for (const u of st.gate.open_unsupported) out.push({ kind: "flag", key: `f${u.version}-${u.index}`, text: u.text, reason: u.reason, version: u.version, index: u.index });
    for (const p of st.interpretation?.proposals ?? [])
      if (p.status === "needs_confirmation") out.push({ kind: "confirm", key: `c${p.path}${JSON.stringify(p.value)}`, text: p.label, path: p.path, value: p.value, quote: p.quote });
    for (const q of st.questions.filter((x) => x.severity === "required"))
      out.push({ kind: "q", key: q.id, text: q.question, options: q.options, hint: q.free_text_hint });
    for (const [k, q] of (st.interpretation?.questions ?? []).entries()) out.push({ kind: "ai", key: `ai${k}${q}`, text: q });
    return out.filter((x) => !dismissed.has(x.key));
  }, [st, dismissed]);
  useEffect(() => { if (i >= items.length) setI(Math.max(0, items.length - 1)); }, [items.length, i]);

  if (!items.length) {
    return (
      <div className="flex items-center gap-2 rounded border border-up/40 bg-up/10 px-3 py-2 text-[13px] text-tv-text">
        <Check size={15} className="text-up" /> All rules are decided.
      </div>
    );
  }
  const it = items[Math.min(i, items.length - 1)];
  const send = () => { if (typed.trim()) { onMessage(`${it.text} — ${typed.trim()}`); setTyped(""); } };

  return (
    <div className="rounded-md border border-accent/50 bg-accent/5 p-3">
      <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-wide text-tv-muted">
        <span>Question {Math.min(i, items.length - 1) + 1} of {items.length}</span>
        <div className="flex gap-0.5">{items.map((x, k) => <span key={x.key} className={`h-1 w-3 rounded ${k === i ? "bg-accent" : "bg-tv-hover"}`} />)}</div>
      </div>
      {it.kind === "flag" && <>
        <p className="text-[14px] font-medium text-tv-strong">This can't be tested as written: “{it.text}”</p>
        <p className="mt-1 text-tv-muted">{it.reason}</p>
        <button className="btn-outline mt-3 w-full" disabled={busy} onClick={() => onAck(it.version, it.index)}>Continue without it</button>
      </>}
      {it.kind === "confirm" && <>
        <p className="text-[14px] font-medium text-tv-strong">The AI suggested a value for “{it.text}” that you didn't state.</p>
        <pre className="mono mt-2 max-h-24 overflow-auto rounded bg-tv-bg p-2 text-[11px] text-tv-text">{JSON.stringify(it.value)}</pre>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button className="btn-primary" disabled={busy} onClick={() => onPatch([{ path: it.path, value: it.value }], `confirmed AI suggestion for ${it.path}`)}>Use it</button>
          <button className="btn-outline" onClick={() => setDismissed(new Set([...dismissed, it.key]))}>Ignore</button>
        </div>
      </>}
      {(it.kind === "q" || it.kind === "ai") && <>
        <p className="text-[14px] font-medium leading-snug text-tv-strong">{it.text}</p>
        {it.kind === "q" && it.options.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {it.options.map((o, k) => (
              <button key={k} disabled={busy} onClick={() => onPatch([{ path: o.path, value: o.value }], `answered: ${it.text}`)}
                className="block w-full rounded border border-tv-line bg-tv-panel px-3 py-2 text-left text-[13px] text-tv-text transition hover:border-accent hover:bg-accent/10 disabled:opacity-50">
                {o.label}{o.note && <span className="mt-0.5 block text-[11px] text-tv-muted">{o.note}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="mt-3 flex gap-1.5">
          <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={it.kind === "q" && it.options.length ? "Or type your own answer…" : (it.kind === "q" && it.hint) || "Type your answer…"} />
          <button className="btn-primary px-2.5" disabled={!typed.trim() || busy} onClick={send}>{busy ? <Spinner /> : <Send size={14} />}</button>
        </div>
      </>}
      <div className="mt-3 flex justify-between">
        <button className="btn-ghost px-2 text-tv-muted" disabled={i === 0} onClick={() => setI(i - 1)}><ChevronLeft size={14} /> Previous</button>
        <button className="btn-ghost px-2 text-tv-muted" disabled={i >= items.length - 1} onClick={() => setI(i + 1)}>Skip for now <ChevronRight size={14} /></button>
      </div>
    </div>
  );
}
