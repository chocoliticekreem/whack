"use client";

import { ChevronDown, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { countdown, fmtW, londonClock, type MyBet, type Settled } from "./city-store";
import type { Feed, Market, Trader } from "./city-types";
import { MiniSpark, Tick, signedW, toneOf, useCountUp, useDismiss } from "./Live";
import { Badge, type TipTarget } from "./MarketCard";
import { CoinText } from "./Coin";
import { plainLabel, plainTitle, plainWatches, recordText, resultEta, seriesOf } from "./plain";

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
  // TOPS loop: most trusted first, points break ties.
  const ranked = useMemo(() => [...traders].sort((a, b) => (b.trust ?? 0) - (a.trust ?? 0) || b.points - a.points), [traders]);
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
            <p className="ow-drop-head ow-trust-head">The market leans on traders who&apos;ve been right.</p>
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
                      <span className="ow-trust">
                        <span className="ow-trust-bar" aria-hidden>
                          <i style={{ width: `${Math.round((t.trust ?? 0) * 100)}%` }} />
                        </span>
                        <span className="tl-data">{Math.round((t.trust ?? 0) * 100)}% trust</span>
                      </span>
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
  option: string;
  label: string;
  stake: number;
  toWin: number;
  placedAt: number;
  settlesAt: number;
}

interface HistoryRow {
  betId: string;
  market: string;
  question: string;
  option: string;
  label: string;
  stake: number;
  pnl: number;
  result: "won" | "lost" | "void";
  placedAt: number;
  settledAt: number;
}

/** A bet the exchange is still holding back (bet delay), from GET /positions' `pending`. */
interface PendingOrder {
  betId: string;
  market: string;
  option: string;
  label: string;
  stake: number;
  placedAt: number;
  fillsAt: number;
}

type OrderState =
  | { kind: "pending"; fillsAt: number }
  | { kind: "open"; settlesAt: number }
  | { kind: "won" | "lost" | "void"; pnl: number }
  | { kind: "cancelled" };

interface Order {
  key: string;
  market: string;
  question: string;
  pick: string;
  stake: number;
  payout: number | null;
  at: number;
  state: OrderState;
}

