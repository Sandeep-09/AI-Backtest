import { ReactNode, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, XOctagon } from "lucide-react";
import { api, Issue, Job } from "../api";

export function Spinner({ className = "" }: { className?: string }) {
  return <Loader2 className={`animate-spin ${className}`} size={16} />;
}

export function Panel({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-700 px-4 py-2.5">
          <h3 className="text-sm font-semibold text-ink-100">{title}</h3>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Progress({ value, label }: { value: number; label?: string }) {
  return (
    <div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-ink-700">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
      </div>
      {label && <div className="mt-1 text-xs text-ink-400">{label}</div>}
    </div>
  );
}

const SEV: Record<string, { cls: string; icon: ReactNode }> = {
  blocking: { cls: "border-red-500/40 bg-red-500/10 text-red-200", icon: <XOctagon size={14} /> },
  warning: { cls: "border-amber-500/40 bg-amber-500/10 text-amber-100", icon: <AlertTriangle size={14} /> },
  info: { cls: "border-ink-600 bg-ink-850 text-ink-300", icon: <Info size={14} /> },
};

export function IssueList({ issues, empty = "No issues." }: { issues: Issue[]; empty?: string }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!issues.length) return <div className="flex items-center gap-2 text-sm text-ink-400"><CheckCircle2 size={14} className="text-long" />{empty}</div>;
  const order = { blocking: 0, warning: 1, info: 2 } as Record<string, number>;
  return (
    <ul className="space-y-1.5">
      {[...issues].sort((a, b) => order[a.severity] - order[b.severity]).map((i) => (
        <li key={i.id} className={`rounded-lg border px-3 py-2 text-sm ${SEV[i.severity]?.cls}`}>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0">{SEV[i.severity]?.icon}</span>
            <div className="min-w-0 flex-1">
              <div className="break-words">{i.message}</div>
              {i.details && (
                <button className="mt-1 text-xs underline opacity-70" onClick={() => setOpen(open === i.id ? null : i.id)}>
                  {open === i.id ? "hide details" : "details"}
                </button>
              )}
              {open === i.id && <pre className="mono mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-xs opacity-80">{JSON.stringify(i.details, null, 1)}</pre>}
            </div>
            <span className="chip shrink-0 bg-black/20 text-[10px] uppercase">{i.severity}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function useJob(jobId: string | null, onDone?: (j: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "done" || j.status === "error") { done.current?.(j); return; }
      } catch { /* retry */ }
      setTimeout(tick, 700);
    };
    tick();
    return () => { stop = true; };
  }, [jobId]);
  return job;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</div>;
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "pos" | "neg" | "none" }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wide text-ink-400">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold ${tone === "pos" ? "text-long" : tone === "neg" ? "text-short" : "text-ink-100"}`}>{value}</div>
    </div>
  );
}

export const fmtBytes = (n: number) => {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = n;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
};
