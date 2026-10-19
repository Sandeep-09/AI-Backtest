import { ReactNode, useEffect, useRef, useState } from "react";
import { AlertTriangle, Info, Loader2, X, XOctagon } from "lucide-react";
import { api, Issue, Job } from "../api";

export function Spinner({ size = 14, className = "" }: { size?: number; className?: string }) {
  return <Loader2 className={`animate-spin ${className}`} size={size} />;
}

export function Progress({ value, label }: { value: number; label?: string }) {
  return (
    <div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-tv-hover">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
      </div>
      {label && <div className="mt-1 truncate text-[11px] text-tv-muted">{label}</div>}
    </div>
  );
}

const SEV: Record<string, { cls: string; icon: ReactNode; word: string }> = {
  blocking: { cls: "text-down", icon: <XOctagon size={13} />, word: "Must fix" },
  warning: { cls: "text-warn", icon: <AlertTriangle size={13} />, word: "Check" },
  info: { cls: "text-tv-muted", icon: <Info size={13} />, word: "Note" },
};

export function NoteList({ issues, empty = "Nothing to report." }: { issues: Issue[]; empty?: string }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!issues.length) return <div className="text-tv-muted">{empty}</div>;
  const order = { blocking: 0, warning: 1, info: 2 } as Record<string, number>;
  return (
    <ul className="divide-y divide-tv-line">
      {[...issues].sort((a, b) => order[a.severity] - order[b.severity]).map((i) => (
        <li key={i.id} className="flex items-start gap-2 py-1.5">
          <span className={`mt-0.5 shrink-0 ${SEV[i.severity]?.cls}`}>{SEV[i.severity]?.icon}</span>
          <div className="min-w-0 flex-1">
            <span className="text-tv-text">{i.message}</span>
            {i.details && <button className="ml-2 text-[11px] text-accent" onClick={() => setOpen(open === i.id ? null : i.id)}>{open === i.id ? "hide" : "details"}</button>}
            {open === i.id && <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] text-tv-muted">{JSON.stringify(i.details, null, 1)}</pre>}
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
    if (!jobId) { setJob(null); return; }
    let stop = false;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "done" || j.status === "error") { done.current?.(j); return; }
      } catch { /* retry */ }
      if (!stop) setTimeout(tick, 700);
    };
    tick();
    return () => { stop = true; };
  }, [jobId]);
  return job;
}

export function Toast({ text, onClose }: { text: string | null; onClose: () => void }) {
  if (!text) return null;
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex max-w-xl -translate-x-1/2 items-start gap-2 rounded bg-down px-3 py-2 text-white shadow-lg">
      <span className="flex-1">{text}</span>
      <button onClick={onClose}><X size={14} /></button>
    </div>
  );
}

export function Modal({ title, onClose, children, width = "max-w-lg" }: { title: ReactNode; onClose: () => void; children: ReactNode; width?: string }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/60 p-4 pt-[8vh]" onMouseDown={onClose}>
      <div className={`w-full ${width} rounded-md border border-tv-line bg-tv-panel shadow-2xl`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-tv-line px-4 py-3">
          <h2 className="text-[15px] font-semibold text-tv-strong">{title}</h2>
          <button className="text-tv-muted hover:text-tv-text" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="max-h-[75vh] overflow-auto p-4 scroll-thin">{children}</div>
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onMouseDown={onClose}>
      <div className="flex h-full w-full max-w-md flex-col border-l border-tv-line bg-tv-panel shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-tv-line px-4 py-3">
          <h2 className="text-[15px] font-semibold text-tv-strong">{title}</h2>
          <button className="text-tv-muted hover:text-tv-text" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-auto p-4 scroll-thin">{children}</div>
      </div>
    </div>
  );
}

export const fmtBytes = (n: number) => {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = n;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
};
