"use client";

import { Check, Clock, Lock, Pause, X } from "lucide-react";
import { useState, type MouseEvent } from "react";
import { countdown, fmtPts, londonClock, type MyBet, type Settled } from "./city-store";
import type { Market, Trader } from "./city-types";

export type Phase = "open" | "halted" | "locked" | "awaiting" | "settled" | "void";

export function phaseOf(m: Market, now: number, result: Settled | undefined): Phase {
  if (result) return result.void || result.winner == null ? "void" : "settled";
  if (m.status === "settled" || m.status === "void") return "awaiting";
  if (now < m.closesAt && m.status === "halted") return "halted";
  if (now < m.closesAt && m.status !== "closed") return "open";
  if (now < m.settlesAt) return "locked";
  return "awaiting";
}

const BADGE: Record<Phase, string> = {
  open: "Open",
  halted: "Halted",
  locked: "Locked",
  awaiting: "Awaiting data",
  settled: "Settled",
  void: "Void",
};

const STAKES = [50, 100] as const;

export type TipTarget = { trader: string; why?: string; el: HTMLElement };

function Spark({ lines, lead }: { lines: number[][]; lead: number }) {
  const W = 120;
  const H = 34;
  const path = (pts: number[]) => {
    if (pts.length < 2) return "";
    const lo = 0;
    const hi = 1;
    return pts
      .map((p, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * W).toFixed(1)},${(H - 2 - ((p - lo) / (hi - lo)) * (H - 4)).toFixed(1)}`)
      .join(" ");
  };
  return (
    <svg className="tl-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
      {lines.map((pts, i) => (
        <path key={i} d={path(pts)} data-lead={i === lead || undefined} />
      ))}
    </svg>
  );
}

function sourceLine(m: Market): string {
  if (m.source) return m.source.replace(/\s*Europe\/London$/, "");
  return `TfL · settles ${londonClock(m.settlesAt, true)}`;
}

export function MarketCard({
  market: m,
  now,
  traders,
  bet,
  result,
  selected,
  draft,
  stake,
  points,
  history,
  onSelect,
  onPick,
  onStake,
  onConfirm,
  onCancel,
  onTip,
}: {
  market: Market;
  now: number;
  traders: Map<string, Trader>;
  bet: MyBet | undefined;
  result: Settled | undefined;
  selected: boolean;
  draft: string | null;
  stake: number | "all";
  points: number;
  history: Record<string, number[]>;
  onSelect: () => void;
  onPick: (option: string) => void;
  onStake: (s: number | "all") => void;
  onConfirm: () => void;
  onCancel: () => void;
  onTip: (t: TipTarget | null) => void;
}) {
  const [rules, setRules] = useState(false);
  const phase = phaseOf(m, now, result);
  const lead = m.options.reduce((best, o, i) => (o.prob > m.options[best].prob ? i : best), 0);
  const leader = m.options[lead];
  const canBet = phase === "open" && !bet;
  const stakeN = stake === "all" ? points : Math.min(stake, points);
  const drafted = m.options.find((o) => o.id === draft);
  const aiResult = new Map(result?.aiResults.map((a) => [a.trader, a.won]));

  const tip = (e: MouseEvent<HTMLElement>, trader: string, why: string) => {
    e.stopPropagation();
    onTip({ trader, why, el: e.currentTarget });
  };

  return (
    <article className="tl-market" data-phase={phase} data-selected={selected || undefined} onClick={onSelect}>
      <header className="tl-m-top">
        <span className="tl-badge" data-phase={phase}>
          {phase === "open" && <span className="tl-live-dot" />}
          {phase === "halted" && <Pause size={11} strokeWidth={3} />}
          {phase === "locked" && <Lock size={11} strokeWidth={3} />}
          {phase === "settled" && <Check size={11} strokeWidth={3} />}
          {BADGE[phase]}
        </span>
        <span className="tl-m-clock tl-num">
          {phase === "open" && (
            <>
              <Clock size={12} /> locks in {countdown(m.closesAt - now)}
            </>
          )}
          {phase === "halted" && "Data feed paused"}
          {phase === "locked" && <>Trading closed · waiting for London · {countdown(m.settlesAt - now)}</>}
          {phase === "awaiting" && "Waiting for London…"}
          {(phase === "settled" || phase === "void") && `settled ${londonClock(m.settlesAt)}`}
        </span>
      </header>

      <h3 className="tl-q">{m.question}</h3>

      {phase !== "settled" && phase !== "void" && (
        <div className="tl-m-lead">
          <span className="tl-big tl-num">{Math.round(leader.prob * 100)}%</span>
          <span className="tl-lead-label">
            {leader.label}
            <span className="tl-muted">market favourite</span>
          </span>
          <Spark lines={m.options.map((o) => history[o.id] ?? [o.prob])} lead={lead} />
        </div>
      )}

      {phase === "settled" && result && (
        <div className="tl-reading">
          <b>Settled</b> {result.reading}
        </div>
      )}
      {phase === "void" && (
        <div className="tl-reading tl-reading-void">
          <b>No data — refunded.</b> {result?.localVoid ? "The feed never reported." : result?.reading}
        </div>
      )}

      <div className="tl-opts" data-n={m.options.length}>
        {m.options.map((o) => {
          const backers = m.aiBets.filter((b) => b.option === o.id);
          const state =
            phase === "settled"
              ? o.id === result?.winner
                ? "won"
                : "lost"
              : draft === o.id || bet?.option === o.id
                ? "picked"
                : "idle";
          return (
            <button
              key={o.id}
              type="button"
              className="tl-opt"
              data-state={state}
              disabled={!canBet}
              onClick={(e) => {
                e.stopPropagation();
                onPick(o.id);
              }}
            >
              <span className="tl-opt-label">
                {state === "won" && <Check size={14} strokeWidth={3} />}
                {o.label}
              </span>
              <span className="tl-opt-odds tl-num">×{o.odds.toFixed(1)}</span>
              <span className="tl-opt-pct tl-num">{Math.round(o.prob * 100)}%</span>
              {(backers.length > 0 || bet?.option === o.id) && (
                <span className="tl-chips">
                  {bet?.option === o.id && <span className="tl-chip tl-chip-you">You · {fmtPts(bet.stake)}</span>}
                  {backers.map((b) => {
                    const t = traders.get(b.trader);
                    const r = aiResult.get(b.trader);
                    return (
                      <span
                        key={b.trader}
                        role="button"
                        tabIndex={0}
                        className="tl-chip"
                        data-result={r === undefined ? undefined : r ? "won" : "lost"}
                        onMouseEnter={(e) => tip(e, b.trader, b.why)}
                        onMouseLeave={() => onTip(null)}
                        onClick={(e) => tip(e, b.trader, b.why)}
                      >
                        <span className="tl-emoji">{t?.emoji ?? "🤖"}</span>
                        {t?.name ?? b.trader}
                        {r !== undefined && (r ? <Check size={11} strokeWidth={3} /> : <X size={11} strokeWidth={3} />)}
                      </span>
                    );
                  })}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {canBet && drafted && (
        <div className="tl-stake" onClick={(e) => e.stopPropagation()}>
          <div className="tl-stake-row">
            {STAKES.map((s) => (
              <button key={s} type="button" className="tl-stake-btn" data-on={stake === s || undefined} disabled={points < s} onClick={() => onStake(s)}>
                {s}
              </button>
            ))}
            <button type="button" className="tl-stake-btn tl-allin" data-on={stake === "all" || undefined} disabled={points <= 0} onClick={() => onStake("all")}>
              All in
            </button>
            <button type="button" className="tl-stake-x" aria-label="Cancel" onClick={onCancel}>
              <X size={16} />
            </button>
          </div>
          <button type="button" className="tl-confirm" disabled={stakeN <= 0} onClick={onConfirm}>
            Stake {fmtPts(stakeN)} on {drafted.label} → win {fmtPts(stakeN * drafted.odds)}
          </button>
        </div>
      )}

      {bet && (
        <div className="tl-mine" data-result={bet.done}>
          {!bet.done && (
            <>
              Your bet: <b>{fmtPts(bet.stake)}</b> on {bet.label} → wins <b>{fmtPts(bet.payout)}</b>
            </>
          )}
          {bet.done === "won" && <>You won <b>+{fmtPts(bet.payout)}</b></>}
          {bet.done === "lost" && <>You lost {fmtPts(bet.stake)}</>}
          {bet.done === "void" && <>Refunded {fmtPts(bet.stake)}</>}
        </div>
      )}

      <footer className="tl-src">
        <span className="tl-src-line">Settles on {sourceLine(m)}</span>
        <button
          type="button"
          className="tl-rules-btn"
          onClick={(e) => {
            e.stopPropagation();
            setRules((r) => !r);
          }}
          aria-expanded={rules}
        >
          Rules
        </button>
      </footer>
      {rules && (
        <p className="tl-rule">
          {m.rule ?? "Settles on the first official reading at the settle time. No reading means void and every stake is refunded."}{" "}
          Prices come from {m.futures} simulated futures and move with every bet.
        </p>
      )}
    </article>
  );
}