/** A pending bet this tab saw vanish without filling: the exchange cancelled it. */
interface Cancelled {
  betId: string;
  market: string;
  option: string;
  label: string;
  stake: number;
  at: number;
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
  listed,
  onOpenMarket,
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
  listed: Set<string>;
  onOpenMarket: (market: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"session" | "orders">("session");
  const [prevBets, setPrevBets] = useState(bets);
  const [cancelled, setCancelled] = useState<Cancelled[]>([]);
  if (prevBets !== bets) {
    setPrevBets(bets);
    const gone: Cancelled[] = [];
    for (const b of Object.values(prevBets)) {
      const was = b.pending?.betId;
      if (was && !bets[b.market]) gone.push({ betId: was, market: b.market, option: b.option, label: b.label, stake: b.stake, at: now });
    }
    if (gone.length) setCancelled([...gone, ...cancelled].slice(0, 40));
  }
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, () => setOpen(false), [btn, panel]);
  const [series, setSeries] = useState<number[]>([pnl]);
  if (series[series.length - 1] !== pnl) setSeries([...series, pnl].slice(-80));
  const shownPnl = useCountUp(pnl, 600);

  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [serverPending, setServerPending] = useState<PendingOrder[]>([]);
  useEffect(() => {
    if (!open || !live || !token) return;
    let alive = true;
    const q = `player=${encodeURIComponent(player)}`;
    const load = async () => {
      const [p, h] = await Promise.all([
        getJson<{ positions: Position[]; pending?: PendingOrder[] }>(`positions?${q}`, token),
        getJson<{ history: HistoryRow[] }>(`history?${q}&limit=200`, token),
      ]);
      if (!alive) return;
      if (p) {
        setPositions(p.positions);
        setServerPending(p.pending ?? []);
      }
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

  const orders = useMemo(() => {
    const out: Order[] = [];
    const ids = new Set<string>();
    const onServer = new Set<string>();
    const named = (market: string, option: string, label: string, question: string) => {
      const m = markets[market];
      return { question: m ? plainTitle(m) : question, pick: m ? plainLabel(m, option) : label };
    };
    if (live) {
      for (const h of history) {
        ids.add(h.betId);
        onServer.add(h.market);
        out.push({
          key: h.betId,
          market: h.market,
          ...named(h.market, h.option, h.label, h.question),
          stake: h.stake,
          payout: h.result === "won" ? h.stake + h.pnl : null,
          at: h.placedAt,
          state: { kind: h.result, pnl: h.pnl },
        });
      }
      for (const p of positions) {
        if (ids.has(p.betId)) continue;
        ids.add(p.betId);
        onServer.add(p.market);
        out.push({
          key: p.betId,
          market: p.market,
          ...named(p.market, p.option, p.label, p.question),
          stake: p.stake,
          payout: p.toWin,
          at: p.placedAt,
          state: { kind: "open", settlesAt: p.settlesAt },
        });
      }
      for (const p of serverPending) {
        if (ids.has(p.betId)) continue;
        ids.add(p.betId);
        out.push({
          key: p.betId,
          market: p.market,
          ...named(p.market, p.option, p.label, ""),
          stake: p.stake,
          payout: null,
          at: p.placedAt,
          state: { kind: "pending", fillsAt: p.fillsAt },
        });
      }
    }
    // Why: a bet placed seconds ago is not on the server's lists yet; the local wallet shows it at once, on top.
    const local = Object.values(bets);
    local.forEach((b, i) => {
      if (b.pending ? ids.has(b.pending.betId) : onServer.has(b.market)) return;
      const m = markets[b.market];
      const state: OrderState = b.pending
        ? { kind: "pending", fillsAt: b.pending.fillsAt }
        : b.done
          ? { kind: b.done, pnl: b.done === "won" ? b.payout - b.stake : b.done === "lost" ? -b.stake : 0 }
          : { kind: "open", settlesAt: m?.settlesAt ?? 0 };
      out.push({
        key: b.pending?.betId ?? `local-${b.market}`,
        market: b.market,
        ...named(b.market, b.option, b.label, ""),
        stake: b.stake,
        payout: b.pending ? null : b.payout,
        at: Number.MAX_SAFE_INTEGER - local.length + i,
        state,
      });
    });
    for (const c of cancelled) {
      if (ids.has(c.betId)) continue;
      out.push({
        key: `x-${c.betId}`,
        market: c.market,
        ...named(c.market, c.option, c.label, ""),
        stake: c.stake,
        payout: null,
        at: c.at,
        state: { kind: "cancelled" },
      });
    }
    return out.sort((a, b) => b.at - a.at);
  }, [live, history, positions, serverPending, bets, markets, cancelled]);

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
            <div className="ow-tabs" role="tablist">
              <button type="button" role="tab" aria-selected={tab === "session"} onClick={() => setTab("session")}>
                Session
              </button>
              <button type="button" role="tab" aria-selected={tab === "orders"} onClick={() => setTab("orders")}>
                Orders <span className="tl-data">{orders.length}</span>
              </button>
            </div>
            {tab === "orders" ? (
              <OrderLog
                orders={orders}
                markets={markets}
                now={now}
                listed={listed}
                onOpen={(id) => {
                  setOpen(false);
                  onOpenMarket(id);
                }}
              />
            ) : (
              <>
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
              </>
            )}
          </div>
        </>
      )}
    </>
  );
}

const shortWait = (ms: number) => {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return s < 60 ? `${s}s` : countdown(ms);
};

