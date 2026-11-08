"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtPts, londonClock } from "../city-store";

/** Shapes served by services/city/venue_server.py (proxied through /api/venue/*). */
type Level = [number, number];
interface Depth {
  bids: Level[];
  asks: Level[];
  last: number | null;
}
interface Contract {
  id: string;
  market: string;
  series: string;
  question: string;
  yes: string;
  no: string;
  closesAt: number;
  settlesAt: number;
  source: string;
  rule: string;
  status: string;
  forecast: number;
  bid: number | null;
  ask: number | null;
  last: number | null;
  depth: Depth;
  outcome: string | null;
  fast?: boolean;
}
interface Print {
  contract: string;
  yesPrice: number;
  qty: number;
  at: number;
  aggressor: "yes" | "no";
}
interface Order {
  id: string;
  contract: string;
  side: "yes" | "no";
  price: number;
  qty: number;
  filled: number;
  status: string;
  reason: string | null;
}
interface Positions {
  cash: number;
  held: number;
  equityAtCost: number;
  pnl: number;
  positions: { contract: string; yes: number; no: number; cost: number }[];
  orders: Order[];
}
interface Group {
  accounts: number;
  pnl: number;
  equityAtCost: number;
}
interface Stats {
  trades: number;
  groups: Record<string, Group>;
  marketMakerUptime: Record<string, number | null>;
  audit: string | string[];
}
interface Account {
  account: string;
  token: string;
}

const KEY = "overwatch-venue-account";
const DEPTH = 5;
const GROUP_LABEL: Record<string, string> = {
  "AI market maker": "Market makers",
  "AI fund · tier 1": "Large funds",
  "AI fund · tier 2": "Mid funds",
  "AI fund · tier 3": "Small funds",
  "AI retail": "Retail traders",
  Player: "Players",
};
const GROUP_ORDER = ["AI market maker", "AI fund · tier 1", "AI fund · tier 2", "AI fund · tier 3", "AI retail", "Player"];

function loadAccount(): Account | null {
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) ?? "null");
    return raw && typeof raw.account === "string" && typeof raw.token === "string" ? raw : null;
  } catch {
    return null;
  }
}

async function call<T>(path: string, init?: { body?: unknown; token?: string }): Promise<{ ok: boolean; status: number; data: T }> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (init?.token) headers["x-player-token"] = init.token;
  const res = await fetch(`/api/venue/${path}`, {
    method: init?.body === undefined ? "GET" : "POST",
    headers,
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  const data = (await res.json().catch(() => ({}))) as T;
  return { ok: res.ok, status: res.status, data };
}

function usePoll<T>(path: string | null, ms: number, token?: string, onData?: (data: T) => void): T | null {
  const [data, setData] = useState<T | null>(null);
  const sink = useRef(onData);
  useEffect(() => {
    sink.current = onData;
  });
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const tick = async () => {
      try {
        const got = await call<T>(path, { token });
        if (alive && got.ok) {
          setData(got.data);
          sink.current?.(got.data);
        }
      } catch {
        /* offline: keep the last good copy */
      }
    };
    void tick();
    const id = window.setInterval(tick, ms);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [path, ms, token]);
  return data;
}

const pct = (p: number) => `${Math.round(100 * p)}%`;
const price = (p: number | null | undefined) => (p == null ? "–" : `${p}`);

function Ladder({ depth }: { depth: Depth }) {
  const asks = depth.asks.slice(0, DEPTH).reverse();
  const bids = depth.bids.slice(0, DEPTH);
  const most = Math.max(1, ...depth.bids.map((l) => l[1]), ...depth.asks.map((l) => l[1]));
  const row = (side: "ask" | "bid", [p, q]: Level) => (
    <div key={`${side}${p}`} className="ex-level" data-side={side}>
      <span className="ex-bar" style={{ width: `${(100 * q) / most}%` }} />
      <span className="tl-data">{p}</span>
      <span className="tl-data ex-qty">{fmtPts(q)}</span>
    </div>
  );
  const spread = depth.bids.length && depth.asks.length ? depth.asks[0][0] - depth.bids[0][0] : null;
  return (
    <div className="ex-ladder" aria-label="Order book: YES price and contracts">
      <div className="ex-ladder-head">
        <span>YES price</span>
        <span>Contracts</span>
      </div>
      {Array.from({ length: DEPTH - asks.length }, (_, i) => (
        <div key={`a-pad${i}`} className="ex-level" aria-hidden />
      ))}
      {asks.map((l) => row("ask", l))}
      <div className="ex-spread tl-num">
        <b className="tl-data">{depth.last ?? "–"}</b>
        <span>{spread == null ? "no two-sided market" : `spread ${spread}`}</span>
      </div>
      {bids.map((l) => row("bid", l))}
      {Array.from({ length: DEPTH - bids.length }, (_, i) => (
        <div key={`b-pad${i}`} className="ex-level" aria-hidden />
      ))}
    </div>
  );
}

