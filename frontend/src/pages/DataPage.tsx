import { useEffect, useMemo, useState } from "react";
import { Database, RefreshCw, Play } from "lucide-react";
import { api, Issue, Json } from "../api";
import { ErrorBox, fmtBytes, IssueList, Panel, Progress, Spinner, useJob } from "../components/ui";

export default function DataPage({ onChanged }: { onChanged: () => void }) {
  const [files, setFiles] = useState<Json[]>([]);
  const [cov, setCov] = useState<Json>({});
  const [issues, setIssues] = useState<Issue[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [health, setHealth] = useState<Json>(null);

  const load = async () => {
    const [f, c, i, h] = await Promise.all([api.files(), api.coverage(), api.issues(), api.health()]);
    setFiles(f.files); setCov(c.coverage); setIssues(i.issues); setHealth(h);
  };
  useEffect(() => { load().catch((e) => setErr(String(e))); }, []);
  const job = useJob(jobId, () => { load(); onChanged(); });

  const scan = async () => { setBusy(true); setErr(null); try { await api.scan(); await load(); } catch (e) { setErr(String(e)); } setBusy(false); };
  const ingest = async (re = false) => { setErr(null); try { const r = await api.ingest(re); setJobId(r.job_id); } catch (e) { setErr(String(e)); } };

  const groups = useMemo(() => {
    const g: Record<string, Json[]> = {};
    for (const f of files) {
      const parts = f.path.split("/");
      const key = parts.slice(0, -1).slice(-2).join("/");
      (g[key] ||= []).push(f);
    }
    return g;
  }, [files]);
  const pending = files.filter((f) => f.status === "new" || f.status === "error").length;
  const running = job && (job.status === "running" || job.status === "queued");

  return (
    <div className="space-y-4">
      <Panel title={<span className="flex items-center gap-2"><Database size={15} /> Market data</span>} right={<>
        <button className="btn-ghost" onClick={scan} disabled={busy || !!running}>{busy ? <Spinner /> : <RefreshCw size={14} />} Rescan folder</button>
        <button className="btn-primary" onClick={() => ingest(false)} disabled={!pending || !!running}><Play size={14} /> Read {pending} new file(s)</button>
      </>}>
        <p className="text-sm text-ink-300">
          Folder: <span className="mono text-ink-100">{health?.data_dir}</span>{health && !health.data_dir_exists && <span className="ml-2 text-short">not found</span>}.
          The app reads files read-only, checks what they actually contain, and builds bars from the trade prints.
          Large MBO files are streamed; the first read can take several minutes per GB.
        </p>
        {job && <div className="mt-3"><Progress value={job.progress} label={`${job.status}: ${job.message}`} /></div>}
        {job?.status === "done" && job.result?.failed?.length > 0 && <div className="mt-2"><ErrorBox error={`${job.result.failed.length} file(s) failed: ${job.result.failed.map((f: Json) => f.error).join("; ")}`} /></div>}
        <ErrorBox error={err} />
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        {Object.entries(cov).map(([root, c]: [string, Json]) => (
          <Panel key={root} title={`${root} coverage`} right={c.synthetic && <span className="chip bg-amber-500/20 text-amber-200">SYNTHETIC</span>}>
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div><div className="label">Trade dates</div><div className="text-ink-100">{c.first_day} → {c.last_day}</div><div className="text-xs text-ink-400">{c.trade_dates} sessions</div></div>
              <div><div className="label">Granularity</div><div className="text-ink-100">{c.intrabar.includes("tick") ? "Tick trades" : c.intrabar.join(", ")}</div><div className="text-xs text-ink-400">base bars {c.base_res.join(", ")}</div></div>
              <div><div className="label">Rolls</div>
                <div className="text-xs text-ink-300">same-day volume: {c.rolls.most_active_same_day.map((r: Json) => `${r.trade_date} ${r.from}→${r.to}`).join(", ") || "none"}</div>
                <div className="text-xs text-ink-300">prior-session volume: {c.rolls.front_by_prior_volume.map((r: Json) => `${r.trade_date} ${r.from}→${r.to}`).join(", ") || "none"}</div>
              </div>
            </div>
            <table className="tbl mt-3 w-full">
              <thead><tr><th>Contract</th><th>From</th><th>To</th><th>Sessions</th><th className="text-right">Volume</th></tr></thead>
              <tbody>{c.contracts.map((k: Json) => (
                <tr key={k.iid} className="border-t border-ink-800"><td className="mono">{k.symbol}</td><td>{k.first_day}</td><td>{k.last_day}</td><td>{k.trade_dates}</td><td className="text-right">{k.volume.toLocaleString()}</td></tr>
              ))}</tbody>
            </table>
            {c.contracts.some((k: Json) => k.symbol.includes(" id")) && (
              <p className="mt-2 text-xs text-ink-400">“id…” contracts come from continuous downloads (e.g. NQ.v.0) whose month isn't in the file. Add Databento definition files, or a raw-symbol file for that contract, to name them.</p>
            )}
          </Panel>
        ))}
      </div>

      <Panel title="Validation">
        <IssueList issues={issues} empty="No problems found in the files read so far." />
      </Panel>

      <Panel title={`Files (${files.length})`}>
        <div className="max-h-[420px] overflow-auto">
          {Object.entries(groups).map(([k, fs]) => (
            <details key={k} className="border-b border-ink-800 py-1.5" open={Object.keys(groups).length < 4}>
              <summary className="cursor-pointer text-sm text-ink-200"><span className="mono">{k}</span> <span className="text-ink-400">— {fs.length} files, {fmtBytes(fs.reduce((a, f) => a + f.size, 0))}</span></summary>
              <table className="tbl mt-1 w-full">
                <tbody>{fs.map((f) => (
                  <tr key={f.fingerprint} className="border-t border-ink-800/60">
                    <td className="mono text-xs">{f.name}</td><td className="text-xs">{f.schema}</td>
                    <td className="text-xs">{(f.meta?.symbols || []).join(", ")}</td><td className="text-xs">{fmtBytes(f.size)}</td>
                    <td><span className={`chip ${f.status === "ingested" ? "bg-green-500/15 text-green-300" : f.status === "error" ? "bg-red-500/15 text-red-300" : "bg-ink-700 text-ink-300"}`}>{f.status}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </details>
          ))}
          {!files.length && <div className="text-sm text-ink-400">No data files found. Mount your Databento folder at the path above.</div>}
        </div>
      </Panel>
    </div>
  );
}
