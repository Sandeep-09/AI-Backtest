import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ChevronDown, Database, Loader2, MessageSquare, Play, Plus, ReceiptText, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { api, CompactChart, Issue, Json, shortHash, StrategyState, TradeMark } from "./api";
import BottomPanel, { Tab, TradeDetail } from "./components/BottomPanel";
import ChartPane from "./components/ChartPane";
import ChatPanel from "./components/ChatPanel";
import QuestionStepper from "./components/QuestionStepper";
import RulesList from "./components/RulesList";
import { Drawer, fmtBytes, Modal, NoteList, Progress, Spinner, Toast, useJob } from "./components/ui";

const ls = { get: (k: string, d: number) => { try { const v = localStorage.getItem(k); return v ? Number(v) : d; } catch { return d; } },
             set: (k: string, v: number) => { try { localStorage.setItem(k, String(v)); } catch { /* ignore */ } } };

function dragHandler(axis: "x" | "y", value: number, set: (v: number) => void, min: number, max: number, invert = true) {
  return (e: React.MouseEvent) => {
    e.preventDefault();
    const start = axis === "x" ? e.clientX : e.clientY, v0 = value;
    const move = (ev: MouseEvent) => {
      const d = (axis === "x" ? ev.clientX : ev.clientY) - start;
      set(Math.min(max, Math.max(min, v0 + (invert ? -d : d))));
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
}

export default function App() {
  const [health, setHealth] = useState<Json>(null);
  const [files, setFiles] = useState<Json[]>([]);
  const [coverage, setCoverage] = useState<Json>({});
  const [issues, setIssues] = useState<Issue[]>([]);
  const [ingestJob, setIngestJob] = useState<string | null>(null);
  const [list, setList] = useState<{ id: number; name: string; latest: number }[]>([]);
  const [sid, setSid] = useState<number | null>(() => { const m = location.hash.match(/s=(\d+)/); return m ? Number(m[1]) : null; });
  const [st, setSt] = useState<StrategyState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"strategy" | "chat" | "trade">("chat");
  const [detail, setDetail] = useState<Json>(null);
  const [bottomTab, setBottomTab] = useState<Tab>("overview");
  const [bottomOpen, setBottomOpen] = useState(true);
  const [bottomH, setBottomH] = useState(() => ls.get("bt.bottomH", 290));
  const [rightW, setRightW] = useState(() => ls.get("bt.rightW", 380));
  const [selected, setSelected] = useState<number | null>(null);
  const [run, setRun] = useState<Json>(null);
  const [runChart, setRunChart] = useState<{ chart: CompactChart; trades: TradeMark[] } | null>(null);
  const [showData, setShowData] = useState(false);
  const [showApprove, setShowApprove] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const autoPreview = useRef<string | null>(null);
  useEffect(() => ls.set("bt.bottomH", bottomH), [bottomH]);
  useEffect(() => ls.set("bt.rightW", rightW), [rightW]);
  useEffect(() => { location.hash = sid ? `s=${sid}` : ""; }, [sid]);

  const loadData = useCallback(async () => {
    const [h, f, c, i] = await Promise.all([api.health(), api.files(), api.coverage(), api.issues()]);
    setHealth(h); setFiles(f.files); setCoverage(c.coverage); setIssues(i.issues);
  }, []);
  const loadList = useCallback(() => api.strategies().then((r) => setList(r.items)).catch(() => {}), []);
  useEffect(() => { loadData().catch((e) => setErr(String(e))); loadList(); }, [loadData, loadList]);
  useEffect(() => {
    if (!sid) { setSt(null); setRightTab("chat"); return; }
    api.get(sid).then((s) => { setSt(s); setRightTab("strategy"); }).catch(() => setSid(null));
  }, [sid]);

  const act = async (label: string, fn: () => Promise<StrategyState | void>) => {
    setBusy(label); setErr(null);
    try { const r = await fn(); if (r) { setSt(r); if (r.strategy.id !== sid) setSid(r.strategy.id); loadList(); } }
    catch (e) { setErr(String(e).replace(/^Error: /, "")); }
    setBusy(null);
  };

  // Keep the chart in step with the rules: preview every new version as soon as it's previewable.
  useEffect(() => {
    if (st && st.is_latest && !st.preview && st.preview_requirements.length === 0 && autoPreview.current !== st.version.spec_hash) {
      autoPreview.current = st.version.spec_hash;
      act("preview", () => api.preview(st.strategy.id, st.version.version));
    }
  }, [st?.version.spec_hash]); // eslint-disable-line react-hooks/exhaustive-deps

  // The latest run of the version being viewed.
  const runRow = st?.runs.find((r) => r.version === st.version.version) ?? null;
  const loadRun = useCallback(async (id: number) => {
    const r = await api.getRun(id);
    setRun(r);
    if (r.status === "done") setRunChart(await api.runChart(id));
  }, []);
  useEffect(() => { setRun(null); setRunChart(null); setSelected(null); if (runRow) loadRun(runRow.id).catch(() => {}); }, [runRow?.id, loadRun]); // eslint-disable-line react-hooks/exhaustive-deps
  const runJob = useJob(run && run.status !== "done" && run.status !== "error" ? run.job?.id : null, () => {
    if (runRow) loadRun(runRow.id);
    if (st) api.get(st.strategy.id).then(setSt);
  });
  const ingest = useJob(ingestJob, () => { loadData(); if (sid) api.get(sid).then(setSt); });

  const showingRun = run?.status === "done" && runChart;
  const chart = showingRun ? runChart!.chart : st?.preview?.window_chart ?? null;
  const trades = showingRun ? runChart!.trades : st?.preview?.trades ?? [];
  useEffect(() => {
    // Select the first long example of a fresh preview so its labels are visible.
    if (!showingRun && st?.preview?.trades?.length) setSelected(st.preview.trades[0].n);
  }, [st?.preview?.spec_hash, showingRun]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setDetail(null);
    if (selected === null) return;
    if (showingRun) api.trade(run.id, selected).then(setDetail).catch(() => {});
    else {
      const t = st?.preview?.trade_details?.[String(selected)];
      if (t) setDetail({ trade: t });
    }
  }, [selected, showingRun, run?.id, st?.preview?.spec_hash]); // eslint-disable-line react-hooks/exhaustive-deps

  const newFiles = files.filter((f) => f.status === "new" || f.status === "error").length;
  const required = st?.questions.filter((q) => q.severity === "required").length ?? 0;
  const openItems = required + (st?.gate.open_unsupported.length ?? 0);
  const noteCount = issues.filter((i) => i.severity !== "info").length;
  const ingesting = ingest && (ingest.status === "running" || ingest.status === "queued");
  const running = runJob && (runJob.status === "running" || runJob.status === "queued");

  const startIngest = async () => { setShowData(true); try { const r = await api.ingest(false); setIngestJob(r.job_id); } catch (e) { setErr(String(e)); } };
  const approveAndRun = async () => {
    if (!st) return;
    await act("run", async () => {
      let s = st;
      if (!st.approval) s = await api.approve(st.strategy.id, st.version.version, st.version.spec_hash);
      await api.run(s.strategy.id, s.version.version, s.version.spec_hash);
      setShowApprove(false);
      setBottomTab("overview"); setBottomOpen(true);
      return api.get(s.strategy.id);
    });
  };

  // One main button: always the next step.
  const main = useMemo(() => {
    if (ingesting) return { label: `Reading data… ${Math.round((ingest!.progress ?? 0) * 100)}%`, icon: <Loader2 size={14} className="animate-spin" />, onClick: () => setShowData(true), cls: "btn-outline" };
    if (!Object.keys(coverage).length && newFiles) return { label: `Read data (${newFiles} files)`, icon: <Database size={14} />, onClick: startIngest, cls: "btn-primary" };
    if (!st) return { label: "Describe a strategy", icon: <Plus size={14} />, onClick: () => setRightTab("chat"), cls: "btn-primary" };
    if (running) return { label: `Backtesting… ${Math.round((runJob!.progress ?? 0) * 100)}%`, icon: <Loader2 size={14} className="animate-spin" />, onClick: () => setBottomTab("overview"), cls: "btn-outline" };
    if (openItems) return { label: `Answer ${openItems} question${openItems === 1 ? "" : "s"}`, icon: <SlidersHorizontal size={14} />, onClick: () => setRightTab("strategy"), cls: "btn-primary" };
    if (busy === "preview") return { label: "Drawing preview…", icon: <Loader2 size={14} className="animate-spin" />, onClick: () => {}, cls: "btn-outline" };
    if (st.approval && run?.status === "done") return { label: "Run again", icon: <Play size={14} />, onClick: () => setShowApprove(true), cls: "btn-outline" };
    if (st.gate.can_approve || st.approval) return { label: "Approve & run", icon: <ShieldCheck size={14} />, onClick: () => setShowApprove(true), cls: "btn-go" };
    return { label: st.gate.reasons[0] ? `Blocked: ${st.gate.reasons[0]}` : "Preview first", icon: null, onClick: () => setShowApprove(true), cls: "btn-outline" };
  }, [ingesting, ingest, coverage, newFiles, st, running, runJob, openItems, busy, run]); // eslint-disable-line react-hooks/exhaustive-deps

  const s = st?.spec;
  const banner = !chart ? null : showingRun
    ? <span className="rounded bg-tv-panel/90 px-2 py-1 text-[11px] text-tv-muted">Backtest · {trades.length} trades · {run.result.data.trade_from} → {run.result.data.trade_to}</span>
    : st?.preview?.ok ? <span className="rounded bg-warn/15 px-2 py-1 text-[11px] text-warn">Preview of the rules · first {st.preview.scanned?.trade_dates} sessions · {trades.length} trade{trades.length === 1 ? "" : "s"}{st.preview.source !== "data" ? " · ILLUSTRATIVE bars, not your data" : ""}</span> : null;

  return (
    <div className="flex h-full flex-col bg-tv-bg text-tv-text">
      {/* Top bar */}
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-tv-line bg-tv-bg px-2">
        <div className="flex items-center gap-1.5 px-1 font-semibold text-tv-strong"><Activity size={17} className="text-accent" /> Backtest</div>
        <div className="relative">
          <button className="btn-ghost max-w-[320px] font-medium" onClick={() => setShowMenu(!showMenu)}>
            <span className="truncate">{st?.strategy.name ?? "No strategy"}</span><ChevronDown size={14} />
          </button>
          {showMenu && (
            <div className="absolute left-0 top-10 z-30 w-80 rounded border border-tv-line bg-tv-panel py-1 shadow-xl" onMouseLeave={() => setShowMenu(false)}>
              <button className="block w-full px-3 py-1.5 text-left hover:bg-tv-hover" onClick={() => { setSid(null); setShowMenu(false); }}><Plus size={13} className="mr-1 inline" />New strategy</button>
              <div className="my-1 border-t border-tv-line" />
              {list.map((x) => (
                <button key={x.id} className={`block w-full truncate px-3 py-1.5 text-left hover:bg-tv-hover ${x.id === sid ? "text-accent" : ""}`} onClick={() => { setSid(x.id); setShowMenu(false); }}>{x.name}</button>
              ))}
              {!list.length && <div className="px-3 py-1.5 text-tv-muted">No saved strategies yet.</div>}
            </div>
          )}
        </div>
        {s && (
          <div className="hidden items-center gap-1 text-[12px] text-tv-muted md:flex">
            {[s.instrument, s.timeframe, s.session ? { rth: "RTH", eth: "Globex", custom: "Custom" }[s.session.type as string] : null, s.directions === "both" ? "Long & short" : s.directions ? `${s.directions} only` : null]
              .filter(Boolean).map((x) => <span key={x} className="rounded bg-tv-panel px-1.5 py-0.5 text-tv-text">{x}</span>)}
            <span className="ml-1" title={`Rules ID ${st!.version.spec_hash}`}>rules v{st!.version.version}</span>
            {st!.approval && <span className="flex items-center gap-1 text-up"><ShieldCheck size={13} />approved</span>}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn-ghost text-[12px]" onClick={() => setShowData(true)}>
            <span className={`h-2 w-2 rounded-full ${issues.some((i) => i.severity === "blocking") ? "bg-down" : noteCount ? "bg-warn" : Object.keys(coverage).length ? "bg-up" : "bg-tv-dim"}`} />
            Data{noteCount ? ` · ${noteCount} note${noteCount === 1 ? "" : "s"}` : ""}
          </button>
          <span className={`hidden text-[11px] lg:inline ${health?.llm.mode === "openrouter" ? "text-tv-muted" : "text-warn"}`}>
            {health?.llm.mode === "openrouter" ? `AI: ${health.llm.model}` : "AI: offline parser"}</span>
          <button className={`${main.cls} min-w-[150px]`} onClick={main.onClick} disabled={main.label.startsWith("Blocked")}>{main.icon}{main.label}</button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Chart + bottom panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            <ChartPane chart={chart} trades={trades} selected={selected} banner={banner}
              onSelect={(n) => { setSelected(n); if (n !== null) setRightTab("trade"); }}
              emptyState={
                <div className="max-w-sm space-y-3 text-center">
                  {!Object.keys(coverage).length ? (
                    <><div className="text-[15px] text-tv-strong">Your data hasn't been read yet</div>
                      <div className="text-tv-muted">{files.length ? `${files.length} files found (${fmtBytes(files.reduce((a, f) => a + f.size, 0))}).` : "No data files found in the data folder."}</div>
                      {newFiles > 0 && <button className="btn-primary" onClick={startIngest}><Database size={14} />Read your data</button>}</>
                  ) : !st ? (
                    <><div className="text-[15px] text-tv-strong">Describe a strategy to see it on the chart</div><div className="text-tv-muted">Use the panel on the right, or load a template.</div></>
                  ) : busy === "preview" ? <Spinner size={22} className="mx-auto text-tv-muted" /> : (
                    <><div className="text-[15px] text-tv-strong">A few rules are still open</div>
                      <div className="text-tv-muted">Answer the questions on the right and the trades appear here.</div>
                      {st.preview_requirements.length > 0 && <div className="text-[12px] text-tv-dim">Needed for the chart: {st.preview_requirements.join(", ")}</div>}</>
                  )}
                </div>
              } />
          </div>
          <div className="h-1 shrink-0 cursor-row-resize bg-tv-line hover:bg-accent" onMouseDown={dragHandler("y", bottomH, setBottomH, 120, 700)} style={{ display: bottomOpen && st ? undefined : "none" }} />
          <div className="shrink-0 bg-tv-panel" style={{ height: bottomOpen && st ? bottomH : undefined }}>
            <BottomPanel st={st} tab={bottomTab} setTab={setBottomTab} open={bottomOpen && !!st} setOpen={setBottomOpen} run={run} runJob={runJob}
              previewTrades={st?.preview?.trades ?? []} previewDetails={st?.preview?.trade_details ?? {}}
              selected={selected} onSelect={(n) => { setSelected(n); if (n !== null) setRightTab("trade"); }} onApprove={() => setShowApprove(true)} />
          </div>
        </div>

        {/* Right panel */}
        <div className="w-1 shrink-0 cursor-col-resize bg-tv-line hover:bg-accent" onMouseDown={dragHandler("x", rightW, setRightW, 300, 640)} />
        <aside className="flex shrink-0 flex-col bg-tv-panel" style={{ width: rightW }}>
          <div className="flex border-b border-tv-line">
            <button className={`tab flex-1 ${rightTab === "strategy" ? "tab-on" : ""}`} onClick={() => setRightTab("strategy")} disabled={!st}>
              <SlidersHorizontal size={13} className="mr-1 inline" />Strategy{openItems ? <span className="ml-1.5 rounded-full bg-accent px-1.5 text-[10px] text-white">{openItems}</span> : null}</button>
            <button className={`tab flex-1 ${rightTab === "chat" ? "tab-on" : ""}`} onClick={() => setRightTab("chat")}><MessageSquare size={13} className="mr-1 inline" />Chat</button>
            {selected !== null && <button className={`tab flex-1 ${rightTab === "trade" ? "tab-on" : ""}`} onClick={() => setRightTab("trade")}><ReceiptText size={13} className="mr-1 inline" />Trade #{selected}</button>}
          </div>
          <div className="min-h-0 flex-1">
            {rightTab === "trade" && selected !== null ? (
              <div className="h-full overflow-auto p-3 scroll-thin">
                {detail ? <TradeDetail t={detail.trade} rules={detail.rules} ruleId={detail.spec_hash} verified={detail.hash_matches_trade} />
                  : <Spinner className="text-tv-muted" />}
                {!showingRun && detail && <div className="mt-3 rounded bg-warn/10 px-2 py-1.5 text-[12px] text-warn">Preview trade: shows how the rules behave. Approve & run for real results.</div>}
              </div>
            ) : rightTab === "strategy" && st ? (
              <div className="h-full space-y-3 overflow-auto p-3 scroll-thin">
                {!st.is_latest && <div className="rounded bg-warn/15 px-2 py-1 text-[12px] text-warn">You're viewing an older version of these rules.</div>}
                <QuestionStepper st={st} busy={!!busy}
                  onPatch={(ops, note) => act("patch", () => api.patch(st.strategy.id, ops, note))}
                  onMessage={(t) => act("chat", () => api.message(st.strategy.id, t))}
                  onAck={(v, i) => act("ack", () => api.ack(st.strategy.id, v, i))} />
                <div>
                  <div className="label px-1">Rules being tested</div>
                  <RulesList st={st} busy={!!busy} onPatch={async (ops) => { await act("patch", () => api.patch(st.strategy.id, ops)); }} />
                </div>
              </div>
            ) : (
              <ChatPanel st={st} busy={busy === "chat" || busy === "create"} aiLabel={health?.llm.mode === "openrouter" ? `${health.llm.model}` : "offline parser"}
                onCreate={(t) => act("create", () => api.create(t))} onMessage={(t) => act("chat", () => api.message(st!.strategy.id, t))}
                onTemplate={(f) => act("create", () => api.loadTemplate(f))} onImport={(j) => act("create", () => api.importInterp(j))} />
            )}
          </div>
        </aside>
      </div>

      {showApprove && st && (
        <Modal title="Approve & run backtest" onClose={() => setShowApprove(false)}>
          {!st.gate.can_approve && !st.approval ? (
            <div className="space-y-2">
              <div className="text-tv-text">Not ready yet:</div>
              <ul className="list-disc pl-5 text-warn">{st.gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-tv-muted">The backtest will run exactly these rules (v{st.version.version}, ID <span className="mono">{shortHash(st.version.spec_hash)}</span>) over the full date range. Changing anything later creates new rules that need approving again.</p>
              <table className="w-full text-[13px]"><tbody>
                {st.assumptions.rows.filter((r) => ["Instrument", "Contract", "Date range (trading)", "Timeframe", "Session", "Sizing", "Commission", "Slippage", "Same-bar stop & target", "Target / limit fills"].includes(r.label)).map((r) => (
                  <tr key={r.label} className="border-t border-tv-line align-top"><td className="w-40 py-1.5 pr-2 text-tv-muted">{r.label}</td><td className="py-1.5 text-tv-text">{r.value}</td></tr>
                ))}
              </tbody></table>
              <button className="text-[12px] text-accent" onClick={() => { setShowApprove(false); setBottomTab("assumptions"); setBottomOpen(true); }}>Show all assumptions and data notes</button>
              <div className="flex justify-end gap-2 pt-1">
                <button className="btn-outline" onClick={() => setShowApprove(false)}>Cancel</button>
                <button className="btn-go" disabled={busy === "run" || !st.is_latest} onClick={approveAndRun}>{busy === "run" ? <Spinner /> : <ShieldCheck size={14} />}{st.approval ? "Run backtest" : "Approve & run"}</button>
              </div>
            </div>
          )}
        </Modal>
      )}

      {showData && (
        <Drawer title="Your data" onClose={() => setShowData(false)}>
          <div className="space-y-4">
            <div className="text-[12px] text-tv-muted">Folder <span className="mono text-tv-text">{health?.data_dir}</span> · {files.length} files · {fmtBytes(files.reduce((a, f) => a + f.size, 0))} · read-only</div>
            {ingest && <Progress value={ingest.progress} label={ingest.status === "done" ? "Finished" : ingest.message} />}
            {newFiles > 0 && !ingesting && <button className="btn-primary w-full" onClick={startIngest}><Database size={14} />Read {newFiles} new file{newFiles === 1 ? "" : "s"}</button>}
            {Object.entries(coverage).map(([root, c]: [string, Json]) => (
              <div key={root} className="rounded border border-tv-line p-3">
                <div className="flex items-center justify-between"><span className="font-semibold text-tv-strong">{root}</span>{c.synthetic && <span className="chip bg-warn/20 text-warn">SYNTHETIC</span>}</div>
                <div className="mt-1 text-[12px] text-tv-text">{c.first_day} → {c.last_day} · {c.trade_dates} sessions · {c.intrabar.includes("tick") ? "tick data" : "bar data"}</div>
                <div className="mt-1 text-[12px] text-tv-muted">Contracts: {c.contracts.map((k: Json) => k.symbol).join(", ")}</div>
                {c.rolls.most_active_same_day.length > 0 && <div className="text-[12px] text-tv-muted">Rolls: {c.rolls.most_active_same_day.map((r: Json) => `${r.trade_date} → ${r.to}`).join(", ")}</div>}
              </div>
            ))}
            <div>
              <div className="label">Notes</div>
              <div className="text-[12px]"><NoteList issues={issues} empty="No problems found." /></div>
            </div>
          </div>
        </Drawer>
      )}
      <Toast text={err} onClose={() => setErr(null)} />
    </div>
  );
}