interface Point {
  at: number;
  price: number;
  model: number;
}

function Chart({ points, model }: { points: Point[]; model: number }) {
  const W = 600;
  const H = 180;
  if (points.length < 2) return <div className="ex-chart ex-chart-empty">Waiting for trades…</div>;
  const t0 = points[0].at;
  const span = Math.max(1, points[points.length - 1].at - t0);
  const vals = points.flatMap((p) => [p.price, p.model]);
  const lo = Math.max(0, Math.min(...vals) - 5);
  const hi = Math.min(100, Math.max(...vals) + 5);
  const x = (at: number) => ((at - t0) / span) * W;
  const y = (v: number) => H - ((v - lo) / Math.max(1, hi - lo)) * H;
  const line = (k: "price" | "model") => points.map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(p[k]).toFixed(1)}`).join("");
  const last = points[points.length - 1];
  const area = `${line("price")}L${W},${H}L0,${H}Z`;
  return (
    <div className="ex-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="Traded price and model over time">
        <defs>
          <linearGradient id="ex-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--ex-accent)" stopOpacity="0.28" />
            <stop offset="1" stopColor="var(--ex-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#ex-fill)" />
        <path d={line("model")} className="ex-chart-model" />
        <path d={line("price")} className="ex-chart-price" />
        <circle cx={x(last.at)} cy={y(last.price)} r="4" className="ex-chart-dot" />
      </svg>
      <div className="ex-chart-key tl-num">
        <span data-k="price">Traded price {last.price}</span>
        <span data-k="model">Our model {Math.round(100 * model)}</span>
      </div>
    </div>
  );
}

/** The venue's per-contract position limit (services/city/integrity.py): 10% of equity on clean sheet, which one
 * person on a platform can see first, 25% everywhere else. */
const capShare = (series: string) => (series === "clean_sheet" ? 0.1 : 0.25);

/** The most contracts an order on `side` at `px` can be for: what it would pair off against the opposite holding
 * (selling adds no risk), plus what fits under the limit after this contract's current risk. Mirrors
 * venue_gateway.Gateway._check. */
function maxContracts(c: Contract, side: "yes" | "no", px: number, mine: Positions | null): number {
  if (!mine) return 0;
  const pos = mine.positions.find((p) => p.contract === c.id) ?? { yes: 0, no: 0, cost: 0 };
  const open = mine.orders.filter((o) => o.contract === c.id && (o.status === "pending" || o.status === "open"));
  const atRisk = pos.cost + open.reduce((n, o) => n + o.price * (o.qty - o.filled), 0);
  const closing = Math.max(0, (side === "yes" ? pos.no : pos.yes) - open.filter((o) => o.side === side).reduce((n, o) => n + o.qty - o.filled, 0));
  const room = Math.floor(capShare(c.series) * mine.equityAtCost) - atRisk;
  return Math.min(500, closing + Math.max(0, Math.floor(room / px)));
}

function Ticket({ c, account, mine, holding, onDone }: { c: Contract; account: Account | null; mine: Positions | null; holding: { yes: number; no: number }; onDone: (msg: string) => void }) {
  const [side, setSide] = useState<"yes" | "no">("yes");
  // The quantity a player typed belongs to one contract and side; switching either starts from the default again.
  const [typed, setTyped] = useState<{ key: string; n: number } | null>(null);
  const [limit, setLimit] = useState<number | "">("");
  const best = side === "yes" ? c.ask : c.bid == null ? null : 100 - c.bid;
  const px = limit === "" ? (best == null ? null : Math.min(99, best + 1)) : limit;
  const open = c.status === "open";
  const max = px == null ? 0 : maxContracts(c, side, px, mine);
  const key = `${c.id}|${side}`;
  const qty = typed?.key === key ? typed.n : null;
  const setQty = (v: number) => setTyped({ key, n: v });
  // Start at 5 contracts or the most the limit allows, whichever is fewer; the player can change it.
  const n = qty ?? Math.max(1, Math.min(5, max));
  const over = n > max;
  const send = async (s: "yes" | "no", q: number, p: number | null, why: string) => {
    if (!account || p == null) return;
    const got = await call<Order & { error?: string }>("order", {
      token: account.token,
      body: { account: account.account, contract: c.id, side: s, price: p, qty: q, tif: "ioc" },
    });
    onDone(got.ok ? `${why}: sent, fills after the ${c.series.startsWith("clean") ? "45" : "few"}-second speed bump` : `Refused: ${got.data.reason ?? got.data.error ?? got.status}`);
  };
  const sellSide = holding.yes ? "no" : holding.no ? "yes" : null;
  const sellQty = holding.yes || holding.no;
  const sellPx = sellSide === "no" ? (c.bid == null ? null : Math.min(99, 100 - c.bid + 1)) : c.ask == null ? null : Math.min(99, c.ask + 1);
  return (
    <div className="ex-ticket">
      <div className="ex-seg" role="group" aria-label="Side">
        <button type="button" data-on={side === "yes" || undefined} data-tone="up" onClick={() => setSide("yes")}>
          Buy YES <span className="tl-num">{price(c.ask)}</span>
        </button>
        <button type="button" data-on={side === "no" || undefined} data-tone="down" onClick={() => setSide("no")}>
          Buy NO <span className="tl-num">{price(c.bid == null ? null : 100 - c.bid)}</span>
        </button>
      </div>
      <div className="ex-fields">
        <label>
          Contracts
          <input type="number" min={1} max={500} value={n} onChange={(e) => setQty(Math.max(1, Math.min(500, Number(e.target.value) || 1)))} />
          {mine && px != null && (
            <button type="button" className="ex-max tl-num" onClick={() => setQty(Math.max(1, max))}>
              max {max}
            </button>
          )}
        </label>
        <label>
          Limit price
          <input
            type="number"
            min={1}
            max={99}
            placeholder={px == null ? "–" : String(px)}
            value={limit}
            onChange={(e) => setLimit(e.target.value === "" ? "" : Math.max(1, Math.min(99, Number(e.target.value))))}
          />
        </label>
      </div>
      <p className="ex-note tl-num" data-warn={over || undefined}>
        {px == null
          ? "Nobody is offering this side yet."
          : over
            ? max === 0
              ? `You're at your limit on this contract: at most ${Math.round(capShare(c.series) * 100)}% of your points at risk on one contract.`
              : `Over your limit: at most ${max} contracts at ${px} (${Math.round(capShare(c.series) * 100)}% of your points at risk on one contract).`
            : `Pays 100 if ${side === "yes" ? c.yes : c.no}. Costs up to ${fmtPts(px * n)}, so you'd win up to ${fmtPts((100 - px) * n)}.`}
      </p>
      <button type="button" className="ex-go" disabled={!account || !open || px == null || over} onClick={() => void send(side, n, px, `Buy ${n} ${side.toUpperCase()}`)}>
        {!account ? "Opening your account…" : !open ? `Market ${c.status}` : over ? "Over your limit" : `Buy ${n} ${side.toUpperCase()} at up to ${price(px)}`}
      </button>
      {sellSide && (
        <button type="button" className="ex-sell" disabled={!open || sellPx == null} onClick={() => void send(sellSide, sellQty, sellPx, `Sell ${sellQty}`)}>
          Sell my {sellQty} {holding.yes ? "YES" : "NO"}
        </button>
      )}
    </div>
  );
}

