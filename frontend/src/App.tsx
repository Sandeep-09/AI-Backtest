import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";
import { api, Issue, Json, shortHash, StrategyState } from "./api";
import ApprovePage from "./pages/ApprovePage";
import DataPage from "./pages/DataPage";
import DescribePage from "./pages/DescribePage";
import ResultsPage from "./pages/ResultsPage";
import ReviewPage from "./pages/ReviewPage";

type Step = "data" | "describe" | "review" | "approve" | "results";
const STEPS: { key: Step; label: string; needs?: boolean }[] = [
  { key: "data", label: "1 · Data" }, { key: "describe", label: "2 · Describe" }, { key: "review", label: "3 · Review & modify", needs: true },
  { key: "approve", label: "4 · Assumptions & approve", needs: true }, { key: "results", label: "5 · Results", needs: true },
];

function readHash(): { sid: number | null; step: Step; run: number | null } {
  const m = window.location.hash.match(/^#\/(?:s\/(\d+)\/)?(\w+)?(?:\/r\/(\d+))?/);
  return { sid: m?.[1] ? Number(m[1]) : null, step: (m?.[2] as Step) || "data", run: m?.[3] ? Number(m[3]) : null };
}

export default function App() {
  const init = readHash();
  const [step, setStep] = useState<Step>(init.step);
  const [sid, setSid] = useState<number | null>(init.sid);
  const [runId, setRunId] = useState<number | null>(init.run);
  const [st, setSt] = useState<StrategyState | null>(null);
  const [health, setHealth] = useState<Json>(null);
  const [issues, setIssues] = useState<Issue[]>([]);

  const refreshMeta = useCallback(() => {
    api.health().then(setHealth).catch(() => {});
    api.issues().then((r) => setIssues(r.issues)).catch(() => {});
  }, []);
  useEffect(() => { refreshMeta(); }, [refreshMeta]);
  useEffect(() => {
    window.location.hash = `#/${sid ? `s/${sid}/` : ""}${step}${runId ? `/r/${runId}` : ""}`;
  }, [sid, step, runId]);
  useEffect(() => {
    if (!sid) { setSt(null); return; }
    api.get(sid).then(setSt).catch(() => setSid(null));
  }, [sid]);
  useEffect(() => { if (step === "results" && sid) api.get(sid).then(setSt).catch(() => {}); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (id: number) => { setSid(id); setRunId(null); setStep("review"); };
  const required = st?.questions.filter((q) => q.severity === "required").length ?? 0;
  const blocking = issues.filter((i) => i.severity === "blocking").length;
  const warnings = issues.filter((i) => i.severity === "warning").length;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-ink-700 bg-ink-950/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1700px] flex-wrap items-center gap-3 px-4 py-2.5">
          <div className="flex items-center gap-2 font-semibold text-ink-100"><Activity size={18} className="text-accent" /> AI Backtest <span className="text-xs font-normal text-ink-400">NQ · GC</span></div>
          <nav className="flex flex-wrap gap-1">
            {STEPS.map((s) => (
              <button key={s.key} disabled={s.needs && !st} onClick={() => setStep(s.key)}
                className={`rounded-lg px-3 py-1.5 text-sm transition disabled:opacity-30 ${step === s.key ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:bg-ink-850 hover:text-ink-200"}`}>{s.label}</button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs">
            {health && <span className={`chip ${health.llm.mode === "openrouter" ? "bg-accent/15 text-accent" : "bg-amber-500/15 text-amber-200"}`}>
              {health.llm.mode === "openrouter" ? `AI: ${health.llm.model}` : "AI: offline parser"}</span>}
            <button className="chip bg-ink-800 text-ink-300" onClick={() => setStep("data")}>
              {blocking ? <span className="text-short">{blocking} blocking</span> : warnings ? <span className="text-amber-300">{warnings} data warnings</span> : "data ok"}
            </button>
          </div>
        </div>
      </header>
      <div className="mx-auto flex max-w-[1700px] gap-4 px-4 py-4">
        <main className="min-w-0 flex-1">
          {step === "data" && <DataPage onChanged={() => { refreshMeta(); if (sid) api.get(sid).then(setSt); }} />}
          {step === "describe" && <DescribePage onOpen={open} llm={health?.llm ?? null} />}
          {step === "review" && st && <ReviewPage st={st} setSt={setSt} goApprove={() => setStep("approve")} />}
          {step === "approve" && st && <ApprovePage st={st} setSt={setSt} onRun={(r) => { setRunId(r); setStep("results"); api.get(st.strategy.id).then(setSt); }} />}
          {step === "results" && st && <ResultsPage st={st} runId={runId} setRunId={setRunId} onRunDone={() => api.get(st.strategy.id).then(setSt)} />}
          {step !== "data" && step !== "describe" && !st && <div className="text-sm text-ink-400">Open or describe a strategy first.</div>}
        </main>
        {st && (
          <aside className="hidden w-72 shrink-0 lg:block">
            <div className="card sticky top-16 space-y-3 p-4 text-sm">
              <div><div className="label">Strategy</div><div className="text-ink-100">{st.strategy.name}</div></div>
              <div className="flex items-center justify-between"><span className="text-ink-400">Version</span><span className="mono">v{st.version.version} · {shortHash(st.version.spec_hash)}</span></div>
              <div className="flex items-center justify-between"><span className="text-ink-400">Rules undecided</span>
                <span className={required ? "text-amber-300" : "text-long"}>{required ? required : <CheckCircle2 size={14} />}</span></div>
              <div className="flex items-center justify-between"><span className="text-ink-400">Preview</span>
                <span>{st.preview?.ok ? (st.preview.source === "data" ? "your data" : "illustrative") : <CircleDashed size={14} className="text-ink-500" />}</span></div>
              <div className="flex items-center justify-between"><span className="text-ink-400">Approval</span>
                {st.approval ? <span className="flex items-center gap-1 text-long"><ShieldCheck size={14} /> approved</span> : <span className="text-ink-400">not approved</span>}</div>
              {!st.gate.can_approve && !st.approval && <ul className="space-y-0.5 text-xs text-ink-400">{st.gate.reasons.map((r) => <li key={r} className="flex gap-1"><AlertTriangle size={11} className="mt-0.5 shrink-0 text-amber-400" />{r}</li>)}</ul>}
              <div className="flex items-center justify-between"><span className="text-ink-400">Runs</span><span>{st.runs.length ? `#${st.runs[0].id} ${st.runs[0].status}` : "none"}</span></div>
              <button className="btn-ghost w-full justify-center" onClick={() => { setSid(null); setStep("describe"); }}>New strategy</button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
