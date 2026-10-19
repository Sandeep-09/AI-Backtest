import { useState } from "react";
import { Lock, PlayCircle, ShieldCheck } from "lucide-react";
import { api, shortHash, StrategyState } from "../api";
import { ErrorBox, IssueList, Panel, Spinner } from "../components/ui";

export default function ApprovePage({ st, setSt, onRun }: { st: StrategyState; setSt: (s: StrategyState) => void; onRun: (runId: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const sid = st.strategy.id, v = st.version.version, h = st.version.spec_hash;
  const approved = !!st.approval;

  const go = async () => {
    setBusy(true); setErr(null);
    try {
      if (!approved) setSt(await api.approve(sid, v, h));
      const r = await api.run(sid, v, h);
      onRun(r.run_id);
    } catch (e) { setErr(String(e).replace(/^Error: /, "")); }
    setBusy(false);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-2">
        <Panel title="Backtest assumptions" right={<span className="mono text-xs text-ink-400">v{v} · {shortHash(h)}</span>}>
          <table className="tbl w-full">
            <tbody>{st.assumptions.rows.map((r) => (
              <tr key={r.label} className="border-t border-ink-800 align-top">
                <td className="w-48 text-ink-400">{r.label}</td>
                <td className={r.missing ? "text-amber-300" : "text-ink-100"}>{r.missing ? `⚠ ${r.value}` : r.value}</td>
              </tr>
            ))}</tbody>
          </table>
        </Panel>
        <Panel title="Execution limitations (what this backtest cannot know)">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-300">{st.assumptions.limitations.map((l, k) => <li key={k} className={l.startsWith("SOME") ? "font-semibold text-amber-300" : ""}>{l}</li>)}</ul>
        </Panel>
        <Panel title={`Data checks for ${st.spec.instrument ?? "the instrument"}`}>
          <IssueList issues={st.assumptions.data_issues} empty="No data issues for this instrument." />
        </Panel>
      </div>
      <div className="space-y-4">
        <Panel title="Approval">
          {approved ? (
            <div className="mb-3 rounded-lg border border-green-500/40 bg-green-500/10 p-3 text-sm text-green-200">
              <ShieldCheck size={16} className="mb-1" />Approved {st.approval!.approved_at.slice(0, 16).replace("T", " ")} UTC. The run will execute exactly spec <span className="mono">{shortHash(st.approval!.spec_hash)}</span>.
            </div>
          ) : st.gate.can_approve ? (
            <p className="mb-3 text-sm text-ink-300">Approving saves version {v} (spec <span className="mono">{shortHash(h)}</span>) as the specification to execute. Any later change creates a new version that needs its own preview and approval.</p>
          ) : (
            <div className="mb-3 space-y-1 text-sm">
              <div className="flex items-center gap-2 font-medium text-amber-200"><Lock size={14} /> Not ready to approve</div>
              <ul className="list-disc pl-5 text-ink-300">{st.gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          )}
          <button className="btn-go w-full justify-center py-2.5 text-base" disabled={busy || (!approved && !st.gate.can_approve) || !st.is_latest} onClick={go}>
            {busy ? <Spinner /> : <PlayCircle size={18} />} {approved ? "Run backtest" : "Approve strategy and run backtest"}
          </button>
          {!st.is_latest && <div className="mt-2 text-xs text-amber-300">You are viewing an older version; open the latest to approve.</div>}
          <ErrorBox error={err} />
        </Panel>
        <Panel title="Version history">
          <ul className="space-y-1 text-sm">
            {[...st.versions].reverse().map((x) => (
              <li key={x.version} className="flex items-center justify-between">
                <span>v{x.version} <span className="text-xs text-ink-500">{x.source} · {x.changes} change(s)</span></span>
                <span className="flex items-center gap-2"><span className="mono text-xs text-ink-500">{shortHash(x.spec_hash)}</span>{x.approved && <span className="chip bg-green-500/15 text-green-300">approved</span>}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
