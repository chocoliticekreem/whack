"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { countdown, fmtW, type MyBet, type Settled } from "./city-store";
import type { Market, Trader } from "./city-types";
import { MiniSpark, Tick, signedW, toneOf, useCountUp, useDismiss } from "./Live";
import { Badge, type TipTarget } from "./MarketCard";
import { plainLabel, plainTitle, plainWatches, recordText } from "./plain";

const PANEL_POLL_MS = 2500;
const SESSION_START = typeof window === "undefined" ? 0 : Date.now();

export function TradersMenu({
  traders,
  hist,
  lastWhy,
  onTip,
  onBoard,
}: {
  traders: Trader[];
  hist: Record<string, number[]>;
  lastWhy: Map<string, string>;
  onTip: (t: TipTarget | null) => void;
  onBoard: () => void;
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, () => setOpen(false), [btn, panel]);
  const ranked = useMemo(() => [...traders].sort((a, b) => b.points - a.points), [traders]);
  const n = traders.length || 7;
  return (
    <>
      <button ref={btn} type="button" className="ow-menu-btn ow-traders-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="ow-stack" aria-hidden>
          {ranked.slice(0, 3).map((t) => (
            <Badge key={t.id} id={t.id} name={t.name} small />
          ))}
        </span>
        <span className="tl-desk">
          {n} <span className="ow-long">computer </span>traders
        </span>
        <ChevronDown size={14} className="ow-chev" aria-hidden />
      </button>
      {open && (
        <>
          <div className="ow-sheet-scrim" aria-hidden />
          <div ref={panel} className="ow-drop ow-traders" role="dialog" aria-label="Computer traders">
            <p className="ow-drop-head">
              <b>{n} computer traders.</b> Each one watches one live London data feed and bets against you.
            </p>
            <ol className="ow-trader-list">
              {ranked.map((t, i) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className="ow-trader"
                    onMouseEnter={(e) => onTip({ trader: t.id, why: lastWhy.get(t.id), el: e.currentTarget })}
                    onMouseLeave={() => onTip(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      onTip({ trader: t.id, why: lastWhy.get(t.id), el: e.currentTarget });
                    }}
                  >
                    <span className="ow-rank tl-data">{i + 1}</span>
                    <Badge id={t.id} name={t.name} />
                    <span className="ow-trader-text">
                      <b>{t.name}</b>
                      <span className="tl-muted">{plainWatches(t.id, t.watches)}</span>
                    </span>
                    <span className="ow-trader-num">
                      <Tick value={t.points} className="ow-pts tl-data">
                        {fmtW(t.points)}
                      </Tick>
                      <MiniSpark values={hist[t.id] ?? [t.points]} />
                      <span className="ow-rec">{recordText(t.record.won, t.record.lost)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
            <button
              type="button"
              className="ow-drop-foot"
              onClick={() => {
                setOpen(false);
                onBoard();
              }}
            >
              Leaderboard
            </button>
          </div>
        </>
      )}
    </>
  );
}

interface Position {
  betId: string;
  market: string;
  question: string;
  label: string;
  stake: number;
  toWin: number;
  settlesAt: number;
}

interface HistoryRow {
  betId: string;
  question: string;
  label: string;
  pnl: number;
  result: "won" | "lost" | "void";
  settledAt: number;
}

interface Row {
  key: string;
  question: string;
  answer: string;
  stake: number;
  payout: number;
  chance: number | null;
  settlesAt: number;
  pendingS: number | null;
}

async function getJson<T>(path: string, token: string | null): Promise<T | null> {
  try {
    const res = await fetch(`/api/city/${path}`, { cache: "no-store", headers: token ? { "x-player-token": token } : {} });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

/** The header's session P&L button and its dropdown: bets in play at live prices, recent results, the session total. */
export function EarningsMenu({
  pnl,
  bets,
  markets,
  results,
  now,
  player,
  token,
  live,
  pulse,
}: {
  pnl: number;
  bets: Record<string, MyBet>;
  markets: Record<string, Market>;
  results: Record<string, Settled>;
  now: number;
  player: string;
  token: string | null;
  live: boolean;
  pulse: number;
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, () => setOpen(false), [btn, panel]);
  const [series, setSeries] = useState<number[]>([pnl]);
  if (series[series.length - 1] !== pnl) setSeries([...series, pnl].slice(-80));
  const shownPnl = useCountUp(pnl, 600);

  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  useEffect(() => {
    if (!open || !live || !token) return;
    let alive = true;
    const q = `player=${encodeURIComponent(player)}`;
    const load = async () => {
      const [p, h] = await Promise.all([
        getJson<{ positions: Position[] }>(`positions?${q}`, token),
        getJson<{ history: HistoryRow[] }>(`history?${q}&limit=6`, token),
      ]);
      if (!alive) return;
      if (p) setPositions(p.positions);
      if (h) setHistory(h.history);
    };
    void load();
    const id = window.setInterval(() => void load(), PANEL_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [open, live, token, player]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    const seen = new Set<string>();
    for (const b of Object.values(bets)) {
      const m = markets[b.market];
      // Why: a bet whose market is no longer in the book is from an earlier visit; the server's positions cover it.
      if (b.done || !m) continue;
      seen.add(b.market);
      const o = m?.options.find((x) => x.id === b.option);
      out.push({
        key: b.market,
        question: plainTitle(m),
        answer: plainLabel(m, b.option),
        stake: b.stake,
        payout: b.payout,
        chance: o && !results[b.market] ? o.prob : null,
        settlesAt: m.settlesAt,
        pendingS: b.pending ? Math.max(0, Math.ceil((b.pending.fillsAt - now) / 1000)) : null,
      });
    }
    if (live) {
      for (const p of positions) {
        if (seen.has(p.market)) continue;
        const m = markets[p.market];
        const o = m?.options.find((x) => x.label === p.label);
        out.push({
          key: p.betId,
          question: m ? plainTitle(m) : p.question,
          answer: m && o ? plainLabel(m, o.id) : p.label,
          stake: p.stake,
          payout: p.toWin,
          chance: o ? o.prob : null,
          settlesAt: p.settlesAt,
          pendingS: null,
        });
      }
    }
    return out;
  }, [bets, markets, results, positions, live, now]);

  const settled = useMemo(() => {
    if (live)
      return history
        .filter((h) => h.settledAt >= SESSION_START).slice(0, 4).map((h) => ({ key: h.betId, question: h.question, answer: h.label, pnl: h.pnl, result: h.result }));
    return Object.values(bets)
      .filter((b) => b.done && markets[b.market])
      .reverse()
      .slice(0, 4)
      .map((b) => {
        const m = markets[b.market];
        return {
          key: b.market,
          question: m ? plainTitle(m) : "",
          answer: m ? plainLabel(m, b.option) : b.label,
          pnl: b.done === "won" ? b.payout - b.stake : b.done === "lost" ? -b.stake : 0,
          result: b.done as "won" | "lost" | "void",
        };
      });
  }, [live, history, bets, markets]);

  const atRisk = rows.reduce((s, r) => s + r.stake, 0);
  const potential = rows.reduce((s, r) => s + (r.pendingS == null ? r.payout : 0), 0);

  return (
    <>
      <button
        ref={btn}
        type="button"
        className="ow-menu-btn ow-earn-btn"
        data-tone={toneOf(pnl)}
        aria-expanded={open}
        aria-label={`This session ${signedW(pnl)}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span key={pulse} className="ow-pnl tl-data" data-pulse={pulse > 0 || undefined}>
          {signedW(shownPnl)}
        </span>
        <ChevronDown size={14} className="ow-chev" aria-hidden />
      </button>
      {open && (
        <>
          <div className="ow-sheet-scrim" aria-hidden />
          <div ref={panel} className="ow-drop ow-earn" role="dialog" aria-label="Your session">
            <header className="ow-earn-head">
              <div>
                <span className="ow-cap">This session</span>
                <b className="ow-earn-total tl-data" data-tone={toneOf(pnl)}>
                  {signedW(shownPnl)}
                </b>
              </div>
              <MiniSpark values={series} w={112} h={34} className="ow-spark ow-spark-big" />
            </header>

            <section className="ow-sec">
              <h4>
                In play <span className="tl-data">{rows.length}</span>
              </h4>
              {rows.length === 0 ? (
                <p className="ow-empty">No open bets.</p>
              ) : (
                <>
                  <div className="ow-pos ow-pos-cols" aria-hidden>
                    <span>Answer</span>
                    <span>Bet</span>
                    <span>Win</span>
                    <span>Worth now</span>
                  </div>
                  {rows.map((r) => (
                    <div key={r.key} className="ow-pos-wrap" data-pending={r.pendingS != null || undefined}>
                      <div className="ow-pos-q">
                        <span>{r.question}</span>
                        <span className={r.pendingS == null && r.settlesAt > now ? "tl-data" : "ow-state"}>
                          {r.pendingS != null
                            ? r.pendingS > 0
                              ? `fills in ${r.pendingS}s`
                              : "filling"
                            : r.settlesAt > now
                              ? countdown(r.settlesAt - now)
                              : "result due"}
                        </span>
                      </div>
                      <div className="ow-pos tl-data">
                        <span className="ow-pos-a">
                          {r.answer}
                          {r.chance != null && r.pendingS == null && <i>{Math.round(r.chance * 100)}%</i>}
                        </span>
                        <span>{fmtW(r.stake)}</span>
                        <span>→ {r.pendingS == null ? fmtW(r.payout) : "…"}</span>
                        <span className="ow-now">
                          {r.chance != null && r.pendingS == null ? (
                            <Tick value={Math.round(r.payout * r.chance)}>≈ {fmtW(r.payout * r.chance)}</Tick>
                          ) : (
                            "–"
                          )}
                        </span>
                      </div>
                    </div>
                  ))}
                  <div className="ow-pos ow-pos-sum tl-data">
                    <span>Total</span>
                    <span>{fmtW(atRisk)}</span>
                    <span>→ {fmtW(potential)}</span>
                    <span />
                  </div>
                </>
              )}
            </section>

            <section className="ow-sec">
              <h4>Just settled</h4>
              {settled.length === 0 ? (
                <p className="ow-empty">Results land here.</p>
              ) : (
                settled.map((s) => (
                  <div key={s.key} className="ow-done">
                    <span className="ow-done-q">
                      <b>{s.answer}</b>
                      {s.question && s.question !== s.answer && <span>{s.question}</span>}
                    </span>
                    {s.result === "void" ? (
                      <span className="ow-state">refunded</span>
                    ) : (
                      <b className="tl-data" data-tone={toneOf(s.pnl)}>
                        {signedW(s.pnl)}
                      </b>
                    )}
                  </div>
                ))
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}
