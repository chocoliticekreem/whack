"use client";

import { useState } from "react";
import { countdown, fmtW, londonClock, type MyBet } from "./city-store";
import type { Market } from "./city-types";
import { plainLabel, plainTitle, resultEta } from "./plain";

/** How long a settled bet stays on the right before it moves to past orders (Orders tab). */
const RESULT_SHOW_MS = 10_000;

function status(b: MyBet, m: Market, now: number): { text: string; tone?: "up" | "down" | "wait" } {
  if (b.pending) {
    const s = Math.ceil((b.pending.fillsAt - now) / 1000);
    return { text: s > 0 ? `Filling in ${s}s` : "Filling at the next TfL check…", tone: "wait" };
  }
  if (b.done === "won") return { text: `Won +${fmtW(b.payout - b.stake)}`, tone: "up" };
  if (b.done === "lost") return { text: `Lost −${fmtW(b.stake)}`, tone: "down" };
  if (b.done === "void") return { text: `Refunded ${fmtW(b.stake)}` };
  if (now < m.closesAt) return { text: `Betting open · closes in ${countdown(m.closesAt - now)}` };
  const eta = resultEta(m).at;
  return { text: now < eta ? `Result in ${countdown(eta - now)} · about ${londonClock(eta)}` : "Result any moment…", tone: "wait" };
}

/** The player's bets on the map's right edge: every bet waiting on a result, soonest result first. A settled bet shows
 * its result for 10 s, then leaves for past orders, so the left feed stays for fresh bets and nothing goes stale here. */
export function MyBets({
  bets,
  markets,
  now,
  onOpen,
}: {
  bets: Record<string, MyBet>;
  markets: Record<string, Market>;
  now: number;
  onOpen: (market: string) => void;
}) {
  // Why: bets already settled when the page opened are past orders; only results that land while watching get 10 s.
  const [doneAt, setDoneAt] = useState<Record<string, number>>(() =>
    Object.fromEntries(Object.values(bets).filter((b) => b.done).map((b) => [b.market, 0])),
  );
  const fresh = Object.values(bets).filter((b) => b.done && doneAt[b.market] === undefined);
  if (fresh.length) setDoneAt({ ...doneAt, ...Object.fromEntries(fresh.map((b) => [b.market, now])) });

  const rows = Object.values(bets)
    .filter((b) => markets[b.market] && (!b.done || now - (doneAt[b.market] ?? now) < RESULT_SHOW_MS))
    .sort((a, b) => Number(!a.done) - Number(!b.done) || resultEta(markets[a.market]).at - resultEta(markets[b.market]).at);
  if (!rows.length) return null;
  return (
    <aside className="tl-mybets" aria-label="Your bets" onClick={(e) => e.stopPropagation()}>
      <h2>Your bets</h2>
      <ol>
        {rows.map((b) => {
          const m = markets[b.market];
          const s = status(b, m, now);
          return (
            <li key={b.market} data-won={b.done === "won" || undefined} data-lost={b.done === "lost" || undefined}>
              <button type="button" onClick={() => onOpen(b.market)}>
                <span className="tl-mybets-q">{plainTitle(m)}</span>
                <span className="tl-mybets-pick">
                  <b>{plainLabel(m, b.option)}</b> · {fmtW(b.stake)}
                  {!b.pending && !b.done && <span className="tl-muted"> to win {fmtW(b.payout)}</span>}
                </span>
                <span className="tl-mybets-state tl-num" data-tone={s.tone}>
                  {s.text}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
