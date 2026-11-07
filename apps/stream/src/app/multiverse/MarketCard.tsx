"use client";

import { Bus, Camera, Check, CloudRain, Shuffle, TrafficCone, TrainFront, X, type LucideIcon } from "lucide-react";
import { useState, type MouseEvent } from "react";
import { countdown, fmtPts, londonClock, type MyBet, type Settled } from "./city-store";
import type { Market, Trader } from "./city-types";
import { MarketClues, usualGhost } from "./Clues";

export type Phase = "open" | "halted" | "locked" | "awaiting" | "settled" | "void";

export function phaseOf(m: Market, now: number, result: Settled | undefined): Phase {
  if (result) return result.void || result.winner == null ? "void" : "settled";
  if (m.status === "settled" || m.status === "void") return "awaiting";
  if (now < m.closesAt && m.status === "halted") return "halted";
  if (now < m.closesAt && m.status !== "closed") return "open";
  if (now < m.settlesAt) return "locked";
  return "awaiting";
}

const STATUS: Record<Phase, string> = {
  open: "Open",
  halted: "Paused",
  locked: "Locked",
  awaiting: "Waiting for result",
  settled: "Settled",
  void: "Void",
};

const STAKES = [50, 100] as const;

export type TipTarget = { trader: string; why?: string; el: HTMLElement };

const TRADER_ICON: Record<string, LucideIcon> = {
  tube: TrainFront,
  cams: Camera,
  lines: TrafficCone,
  rain: CloudRain,
  bus: Bus,
  contrarian: Shuffle,
};
const TRADER_SLOT: Record<string, number> = { tube: 0, cams: 1, lines: 2, rain: 3, bus: 4, contrarian: 5 };

function slotOf(id: string): number {
  if (id in TRADER_SLOT) return TRADER_SLOT[id];
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 6;
}

