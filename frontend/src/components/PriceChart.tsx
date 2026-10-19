/*
 * Candles + indicators + one trade overlay.
 * Planned levels (from the rules) are dashed lines; actual historical fills and exits are solid markers.
 * Risk (entry→stop) and reward (entry→target) are shaded boxes spanning the trade's lifetime.
 */
import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, CrosshairMode, IChartApi, ISeriesApi, ISeriesPrimitive,
  LineSeries, LineStyle, SeriesAttachedParameter, SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import type { ChartData, Overlay } from "../api";

const PALETTE = ["#60a5fa", "#f59e0b", "#a78bfa", "#2dd4bf", "#f472b6", "#facc15"];

interface Box { t0: number; t1: number; p0: number; p1: number; fill: string; stroke: string; label?: string; changed?: boolean }

class BoxesPrimitive implements ISeriesPrimitive<Time> {
  private p: SeriesAttachedParameter<Time> | null = null;
  constructor(private boxes: Box[]) {}
  attached(param: SeriesAttachedParameter<Time>) { this.p = param; }
  detached() { this.p = null; }
  setBoxes(b: Box[]) { this.boxes = b; this.p?.requestUpdate(); }
  paneViews() {
    const self = this;
    return [{
      zOrder: () => "bottom" as const,
      renderer: () => ({
        draw(target: any) { // eslint-disable-line @typescript-eslint/no-explicit-any
          const p = self.p;
          if (!p) return;
          target.useBitmapCoordinateSpace((scope: any) => { // eslint-disable-line @typescript-eslint/no-explicit-any
            const ctx: CanvasRenderingContext2D = scope.context;
            const hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            const ts = p.chart.timeScale();
            for (const b of self.boxes) {
              const x0 = ts.timeToCoordinate(b.t0 as UTCTimestamp);
              let x1 = ts.timeToCoordinate(b.t1 as UTCTimestamp);
              const y0 = p.series.priceToCoordinate(b.p0);
              const y1 = p.series.priceToCoordinate(b.p1);
              if (x0 === null || y0 === null || y1 === null) continue;
              if (x1 === null) x1 = (x0 + 40) as never;
              const bar = Math.max(4, ts.options().barSpacing);
              const L = Math.min(x0, x1 as number) - bar / 2, R = Math.max(x0, x1 as number) + bar / 2;
              ctx.fillStyle = b.fill;
              ctx.fillRect(L * hr, Math.min(y0, y1) * vr, (R - L) * hr, Math.abs(y1 - y0) * vr);
              ctx.strokeStyle = b.stroke;
              ctx.lineWidth = (b.changed ? 2.5 : 1) * hr;
              ctx.strokeRect(L * hr, Math.min(y0, y1) * vr, (R - L) * hr, Math.abs(y1 - y0) * vr);
              if (b.label) {
                ctx.fillStyle = b.stroke;
                ctx.font = `${11 * vr}px Inter, sans-serif`;
                ctx.fillText(b.label, (L + 4) * hr, (Math.min(y0, y1) + 13) * vr);
              }
            }
          });
        },
      }),
    }];
  }
}

