"use client";

import { Fragment } from "react";
import type { CitySignals, HubsVsUsual, LineBoard, Signal, UsualTrend } from "./city-types";
import { busyText } from "./plain";

const pct = (v: number) => `${Math.round(v * 100)}%`;
const signed = (v: number, dp = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(dp)}`;
const shortHub = (name: string) => name.replace(/ Circus$/, "").replace(/ St Pancras$/, "");
const TREND: Record<UsualTrend, string> = { up: "rises", down: "falls", flat: "is flat" };
const STATUS: Record<string, string> = { FT: "full time", HT: "half time", "1H": "first half", "2H": "second half", AET: "after extra time", PEN: "on penalties" };

/** Plain text with every number set in Geist Mono. */
function Nums({ text }: { text: string }) {
  const parts = text.split(/(\d+-\d+|(?<!\d)[+−-]?\d+(?:[.,:]\d+)*%?)/);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <span key={i} className="tl-data">
            {p}
          </span>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

/** The disruption that matters most right now; planned closures are not news. */
function worstLine(board: LineBoard) {
  const live = board.disrupted.filter((d) => !/planned/i.test(d.desc));
  return [...(live.length ? live : board.disrupted)].sort((a, b) => a.sev - b.sev)[0];
}

function linesText(board: LineBoard): string {
  const worst = worstLine(board);
  const head = `Tube: ${board.good} of ${board.of} lines running normally`;
  return worst ? `${head} · ${worst.name}: ${worst.desc.toLowerCase()}` : head;
}

function footballText(f: NonNullable<CitySignals["football"]>, hubName: (id: string) => string | undefined): string {
  const status = STATUS[f.status] ?? f.status;
  const heading = f.status === "FT" && f.hub ? hubName(f.hub) : undefined;
  return [`${f.home} ${f.score} ${f.away}`, status, heading && `fans heading for ${shortHub(heading)}`].filter(Boolean).join(", ");
}

/** The top ticker: each hub vs a usual day, the Tube board and the latest football result, scrolling like an
 *  exchange tape. Nothing when there is no data. */
export function CityStrip({ signals }: { signals: CitySignals | null }) {
  const hubs = signals?.hubs?.hubs.length ? signals.hubs : null;
  const lines = signals?.lines ?? null;
  const football = signals?.football ?? null;
  if (!hubs && !lines && !football) return null;
  const hubName = (id: string) => hubs?.hubs.find((h) => h.id === id)?.name;
  const worst = lines ? worstLine(lines) : undefined;
  const items = (
    <>
      {hubs?.hubs.map((h) => {
        const delta = Math.round((h.vsUsual - 1) * 100);
        return (
          <span key={h.id} className="tl-tick" data-dir={delta > 0 ? "up" : delta < 0 ? "down" : undefined}>
            <span className="tl-tick-name">{h.name}</span>
            <span className="tl-data">{Math.round(h.vsUsual * 100)}%</span>
            <span className="tl-tick-chg tl-data">
              {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"}
              {Math.abs(delta)}
            </span>
          </span>
        );
      })}
      {lines && (
        <span className="tl-tick" title={worst?.reason || undefined}>
          <span className="tl-tick-name">Tube</span>
          <span className="tl-data">
            {lines.good}/{lines.of}
          </span>
          <span className="tl-tick-note">{worst ? `${worst.name} ${worst.desc.toLowerCase()}` : "all normal"}</span>
        </span>
      )}
      {football && (
        <span className="tl-tick">
          <Nums text={footballText(football, hubName)} />
        </span>
      )}
    </>
  );
  return (
    <div className="tl-strip" role="region" aria-label="London right now, busyness vs a usual day">
      <div className="tl-ticker">
        <div className="tl-ticker-track">{items}</div>
        <div className="tl-ticker-track" aria-hidden>
          {items}
        </div>
      </div>
    </div>
  );
}

/** Five bars, one per hub in the market's order, against a dashed 100% (a usual day) line. */
function HubBars({ data }: { data: HubsVsUsual }) {
  const W = 5;
  const GAP = 3;
  const H = 18;
  const LO = 0.6;
  const HI = 1.4;
  const y = (v: number) => H - Math.max(2, Math.min(H, ((v - LO) / (HI - LO)) * H));
  return (
    <svg className="tl-bars" viewBox={`0 0 ${data.hubs.length * (W + GAP) - GAP} ${H}`} width={data.hubs.length * (W + GAP) - GAP} height={H} role="img" aria-label="Each hub against a usual day">
      {data.hubs.map((h, i) => (
        <rect key={h.id} x={i * (W + GAP)} y={y(h.vsUsual)} width={W} height={H - y(h.vsUsual)} rx={1} data-low={h.furthestBelow || undefined}>
          <title>{`${h.name} ${pct(h.vsUsual)} of a usual ${data.day}`}</title>
        </rect>
      ))}
      <line x1={0} x2={data.hubs.length * (W + GAP) - GAP} y1={y(1)} y2={y(1)} />
    </svg>
  );
}

function vsUsualText(d: HubsVsUsual): string {
  const low = d.hubs.find((h) => h.id === d.laggard);
  const top = d.hubs.find((h) => h.id === d.leader);
  const parts = [top && `${shortHub(top.name)} ${pct(top.vsUsual)} highest`, low && `${shortHub(low.name)} ${pct(low.vsUsual)} furthest below`];
  return `Vs a usual ${d.day}: ${parts.filter(Boolean).join(", ")}`;
}

function clueText(s: Signal, day: string): string {
  switch (s.id) {
    case "vs_usual":
      return vsUsualText(s.data);
    case "usual_curve":
      return `${shortHub(s.data.name)} ${pct(s.data.vsUsual)} of a usual ${day} · usual curve ${TREND[s.data.usualTrend]} next 15 min`;
    case "pulse": {
      const d = s.data;
      const usual = d.usual == null ? "" : ` · usual ${d.usual.toFixed(1)}, ${TREND[d.usualTrend].replace("is ", "")} next 15 min`;
      const move = d.change5 == null ? "" : ` · last 5 min ${signed(d.change5)}`;
      return `City Pulse ${d.pulse.toFixed(1)}${usual}${move}`;
    }
    case "vix_driver": {
      const d = s.data;
      const move = d.change5 == null ? "" : ` · last 5 min ${signed(d.change5, 2)}`;
      return `VIX ${d.vix.toFixed(2)} · moved most by ${d.driver} (${signed(d.driverDelta, 2)})${move}`;
    }
    case "stacks": {
      const d = s.data;
      const each = Object.entries(d.stacks)
        .map(([k, v]) => `${k} ${v}`)
        .join(" · ");
      const move = d.change10 == null ? "" : ` · ${signed(d.change10, 0)} vs 10 min ago`;
      return `${d.total} holding (${each}) · 30-min avg ${d.avg30.toFixed(1)}${move}`;
    }
    case "lines": {
      const since = s.data.sinceLastChangeMin;
      return linesText(s.data) + (since == null ? "" : ` · last change ${since} min ago`);
    }
    default:
      return (s as { text: string }).text.replace(/\s*[▲▼▶]/g, "");
  }
}

/** One or two quiet lines under the question; the vs-usual line carries the five-bar chart. */
export function MarketClues({ signals }: { signals: Signal[] | null | undefined }) {
  const list = (signals ?? []).filter((s) => s && typeof s.text === "string");
  if (!list.length) return null;
  const hubs = list.find((s) => s.id === "vs_usual");
  const curve = list.find((s) => s.id === "usual_curve");
  const day = hubs?.id === "vs_usual" ? hubs.data.day : "Saturday";
  // Why: with both present the curve line already names the laggard, so it sits beside the bars as one line.
  const lines = hubs && curve ? list.filter((s) => s !== hubs) : list;
  return (
    <div className="tl-clues">
      {lines.slice(0, 2).map((s) => {
        const bars = s.id === "vs_usual" ? s.data : s === curve && hubs?.id === "vs_usual" ? hubs.data : null;
        return (
          <p key={s.id} className="tl-clue">
            {bars && <HubBars data={bars} />}
            <span>
              <Nums text={clueText(s, day)} />
            </span>
          </p>
        );
      })}
    </div>
  );
}

/** The usual-day curve for the next 15 min as ratios to now, when a clue provides it. */
export function usualGhost(signals: Signal[] | null | undefined): number[] | null {
  for (const s of signals ?? []) {
    if (s.id === "usual_curve") {
      const hubs = signals?.find((x) => x.id === "vs_usual");
      const row = hubs?.id === "vs_usual" ? hubs.data.hubs.find((h) => h.id === s.data.hub) : undefined;
      const curve = row?.typicalNext15.map((p) => p.typical) ?? [s.data.typicalNow, s.data.typicalIn15];
      if (curve[0]) return curve.map((v) => v / curve[0]);
    }
    if (s.id === "pulse" && s.data.usual && s.data.typicalIn15) return [1, s.data.typicalIn15 / s.data.usual];
  }
  return null;
}