function initials(name: string): string {
  const words = name.replace(/^the\s+/i, "").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

/** One colour and glyph per trader everywhere it appears; unknown ids fall back to a monogram. */
export function Badge({ id, name, kind = "ai", small }: { id: string; name: string; kind?: "ai" | "human" | "me"; small?: boolean }) {
  const Icon = kind === "ai" ? TRADER_ICON[id] : undefined;
  return (
    <span className="tl-av" data-c={kind === "ai" ? slotOf(id) : kind} data-small={small || undefined} aria-hidden>
      {Icon ? <Icon size={small ? 11 : 15} strokeWidth={2.25} /> : initials(name)}
    </span>
  );
}

function Spark({ lines, lead, ghost }: { lines: number[][]; lead: number; ghost: number[] | null }) {
  const W = 140;
  const H = 40;
  // Why: the usual curve looks 15 min ahead, so it continues past the price line instead of sharing its time axis.
  const PW = ghost ? W * 0.72 : W;
  const yOf = (p: number) => H - 2 - p * (H - 4);
  const path = (pts: number[]) => {
    if (pts.length < 2) return "";
    return pts.map((p, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * PW).toFixed(1)},${yOf(p).toFixed(1)}`).join(" ");
  };
  const leadPts = lines[lead] ?? [];
  const y0 = yOf(leadPts[leadPts.length - 1] ?? 0.5);
  const ghostPath =
    ghost && ghost.length > 1
      ? ghost
          .map((r, i) => {
            const x = PW + (i / (ghost.length - 1)) * (W - PW);
            const y = Math.max(2, Math.min(H - 2, y0 - (r - 1) * H * 2));
            return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
          })
          .join(" ")
      : "";
  return (
    <svg className="tl-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
      {lines.map((pts, i) => (
        <path key={i} d={path(pts)} data-lead={i === lead || undefined} />
      ))}
      {ghostPath && <path className="tl-spark-ghost" d={ghostPath} />}
    </svg>
  );
}

function sourceLine(m: Market): string {
  if (m.source) return m.source.replace(/\s*Europe\/London$/, "").replace(/\s+·\s+/g, ", ");
  return `TfL, settles ${londonClock(m.settlesAt, true)}`;
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

  const n = m.options.length;
  const sideOf = (i: number) => (n === 2 && i === 1 ? "no" : "yes");
  const draftedSide = sideOf(m.options.findIndex((o) => o.id === draft));

  return (
    <article className="tl-market" data-phase={phase} data-selected={selected || undefined} onClick={onSelect}>
      <header className="tl-m-top">
        <span className="tl-status" data-phase={phase}>
          <span className="tl-status-dot" />
          {STATUS[phase]}
        </span>
        <span className="tl-m-clock">
          {phase === "open" && (
            <>
              Closes in <span className="tl-data">{countdown(m.closesAt - now)}</span>
            </>
          )}
          {phase === "locked" && (
            <>
              Settles in <span className="tl-data">{countdown(m.settlesAt - now)}</span>
            </>
          )}
          {(phase === "settled" || phase === "void") && <span className="tl-data">{londonClock(m.settlesAt)}</span>}
        </span>
      </header>

      <h3 className="tl-q">{m.question}</h3>

      <MarketClues signals={m.signals} />

      {phase !== "settled" && phase !== "void" && (
        <div className="tl-m-lead">
          <span className="tl-big tl-num">{Math.round(leader.prob * 100)}%</span>
          <span className="tl-lead-label">{leader.label}</span>
          <Spark lines={m.options.map((o) => history[o.id] ?? [o.prob])} lead={lead} ghost={usualGhost(m.signals)} />
        </div>
      )}

      {phase === "settled" && result && <p className="tl-reading">{result.reading}</p>}
      {phase === "void" && (
        <p className="tl-reading" data-void>
          No data, every stake refunded. {result?.localVoid ? "The feed never reported." : result?.reading}
        </p>
      )}

      <div className="tl-opts" data-n={n}>
        {m.options.map((o, i) => {
          const backers = m.aiBets.filter((b) => b.option === o.id);
          const mine = bet?.option === o.id;
          const state =
            phase === "settled"
              ? o.id === result?.winner
                ? "won"
                : "lost"
              : draft === o.id || mine
                ? "picked"
                : "idle";
          return (
            <div key={o.id} className="tl-optcol" data-state={state}>
              {n !== 2 && (
                <>
                  <span className="tl-opt-name">
                    {state === "won" && <Check size={14} strokeWidth={3} />}
                    {o.label}
                  </span>
                  <span className="tl-opt-pct tl-data">{Math.round(o.prob * 100)}%</span>
                </>
              )}
              <button
                type="button"
                className="tl-opt"
                data-state={state}
                data-side={sideOf(i)}
                disabled={!canBet}
                onClick={(e) => {
                  e.stopPropagation();
                  onPick(o.id);
                }}
              >
                <span className="tl-opt-label">
                  {n === 2 && state === "won" && <Check size={14} strokeWidth={3} />}
                  {n === 2 ? o.label : "Buy"}
                </span>
                <span className="tl-opt-odds tl-data">×{o.odds.toFixed(1)}</span>
              </button>
              {(backers.length > 0 || mine) && (
                <span className="tl-backers">
                  {mine && bet && (
                    <span className="tl-backer tl-backer-you">
                      You <span className="tl-data">{fmtPts(bet.stake)}</span>
                    </span>
                  )}
                  {backers.map((b) => {
                    const t = traders.get(b.trader);
                    const r = aiResult.get(b.trader);
                    return (
                      <span
                        key={b.trader}
                        role="button"
                        tabIndex={0}
                        className="tl-backer"
                        data-result={r === undefined ? undefined : r ? "won" : "lost"}
                        onMouseEnter={(e) => tip(e, b.trader, b.why)}
                        onMouseLeave={() => onTip(null)}
                        onClick={(e) => tip(e, b.trader, b.why)}
                      >
                        <Badge id={b.trader} name={t?.name ?? b.trader} small />
                        {t?.name ?? b.trader}
                      </span>
                    );
                  })}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {canBet && drafted && (
        <div className="tl-stake" onClick={(e) => e.stopPropagation()}>
          <div className="tl-stake-row">
            <div className="tl-seg" role="group" aria-label="Stake">
              {STAKES.map((s) => (
                <button key={s} type="button" className="tl-stake-btn tl-data" data-on={stake === s || undefined} disabled={points < s} onClick={() => onStake(s)}>
                  {s}
                </button>
              ))}
              <button type="button" className="tl-stake-btn" data-on={stake === "all" || undefined} disabled={points <= 0} onClick={() => onStake("all")}>
                All in
              </button>
            </div>
            <button type="button" className="tl-stake-x" aria-label="Cancel" onClick={onCancel}>
              <X size={16} />
            </button>
          </div>
          <button type="button" className="tl-confirm" data-side={draftedSide} disabled={stakeN <= 0} onClick={onConfirm}>
            Stake {fmtPts(stakeN)} on {drafted.label}
            <span className="tl-confirm-win">
              to win <span className="tl-data">{fmtPts(stakeN * drafted.odds)}</span>
            </span>
          </button>
        </div>
      )}

      {bet && (
        <p className="tl-mine" data-result={bet.done}>
          {!bet.done && (
            <>
              Your bet: <b className="tl-data">{fmtPts(bet.stake)}</b> on {bet.label}, pays <b className="tl-data">{fmtPts(bet.payout)}</b>
            </>
          )}
          {bet.done === "won" && (
            <>
              You won <b className="tl-data">+{fmtPts(bet.payout)}</b>
            </>
          )}
          {bet.done === "lost" && <>You lost {fmtPts(bet.stake)}</>}
          {bet.done === "void" && <>Refunded {fmtPts(bet.stake)}</>}
        </p>
      )}

      <footer className="tl-src">
        <span className="tl-src-line">{sourceLine(m)}</span>
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