export function Exchange() {
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [picked, setPicked] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [status, setStatus] = useState<{ msg: string; at: number } | null>(null);
  const [history, setHistory] = useState<Record<string, Point[]>>({});
  const joining = useRef(false);
  const contracts = usePoll<{ now: number; live: boolean; contracts: Contract[] }>("contracts", 2000);
  const live = useMemo(() => (contracts?.contracts ?? []).filter((c) => c.status === "open" || c.status === "closed"), [contracts]);
  const chosen = live.find((c) => c.id === picked) ?? live.find((c) => c.status === "open") ?? live[0] ?? null;
  const book = usePoll<Depth & { tape: Print[] }>(chosen ? `book?contract=${encodeURIComponent(chosen.id)}` : null, 1000, undefined, (b) => {
    if (!chosen || !b.tape?.length) return;
    const id = chosen.id;
    const model = 100 * chosen.forecast;
    setHistory((h) => {
      const prev = h[id] ?? [];
      const seen = prev.length ? prev[prev.length - 1].at : 0;
      const fresh = b.tape.filter((t) => t.contract === id && t.at > seen).map((t) => ({ at: t.at, price: t.yesPrice, model }));
      return fresh.length ? { ...h, [id]: [...prev, ...fresh].slice(-300) } : h;
    });
  });
  const mine = usePoll<Positions>(account ? `positions?account=${account.account}` : null, 2000, account?.token);
  const stats = usePoll<Stats>("stats", 10_000);

  // Why: no sign-up step; the venue keeps accounts in memory, so a visitor gets one on arrival.
  useEffect(() => {
    if (account || joining.current) return;
    joining.current = true;
    void call<Account & { error?: string }>("account/new", { body: { name: "Player" } }).then((got) => {
      if (!got.ok) return;
      const a = { account: got.data.account, token: got.data.token };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(a));
      } catch {
        /* private mode: the account lasts this visit */
      }
      setAccount(a);
    });
  }, [account]);

  const cancel = useCallback(
    async (o: Order) => {
      if (!account) return;
      const got = await call<{ reason: string }>("cancel", { token: account.token, body: { account: account.account, order: o.id } });
      setToast(got.ok ? "Order cancelled" : `Couldn't cancel: ${got.data.reason}`);
    },
    [account],
  );
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(id);
  }, [toast]);

  const markets = useMemo(() => {
    const by = new Map<string, Contract[]>();
    for (const c of live) by.set(c.market, [...(by.get(c.market) ?? []), c]);
    return [...by.values()].filter((g) => g.some((c) => c.status === "open"));
  }, [live]);
  const holding = (cid: string) => mine?.positions.find((p) => p.contract === cid) ?? { yes: 0, no: 0 };
  const offline = contracts == null;
  const lastPx = chosen ? (book?.last ?? chosen.last) : null;
  const edge = chosen && lastPx != null ? lastPx - Math.round(100 * chosen.forecast) : null;

  return (
    <div className="ex">
      <header className="ex-top">
        <Link href="/multiverse" className="ex-back">
          <ArrowLeft size={16} aria-hidden /> OverWatch
        </Link>
        <div className="ex-title">
          <h1>Exchange</h1>
          <span className="ex-live tl-num" data-on={!offline && contracts.live ? true : undefined}>
            {offline ? "Connecting…" : `${contracts.live ? "Live" : "Replay"} · ${londonClock(contracts.now, true)}`}
          </span>
        </div>
        <div className="ex-wallet tl-num">
          <span>
            <i>Cash</i>
            <b className="tl-data">{mine ? fmtPts(mine.cash) : "1,000"}</b>
          </span>
          <span>
            <i>In orders</i>
            <b className="tl-data">{mine ? fmtPts(mine.held) : "0"}</b>
          </span>
          <span>
            <i>P&amp;L</i>
            <b className="tl-data" data-tone={mine && mine.pnl > 0 ? "up" : mine && mine.pnl < 0 ? "down" : undefined}>
              {mine && mine.pnl > 0 ? "+" : ""}
              {mine ? fmtPts(mine.pnl) : "0"}
            </b>
          </span>
        </div>
      </header>

      {offline ? (
        <main className="ex-main">
          <p className="ex-card">The exchange isn&apos;t reachable right now. Try again in a minute.</p>
        </main>
      ) : (
        <main className="ex-main">
          <div className="ex-left">
          <section className="ex-card ex-list" aria-label="Markets">
            <h2>Markets</h2>
            {markets.length === 0 && <p className="tl-muted">No market is open right now.</p>}
            {markets.length > 0 && (
              <div className="ex-row ex-row-head" aria-hidden>
                <span />
                <span>Bid</span>
                <span>Ask</span>
                <span>Model</span>
              </div>
            )}
            {markets.map((group) => (
              <div key={group[0].market} className="ex-market" data-closed={group.every((c) => c.status !== "open") || undefined}>
                <h3>
                  {group[0].question}
                  {group.every((c) => c.status !== "open") && <span className="tl-muted"> · awaiting result</span>}
                </h3>
                {group.map((c) => (
                  <button key={c.id} type="button" className="ex-row" data-on={chosen?.id === c.id || undefined} onClick={() => setPicked(c.id)}>
                    <span className="ex-name">{c.yes}</span>
                    <span className="tl-data" data-tone="up">
                      {price(c.bid)}
                    </span>
                    <span className="tl-data" data-tone="down">
                      {price(c.ask)}
                    </span>
                    <span className="tl-num ex-model">{pct(c.forecast)}</span>
                    <span className="ex-flag">
                      {c.status === "open" && c.fast ? <i className="ex-fast" title="Fast market: one-sided trading, so market makers may quote wider" /> : null}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </section>

          {stats && (
            <section className="ex-card ex-who" aria-label="Who is trading">
              <h2>On the other side</h2>
              {GROUP_ORDER.filter((g) => stats.groups[g]).map((g) => (
                <div key={g} className="ex-group tl-num">
                  <span>{GROUP_LABEL[g] ?? g}</span>
                  <b className="tl-data">{fmtPts(stats.groups[g].accounts)}</b>
                </div>
              ))}
              <p className="tl-muted tl-num">{fmtPts(stats.trades)} trades so far · no house</p>
            </section>
          )}
          </div>

          {chosen && (
            <section className="ex-center" aria-label="Selected market">
              <div className="ex-card ex-hero">
                <p className="ex-q">{chosen.question}</p>
                <div className="ex-hero-row">
                  <h2>{chosen.yes}</h2>
                  <b className="ex-px tl-data">{lastPx ?? "–"}</b>
                  {edge != null && (
                    <span className="ex-edge tl-num" data-tone={edge > 0 ? "up" : edge < 0 ? "down" : undefined}>
                      {edge > 0 ? "+" : ""}
                      {edge} vs model
                    </span>
                  )}
                  <span className="ex-hero-meta tl-num">
                    Closes {londonClock(chosen.closesAt)} · settles {londonClock(chosen.settlesAt)}
                    {chosen.fast && " · fast market"}
                  </span>
                </div>
                <Chart points={history[chosen.id] ?? []} model={chosen.forecast} />
              </div>
              <div className="ex-card ex-split">
                <div>
                  <h3>Order book</h3>
                  <Ladder depth={book ?? chosen.depth} />
                </div>
                <div className="ex-tape" aria-label="Recent trades">
                  <h3>Trades</h3>
                  <div className="ex-tape-head" aria-hidden>
                    <span>Price</span>
                    <span>Qty</span>
                    <span>Time</span>
                  </div>
                  {(book?.tape ?? []).slice(-(2 * DEPTH + 1)).reverse().map((t, i) => (
                    <div key={`${t.at}${i}`} className="ex-print tl-num">
                      <span className="tl-data" data-tone={t.aggressor === "yes" ? "up" : "down"}>
                        {t.yesPrice}
                      </span>
                      <span>×{t.qty}</span>
                      <span className="tl-muted">{londonClock(t.at, true)}</span>
                    </div>
                  ))}
                  {!book?.tape?.length && <p className="tl-muted">No trades yet.</p>}
                </div>
              </div>
            </section>
          )}

          {chosen && (
            <aside className="ex-card ex-trade" aria-label="Trade">
              <h2>Trade</h2>
              <p className="ex-sub">
                YES pays 100 if {chosen.yes}. NO pays 100 if not.
              </p>
              <Ticket
                c={chosen}
                account={account}
                mine={mine}
                holding={holding(chosen.id)}
                onDone={(msg) => {
                  setStatus({ msg, at: Date.now() });
                  setToast(msg);
                }}
              />
              <div className="ex-mine">
                <h3>Order status</h3>
                {status ? (
                  <p className="ex-status tl-num" data-bad={status.msg.startsWith("Refused") || undefined}>
                    {status.msg} <span className="tl-muted">· {londonClock(status.at, true)}</span>
                  </p>
                ) : (
                  <p className="tl-muted">No order sent yet.</p>
                )}
                <h3>Open orders</h3>
                {!mine?.orders.length && <p className="tl-muted">None resting.</p>}
                {mine?.orders.map((o) => (
                  <div key={o.id} className="ex-print tl-num">
                    <span>
                      {o.side.toUpperCase()} {o.qty - o.filled} at {o.price}
                    </span>
                    <span className="tl-muted">{o.status}</span>
                    <button type="button" className="ex-link" onClick={() => void cancel(o)}>
                      Cancel
                    </button>
                  </div>
                ))}
                <h3>Positions</h3>
                {!mine?.positions.length && <p className="tl-muted">None yet.</p>}
                {mine?.positions.map((p) => (
                  <div key={p.contract} className="ex-print tl-num">
                    <span>{live.find((c) => c.id === p.contract)?.yes ?? p.contract}</span>
                    <span>{p.yes ? `${p.yes} YES` : `${p.no} NO`}</span>
                    <span className="tl-muted">cost {fmtPts(p.cost)}</span>
                  </div>
                ))}
              </div>
            </aside>
          )}

        </main>
      )}
      {toast && (
        <div className="ex-toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}
