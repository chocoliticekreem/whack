"use client";

import { ArrowLeft, ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { defaultName, fmtPts, londonClock } from "../city-store";
import type { ServerWallet } from "../Wallet";
import { BetTable, betRows, type BetRow } from "./BetTable";
import { HISTORY_LIMIT, pct, seriesOf, signed, tone, usePortfolio, when, type Entry, type Portfolio as Data, type Position, type Versus } from "./data";
import { EquityChart } from "./EquityChart";

function Stat({ label, value, sub, t }: { label: string; value: string; sub?: string; t?: string }) {
  return (
    <div className="pf-stat">
      <span className="pf-label">{label}</span>
      <b className="tl-data" data-tone={t}>
        {value}
      </b>
      {sub && <span className="pf-sub tl-num">{sub}</span>}
    </div>
  );
}

function Summary({ data }: { data: Data }) {
  const { wallet: w, history } = data;
  const decided = w.totals.won + w.totals.lost;
  const best = history.reduce<number | null>((m, h) => (h.pnl > 0 && (m == null || h.pnl > m) ? h.pnl : m), null);
  const worst = history.reduce<number | null>((m, h) => (h.pnl < 0 && (m == null || h.pnl < m) ? h.pnl : m), null);
  return (
    <section className="pf-card pf-span">
      <header className="pf-card-head">
        <h2>Your points at a glance</h2>
        <span className="pf-sub tl-num">As of {londonClock(w.asOf, true)} London</span>
      </header>
      <div className="pf-stats">
        <Stat label="Cash" value={fmtPts(w.cash)} sub="Available to bet" />
        <Stat label="In play" value={fmtPts(w.escrow)} sub={`${data.positions.length} open ${data.positions.length === 1 ? "bet" : "bets"}`} />
        <Stat label="Total equity" value={fmtPts(w.total)} sub={`Started with ${fmtPts(w.totals.granted)}`} />
        <Stat label="Today's P&L" value={signed(w.pnl.today)} t={tone(w.pnl.today)} sub="Since midnight London" />
        <Stat label="All-time P&L" value={signed(w.pnl.allTime)} t={tone(w.pnl.allTime)} sub={`Returned ${fmtPts(w.totals.returned)} of ${fmtPts(w.totals.staked)} staked`} />
        <Stat label="Win rate" value={pct(decided ? w.totals.won / decided : null)} sub={`${w.totals.won} won · ${w.totals.lost} lost · ${w.totals.void} void`} />
        <Stat label="Bets" value={fmtPts(w.totals.betsPlaced)} sub={`${w.totals.betsSettled} settled`} />
        <Stat label="Average stake" value={w.totals.betsPlaced ? fmtPts(w.totals.staked / w.totals.betsPlaced) : "–"} sub="Points per bet" />
        <Stat label="Biggest win" value={best == null ? "–" : signed(best)} t={best == null ? undefined : "up"} />
        <Stat label="Biggest loss" value={worst == null ? "–" : signed(worst)} t={worst == null ? undefined : "down"} />
      </div>
    </section>
  );
}

function Positions({ positions, markets }: { positions: Position[]; markets: Data["markets"] }) {
  return (
    <section className="pf-card pf-span">
      <header className="pf-card-head">
        <h2>
          Bets still running <span className="pf-count tl-num">{positions.length}</span>
        </h2>
      </header>
      {positions.length === 0 ? (
        <p className="pf-empty">No open bets.</p>
      ) : (
        <div className="pf-scroll">
          <table className="pf-table">
            <thead>
              <tr>
                <th>Market</th>
                <th>Option</th>
                <th className="pf-r">Stake</th>
                <th className="pf-r">Odds</th>
                <th className="pf-r">Potential payout</th>
                <th className="pf-r">Live price</th>
                <th className="pf-r">Settles at</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const m = markets[p.market];
                const now = m && m.settlesAt === p.settlesAt ? m.options.find((o) => o.id === p.option) : undefined;
                return (
                  <tr key={p.betId}>
                    <td className="pf-q">
                      {p.question}
                      <span className="pf-tag">{seriesOf(p.market)}</span>
                    </td>
                    <td>{p.label}</td>
                    <td className="pf-r tl-data">{fmtPts(p.stake)}</td>
                    <td className="pf-r tl-data">{p.odds.toFixed(2)}x</td>
                    <td className="pf-r tl-data">
                      {fmtPts(p.toWin)} <span className="tl-muted">({signed(p.profit)})</span>
                    </td>
                    <td className="pf-r tl-data">{now ? `${Math.round(now.prob * 100)}% · ${now.odds.toFixed(2)}x` : "–"}</td>
                    <td className="pf-r tl-data">{londonClock(p.settlesAt)}</td>
                    <td className="pf-r">
                      <Link className="pf-link" href="/multiverse">
                        {m?.status === "open" ? "Live" : "Market"} <ArrowUpRight size={13} />
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface SeriesRow {
  series: string;
  bets: number;
  won: number;
  lost: number;
  void: number;
  open: number;
  staked: number;
  pnl: number;
}

function bySeries(rows: BetRow[]): SeriesRow[] {
  const out = new Map<string, SeriesRow>();
  for (const r of rows) {
    const s = out.get(r.series) ?? { series: r.series, bets: 0, won: 0, lost: 0, void: 0, open: 0, staked: 0, pnl: 0 };
    s.bets += 1;
    s[r.result] += 1;
    s.staked += r.stake;
    s.pnl += r.pnl ?? 0;
    out.set(r.series, s);
  }
  return [...out.values()].sort((a, b) => b.bets - a.bets);
}

function Breakdown({ rows, versus, traders }: { rows: BetRow[]; versus: Versus[] | null; traders: Record<string, string> }) {
  const series = useMemo(() => bySeries(rows), [rows]);
  const maxAbs = Math.max(1, ...series.map((s) => Math.abs(s.pnl)));
  return (
    <section className="pf-card pf-span">
      <header className="pf-card-head">
        <h2>How you did on each kind of question</h2>
      </header>
      <div className="pf-split">
        <div>
          {series.length === 0 ? (
            <p className="pf-empty">No bets yet.</p>
          ) : (
            <div className="pf-scroll">
              <table className="pf-table">
                <thead>
                  <tr>
                    <th>Series</th>
                    <th className="pf-r">Bets</th>
                    <th className="pf-r">Hit rate</th>
                    <th className="pf-r">Staked</th>
                    <th className="pf-r">P&amp;L</th>
                    <th className="pf-bar-col" aria-hidden />
                  </tr>
                </thead>
                <tbody>
                  {series.map((s) => (
                    <tr key={s.series}>
                      <td>
                        <span className="pf-tag">{s.series}</span>
                      </td>
                      <td className="pf-r tl-data">{s.bets}</td>
                      <td className="pf-r tl-data" title={`${s.won} won, ${s.lost} lost, ${s.void} void, ${s.open} open`}>
                        {pct(s.won + s.lost ? s.won / (s.won + s.lost) : null)}
                        <span className="tl-muted"> {s.won}/{s.won + s.lost}</span>
                      </td>
                      <td className="pf-r tl-data">{fmtPts(s.staked)}</td>
                      <td className="pf-r tl-data" data-tone={tone(s.pnl)}>
                        {signed(s.pnl)}
                      </td>
                      <td className="pf-bar-col" aria-hidden>
                        <span className="pf-bar" data-tone={tone(s.pnl)}>
                          <span style={{ width: `${(Math.abs(s.pnl) / maxAbs) * 50}%`, [s.pnl < 0 ? "right" : "left"]: "50%" }} />
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div>
          <h3>You vs the computer traders</h3>
          <p className="pf-sub">Head-to-head P&amp;L on settled markets you both bet on, since the exchange last started.</p>
          {versus == null ? (
            <p className="pf-empty">The exchange does not serve head-to-head records yet.</p>
          ) : versus.length === 0 ? (
            <p className="pf-empty">No settled market yet where you and an AI trader both had a bet.</p>
          ) : (
            <div className="pf-scroll">
              <table className="pf-table">
                <thead>
                  <tr>
                    <th>Trader</th>
                    <th className="pf-r">Markets</th>
                    <th className="pf-r">Your record</th>
                    <th className="pf-r">Your P&amp;L</th>
                    <th className="pf-r">Their P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {versus.map((v) => (
                    <tr key={v.trader}>
                      <td>{traders[v.trader] ?? v.trader}</td>
                      <td className="pf-r tl-data">{v.markets}</td>
                      <td className="pf-r tl-data">
                        {v.won}–{v.lost}
                        {v.tied ? `–${v.tied}` : ""}
                      </td>
                      <td className="pf-r tl-data" data-tone={tone(v.pnl)}>
                        {signed(v.pnl)}
                      </td>
                      <td className="pf-r tl-data" data-tone={tone(v.theirPnl)}>
                        {signed(v.theirPnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

const KIND_LABEL: Record<string, string> = {
  grant: "Grant",
  bet_hold: "Stake held",
  bet_win: "Win paid",
  bet_loss: "Loss",
  void_refund: "Void refund",
  adjustment: "Adjustment",
};
const ACCOUNT_LABEL: Record<Entry["account"], string> = { player_cash: "Cash", player_escrow: "Escrow", house: "House" };

function Activity({ entries }: { entries: Entry[] }) {
  return (
    <section className="pf-card pf-span">
      <header className="pf-card-head">
        <h2>
          Every points movement <span className="pf-count tl-num">{entries.length}</span>
        </h2>
        <span className="pf-sub">Every ledger entry on your account, newest first. Read-only.</span>
      </header>
      {entries.length === 0 ? (
        <p className="pf-empty">No ledger entries.</p>
      ) : (
        <div className="pf-scroll pf-log">
          <table className="pf-table">
            <thead>
              <tr>
                <th className="pf-r">Entry</th>
                <th>Time</th>
                <th>Kind</th>
                <th className="pf-r">Amount</th>
                <th>Account</th>
                <th>Market</th>
                <th>Bet id</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="pf-r tl-data tl-muted">#{e.id}</td>
                  <td className="pf-time tl-data">{when(e.ts, true)}</td>
                  <td>{KIND_LABEL[e.kind] ?? e.kind}</td>
                  <td className="pf-r tl-data" data-tone={tone(e.amount)}>
                    {signed(e.amount)}
                  </td>
                  <td>{ACCOUNT_LABEL[e.account] ?? e.account}</td>
                  <td className="tl-data">{e.market ?? "–"}</td>
                  <td className="tl-data tl-muted" title={e.betId ?? undefined}>
                    {e.betId ? e.betId.slice(0, 8) : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Limits({ wallet: w }: { wallet: ServerWallet }) {
  const used = w.limits.dailyLoss - w.limits.remainingToday;
  return (
    <section className="pf-card pf-span">
      <header className="pf-card-head">
        <h2>Limits and responsible play</h2>
        <Link className="pf-link" href="/multiverse">
          Change limits in the wallet <ArrowUpRight size={13} />
        </Link>
      </header>
      {w.break.active && w.break.until && <p className="pf-banner tl-num">You are on a break until {when(w.break.until)}. Betting is paused.</p>}
      <div className="pf-stats pf-stats-tight">
        <Stat label="Daily loss limit" value={fmtPts(w.limits.dailyLoss)} sub={w.limits.pending ? `Raise to ${fmtPts(w.limits.pending.value)} from ${when(w.limits.pending.effectiveAt)}` : "No pending change"} />
        <Stat label="Lost today" value={fmtPts(w.limits.lostToday)} />
        <Stat label="In play" value={fmtPts(w.limits.inPlay)} />
        <Stat label="Can still risk today" value={fmtPts(w.limits.remainingToday)} />
        <Stat label="Confirm above" value={fmtPts(w.limits.confirmAbove)} sub="Stakes over 25% of cash ask first" />
        <Stat label="Break" value={w.break.active ? "Active" : "None"} sub={w.break.active && w.break.until ? `Until ${when(w.break.until)}` : "1 or 24 hours, cannot be cancelled"} />
      </div>
      <div className="pf-meter" role="meter" aria-valuemin={0} aria-valuemax={w.limits.dailyLoss} aria-valuenow={used} aria-label="Daily loss limit used">
        <span style={{ width: `${Math.min(100, (used / Math.max(1, w.limits.dailyLoss)) * 100)}%` }} />
      </div>
      <p className="pf-sub tl-num">
        {fmtPts(used)} of {fmtPts(w.limits.dailyLoss)} used today. Lowering a limit applies at once; raising one waits 24 hours. Account {w.player.kycStatus}, points only, no real money.
      </p>
    </section>
  );
}

function Shell({ name, children }: { name?: string; children: React.ReactNode }) {
  return (
    <div className="pf">
      <header className="pf-top">
        <Link className="pf-back" href="/multiverse">
          <ArrowLeft size={16} />
          OverWatch
        </Link>
        <div className="pf-title">
          <h1>Portfolio</h1>
          {name && <span className="pf-sub">{name}</span>}
        </div>
      </header>
      <main className="pf-main">{children}</main>
    </div>
  );
}

export function Portfolio() {
  const state = usePortfolio();
  const rows = useMemo(() => (state.kind === "ready" ? betRows(state.data.history, state.data.positions, state.data.sources) : []), [state]);

  if (state.kind === "loading")
    return (
      <Shell>
        <p className="pf-empty pf-card">Loading your account…</p>
      </Shell>
    );
  if (state.kind === "no-player")
    return (
      <Shell>
        <div className="pf-card pf-blank">
          <h2>No player on this device yet, place a bet to start</h2>
          <p className="pf-sub">Your portfolio lives with the player this browser created. Place one bet on the game page and it appears here.</p>
          <Link className="pf-btn pf-btn-primary" href="/multiverse">
            <ArrowLeft size={14} />
            Back to OverWatch
          </Link>
        </div>
      </Shell>
    );
  if (state.kind === "offline")
    return (
      <Shell>
        <div className="pf-card pf-blank">
          <h2>The exchange is not reachable</h2>
          <p className="pf-sub">{state.error} The portfolio reads the live ledger; it is not available with demo data.</p>
          <Link className="pf-btn" href="/multiverse">
            <ArrowLeft size={14} />
            Back to OverWatch
          </Link>
        </div>
      </Shell>
    );

  const d = state.data;
  const capped = d.wallet.totals.betsSettled > d.history.length;
  return (
    <Shell name={`${d.wallet.player.nickname ?? defaultName(d.wallet.player.id)} · ${d.wallet.player.id} · since ${when(d.wallet.player.createdAt)}`}>
      <Summary data={d} />
      <section className="pf-card pf-span">
        <header className="pf-card-head">
          <h2>Equity curve</h2>
          <span className="pf-sub">Cumulative P&amp;L by settled bet</span>
        </header>
        <EquityChart series={d.series} />
      </section>
      <Positions positions={d.positions} markets={d.markets} />
      <section className="pf-card pf-span">
        <header className="pf-card-head">
          <h2>
            Bet history <span className="pf-count tl-num">{rows.length}</span>
          </h2>
          {capped && <span className="pf-sub">Latest {HISTORY_LIMIT} settled bets</span>}
        </header>
        <BetTable rows={rows} />
      </section>
      <Breakdown rows={rows} versus={d.versus} traders={d.traders} />
      <Activity entries={d.entries} />
      <Limits wallet={d.wallet} />
    </Shell>
  );
}
