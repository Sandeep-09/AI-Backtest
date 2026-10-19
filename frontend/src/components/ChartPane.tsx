/*
 * Main chart, TradingView style.
 * All trades are drawn; the selected trade shows shaded risk/reward and its levels
 * (dashed = planned by the rules, solid = what actually filled).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, CrosshairMode, IChartApi, ISeriesApi, ISeriesMarkersPluginApi,
  ISeriesPrimitive, LineSeries, LineStyle, SeriesAttachedParameter, SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import { ChevronLeft, ChevronRight, Maximize2 } from "lucide-react";
import type { CompactChart, TradeMark } from "../api";

const LINE_COLORS = ["#2962ff", "#f7a600", "#e040fb", "#00bcd4", "#ff9800", "#ab47bc", "#26a69a", "#ffeb3b"];

interface Seg { t0: number; t1: number; p0: number; p1: number; color: string; dash?: boolean; width?: number; label?: string }
interface Box { t0: number; t1: number; p0: number; p1: number; fill: string; stroke: string; label: string; strong?: boolean }

class Overlay implements ISeriesPrimitive<Time> {
  private p: SeriesAttachedParameter<Time> | null = null;
  boxes: Box[] = [];
  segs: Seg[] = [];
  attached(param: SeriesAttachedParameter<Time>) { this.p = param; }
  detached() { this.p = null; }
  set(boxes: Box[], segs: Seg[]) { this.boxes = boxes; this.segs = segs; this.p?.requestUpdate(); }
  paneViews() {
    const self = this;
    return [{
      zOrder: () => "top" as const,
      renderer: () => ({
        draw(target: any) { // eslint-disable-line @typescript-eslint/no-explicit-any
          const p = self.p;
          if (!p) return;
          target.useBitmapCoordinateSpace((scope: any) => { // eslint-disable-line @typescript-eslint/no-explicit-any
            const ctx: CanvasRenderingContext2D = scope.context;
            const hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            const ts = p.chart.timeScale();
            const half = Math.max(2, ts.options().barSpacing / 2);
            const X = (t: number) => ts.timeToCoordinate(t as UTCTimestamp);
            const Y = (v: number) => p.series.priceToCoordinate(v);
            for (const b of self.boxes) {
              const x0 = X(b.t0), x1 = X(b.t1), y0 = Y(b.p0), y1 = Y(b.p1);
              if (x0 === null || x1 === null || y0 === null || y1 === null) continue;
              const L = Math.min(x0, x1) - half, R = Math.max(x0, x1) + half;
              ctx.fillStyle = b.fill;
              ctx.fillRect(L * hr, Math.min(y0, y1) * vr, (R - L) * hr, Math.abs(y1 - y0) * vr);
              ctx.strokeStyle = b.stroke;
              ctx.lineWidth = (b.strong ? 2 : 1) * hr;
              ctx.strokeRect(L * hr, Math.min(y0, y1) * vr, (R - L) * hr, Math.abs(y1 - y0) * vr);
              ctx.fillStyle = b.stroke;
              ctx.font = `${11 * vr}px -apple-system, Roboto, sans-serif`;
              ctx.fillText(b.label, (L + 4) * hr, (Math.min(y0, y1) + 13) * vr);
            }
            const labels: { x: number; y: number; text: string; color: string }[] = [];
            for (const s of self.segs) {
              const x0 = X(s.t0), x1 = X(s.t1), y0 = Y(s.p0), y1 = Y(s.p1);
              if (x0 === null || x1 === null || y0 === null || y1 === null) continue;
              ctx.beginPath();
              ctx.setLineDash(s.dash ? [5 * hr, 4 * hr] : []);
              ctx.strokeStyle = s.color;
              ctx.lineWidth = (s.width ?? 1) * hr;
              ctx.moveTo(x0 * hr, y0 * vr);
              ctx.lineTo(x1 * hr, y1 * vr);
              ctx.stroke();
              ctx.setLineDash([]);
              if (s.label) labels.push({ x: x1 + half + 4, y: y1 - 7, text: s.label, color: s.color });
            }
            // Place labels top-to-bottom so none overlap (14px rows).
            labels.sort((a, b) => a.y - b.y);
            let lastY = -1e9;
            ctx.font = `${10.5 * vr}px -apple-system, Roboto, sans-serif`;
            for (const lb of labels) {
              const y = Math.max(lb.y, lastY + 15);
              lastY = y;
              const w = ctx.measureText(lb.text).width;
              ctx.fillStyle = "rgba(19,23,34,0.9)";
              ctx.fillRect(lb.x * hr - 2 * hr, y * vr, w + 4 * hr, 14 * vr);
              ctx.fillStyle = lb.color;
              ctx.fillText(lb.text, lb.x * hr, (y + 11) * vr);
            }
          });
        },
      }),
    }];
  }
}

function snapIdx(times: number[], t: number): number {
  let lo = 0, hi = times.length - 1, ans = 0;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (times[m] <= t) { ans = m; lo = m + 1; } else hi = m - 1;
  }
  return ans;
}

const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

export default function ChartPane({ chart, trades, selected, onSelect, banner, emptyState }: {
  chart: CompactChart | null; trades: TradeMark[]; selected: number | null; onSelect: (n: number | null) => void;
  banner?: React.ReactNode; emptyState?: React.ReactNode;
}) {
  const el = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; candles: ISeriesApi<"Candlestick">; markers: ISeriesMarkersPluginApi<Time>; overlay: Overlay } | null>(null);
  const [legend, setLegend] = useState<{ k: number } | null>(null);
  const times = useMemo(() => chart?.t ?? [], [chart]);
  const colors = useMemo(() => (chart?.ind ?? []).map((_, k) => LINE_COLORS[k % LINE_COLORS.length]), [chart]);

  // Build the chart when the data changes.
  useEffect(() => {
    if (!el.current || !chart || !chart.t.length) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#131722" }, textColor: "#b2b5be", fontSize: 11,
                fontFamily: "-apple-system, BlinkMacSystemFont, Trebuchet MS, Roboto, sans-serif", panes: { separatorColor: "#2a2e39", enableResize: true } },
      grid: { vertLines: { color: "rgba(42,46,57,0.6)" }, horzLines: { color: "rgba(42,46,57,0.6)" } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#758696", labelBackgroundColor: "#2a2e39" }, horzLine: { color: "#758696", labelBackgroundColor: "#2a2e39" } },
      rightPriceScale: { borderColor: "#2a2e39" },
      timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false, rightOffset: 8 },
      localization: { timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") },
    });
    const out = new Set(chart.out);
    const candles = c.addSeries(CandlestickSeries, { upColor: "#089981", downColor: "#f23645", borderVisible: false, wickUpColor: "#089981", wickDownColor: "#f23645", priceLineVisible: false });
    candles.setData(chart.t.map((t, k) => ({ time: t as UTCTimestamp, open: chart.o[k], high: chart.h[k], low: chart.l[k], close: chart.c[k],
      ...(out.has(k) ? { color: "#434651", wickColor: "#434651" } : {}) })));
    chart.ind.forEach((ind, k) => {
      const s = c.addSeries(LineSeries, { color: colors[k], lineWidth: ind.overlay ? 2 : 1, priceLineVisible: false, lastValueVisible: false,
                                          crosshairMarkerVisible: false }, ind.overlay ? 0 : 1);
      s.setData(ind.v.map((v, j) => (v === null ? { time: chart.t[j] as UTCTimestamp } : { time: chart.t[j] as UTCTimestamp, value: v })));
    });
    if (chart.ind.some((i) => !i.overlay)) c.panes()[1]?.setHeight(90);
    const markers = createSeriesMarkers(candles, []);
    const overlay = new Overlay();
    candles.attachPrimitive(overlay);
    c.subscribeCrosshairMove((p) => {
      if (!p.time) { setLegend(null); return; }
      setLegend({ k: snapIdx(chart.t, p.time as number) });
    });
    api.current = { chart: c, candles, markers, overlay };
    return () => { c.remove(); api.current = null; };
  }, [chart, colors]);

  // Click on the chart selects the trade under the cursor.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const h = (p: { time?: Time }) => {
      if (!p.time) return;
      const t = p.time as number;
      const hit = trades.find((x) => snapIdx(times, x.entry_time) <= snapIdx(times, t) && snapIdx(times, t) <= snapIdx(times, x.exit_time));
      if (hit) onSelect(hit.n);
    };
    a.chart.subscribeClick(h);
    return () => a.chart.unsubscribeClick(h);
  }, [trades, times, onSelect, chart]);

  // Markers and overlay for trades; zoom to the selected one.
  useEffect(() => {
    const a = api.current;
    if (!a || !times.length) return;
    const ms: SeriesMarker<Time>[] = [];
    for (const r of chart?.rolls ?? []) ms.push({ time: r.time as UTCTimestamp, position: "aboveBar", color: "#f7a600", shape: "square", text: `Roll → ${r.to}` });
    const segs: Seg[] = [];
    let boxes: Box[] = [];
    for (const tr of trades) {
      const long = tr.direction === "long";
      const sel = tr.n === selected;
      const e = times[snapIdx(times, tr.entry_time)], x = times[snapIdx(times, tr.exit_time)];
      const win = tr.net_pnl > 0;
      ms.push({ time: e as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: sel ? "#2962ff" : "#5b8cff", shape: long ? "arrowUp" : "arrowDown",
                size: sel ? 1.4 : 1 });
      ms.push({ time: x as UTCTimestamp, position: long ? "aboveBar" : "belowBar", color: win ? "#089981" : "#f23645", shape: "circle",
                size: sel ? 1.2 : 0.8 });
      if (!sel) segs.push({ t0: e, t1: x, p0: tr.entry_price, p1: tr.exit_price, color: win ? "rgba(8,153,129,0.6)" : "rgba(242,54,69,0.6)", dash: true });
    }
    const st = trades.find((t) => t.n === selected);
    if (st) {
      const s = times[snapIdx(times, st.signal_time)];
      const e = times[snapIdx(times, st.entry_time)], x = times[snapIdx(times, st.exit_time)];
      ms.push({ time: s as UTCTimestamp, position: st.direction === "long" ? "belowBar" : "aboveBar", color: "#d1d4dc", shape: "circle", text: "Signal", size: 0.7 });
      boxes = [{ t0: e, t1: x, p0: st.entry_price, p1: st.stop, fill: "rgba(242,54,69,0.18)", stroke: "#f23645", label: "Risk", strong: true }];
      if (st.target !== null) boxes.push({ t0: e, t1: x, p0: st.entry_price, p1: st.target, fill: "rgba(8,153,129,0.18)", stroke: "#089981", label: "Reward", strong: true });
      const sameEntry = st.planned_entry === st.entry_price;
      segs.push(
        { t0: s, t1: x, p0: st.planned_entry, p1: st.planned_entry, color: "#5b8cff", dash: true },
        { t0: e, t1: x, p0: st.stop, p1: st.stop, color: "#f23645", dash: true, label: `stop ${st.stop}` },
        { t0: e, t1: x, p0: st.entry_price, p1: st.entry_price, color: "#d1d4dc", width: 1.5,
          label: sameEntry ? `entry ${st.entry_price}` : `entry ${st.entry_price} (planned ${st.planned_entry})` },
        { t0: x, t1: x, p0: st.exit_price, p1: st.exit_price, color: "#f7a600", width: 2, label: `exit ${st.exit_price} · ${st.exit_reason}` },
      );
      if (st.target !== null) segs.push({ t0: e, t1: x, p0: st.target, p1: st.target, color: "#089981", dash: true, label: `target ${st.target}` });
      for (let k = 1; k < st.stop_history.length; k++) {
        const h = st.stop_history[k];
        const t0 = times[snapIdx(times, h.time)];
        segs.push({ t0, t1: x, p0: h.stop, p1: h.stop, color: "#ff8a80", dash: true, label: `stop moved ${h.stop}` });
      }
      const i0 = snapIdx(times, st.signal_time), i1 = snapIdx(times, st.exit_time);
      const span = Math.max(30, i1 - i0);
      a.chart.timeScale().setVisibleLogicalRange({ from: i0 - span * 1.2, to: i1 + span * 0.6 });
    }
    ms.sort((p, q) => (p.time as number) - (q.time as number));
    a.markers.setMarkers(ms);
    a.overlay.set(boxes, segs);
    if (!st && selected === null) {
      if (times.length > 500) a.chart.timeScale().setVisibleLogicalRange({ from: times.length - 400, to: times.length + 5 });
      else a.chart.timeScale().fitContent();
    }
  }, [trades, selected, times, chart]);

  const k = legend?.k ?? times.length - 1;
  const ordered = useMemo(() => [...trades].sort((a, b) => a.entry_time - b.entry_time), [trades]);
  const pos = ordered.findIndex((t) => t.n === selected);
  const go = (d: number) => {
    if (!ordered.length) return;
    const next = ordered[pos < 0 ? (d > 0 ? 0 : ordered.length - 1) : Math.min(ordered.length - 1, Math.max(0, pos + d))];
    onSelect(next.n);
  };

  if (!chart || !chart.t.length) {
    return <div className="flex h-full items-center justify-center bg-tv-bg">{emptyState}</div>;
  }
  return (
    <div className="relative h-full w-full bg-tv-bg">
      <div ref={el} className="absolute inset-0" />
      <div className="pointer-events-none absolute left-2 top-2 z-10 space-y-0.5 text-[12px]">
        <div className="flex gap-2 text-tv-text">
          <span className="text-tv-muted">O</span><span className={chart.c[k] >= chart.o[k] ? "text-up" : "text-down"}>{fmt(chart.o[k])}</span>
          <span className="text-tv-muted">H</span><span className={chart.c[k] >= chart.o[k] ? "text-up" : "text-down"}>{fmt(chart.h[k])}</span>
          <span className="text-tv-muted">L</span><span className={chart.c[k] >= chart.o[k] ? "text-up" : "text-down"}>{fmt(chart.l[k])}</span>
          <span className="text-tv-muted">C</span><span className={chart.c[k] >= chart.o[k] ? "text-up" : "text-down"}>{fmt(chart.c[k])}</span>
        </div>
        {chart.ind.map((ind, j) => (
          <div key={ind.id} className="flex gap-2"><span style={{ color: colors[j] }}>{ind.label}</span><span className="text-tv-text">{fmt(ind.v[k])}</span></div>
        ))}
      </div>
      <div className="absolute right-16 top-2 z-10 flex items-center gap-1 rounded bg-tv-panel/90 p-0.5 text-tv-text shadow">
        <button className="btn-ghost px-2 py-1" title="Previous trade" onClick={() => go(-1)} disabled={!ordered.length}><ChevronLeft size={15} /></button>
        <span className="min-w-[70px] text-center text-[12px] text-tv-muted">{ordered.length ? (pos >= 0 ? `trade ${pos + 1}/${ordered.length}` : `${ordered.length} trades`) : "no trades"}</span>
        <button className="btn-ghost px-2 py-1" title="Next trade" onClick={() => go(1)} disabled={!ordered.length}><ChevronRight size={15} /></button>
        <button className="btn-ghost px-2 py-1" title="Show everything" onClick={() => { onSelect(null); api.current?.chart.timeScale().fitContent(); }}><Maximize2 size={14} /></button>
      </div>
      {banner && <div className="pointer-events-none absolute left-1/2 top-2 z-10 -translate-x-1/2">{banner}</div>}
    </div>
  );
}
