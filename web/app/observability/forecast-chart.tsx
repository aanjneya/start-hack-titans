"use client";

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
const compact = (v: number) => (Math.abs(v) >= 1000 ? `${fmt(Math.round(v / 1000))}k` : fmt(v));

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function EditionSummary({ forecast }: { forecast: Forecast }) {
  const limiting = typeof forecast.details?.limiting_part_name === "string" ? forecast.details.limiting_part_name : forecast.limiting_part;
  return <div className="px-5 py-4">
    <h2 className="text-lg font-semibold text-ink">{forecast.edition || forecast.product_id}</h2>
    <dl className="mt-2 flex flex-wrap gap-x-8 gap-y-2 text-sm">
      <div><dt className="text-muted">Projected packed by close</dt>
        <dd className="mt-0.5 text-2xl font-bold tabular">{forecast.projected_packed == null ? "Unknown" : fmt(forecast.projected_packed)}</dd></div>
      <div><dt className="text-muted">Shortfall</dt>
        <dd className={`mt-0.5 text-2xl font-bold tabular ${forecast.shortfall ? "text-stop" : "text-ink"}`}>
          {forecast.shortfall == null ? "Unknown" : fmt(forecast.shortfall)}</dd></div>
    </dl>
    {limiting && <p className="mt-2 text-xs text-muted">Limiting part · {limiting}</p>}
  </div>;
}

/** Hour by hour until packing closes: copies packed so far (bars), where the forecast flattens when a part runs out,
 *  and the orders still to pack (dotted). Built from the Planner's own hourly forecast, nothing estimated here. */
