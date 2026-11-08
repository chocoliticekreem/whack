"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
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

function usePoll<T>(path: string | null, ms: number, token?: string): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const tick = async () => {
      try {
        const got = await call<T>(path, { token });
        if (alive && got.ok) setData(got.data);
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
  const asks = [...depth.asks].reverse();
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
      {asks.length ? asks.map((l) => row("ask", l)) : <div className="ex-empty">No offers</div>}
      <div className="ex-spread tl-num">{spread == null ? "No two-sided market" : `Spread ${spread}`}</div>
      {depth.bids.length ? depth.bids.map((l) => row("bid", l)) : <div className="ex-empty">No bids</div>}
    </div>
  );
}

function Ticket({ c, account, holding, onDone }: { c: Contract; account: Account | null; holding: { yes: number; no: number }; onDone: (msg: string) => void }) {
  const [side, setSide] = useState<"yes" | "no">("yes");
  const [qty, setQty] = useState(5);
  const [limit, setLimit] = useState<number | "">("");
  const best = side === "yes" ? c.ask : c.bid == null ? null : 100 - c.bid;
  const px = limit === "" ? (best == null ? null : Math.min(99, best + 1)) : limit;
  const open = c.status === "open";
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
          <input type="number" min={1} max={500} value={qty} onChange={(e) => setQty(Math.max(1, Math.min(500, Number(e.target.value) || 1)))} />
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
      <p className="ex-note tl-num">
        {px == null ? "Nobody is offering this side yet." : `Pays 100 if ${side === "yes" ? c.yes : c.no}. Costs up to ${fmtPts(px * qty)}, so you'd win up to ${fmtPts((100 - px) * qty)}.`}
      </p>
      <button type="button" className="ex-go" disabled={!account || !open || px == null} onClick={() => void send(side, qty, px, `Buy ${qty} ${side.toUpperCase()}`)}>
        {!account ? "Join to trade" : !open ? `Market ${c.status}` : `Buy ${qty} ${side.toUpperCase()} at up to ${price(px)}`}
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
  const contracts = usePoll<{ now: number; live: boolean; contracts: Contract[] }>("contracts", 2000);
  const live = useMemo(() => (contracts?.contracts ?? []).filter((c) => c.status === "open" || c.status === "closed"), [contracts]);
  const chosen = live.find((c) => c.id === picked) ?? live.find((c) => c.status === "open") ?? live[0] ?? null;
  const book = usePoll<Depth & { tape: Print[] }>(chosen ? `book?contract=${encodeURIComponent(chosen.id)}` : null, 1000);
  const mine = usePoll<Positions>(account ? `positions?account=${account.account}` : null, 2000, account?.token);
  const stats = usePoll<Stats>("stats", 10_000);

  const join = useCallback(async () => {
    const got = await call<Account & { error?: string }>("account/new", { body: { name: "Player" } });
    if (got.ok) {
      const a = { account: got.data.account, token: got.data.token };
      window.localStorage.setItem(KEY, JSON.stringify(a));
      setAccount(a);
      setToast("You have 1,000 points. Pick a contract and trade.");
    } else setToast(`Couldn't join: ${got.data.error ?? got.status}`);
  }, []);
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
    const open = (g: Contract[]) => (g.some((c) => c.status === "open") ? 0 : 1);
    return [...by.values()].sort((a, b) => open(a) - open(b));
  }, [live]);
  const holding = (cid: string) => mine?.positions.find((p) => p.contract === cid) ?? { yes: 0, no: 0 };
  const offline = contracts == null;

  return (
    <div className="ex">
      <header className="ex-top">
        <Link href="/multiverse" className="ex-back">
          <ArrowLeft size={16} aria-hidden /> OverWatch
        </Link>
        <div className="ex-title">
          <h1>Exchange</h1>
          <span className="tl-muted tl-num">
            {offline ? "Connecting to the venue…" : `${contracts.live ? "Live" : "Replay"} · ${londonClock(contracts.now, true)} · no house, every trade has a buyer and a seller`}
          </span>
        </div>
        <div className="ex-wallet tl-num">
          {account && mine ? (
            <>
              <b className="tl-data">{fmtPts(mine.cash)}</b> pts cash · {fmtPts(mine.held)} reserved · P&L{" "}
              <b className="tl-data" data-tone={mine.pnl > 0 ? "up" : mine.pnl < 0 ? "down" : undefined}>
                {mine.pnl > 0 ? "+" : ""}
                {fmtPts(mine.pnl)}
              </b>
            </>
          ) : (
            <button type="button" className="ex-join" onClick={() => void join()}>
              <b>Get 1,000 free points</b>
              <span>Join and start trading</span>
            </button>
          )}
        </div>
      </header>

      {offline ? (
        <main className="ex-main">
          <p className="ex-card">The exchange venue isn&apos;t reachable yet. It runs beside the city markets: start services/city/venue_server.py.</p>
        </main>
      ) : (
        <main className="ex-main">
          <section className="ex-card ex-list" aria-label="Contracts">
            <h2>Contracts</h2>
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
                  {group.every((c) => c.status !== "open") && <span className="tl-muted"> · {group[0].status}, awaiting result</span>}
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
                    <span className="tl-muted tl-num">{pct(c.forecast)}</span>
                    <span className="ex-flag">
                      {c.status === "open" && c.fast ? <i className="ex-fast" title="Fast market: one-sided trading, so market makers may quote wider" /> : null}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </section>

          {chosen && (
            <section className="ex-card ex-detail" aria-label="Selected contract">
              <header>
                <h2>{chosen.question}</h2>
                <p className="ex-sub">
                  YES = {chosen.yes} <span className="tl-muted">· NO = {chosen.no}</span>
                </p>
                <dl className="ex-facts tl-num">
                  <div>
                    <dt>Closes</dt>
                    <dd>{londonClock(chosen.closesAt)}</dd>
                  </div>
                  <div>
                    <dt>Settles</dt>
                    <dd>{londonClock(chosen.settlesAt)}</dd>
                  </div>
                  <div>
                    <dt>Model</dt>
                    <dd>{pct(chosen.forecast)}</dd>
                  </div>
                  <div>
                    <dt>Last trade</dt>
                    <dd>{price(chosen.last)}</dd>
                  </div>
                </dl>
                <p className="ex-note">
                  Settles on {chosen.source.split(" · ")[0]}.{chosen.fast && " Fast market: trading is one-sided, so market makers may quote wider."}
                </p>
              </header>
              <div className="ex-cols">
                <Ladder depth={book ?? chosen.depth} />
                <div className="ex-side">
                  <Ticket c={chosen} account={account} holding={holding(chosen.id)} onDone={setToast} />
                  <div className="ex-tape" aria-label="Recent trades">
                    <h3>Recent trades</h3>
                    {(book?.tape ?? []).slice(-8).reverse().map((t, i) => (
                      <div key={`${t.at}${i}`} className="ex-print tl-num">
                        <span className="tl-data" data-tone={t.aggressor === "yes" ? "up" : "down"}>
                          {t.yesPrice}
                        </span>
                        <span>×{t.qty}</span>
                        <span className="tl-muted">{t.aggressor === "yes" ? "YES bought" : "YES sold"}</span>
                        <span className="tl-muted">{londonClock(t.at, true)}</span>
                      </div>
                    ))}
                    {!book?.tape?.length && <p className="tl-muted">No trades yet.</p>}
                  </div>
                </div>
              </div>
            </section>
          )}

          {account && mine && (mine.orders.length > 0 || mine.positions.length > 0) && (
            <section className="ex-card" aria-label="Your orders and positions">
              <h2>You</h2>
              {mine.positions.map((p) => (
                <div key={p.contract} className="ex-print tl-num">
                  <span>{live.find((c) => c.id === p.contract)?.yes ?? p.contract}</span>
                  <span>{p.yes ? `${p.yes} YES` : `${p.no} NO`}</span>
                  <span className="tl-muted">cost {fmtPts(p.cost)}</span>
                </div>
              ))}
              {mine.orders.map((o) => (
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
            </section>
          )}

          {stats && (
            <section className="ex-card" aria-label="Who is trading">
              <h2>Who is trading</h2>
              <p className="tl-muted">
                Every order, yours included, goes through the same gateway: the same speed bump, limits and fees. AI accounts also carry 300 ms of latency and see only the public book.
              </p>
              <div className="ex-groups">
                {GROUP_ORDER.filter((g) => stats.groups[g]).map((g) => (
                  <div key={g} className="ex-group tl-num">
                    <b>{g}</b>
                    <span>{fmtPts(stats.groups[g].accounts)} accounts</span>
                    <span className="tl-muted">P&L {stats.groups[g].pnl > 0 ? "+" : ""}{fmtPts(stats.groups[g].pnl)}</span>
                  </div>
                ))}
              </div>
              <p className="tl-muted tl-num">
                {fmtPts(stats.trades)} trades · market makers quoting {(() => {
                  const u = Object.values(stats.marketMakerUptime).filter((x): x is number => x != null);
                  return u.length ? `${pct(Math.min(...u))}–${pct(Math.max(...u))}` : "–";
                })()} of the time · ledger audit {stats.audit === "ok" ? "clean" : "failing"}
              </p>
            </section>
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