function orderStatus(o: Order, m: Market | undefined, now: number): { text: string; tone?: "up" | "down"; wait?: boolean; muted?: boolean } {
  const st = o.state;
  if (st.kind === "pending") return { text: st.fillsAt > now ? `Filling in ${shortWait(st.fillsAt - now)}` : "Filling…", wait: true };
  if (st.kind === "cancelled") return { text: "Cancelled", muted: true };
  if (st.kind === "void") return { text: "Refunded", muted: true };
  if (st.kind === "won") return { text: `Won ${signedW(st.pnl)}`, tone: "up" };
  if (st.kind === "lost") return { text: `Lost ${signedW(st.pnl)}`, tone: "down" };
  const eta = m ? resultEta(m) : null;
  const at = eta?.slow ? eta.at : (m?.settlesAt ?? (st.kind === "open" ? st.settlesAt : 0));
  if (at > now) return { text: `Result in ${shortWait(at - now)}` };
  return { text: eta?.slow ? "Waiting for TfL…" : "Settling…" };
}

/** Every order this player made, newest first, each with a status that ticks with `now`. */
function OrderLog({
  orders,
  markets,
  now,
  listed,
  onOpen,
}: {
  orders: Order[];
  markets: Record<string, Market>;
  now: number;
  listed: Set<string>;
  onOpen: (market: string) => void;
}) {
  if (orders.length === 0) return <p className="ow-empty ow-orders-empty">No orders yet.</p>;
  return (
    <ol className="ow-orders">
      {orders.map((o) => {
        const s = orderStatus(o, markets[o.market], now);
        const body = (
          <>
            <span className="ow-order-q">{o.question || o.pick}</span>
            <span className="ow-order-st tl-data" data-tone={s.tone} data-wait={s.wait || undefined} data-muted={s.muted || undefined}>
              <CoinText text={s.text} size={12} />
            </span>
            <span className="ow-order-pick">{o.pick}</span>
            <span className="ow-order-amt tl-data">
              <CoinText text={`${fmtW(o.stake)} → ${o.payout == null ? "…" : fmtW(o.payout)}`} size={12} />
            </span>
          </>
        );
        return (
          <li key={o.key}>
            {listed.has(o.market) ? (
              <button type="button" className="ow-order" onClick={() => onOpen(o.market)}>
                {body}
              </button>
            ) : (
              <div className="ow-order">{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

const DOCS_URL = "https://github.com/chocoliticekreem/whack/blob/main/docs/pricing-engine.md";
const DASH = "—";

const ago = (s: number) => (s < 90 ? `${s}s` : `${Math.round(s / 60)} min`);

const fmtLevel = (v: number) => v.toFixed(Math.abs(v) < 10 ? 2 : 1);

/** The current round's City Pulse market: the one that closes last. */
const pulseOf = (markets: Market[]) =>
  markets.filter((m) => seriesOf(m) === "city_pulse").sort((a, b) => b.closesAt - a.closesAt)[0];

function feedFreshness(feeds: Record<string, Feed> | undefined): string {
  const list = Object.values(feeds ?? {});
  if (!list.length) return DASH;
  const ages = list.map((f) => f.ageS).filter((a): a is number => a != null);
  const newest = ages.length ? ` · newest ${ago(Math.min(...ages))} ago` : "";
  return `${list.filter((f) => f.ok).length}/${list.length} feeds fresh${newest}`;
}

/** Where the surviving City Pulse futures land at the settle time, and the line they are judged against. */
function pulseSpan(markets: Market[]): string {
  const f = pulseOf(markets)?.futures;
  if (!f) return DASH;
  const ends = f.paths.filter((p, i) => f.alivePaths[i] && p.length).map((p) => p[p.length - 1][1]);
  const line = f.lines.length === 1 ? ` · line ${fmtLevel(f.lines[0])}` : "";
  if (!ends.length) return `futures ${DASH}${line}`;
  return `futures span ${fmtLevel(Math.min(...ends))}–${fmtLevel(Math.max(...ends))}${line}`;
}

function nextResult(markets: Market[], now: number): string {
  const due = markets
    .filter((m) => m.status !== "settled" && m.status !== "void")
    .map((m) => resultEta(m).at)
    .filter((at) => at > now);
  if (!due.length) return DASH;
  const at = Math.min(...due);
  return `next result ${londonClock(at)} · in ${countdown(at - now)}`;
}

function trustLean(markets: Market[], traders: Trader[]): string {
  const top = [...traders].sort((a, b) => (b.trust ?? -1) - (a.trust ?? -1))[0];
  const lead = top?.trust != null ? `${top.name} leads, ${Math.round(top.trust * 100)}% trust` : DASH;
  const lambda = pulseOf(markets)?.tops?.lambda;
  return lambda == null ? lead : `${lead} · market leans ${Math.round(lambda * 100)}% on traders`;
}

/** The header's "Under the hood" panel: the pricing pipeline in five plain steps, each with one live number. */
export function UnderTheHood({
  open,
  onOpenChange,
  feeds,
  markets,
  openCount,
  traders,
  now,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  feeds: Record<string, Feed> | undefined;
  markets: Market[];
  openCount: number;
  traders: Trader[];
  now: number;
}) {
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, () => onOpenChange(false), [btn, panel]);
  const steps = [
    {
      name: "Live data",
      text: "We read London as it happens: TfL crowding, line status, buses, JamCams, rain and flights.",
      live: feedFreshness(feeds),
      quant: "A market halts when its feed goes stale (3 min; 6 min for crowding) or jumps 3σ.",
    },
    {
      name: "50 futures",
      text: "We play out 50 possible next-five-minutes for London.",
      live: pulseSpan(markets),
      quant: "Particle filter: each reading reweights the paths; price = weighted share alive, held in 3–97%.",
    },
    {
      name: "Market maker",
      text: "An automatic market maker turns those odds into prices; it can never lose more than a fixed amount.",
      live: `${openCount} markets open · 5% fee`,
      quant: "LMSR, b = 1200, futures as the base; worst case b·ln n per market (W832 on two options).",
    },
    {
      name: "Fair results",
      text: "Results use only data TfL stamped after betting closed.",
      live: nextResult(markets, now),
      quant: "Settles on the source's own timestamp, not our poll time; 2–5 s bet delay, pause on a feed jump.",
    },
    {
      name: "Track record",
      text: "Computer traders who've been right move the opening odds a little.",
      live: trustLean(markets, traders),
      quant: "TOPS: trust ∝ P(θ > 0); λ = Σw·r / Σw·r², shrunk n/(n + 10), capped at 0.3.",
    },
  ];
  return (
    <>
      <button
        ref={btn}
        type="button"
        className="ow-menu-btn ow-uth-btn tl-wide"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
      >
        <Workflow size={14} aria-hidden />
        <span className="tl-desk">Under the hood</span>
        <ChevronDown size={14} className="ow-chev" aria-hidden />
      </button>
      {open && (
        <>
          <div className="ow-sheet-scrim" aria-hidden />
          <div ref={panel} className="ow-drop ow-uth" role="dialog" aria-label="Under the hood">
            <p className="ow-drop-head">
              <b>Under the hood.</b> Five steps from live London data to the price on each card.
            </p>
            <ol className="ow-uth-steps">
              {steps.map((s, i) => (
                <li key={s.name}>
                  <span className="ow-uth-n tl-data" aria-hidden>
                    {i + 1}
                  </span>
                  <div className="ow-uth-body">
                    <h4>{s.name}</h4>
                    <p>{s.text}</p>
                    <b className="ow-uth-live tl-data" data-empty={s.live === DASH || undefined}>
                      {s.live}
                    </b>
                    <span className="ow-uth-quant">{s.quant}</span>
                  </div>
                </li>
              ))}
            </ol>
            <p className="ow-uth-foot">
              Every model must beat a coin flip on data it wasn&apos;t fitted to.{" "}
              <a href={DOCS_URL} target="_blank" rel="noreferrer">
                Read the method
              </a>
            </p>
          </div>
        </>
      )}
    </>
  );
}
