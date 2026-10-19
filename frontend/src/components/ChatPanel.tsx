/* Talk to the interpreter: describe a new strategy, or change the current one in words. */
import { useEffect, useRef, useState } from "react";
import { FileUp, Send, Sparkles } from "lucide-react";
import { api, StrategyState } from "../api";
import { Spinner } from "./ui";

const EXAMPLES = [
  "NQ 5 min. Long when the close crosses above the 20 EMA. Stop below the signal bar's low. 2R target. 1 contract.",
  "Use the 10 EMA and 20 EMA with a 1:3 risk-to-reward target.",
];

export default function ChatPanel({ st, busy, aiLabel, onCreate, onMessage, onTemplate, onImport }: {
  st: StrategyState | null; busy: boolean; aiLabel: string;
  onCreate: (text: string) => void; onMessage: (text: string) => void; onTemplate: (file: string) => void; onImport: (json: unknown) => void;
}) {
  const [text, setText] = useState("");
  const [templates, setTemplates] = useState<{ file: string; name: string }[]>([]);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { api.templates().then((r) => setTemplates(r.items)).catch(() => {}); }, []);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [st?.messages.length]);
  const submit = () => {
    if (!text.trim()) return;
    if (st) onMessage(text.trim()); else onCreate(text.trim());
    setText("");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-2 overflow-auto p-3 scroll-thin">
        {!st && (
          <div className="space-y-3">
            <p className="text-[14px] text-tv-strong">Describe your strategy in plain words.</p>
            <p className="text-tv-muted">Say which indicators, what triggers an entry, where the stop goes and the target. Anything you leave out is asked as a question — nothing is assumed.</p>
            <div className="space-y-1.5">
              {EXAMPLES.map((e) => <button key={e} className="block w-full rounded border border-tv-line px-2.5 py-1.5 text-left text-[12px] text-tv-muted hover:bg-tv-hover" onClick={() => setText(e)}>{e}</button>)}
            </div>
            {templates.length > 0 && (
              <div>
                <div className="label mt-4">Or start from a template</div>
                {templates.map((t) => (
                  <button key={t.file} disabled={busy} className="mb-1.5 block w-full rounded border border-tv-line px-2.5 py-2 text-left text-[13px] text-tv-text hover:border-accent hover:bg-accent/10" onClick={() => onTemplate(t.file)}>{t.name}</button>
                ))}
              </div>
            )}
          </div>
        )}
        {st?.messages.map((m, k) => (
          <div key={k} className={`max-w-[92%] rounded-lg px-3 py-2 text-[13px] leading-snug ${m.role === "user" ? "ml-auto bg-accent text-white" : "bg-tv-hover text-tv-text"}`}>
            {m.content}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="border-t border-tv-line p-2.5">
        <textarea className="input min-h-[72px] resize-none" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          placeholder={st ? "Change something, e.g. “use a 2R target” or “only between 9:30 and 11:30”" : "e.g. 10/20 EMA on 5 min, long when price closes above both, stop under the 20 EMA, 2R target"} />
        <div className="mt-2 flex items-center gap-2">
          <button className="btn-primary" disabled={!text.trim() || busy} onClick={submit}>
            {busy ? <Spinner /> : st ? <Send size={14} /> : <Sparkles size={14} />} {st ? "Send" : "Interpret"}
          </button>
          {!st && (
            <label className="btn-ghost cursor-pointer text-tv-muted" title="Load a .json interpretation file">
              <FileUp size={14} /> Import file
              <input type="file" accept=".json,application/json" className="hidden" onChange={async (e) => {
                const f = e.target.files?.[0]; if (!f) return; onImport(JSON.parse(await f.text())); e.target.value = "";
              }} />
            </label>
          )}
          <span className="ml-auto truncate text-[11px] text-tv-dim">{aiLabel}</span>
        </div>
      </div>
    </div>
  );
}
