"use client";

import { ArrowDown, ArrowUp, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { fmtPts } from "../city-store";
import { downloadCsv, iso, seriesOf, signed, tone, when, type Position, type Settled } from "./data";

export type Result = "won" | "lost" | "void" | "open";

export interface BetRow {
  betId: string;
  market: string;
  series: string;
  question: string;
  label: string;
  stake: number;
  odds: number;
  result: Result;
  pnl: number | null;
  reading: string;
  source: string;
  placedAt: number;
  closedAt: number | null;
}

export function betRows(history: Settled[], positions: Position[], sources: Record<string, string>): BetRow[] {
  const settled = history.map((h): BetRow => ({
    betId: h.betId,
    market: h.market,
    series: seriesOf(h.market),
    question: h.question,
    label: h.label,
    stake: h.stake,
    odds: h.odds,
    result: h.result,
    pnl: h.pnl,
    reading: h.reading,
    source: sources[seriesOf(h.market)] ?? "",
    placedAt: h.placedAt,
    closedAt: h.settledAt,
  }));
  const open = positions.map((p): BetRow => ({
    betId: p.betId,
    market: p.market,
    series: seriesOf(p.market),
    question: p.question,
    label: p.label,
    stake: p.stake,
    odds: p.odds,
    result: "open",
    pnl: null,
    reading: "",
    source: sources[seriesOf(p.market)] ?? "",
    placedAt: p.placedAt,
    closedAt: null,
  }));
  return [...open, ...settled];
}

const RESULT_LABEL: Record<Result, string> = { won: "Won", lost: "Lost", void: "Void, refunded", open: "Open" };
type SortKey = "placedAt" | "closedAt" | "stake" | "odds" | "pnl";
const PAGE = 20;

export function BetTable({ rows }: { rows: BetRow[] }) {
  const [series, setSeries] = useState("all");
  const [result, setResult] = useState<"all" | Result>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "placedAt", dir: -1 });
  const [page, setPage] = useState(0);

  const allSeries = useMemo(() => [...new Set(rows.map((r) => r.series))].sort(), [rows]);
  const shown = useMemo(() => {
    const f = rows.filter((r) => (series === "all" || r.series === series) && (result === "all" || r.result === result));
    const v = (r: BetRow) => r[sort.key] ?? -Infinity;
    return f.sort((a, b) => (v(a) - v(b)) * sort.dir);
  }, [rows, series, result, sort]);
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const at = Math.min(page, pages - 1);
  const slice = shown.slice(at * PAGE, at * PAGE + PAGE);

  const head = (key: SortKey, label: string, right = true) => {
    const active = sort.key === key;
    return (
      <th className={right ? "pf-r" : undefined} aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
        <button type="button" className="pf-sort" data-active={active || undefined} onClick={() => setSort({ key, dir: active ? (sort.dir === 1 ? -1 : 1) : -1 })}>
          {label}
          {active && (sort.dir === 1 ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
        </button>
      </th>
    );
  };

  const exportCsv = () =>
    downloadCsv(
      `overwatch-bets-${new Date().toISOString().slice(0, 10)}.csv`,
      ["bet_id", "placed_at", "closed_at", "market", "series", "question", "option", "stake", "odds", "result", "pnl", "reading", "source"],
      shown.map((r) => [r.betId, iso(r.placedAt), r.closedAt ? iso(r.closedAt) : "", r.market, r.series, r.question, r.label, r.stake, r.odds, r.result, r.pnl ?? "", r.reading, r.source]),
    );

  return (
    <>
      <div className="pf-toolbar">
        <label className="pf-field">
          <span>Series</span>
          <select
            value={series}
            onChange={(e) => {
              setSeries(e.target.value);
              setPage(0);
            }}
          >
            <option value="all">All series</option>
            {allSeries.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="pf-field">
          <span>Result</span>
          <select
            value={result}
            onChange={(e) => {
              setResult(e.target.value as "all" | Result);
              setPage(0);
            }}
          >
            <option value="all">All results</option>
            <option value="open">Open</option>
            <option value="won">Won</option>
            <option value="lost">Lost</option>
            <option value="void">Void</option>
          </select>
        </label>
        <span className="pf-grow tl-muted tl-num">
          {shown.length} of {rows.length} bets
        </span>
        <button type="button" className="pf-btn" onClick={exportCsv} disabled={!shown.length}>
          <Download size={14} />
          Export CSV
        </button>
      </div>
      {slice.length === 0 ? (
        <p className="pf-empty">{rows.length ? "No bets match these filters." : "Your bets appear here once you place one."}</p>
      ) : (
        <div className="pf-scroll">
          <table className="pf-table pf-bets">
            <thead>
              <tr>
                {head("placedAt", "Placed", false)}
                {head("closedAt", "Settled", false)}
                <th>Market</th>
                <th>Series</th>
                <th>Option</th>
                {head("stake", "Stake")}
                {head("odds", "Odds")}
                <th>Result</th>
                {head("pnl", "P&L")}
                <th>Settlement reading and source</th>
              </tr>
            </thead>
            <tbody>
              {slice.map((r) => (
                <tr key={r.betId}>
                  <td className="pf-time tl-data">{when(r.placedAt, true)}</td>
                  <td className="pf-time tl-data">{r.closedAt ? when(r.closedAt, true) : "–"}</td>
                  <td className="pf-q">{r.question}</td>
                  <td>
                    <span className="pf-tag">{r.series}</span>
                  </td>
                  <td>{r.label}</td>
                  <td className="pf-r tl-data">{fmtPts(r.stake)}</td>
                  <td className="pf-r tl-data">{r.odds.toFixed(2)}x</td>
                  <td>
                    <span className="pf-result" data-r={r.result}>
                      {RESULT_LABEL[r.result]}
                    </span>
                  </td>
                  <td className="pf-r tl-data" data-tone={r.pnl == null ? undefined : tone(r.pnl)}>
                    {r.pnl == null ? "–" : signed(r.pnl)}
                  </td>
                  <td className="pf-reading">
                    {r.reading || (r.result === "open" ? "Not settled yet" : "–")}
                    {r.source && <span className="tl-muted">{r.source}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="pf-pager tl-num">
          <button type="button" className="pf-btn" disabled={at === 0} onClick={() => setPage(at - 1)}>
            Previous
          </button>
          <span>
            Page {at + 1} of {pages}
          </span>
          <button type="button" className="pf-btn" disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>
            Next
          </button>
        </div>
      )}
    </>
  );
}
