"use client";

import { Orbit } from "lucide-react";
import { ago, fmt, parseTs, type Forecast } from "@/lib/api";

/** Chart colours taken from the reference forecast chart (card, rules, bars, deep-sage forecast line). */
const C = {
  border: "#cdd6e5",
  rule: "#e7e9ef",
  axis: "#c5ccd4",
  title: "#1b2330",
  label: "#67707f",
  faint: "#9ba2b1",
  bar: "#dde1e7",
  trend: "#97a597",
  forecast: "#5f7556",
  shade: "#f3f4f1",
};

type Hour = { from: string; to: string; copies: number };

const TZ = "Europe/Berlin";
const hourLabel = (ts: string, withDay: boolean) =>
  parseTs(ts)?.toLocaleString("en-GB", {
    ...(withDay ? { weekday: "short" as const } : {}), hour: "2-digit", minute: "2-digit", timeZone: TZ,
  }) ?? "";
const textW = (text: string, size: number) => text.length * size * 0.56; // rough width of a label in SVG units

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/** Hour by hour until packing closes: copies packed so far (bars), where the forecast flattens when a part runs out,
 *  and the orders still to pack (dotted). Built from the Planner's own hourly forecast, nothing estimated here. */
export function ForecastChart({ forecast, now }: { forecast: Forecast; now: number }) {
  const hours = (Array.isArray(forecast.details?.hourly) ? forecast.details.hourly : []) as Hour[];
  const projected = forecast.projected_packed;
  if (!hours.length || projected == null) return null;

  const made = hours.reduce((sum, h) => sum + (Number(h.copies) || 0), 0);
  const start = projected - made; // packed when the snapshot was taken
  const target = forecast.shortfall == null ? null : projected + forecast.shortfall;
  const cumulative: number[] = [];
  hours.reduce((sum, h) => {
    const next = sum + (Number(h.copies) || 0);
    cumulative.push(next);
    return next;
  }, start);
  const short = forecast.shortfall ?? 0;
  // Packing ends after the last hour that still packs. That is a stop only while orders are left: an edition that
  // finishes early also ends in empty hours.
  let stop = hours.length;
  while (stop > 0 && !(Number(hours[stop - 1].copies) > 0)) stop -= 1;
  const stops = stop < hours.length && short > 0;
  const lastAt = stop > 0 ? hourLabel(hours[stop - 1].to, true) : "";

  // Geometry (SVG units; the chart scales to its card).
  const W = 560, H = 256, L = 46, R = 12, T = 30, B = 30;
  const PW = W - L - R, PH = H - T - B;
  const top = Math.max(target ?? 0, projected);
  const step = niceStep(Math.max((top - start) / 2, top * 0.02, 1));
  const y0 = Math.floor(start / step) * step;
  const y1 = Math.max(Math.ceil(top / step) * step, y0 + step);
  const ticks: number[] = [];
  for (let v = y0; v <= y1 + 1e-6; v += step) ticks.push(v);
  // Tick labels in thousands with as many decimals as the step needs (52.5k, 55k, 57.5k), so none repeat.
  const decimals = step >= 1000 ? (step % 1000 === 0 ? 0 : step % 100 === 0 ? 1 : 2) : 0;
  const tick = (v: number) => (step >= 1000 || v >= 10000
    ? `${(v / 1000).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: Math.max(decimals, step < 1000 ? 1 : 0) })}k`
    : fmt(v));
  const slot = PW / hours.length;
  const x = (i: number) => L + slot * i + slot / 2;
  const y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * PH;
  const barW = Math.max(2, slot * 0.62);
  const stopX = L + slot * stop;

  // X labels: every new day ("Sun 06:00") unless the next day starts right after it; hours in between keep clear.
  const labelEvery = Math.max(1, Math.round(hours.length / 6));
  const gap = Math.max(2, Math.ceil(labelEvery * 0.75));
  const days = hours.map((h) => hourLabel(h.from, true).split(" ")[0]);
  const starts = hours.map((_, i) => i).filter((i) => i === 0 || days[i] !== days[i - 1]);
  const dayStarts = starts.filter((d, k) => k === starts.length - 1 || starts[k + 1] - d >= gap);
  const labelled = hours.map((_, i) => dayStarts.includes(i)
    || (i % labelEvery === 0 && dayStarts.every((d) => Math.abs(i - d) >= gap)));

  // Annotations, kept inside the plot and off each other.
  const markText = `last copies ${lastAt}`;
  const markLeft = stopX - L - 8 >= textW(markText, 13);
  const shortText = `${fmt(short)} short`;
  const shortW = textW(shortText, 17);
  const shortX = stops
    ? Math.min(L + PW - shortW / 2 - 4, Math.max(stopX + shortW / 2 + 8, (stopX + L + PW) / 2))
    : L + PW - shortW / 2 - 4;
  const between = target != null ? y(target) : y(projected);
  const roomy = y(projected) - between >= 34;
  const shortY = roomy ? (y(projected) + between) / 2 + 6 : Math.min(T + PH - 8, y(projected) + 22);
  const dots = [...new Set([Math.max(0, stop - 1), Math.round((stop + hours.length - 1) / 2), hours.length - 1])];

  const status = stops ? `last copies packed ${lastAt} · ${fmt(short)} short`
    : short > 0 ? `${fmt(short)} short by close`
      : stop === 0 ? "nothing left to pack" : stop < hours.length ? `every order packed by ${lastAt}` : "on track to pack every order";

  return (
    <section className="overflow-hidden rounded-xl border bg-surface" style={{ borderColor: C.border }}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-b px-5 py-3.5" style={{ borderColor: C.rule }}>
        <h3 className="min-w-0 text-balance text-[17px] font-semibold" style={{ color: C.title }}>
          {forecast.edition} edition · packing forecast
        </h3>
        <span className="shrink-0 text-sm" style={{ color: C.label }}>copies packed</span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 border-b px-5 py-2.5" style={{ borderColor: C.rule }}>
        <p className="flex min-w-0 items-start gap-2 text-[15px]" style={{ color: C.title }}>
          <Orbit aria-hidden className="mt-1 size-4 shrink-0" style={{ color: C.forecast }} />
          <span className="text-pretty">Forecast ready: {status}</span>
        </p>
        <span className="shrink-0 text-sm tabular" style={{ color: C.label }}>{ago(forecast.computed_at, now)}</span>
      </div>

      {/* On a phone the chart keeps a readable size and scrolls sideways inside its card. */}
      <div className="overflow-x-auto px-2 pt-2 sm:px-4">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full min-w-[440px]"
          role="img"
          aria-label={`${forecast.edition}: ${fmt(start)} packed at the snapshot, ${fmt(projected)} by close${
            target != null ? ` of ${fmt(target)} ordered` : ""}${stops ? `, last copies packed ${lastAt}` : ""}.`}
        >
          {/* Hours after the line stops. */}
          {stops && <rect x={stopX} y={T} width={L + PW - stopX} height={PH} fill={C.shade} />}
          {ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={L + PW} y1={y(v)} y2={y(v)} stroke={C.rule} />
              <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="13" fill={C.label}>{tick(v)}</text>
            </g>
          ))}
          {/* Copies packed by the end of each hour while the line runs. */}
          {cumulative.slice(0, stop).map((v, i) => (
            <rect key={i} x={x(i) - barW / 2} y={y(v)} width={barW} height={Math.max(0, T + PH - y(v))} rx="1.5" fill={C.bar} />
          ))}
          {stop > 1 && (
            <polyline
              points={cumulative.slice(0, stop).map((v, i) => `${x(i)},${y(v)}`).join(" ")}
              fill="none" stroke={C.trend} strokeWidth="1.6"
            />
          )}
          {target != null && (
            <>
              <line x1={L} x2={L + PW} y1={y(target)} y2={y(target)} stroke={C.faint} strokeWidth="1.6" strokeDasharray="2 5" strokeLinecap="round" />
              <text x={L + PW} y={y(target) - 7} textAnchor="end" fontSize="13" fill={C.label}
                stroke="#fff" strokeWidth="4" paintOrder="stroke" strokeLinejoin="round">orders to pack {fmt(target)}</text>
            </>
          )}
          {/* After the stop the forecast stays flat: the copies that will not be packed. */}
          {stops && (
            <>
              <polyline
                points={[`${x(Math.max(0, stop - 1))},${y(cumulative[Math.max(0, stop - 1)])}`, `${x(hours.length - 1)},${y(projected)}`].join(" ")}
                fill="none" stroke={C.forecast} strokeWidth="2.4"
              />
              {dots.map((i) => (
                <circle key={i} cx={x(i)} cy={y(cumulative[i])} r="4" fill={C.forecast} />
              ))}
              <line x1={stopX} x2={stopX} y1={T - 6} y2={T + PH} stroke={C.faint} strokeDasharray="3 4" />
              <text x={markLeft ? stopX - 6 : stopX + 6} y={T + PH - 8} textAnchor={markLeft ? "end" : "start"} fontSize="13"
                fill={C.label} stroke={markLeft ? "#fff" : C.shade} strokeWidth="4" paintOrder="stroke" strokeLinejoin="round">
                {markText}
              </text>
            </>
          )}
          {short > 0 && (
            <text x={shortX} y={shortY} textAnchor="middle" fontSize="17" fontWeight="700" fill={C.forecast}
              stroke={stops ? C.shade : "#fff"} strokeWidth="4" paintOrder="stroke" strokeLinejoin="round">
              {shortText}
            </text>
          )}
          <line x1={L} x2={L + PW} y1={T + PH} y2={T + PH} stroke={C.axis} />
          {hours.map((h, i) => {
            if (!labelled[i]) return null;
            return (
              <text key={h.from} x={x(i)} y={H - 9} textAnchor="middle" fontSize="13" fill={C.label}>
                {hourLabel(h.from, dayStarts.includes(i))}
              </text>
            );
          })}
        </svg>
      </div>

      <ul className="flex flex-wrap gap-x-6 gap-y-1 px-5 pb-4 pt-1 text-sm" style={{ color: C.label }}>
        <li className="flex items-center gap-2"><span aria-hidden className="size-3 rounded-[3px]" style={{ background: C.bar }} />packed by then</li>
        {stops && <li className="flex items-center gap-2"><span aria-hidden className="h-[3px] w-5 rounded" style={{ background: C.forecast }} />forecast after the stop</li>}
        {target != null && <li className="flex items-center gap-2"><span aria-hidden className="w-5 border-t-2 border-dotted" style={{ borderColor: C.faint }} />orders to pack</li>}
      </ul>
    </section>
  );
}

/** One chart per edition (newest forecast each), the edition furthest behind first. */
export function ForecastPanel({ forecasts, now }: { forecasts: Forecast[]; now: number }) {
  const newest = new Map<string, Forecast>();
  for (const f of forecasts) {
    const prev = newest.get(f.product_id);
    if (!prev || f.id > prev.id) newest.set(f.product_id, f);
  }
  const rows = [...newest.values()]
    .filter((f) => Array.isArray(f.details?.hourly) && f.details.hourly.length > 0)
    .sort((a, b) => (b.shortfall ?? 0) - (a.shortfall ?? 0));
  if (!rows.length) {
    return (
      <p className="rounded-xl border border-dashed border-rule bg-surface px-5 py-8 text-sm text-muted">
        No packing forecast yet. It appears after the first data drop.
      </p>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {rows.map((f) => <ForecastChart key={f.product_id} forecast={f} now={now} />)}
    </div>
  );
}
