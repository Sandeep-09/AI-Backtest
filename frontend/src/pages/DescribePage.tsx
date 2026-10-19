import { useEffect, useState } from "react";
import { FileUp, Sparkles } from "lucide-react";
import { api } from "../api";
import { ErrorBox, Panel, Spinner } from "../components/ui";

const EXAMPLES = [
  "Use the 10 EMA and 20 EMA with a 1:3 risk-to-reward target.",
  "NQ 5 min RTH. Go long when the close crosses above the 20 EMA while the 20 EMA is above the 50 EMA. Stop below the signal bar's low. 2R target. 1 contract. Flat at end of day.",
  "GC 15 minute bars. Short when price closes below VWAP after being above it on the previous bar. Stop 1.5x ATR(14). Exit at the opposite VWAP cross.",
];

export default function DescribePage({ onOpen, llm }: { onOpen: (id: number) => void; llm: { mode: string; model: string | null } | null }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [list, setList] = useState<{ id: number; name: string; created_at: string; latest: number }[]>([]);
  useEffect(() => { api.strategies().then((r) => setList(r.items)).catch(() => {}); }, []);

  const go = async () => {
    setBusy(true); setErr(null);
    try { const s = await api.create(text); onOpen(s.strategy.id); } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title="Describe the strategy in plain English" className="lg:col-span-2">
        <textarea className="input min-h-[180px] font-normal leading-relaxed" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Indicators, what triggers an entry, where the stop goes, the target, position size, and any other exits." />
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => <button key={e} className="chip border border-ink-600 text-left text-ink-300 hover:bg-ink-800" onClick={() => setText(e)}>{e.slice(0, 70)}…</button>)}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button className="btn-primary" disabled={!text.trim() || busy} onClick={go}>{busy ? <Spinner /> : <Sparkles size={14} />} Interpret strategy</button>
          <label className="btn-ghost cursor-pointer" title="Load an interpretation prepared outside the app, e.g. by Claude in chat">
            <FileUp size={14} /> Import interpretation (.json)
            <input type="file" accept="application/json,.json" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return;
              setBusy(true); setErr(null);
              try { const s = await api.importInterp(JSON.parse(await f.text())); onOpen(s.strategy.id); } catch (x) { setErr(String(x)); }
              setBusy(false); e.target.value = "";
            }} />
          </label>
          <span className="text-xs text-ink-400">
            {llm?.mode === "openrouter" ? <>AI: <span className="mono">{llm.model}</span> via OpenRouter</> :
              <span className="text-warn">AI not configured: using the offline keyword parser (set OPENROUTER_API_KEY for full interpretation)</span>}
          </span>
        </div>
        <p className="mt-3 text-xs text-ink-400">
          Nothing is assumed. Each rule the AI reads must quote your words; anything missing becomes a question you answer explicitly.
          Naming indicators (e.g. “10/20 EMA”) only adds the indicators — it does not imply a crossover.
        </p>
        <ErrorBox error={err} />
      </Panel>
      <Panel title="Saved strategies">
        {!list.length && <div className="text-sm text-ink-400">None yet.</div>}
        <ul className="space-y-1">
          {list.map((s) => (
            <li key={s.id}><button className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-ink-800" onClick={() => onOpen(s.id)}>
              <div className="truncate text-ink-100">{s.name}</div><div className="text-xs text-ink-400">#{s.id} · v{s.latest} · {s.created_at.slice(0, 16).replace("T", " ")}</div>
            </button></li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
