"use client";

import { Bus, Camera, Check, CloudRain, Shuffle, TrafficCone, TrainFront, X, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { countdown, fmtPts, londonClock, type MyBet, type Settled } from "./city-store";
import type { Market, Trader } from "./city-types";
import { MarketClues, usualGhost } from "./Clues";
import { FuturesFan } from "./FuturesFan";
import { WhatIfLine, type WhatIfOdds } from "./Scenario";
import { pausedText, plainLabel, plainResult, plainTitle } from "./plain";

export type Phase = "open" | "halted" | "locked" | "awaiting" | "settled" | "void";

export function phaseOf(m: Market, now: number, result: Settled | undefined): Phase {
  if (result) return result.void || result.winner == null ? "void" : "settled";
  if (m.status === "settled" || m.status === "void") return "awaiting";
  if (now < m.closesAt && m.status === "halted") return "halted";
  if (now < m.closesAt && m.status !== "closed") return "open";
  if (now < m.settlesAt) return "locked";
  return "awaiting";
}

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

/** The reference stake every button prices its payout at, so the buttons compare at a glance. */
const REF_STAKE = 100;

function clockText(phase: Phase, m: Market, now: number): string {
  if (phase === "halted") return `Paused · betting ends ${londonClock(m.closesAt)}`;
  if (phase === "locked") return `Betting closed · result in ${countdown(m.settlesAt - now)}`;
  if (phase === "awaiting") return "Waiting for the result";
  return `Ended ${londonClock(m.settlesAt)}`;
}

function MyLine({ m, bet, now }: { m: Market; bet: MyBet; now: number }) {
  if (bet.done === "won") {
    return (
      <p className="tl-mine" data-result="won">
        You won <b className="tl-data">{fmtPts(bet.payout)}</b>
      </p>
    );
  }
  if (bet.done === "lost") {
    return (
      <p className="tl-mine" data-result="lost">
        You lost <span className="tl-data">{fmtPts(bet.stake)}</span>
      </p>
    );
  }
  if (bet.done === "void") {
    return (
      <p className="tl-mine" data-result="void">
        Refunded <span className="tl-data">{fmtPts(bet.stake)}</span>
      </p>
    );
  }
  const left = bet.pending ? Math.ceil((bet.pending.fillsAt - now) / 1000) : 0;
  return (
    <p className="tl-mine" data-pending={bet.pending ? true : undefined}>
      You: <b className="tl-data">{fmtPts(bet.stake)}</b> on {plainLabel(m, bet.option)}
      <span className="tl-mine-sub">
        {bet.pending ? (
          left > 0 ? (
            <>
              {" "}
              · pending, fills in <span className="tl-data">{left}s</span>
            </>
          ) : (
            " · filling…"
          )
        ) : (
          <>
            {" "}
            · locked in to win <span className="tl-data">{fmtPts(bet.payout)}</span>
          </>
        )}
      </span>
    </p>
  );
}

function Details({
  m,
  phase,
  result,
  traders,
  history,
  onClose,
}: {
  m: Market;
  phase: Phase;
  result: Settled | undefined;
  traders: Map<string, Trader>;
  history: Record<string, number[]>;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const fut = m.futures?.paths.length ? m.futures : undefined;
  const lead = m.options.reduce((best, o, i) => (o.prob > m.options[best].prob ? i : best), 0);
  const aiResult = new Map(result?.aiResults.map((a) => [a.trader, a.won]));
  const ig = m.integrity;
  // Why: the exchange appends its fair-play text to the rule; it gets its own section here.
  const rule = (m.rule ?? "Settles on the first official reading at the settle time. No reading means void and every stake is refunded.").split(
    / Bets fill \d/,
  )[0];
  const ended = phase === "settled" || phase === "void";
  return (
    <div
      className="tl-modal-bg tl-sheet-bg"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div className="tl-modal tl-sheet" role="dialog" aria-modal aria-label="Market details" onClick={(e) => e.stopPropagation()}>
        <div className="tl-board-head">
          <h2>{plainTitle(m)}</h2>
          <button type="button" className="tl-stake-x" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <p className="tl-d-exact">{m.question}</p>
        {result && <p className="tl-d-reading">Result: {result.reading}</p>}

        <section className="tl-d-sec">
          <h3>{ended ? "Final chances" : "Chances now"}</h3>
          <ul className="tl-d-opts">
            {m.options.map((o) => {
              const plain = plainLabel(m, o.id);
              return (
                <li key={o.id}>
                  <span className="tl-d-opt-name">
                    <b>{plain}</b>
                    {plain !== o.label && <span className="tl-muted"> ({o.label})</span>}
                    {fut && (
                      <span className="tl-d-count">
                        {fut.byOption[o.id] ?? 0} of {fut.n} simulated futures
                      </span>
                    )}
                  </span>
                  <span className="tl-data">{Math.round(o.prob * 100)}%</span>
                  <span className="tl-data tl-muted">×{o.odds.toFixed(1)}</span>
                </li>
              );
            })}
          </ul>
        </section>

        <section className="tl-d-sec">
          <h3>How it is decided</h3>
          <p>{rule}</p>
          <p className="tl-muted">Source: {sourceLine(m)}</p>
        </section>

        {fut && phase !== "void" && (
          <section className="tl-d-sec">
            <h3>Simulated futures</h3>
            <FuturesFan f={fut} settled={phase === "settled"} overUnder={m.kind === "overunder"} />
            <p className="tl-muted">Prices come from these futures and move with every bet.</p>
          </section>
        )}

        {(m.signals ?? []).length > 0 && (
          <section className="tl-d-sec">
            <h3>Clues</h3>
            <MarketClues signals={m.signals} />
          </section>
        )}

        {m.aiBets.length > 0 && (
          <section className="tl-d-sec">
            <h3>AI traders</h3>
            <ul className="tl-d-ai">
              {m.aiBets.map((b) => {
                const t = traders.get(b.trader);
                const r = aiResult.get(b.trader);
                return (
                  <li key={b.trader} data-result={r == null ? undefined : r ? "won" : "lost"}>
                    <Badge id={b.trader} name={t?.name ?? b.trader} small />
                    <span>
                      <b>{t?.name ?? b.trader}</b> backs {plainLabel(m, b.option)}
                      {r === true && " · won"}
                      {r === false && " · lost"}
                      <span className="tl-d-why">{b.why}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        <section className="tl-d-sec">
          <h3>Price since open</h3>
          <div className="tl-rule-price">
            <span>
              {plainLabel(m, m.options[lead].id)} <span className="tl-data">{Math.round(m.options[lead].prob * 100)}%</span>
            </span>
            <Spark lines={m.options.map((o) => history[o.id] ?? [o.prob])} lead={lead} ghost={usualGhost(m.signals)} />
          </div>
        </section>

        {ig && (
          <section className="tl-d-sec">
            <h3>Fair play</h3>
            <p className="tl-muted">
              Bets fill {ig.delayS} s after you place them, at the price then; if the market pauses or closes first the bet is
              cancelled and your stake is untouched.
              {ig.suspendsOn.length > 0 && ` The market pauses for ${ig.coolOffS} s whenever ${ig.suspendsOn.join(" or ")}.`} Max{" "}
              {ig.capPct}% of your points in this market.
            </p>
          </section>
        )}
      </div>
    </div>
  );
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
  whatIf,
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
  /** Kept for City's mount; the AI traders' reasons now live in the details sheet. */
  onTip: (t: TipTarget | null) => void;
  /** Scenario mode: what-if prices beside the live ones. */
  whatIf?: WhatIfOdds;
}) {
  const [sheet, setSheet] = useState<HTMLElement | null>(null);
  const phase = phaseOf(m, now, result);
  const canBet = phase === "open" && !bet;
  const stakeN = stake === "all" ? points : Math.min(stake, points);
  const drafted = m.options.find((o) => o.id === draft);
  const n = m.options.length;
  const sideOf = (i: number) => (n === 2 && i === 1 ? "no" : "yes");
  const draftedSide = sideOf(m.options.findIndex((o) => o.id === draft));
  const ended = phase === "settled" || phase === "void";

  return (
    <article className="tl-market" data-phase={phase} data-selected={selected || undefined} onClick={onSelect}>
      <header className="tl-m-top">
        <span className="tl-m-clock" data-phase={phase}>
          {phase === "open" ? (
            <>
              <span className="tl-data">{countdown(m.closesAt - now)}</span> left to bet
            </>
          ) : (
            clockText(phase, m, now)
          )}
        </span>
        <button
          type="button"
          className="tl-rules-btn"
          onClick={(e) => {
            e.stopPropagation();
            setSheet(e.currentTarget.closest<HTMLElement>(".tl"));
          }}
        >
          Details
        </button>
      </header>

      <h3 className="tl-q">{plainTitle(m)}</h3>

      {phase === "halted" && <p className="tl-paused">{pausedText(m.haltReason, now)}</p>}

      {whatIf && <WhatIfLine odds={whatIf} />}

      {ended ? (
        <p className="tl-outcome" data-void={phase === "void" || undefined}>
          {phase === "settled" && result ? plainResult(m, result) : "No result: every stake refunded"}
        </p>
      ) : (
        <div className="tl-opts" data-n={n}>
          {m.options.map((o, i) => {
            const picked = draft === o.id || bet?.option === o.id;
            return (
              <button
                key={o.id}
                type="button"
                className="tl-opt"
                data-state={picked ? "picked" : "idle"}
                data-side={sideOf(i)}
                disabled={!canBet}
                onClick={(e) => {
                  e.stopPropagation();
                  onPick(o.id);
                }}
              >
                <span className="tl-opt-label">
                  {picked && <Check size={15} strokeWidth={3} aria-label="Your pick" />}
                  {plainLabel(m, o.id)}
                </span>
                <span className="tl-opt-meta">
                  <span className="tl-opt-pct">
                    <span className="tl-data">{Math.round(o.prob * 100)}%</span> chance
                  </span>
                  <span className="tl-opt-pay">
                    Bet <span className="tl-data">{REF_STAKE}</span> pts, get <span className="tl-data">{fmtPts(REF_STAKE * o.odds)}</span> back
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {canBet && !drafted && <p className="tl-hint">Tap an answer, choose your points, then confirm.</p>}

      {canBet && drafted && (
        <div className="tl-stake" onClick={(e) => e.stopPropagation()}>
          <div className="tl-stake-row">
            <div className="tl-seg" role="group" aria-label="Points to bet">
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
            Bet {fmtPts(stakeN)} on {plainLabel(m, drafted.id)}
            <span className="tl-confirm-win">
              get <span className="tl-data">{fmtPts(stakeN * drafted.odds)}</span> back if right
            </span>
          </button>
          {m.integrity && m.integrity.delayS > 0 && (
            <span className="tl-muted tl-stake-note">
              Fills {m.integrity.delayS} s after you confirm, at the price then
            </span>
          )}
        </div>
      )}

      {bet && <MyLine m={m} bet={bet} now={now} />}

      {sheet &&
        createPortal(
          <Details m={m} phase={phase} result={result} traders={traders} history={history} onClose={() => setSheet(null)} />,
          sheet,
        )}
    </article>
  );
}