function snap(times: number[], t: number): number {
  // Last candle time at or before t (fills can happen inside a bar).
  let lo = 0, hi = times.length - 1, ans = times[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) { ans = times[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export default function PriceChart({ data, overlay, height = 420, highlight = [] }: {
  data: ChartData; overlay?: Overlay | null; height?: number; highlight?: string[];
}) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      height, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#111723" }, textColor: "#b3bcd1", fontFamily: "Inter, sans-serif",
                panes: { separatorColor: "#273149" } },
      grid: { vertLines: { color: "#1b2334" }, horzLines: { color: "#1b2334" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#273149" },
      timeScale: { borderColor: "#273149", timeVisible: true, secondsVisible: false },
      localization: { timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ") },
    });
    chartRef.current = chart;
    const candles: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444", wickUpColor: "#22c55e", wickDownColor: "#ef4444", borderVisible: false,
    });
    candles.setData(data.candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close,
      ...(c.in_session ? {} : { color: "#3a4663", wickColor: "#3a4663" }) })));
    data.indicators.forEach((ind, k) => {
      const s = chart.addSeries(LineSeries, {
        color: PALETTE[k % PALETTE.length], lineWidth: highlight.includes("indicators") ? 3 : 2, priceLineVisible: false,
        lastValueVisible: false, title: ind.label,
      }, ind.overlay ? 0 : 1);
      s.setData(ind.points.map((p) => (p.value === undefined ? { time: p.time as UTCTimestamp } : { time: p.time as UTCTimestamp, value: p.value })));
    });
    const times = data.candles.map((c) => c.time);
    const markers: SeriesMarker<Time>[] = [];
    for (const r of data.rolls) markers.push({ time: r.time as UTCTimestamp, position: "aboveBar", color: "#f59e0b", shape: "square", text: `roll ${r.from}→${r.to}` });
    const boxes: Box[] = [];
    if (overlay && times.length) {
      const long = overlay.direction === "long";
      const sig = snap(times, overlay.signal_time), ent = snap(times, overlay.entry_time), ex = snap(times, overlay.exit_time);
      markers.push({ time: sig as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: "#60a5fa", shape: "circle", text: "Signal (bar close)" });
      markers.push({ time: ent as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: long ? "#22c55e" : "#ef4444",
                     shape: long ? "arrowUp" : "arrowDown", text: `Fill ${overlay.entry_price}` });
      markers.push({ time: ex as UTCTimestamp, position: long ? "aboveBar" : "belowBar", color: "#eef1f8", shape: long ? "arrowDown" : "arrowUp",
                     text: `Exit ${overlay.exit_price} (${overlay.exit_reason})` });
      const ch = (k: string) => highlight.includes(k);
      boxes.push({ t0: ent, t1: ex, p0: overlay.entry_price, p1: overlay.stop, fill: "rgba(239,68,68,0.16)", stroke: "#ef4444", label: "risk", changed: ch("stop") });
      if (overlay.target !== null) boxes.push({ t0: ent, t1: ex, p0: overlay.entry_price, p1: overlay.target, fill: "rgba(34,197,94,0.14)", stroke: "#22c55e", label: "reward", changed: ch("target") });
      const pl = (price: number, color: string, title: string, style = LineStyle.Dashed, width: 1 | 2 | 3 = 1) =>
        candles.createPriceLine({ price, color, lineStyle: style, lineWidth: width, axisLabelVisible: true, title });
      pl(overlay.planned_entry, "#93c5fd", overlay.order_level ? "planned entry (order)" : "planned entry (next open)", LineStyle.Dashed, ch("entry") ? 3 : 1);
      pl(overlay.stop, "#f87171", "planned stop", LineStyle.Dashed, ch("stop") ? 3 : 1);
      if (overlay.target !== null) pl(overlay.target, "#4ade80", "planned target", LineStyle.Dashed, ch("target") ? 3 : 1);
      pl(overlay.entry_price, "#e5e7eb", "actual fill", LineStyle.Solid);
      pl(overlay.exit_price, "#fbbf24", "actual exit", LineStyle.Solid);
      if (overlay.stop_history.length > 1) {
        const s = chart.addSeries(LineSeries, { color: "#fca5a5", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, title: "stop moves" });
        const pts = overlay.stop_history.map((h) => ({ time: snap(times, h.time) as UTCTimestamp, value: h.stop }));
        pts.push({ time: ex as UTCTimestamp, value: overlay.stop_history[overlay.stop_history.length - 1].stop });
        const dedup = pts.filter((p, k) => k === 0 || p.time > pts[k - 1].time);
        s.setData(dedup);
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(candles, markers);
    candles.attachPrimitive(new BoxesPrimitive(boxes));
    if (overlay && times.length) {
      const a = Math.max(0, times.indexOf(snap(times, overlay.signal_time)) - 40);
      const z = Math.min(times.length - 1, times.indexOf(snap(times, overlay.exit_time)) + 15);
      chart.timeScale().setVisibleLogicalRange({ from: a, to: z });
    } else chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; };
  }, [data, overlay, height, highlight.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  return <div ref={el} style={{ height }} className="w-full overflow-hidden rounded-lg" />;
}
