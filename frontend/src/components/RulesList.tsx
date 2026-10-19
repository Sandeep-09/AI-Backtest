/* The rules in one line each. Click a rule to edit just that part. */
import { useState } from "react";
import { Info, Pencil } from "lucide-react";
import type { Json, StrategyState } from "../api";
import SpecEditor from "./SpecEditor";
import { Modal } from "./ui";

const EDITOR_FOR: Record<string, string[]> = {
  market: ["market"], indicators: ["indicators"], signal_long: ["long"], signal_short: ["short"], entry: ["order"],
  stop: ["stop"], target: ["target"], management: ["management"], sizing: ["costs"], costs: ["costs"],
};
const TITLE: Record<string, string> = {
  market: "Market", indicators: "Indicators", signal_long: "Long when", signal_short: "Short when", entry: "Entry",
  stop: "Stop", target: "Target", management: "Holding", sizing: "Size", costs: "Costs & fills",
};

export default function RulesList({ st, busy, onPatch }: { st: StrategyState; busy: boolean; onPatch: (ops: { path: string; value: Json }[]) => Promise<void> }) {
  const [edit, setEdit] = useState<string | null>(null);
  const editable = st.is_latest;
  return (
    <div>
      <ul className="divide-y divide-tv-line">
        {st.summary.map((s) => {
          const unset = /Not set|\?/.test(s.short) || s.missing;
          return (
            <li key={s.key}>
              <button disabled={!editable} onClick={() => setEdit(s.key)}
                className={`group flex w-full items-start gap-2 px-1 py-2 text-left hover:bg-tv-hover ${s.changed ? "bg-warn/10" : ""}`}>
                <span className="w-[86px] shrink-0 pt-px text-[12px] text-tv-muted">{TITLE[s.key] ?? s.title}</span>
                <span className={`min-w-0 flex-1 text-[13px] leading-snug ${unset ? "text-warn" : "text-tv-text"}`}>{s.short || "—"}</span>
                <span className="flex shrink-0 items-center gap-1.5 pt-0.5 text-tv-dim">
                  {s.changed && <span className="h-1.5 w-1.5 rounded-full bg-warn" title="changed just now" />}
                  <span title={s.lines.join("\n")}><Info size={13} /></span>
                  {editable && <Pencil size={12} className="opacity-0 group-hover:opacity-100" />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {edit && (
        <Modal title={`Edit: ${TITLE[edit] ?? edit}`} onClose={() => setEdit(null)} width="max-w-2xl">
          <p className="mb-3 text-[12px] text-tv-muted">{st.summary.find((s) => s.key === edit)?.lines.join(" ")}</p>
          <SpecEditor spec={st.spec} busy={busy} only={EDITOR_FOR[edit] ?? []} onApply={async (ops) => { await onPatch(ops); setEdit(null); }} />
        </Modal>
      )}
    </div>
  );
}