export function ForecastChart({ forecast, now }: { forecast: Forecast; now: number }) {
  const hours = (Array.isArray(forecast.details?.hourly) ? forecast.details.hourly : []) as Hour[];
  const projected = forecast.projected_packed;
  if (!hours.length || projected == null) {
    return <section className="rounded-xl border border-rule bg-surface"><EditionSummary forecast={forecast} /></section>;
  }

  const made = hours.reduce((sum, h) => sum + (Number(h.copies) || 0), 0);
  const start = projected - made; // packed when the snapshot was taken
  const target = forecast.shortfall == null ? null : projected + forecast.shortfall;
  const cumulative: number[] = [];
  hours.reduce((sum, h) => {
    const next = sum + (Number(h.copies) || 0);
    cumulative.push(next);
    return next;
  }, start);
  // The line stops from the first hour after which nothing more gets packed.
  let stop = hours.length;
  while (stop > 0 && !(Number(hours[stop - 1].copies) > 0)) stop -= 1;
  const stops = stop < hours.length && (forecast.shortfall ?? 0) > 0;
  if (!stops) stop = hours.length;
  const short = forecast.shortfall ?? 0;

  // Geometry (SVG units; the chart scales to its card).
  const W = 560, H = 250, L = 44, R = 12, T = 30, B = 28;
  const PW = W - L - R, PH = H - T - B;
  const top = Math.max(target ?? 0, projected);
  const step = niceStep((top - start) / 2);
  const y0 = Math.floor(start / step) * step;
  const y1 = Math.max(Math.ceil(top / step) * step, y0 + step);
  const ticks: number[] = [];
  for (let v = y0; v <= y1 + 1e-6; v += step) ticks.push(v);
  const slot = PW / hours.length;
  const x = (i: number) => L + slot * i + slot / 2;
  const y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * PH;
  const barW = Math.max(2, slot * 0.62);
  const stopX = L + slot * stop;
  const labelEvery = Math.max(1, Math.round(hours.length / 6));
  const days = hours.map((h) => hourLabel(h.from, true).split(" ")[0]);
  // Every new day gets a label ("Sun 06:00"); regular hour labels keep clear of it so they never overlap.
  const dayStarts = hours.map((_, i) => i).filter((i) => i === 0 || days[i] !== days[i - 1]);
  const gap = Math.ceil(labelEvery * 0.75);
  const labelled = hours.map((_, i) => dayStarts.includes(i)
    || (i % labelEvery === 0 && dayStarts.every((d) => Math.abs(i - d) >= gap)));

  const stopAt = stops && stop > 0 ? hourLabel(hours[stop - 1].to, true) : stops ? hourLabel(hours[0].from, true) : "";
  const status = forecast.shortfall == null ? "Packing plan by hour"
    : short === 0 ? "On track to pack every order"
    : stops ? `Packing stops ${stopAt}` : "Packing continues through close";

  return (
    <section className="overflow-hidden rounded-xl border bg-surface" style={{ borderColor: C.border }}>
      <EditionSummary forecast={forecast} />
      <div className="flex items-center justify-between gap-3 border-y px-5 py-2.5 text-sm" style={{ borderColor: C.rule }}>
        <p className="font-medium text-ink">{status}</p>
        <span className="shrink-0 text-xs text-muted">{ago(forecast.computed_at, now)}</span>
      </div>

      {/* On a phone the chart keeps a readable size and scrolls sideways inside its card. */}
      <div className="overflow-x-auto px-2 pt-2 sm:px-4">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full min-w-[440px]"
          role="img"
          aria-label={`${forecast.edition}: ${fmt(start)} packed at the snapshot, ${fmt(projected)} by close${
            target != null ? ` of ${fmt(target)} ordered` : ""}${stops ? `, packing stops ${stopAt}` : ""}.`}
        >
          {/* Hours after the line stops. */}
          {stops && <rect x={stopX} y={T} width={L + PW - stopX} height={PH} fill={C.shade} />}
          {ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={L + PW} y1={y(v)} y2={y(v)} stroke={C.rule} />
              <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="12" fill={C.faint}>{compact(v)}</text>
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
          {/* After the stop the forecast stays flat: the copies that will not be packed. */}
          {stops && (
            <>
              <polyline
                points={[`${x(Math.max(0, stop - 1))},${y(cumulative[Math.max(0, stop - 1)])}`, `${x(hours.length - 1)},${y(projected)}`].join(" ")}
                fill="none" stroke={C.forecast} strokeWidth="2.4"
              />
              {[...new Set([Math.max(0, stop - 1), Math.round((stop + hours.length - 1) / 2), hours.length - 1])].map((i) => (
                <circle key={i} cx={x(i)} cy={y(cumulative[i])} r="4" fill={C.forecast} />
              ))}
              <line x1={stopX} x2={stopX} y1={T - 6} y2={T + PH} stroke={C.faint} strokeDasharray="3 4" />
              <text x={stopX - 6} y={T + 8} textAnchor="end" fontSize="12.5" fill={C.faint}>line stops</text>
            </>
          )}
          {target != null && (
            <>
              <line x1={L} x2={L + PW} y1={y(target)} y2={y(target)} stroke={C.faint} strokeWidth="1.6" strokeDasharray="2 5" strokeLinecap="round" />
              <text x={L + PW} y={y(target) - 7} textAnchor="end" fontSize="12.5" fill={C.faint}>orders to pack {compact(target)}</text>
            </>
          )}
          {short > 0 && (
            <text
              x={stops ? (stopX + L + PW) / 2 : L + PW - 4}
              y={Math.max(T + 26, (y(projected) + (target != null ? y(target) : y(projected))) / 2 + 5)}
              textAnchor={stops ? "middle" : "end"} fontSize="16" fontWeight="700" fill={C.forecast}
            >
              {fmt(short)} short
            </text>
          )}
          <line x1={L} x2={L + PW} y1={T + PH} y2={T + PH} stroke={C.axis} />
          {hours.map((h, i) => {
            const newDay = dayStarts.includes(i);
            if (!labelled[i]) return null;
            return (
              <text key={h.from} x={x(i)} y={H - 8} textAnchor="middle" fontSize="12" fill={C.label}>
                {hourLabel(h.from, newDay)}
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
