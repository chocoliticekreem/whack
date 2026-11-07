"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtPts, londonClock } from "./city-store";
import "./wallet.css";

/** Shapes served by services/city/ledger.py (GET /wallet /positions /history, POST /limits /break). */
export interface ServerWallet {
  player: { id: string; nickname: string | null; kycStatus: string; createdAt: number };
  cash: number;
  escrow: number;
  total: number;
  totals: { granted: number; staked: number; returned: number; betsPlaced: number; betsSettled: number; won: number; lost: number; void: number };
  pnl: { today: number; allTime: number; session?: number };
  limits: {
    dailyLoss: number;
    pending: { value: number; effectiveAt: number } | null;
    lostToday: number;
    inPlay: number;
    remainingToday: number;
    confirmAbove: number;
  };
  break: { active: boolean; until: number | null };
  asOf: number;
}

interface Position {
  betId: string;
  market: string;
  question: string;
  label: string;
  stake: number;
  odds: number;
  toWin: number;
  profit: number;
  settlesAt: number;
}

interface HistoryRow {
  betId: string;
  question: string;
  label: string;
  stake: number;
  payout: number;
  result: "won" | "lost" | "void";
  pnl: number;
  reading: string;
  settledAt: number;
}

const POLL_MS = 4000;
const REMIND_MS = 30 * 60_000;
const SESSION_START = typeof window === "undefined" ? 0 : Date.now();

const auth = (token: string | null): Record<string, string> => (token ? { "x-player-token": token } : {});

async function get<T>(path: string, token: string | null): Promise<{ status: number; data: T | null }> {
  try {
    const res = await fetch(`/api/city/${path}`, { cache: "no-store", headers: auth(token) });
    return { status: res.status, data: res.ok ? ((await res.json()) as T) : null };
  } catch {
    return { status: 0, data: null };
  }
}

async function post(path: string, body: unknown, token: string | null): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`/api/city/${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...auth(token) },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = (await res.json()) as { error?: string };
    return { ok: res.ok, error: data.error };
  } catch {
    return { ok: false, error: "Could not reach the exchange" };
  }
}

let creating: Promise<{ player: string; token: string } | { error: string }> | null = null;

/** POST /player/new: the server issues the id, the token and the starting grant. One request at a time per tab. */
export function createPlayer() {
  creating ??= (async () => {
    try {
      const res = await fetch("/api/city/player/new", { method: "POST", headers: { "content-type": "application/json" }, body: "{}", cache: "no-store" });
      const data = (await res.json()) as { player?: string; token?: string; error?: string };
      return data.player && data.token ? { player: data.player, token: data.token } : { error: data.error ?? "Could not create a player" };
    } catch {
      return { error: "Could not reach the exchange" };
    } finally {
      creating = null;
    }
  })();
  return creating;
}

export const setNickname = (player: string, token: string, name: string) => post("player/name", { player, name }, token);

/** The player's balance as the server's ledger has it. `null` until the first reply, and always in demo mode.
 *  A 401 means the token no longer matches (a wiped ledger): `onUnauthorized` gets that token so the caller can make a new player. */
export function useServerWallet(
  player: string,
  token: string | null,
  enabled: boolean,
  onCash: (cash: number) => void,
  onUnauthorized: (staleToken: string | null) => void,
) {
  const [data, setData] = useState<ServerWallet | null>(null);
  const refresh = useCallback(async () => {
    const r = await get<ServerWallet>(`wallet?player=${encodeURIComponent(player)}&since=${SESSION_START}`, token);
    if (r.status === 401) onUnauthorized(token);
    if (!r.data) return;
    setData(r.data);
    onCash(r.data.cash);
  }, [player, token, onCash, onUnauthorized]);
  useEffect(() => {
    if (!enabled) return;
    const first = window.setTimeout(() => void refresh(), 0);
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [enabled, refresh]);
  return { wallet: enabled ? data : null, refresh };
}

/** A promise-returning confirm dialog, used before a stake above 25% of the balance. */
export function useStakeConfirm() {
  const [ask, setAsk] = useState<{ text: string; done: (yes: boolean) => void } | null>(null);
  const confirm = useCallback((text: string) => new Promise<boolean>((done) => setAsk({ text, done })), []);
  const close = (yes: boolean) => {
    ask?.done(yes);
    setAsk(null);
  };
  const element = ask ? (
    <div className="wl-scrim wl-scrim-center" onClick={() => close(false)}>
      <div className="wl-dialog" role="alertdialog" aria-label="Confirm stake" onClick={(e) => e.stopPropagation()}>
        <h3>Large stake</h3>
        <p>{ask.text}</p>
        <div className="wl-row-end">
          <button type="button" className="wl-btn" onClick={() => close(false)}>
            Cancel
          </button>
          <button type="button" className="wl-btn wl-btn-primary" onClick={() => close(true)} autoFocus>
            Place bet
          </button>
        </div>
      </div>
    </div>
  ) : null;
  return [confirm, element] as const;
}

const signed = (n: number) => (n > 0 ? `+${fmtPts(n)}` : n < 0 ? `−${fmtPts(-n)}` : "0");
const tone = (n: number) => (n > 0 ? "up" : n < 0 ? "down" : undefined);

function whenLabel(ms: number) {
  const d = new Date(ms);
  const today = new Date().toDateString() === d.toDateString();
  return today ? londonClock(ms) : `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Europe/London" })} ${londonClock(ms)}`;
}

function PnlChart({ series }: { series: [number, number][] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 520;
  const H = 120;
  const pad = { l: 4, r: 4, t: 10, b: 10 };
  const pts: [number, number][] = series.length ? [[series[0][0] - 1, 0], ...series] : [];
  if (pts.length < 2) return <div className="wl-chart-empty">The chart starts with your first settled bet.</div>;
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const x = (i: number) => pad.l + (i / (pts.length - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / span) * (H - pad.t - pad.b);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (pts.length - 1));
    setHover(Math.max(1, Math.min(pts.length - 1, i)));
  };
  return (
    <figure className="wl-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img" aria-label={`Cumulative P&L, now ${signed(last)} points`}>
        <line className="wl-zero" x1={0} x2={W} y1={y(0)} y2={y(0)} />
        <path className="wl-line" data-tone={tone(last)} d={d} vectorEffect="non-scaling-stroke" />
        {hover != null && (
          <>
            <line className="wl-cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} vectorEffect="non-scaling-stroke" />
            <circle className="wl-dot" data-tone={tone(last)} cx={x(hover)} cy={y(pts[hover][1])} r={4} />
          </>
        )}
      </svg>
      {hover != null && (
        <div className="wl-tip tl-num" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <b>{signed(pts[hover][1])}</b> after bet {hover}
          <span>{whenLabel(pts[hover][0])}</span>
        </div>
      )}
      <figcaption className="wl-axis tl-num">
        <span>Bet 1</span>
        <span>
          Range {signed(lo)} to {signed(hi)}
        </span>
        <span>Bet {pts.length - 1}</span>
      </figcaption>
    </figure>
  );
}

function SessionReminder({ wallet }: { wallet: ServerWallet | null }) {
  const [due, setDue] = useState(false);
  const [minutes, setMinutes] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      const m = Math.floor((Date.now() - SESSION_START) / 60_000);
      if (m > 0 && m % (REMIND_MS / 60_000) === 0 && m !== minutes) {
        setMinutes(m);
        setDue(true);
      }
    }, 10_000);
    return () => window.clearInterval(id);
  }, [minutes]);
  if (!due || !wallet) return null;
  const session = wallet.pnl.session ?? 0;
  return (
    <div className="wl-reminder" role="status">
      <div>
        <b>You have been playing for {minutes} minutes.</b>
        <span className="tl-num">
          Session P&amp;L <span data-tone={tone(session)}>{signed(session)}</span> points
        </span>
      </div>
      <button type="button" className="wl-btn" onClick={() => setDue(false)}>
        Continue
      </button>
    </div>
  );
}

function Limits({ wallet, player, token, onChange }: { wallet: ServerWallet; player: string; token: string | null; onChange: () => void }) {
  const [value, setValue] = useState(String(wallet.limits.dailyLoss));
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (path: string, body: object, ok: string) => {
    setBusy(true);
    const r = await post(path, { player, ...body }, token);
    setBusy(false);
    setMsg(r.ok ? ok : (r.error ?? "Request failed"));
    onChange();
  };
  const n = Number(value);
  const raising = n > wallet.limits.dailyLoss;
  const used = wallet.limits.dailyLoss - wallet.limits.remainingToday;
  return (
    <section className="wl-section">
      <h3>Limits</h3>
      <dl className="wl-kv tl-num">
        <dt>Daily loss limit</dt>
        <dd>{fmtPts(wallet.limits.dailyLoss)}</dd>
        <dt>Lost today</dt>
        <dd>{fmtPts(wallet.limits.lostToday)}</dd>
        <dt>In play</dt>
        <dd>{fmtPts(wallet.limits.inPlay)}</dd>
        <dt>Can still risk today</dt>
        <dd>
          <b>{fmtPts(wallet.limits.remainingToday)}</b>
        </dd>
      </dl>
      <div className="wl-meter" aria-hidden>
        <span style={{ width: `${Math.min(100, (used / Math.max(1, wallet.limits.dailyLoss)) * 100)}%` }} />
      </div>
      {wallet.limits.pending && (
        <p className="wl-note tl-num">
          Raise to {fmtPts(wallet.limits.pending.value)} takes effect {whenLabel(wallet.limits.pending.effectiveAt)}.
        </p>
      )}
      <form
        className="wl-limit-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!Number.isInteger(n) || n <= 0) return setMsg("Enter a whole number of points");
          void run("limits", { dailyLoss: n }, raising ? "Raise requested. It takes effect in 24 hours." : "Limit lowered. It applies now.");
        }}
      >
        <label htmlFor="wl-limit">New daily loss limit</label>
        <div className="wl-row">
          <input id="wl-limit" className="tl-num" inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))} />
          <button type="submit" className="wl-btn" disabled={busy}>
            {raising ? "Request raise" : "Lower now"}
          </button>
        </div>
        <span className="wl-note">Lowering applies immediately. Raising waits 24 hours.</span>
      </form>
      <div className="wl-break">
        <div>
          <b>Take a break</b>
          <span className="wl-note">
            {wallet.break.active && wallet.break.until
              ? `Betting paused until ${whenLabel(wallet.break.until)}. A break cannot be cancelled.`
              : "Pauses all betting. A break cannot be cancelled early."}
          </span>
        </div>
        <div className="wl-row">
          <button type="button" className="wl-btn" disabled={busy} onClick={() => void run("break", { hours: 1 }, "Break started for 1 hour.")}>
            1 hour
          </button>
          <button type="button" className="wl-btn" disabled={busy} onClick={() => void run("break", { hours: 24 }, "Break started for 24 hours.")}>
            24 hours
          </button>
        </div>
      </div>
      {msg && <p className="wl-msg">{msg}</p>}
    </section>
  );
}

export function WalletPanel({
  player,
  token,
  wallet,
  open,
  onClose,
  refresh,
}: {
  player: string;
  token: string | null;
  wallet: ServerWallet | null;
  open: boolean;
  onClose: () => void;
  refresh: () => void;
}) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [series, setSeries] = useState<[number, number][]>([]);
  const lastAsOf = useRef(0);

  const load = useCallback(async () => {
    const q = `player=${encodeURIComponent(player)}`;
    const [p, h] = await Promise.all([
      get<{ positions: Position[] }>(`positions?${q}`, token),
      get<{ history: HistoryRow[]; series: [number, number][] }>(`history?${q}&limit=50`, token),
    ]);
    if (p.data) setPositions(p.data.positions);
    if (h.data) {
      setHistory(h.data.history);
      setSeries(h.data.series);
    }
  }, [player, token]);

  useEffect(() => {
    if (!open || !wallet || wallet.asOf === lastAsOf.current) return;
    lastAsOf.current = wallet.asOf;
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [open, wallet, load]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const record = useMemo(() => (wallet ? `${wallet.totals.won} won · ${wallet.totals.lost} lost · ${wallet.totals.void} void` : ""), [wallet]);

  return (
    <>
      <SessionReminder wallet={wallet} />
      {open && (
        <div className="wl-scrim" onClick={onClose}>
          <aside className="wl-panel" aria-label="Wallet" onClick={(e) => e.stopPropagation()}>
            <header className="wl-head">
              <div>
                <h2>Wallet</h2>
                <span className="wl-note">
                  {wallet?.player.nickname ?? player} · points only, no real money
                </span>
                <a className="wl-note" href="/multiverse/portfolio">Full portfolio and history</a>
              </div>
              <button type="button" className="wl-x" aria-label="Close wallet" onClick={onClose}>
                <X size={18} />
              </button>
            </header>

            {!wallet ? (
              <p className="wl-empty">The wallet needs the live exchange. It is not available with demo data.</p>
            ) : (
              <>
                {wallet.break.active && wallet.break.until && (
                  <p className="wl-banner tl-num">You are on a break until {whenLabel(wallet.break.until)}. Betting is paused.</p>
                )}
                <section className="wl-balance tl-num">
                  <div className="wl-big">
                    <span className="wl-label">Available</span>
                    <b>{fmtPts(wallet.cash)}</b>
                    <span className="wl-unit">pts</span>
                  </div>
                  <dl className="wl-kv">
                    <dt>In play</dt>
                    <dd>{fmtPts(wallet.escrow)}</dd>
                    <dt>Total</dt>
                    <dd>{fmtPts(wallet.total)}</dd>
                  </dl>
                </section>

                <section className="wl-section">
                  <div className="wl-stats tl-num">
                    <div>
                      <span className="wl-label">Today</span>
                      <b data-tone={tone(wallet.pnl.today)}>{signed(wallet.pnl.today)}</b>
                    </div>
                    <div>
                      <span className="wl-label">All time</span>
                      <b data-tone={tone(wallet.pnl.allTime)}>{signed(wallet.pnl.allTime)}</b>
                    </div>
                    <div>
                      <span className="wl-label">This session</span>
                      <b data-tone={tone(wallet.pnl.session ?? 0)}>{signed(wallet.pnl.session ?? 0)}</b>
                    </div>
                  </div>
                  <PnlChart series={series} />
                  <span className="wl-note tl-num">
                    Points won or lost, bet by bet · {record}
                  </span>
                </section>

                <section className="wl-section">
                  <h3>
                    Bets still running <span className="wl-count tl-num">{positions.length}</span>
                  </h3>
                  {positions.length === 0 ? (
                    <p className="wl-empty">No open bets.</p>
                  ) : (
                    <div className="wl-scroll">
                      <table className="wl-table tl-num">
                        <thead>
                          <tr>
                            <th>Market</th>
                            <th className="wl-r">Stake</th>
                            <th className="wl-r">To win</th>
                            <th className="wl-r">Settles</th>
                          </tr>
                        </thead>
                        <tbody>
                          {positions.map((p) => (
                            <tr key={p.betId}>
                              <td>
                                <span className="wl-q">{p.question}</span>
                                <span className="wl-side">{p.label}</span>
                              </td>
                              <td className="wl-r">{fmtPts(p.stake)}</td>
                              <td className="wl-r">{fmtPts(p.toWin)}</td>
                              <td className="wl-r">{londonClock(p.settlesAt)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>

                <section className="wl-section">
                  <h3>
                    Finished bets <span className="wl-count tl-num">{wallet.totals.betsSettled}</span>
                  </h3>
                  {history.length === 0 ? (
                    <p className="wl-empty">Settled bets appear here with the reading that settled them.</p>
                  ) : (
                    <div className="wl-scroll">
                      <table className="wl-table tl-num">
                        <thead>
                          <tr>
                            <th>Time</th>
                            <th>Market</th>
                            <th className="wl-r">Stake</th>
                            <th>Result</th>
                            <th className="wl-r">P&amp;L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {history.map((h) => (
                            <tr key={h.betId}>
                              <td className="wl-time">{whenLabel(h.settledAt)}</td>
                              <td>
                                <span className="wl-q">{h.question}</span>
                                <span className="wl-side">{h.label}</span>
                                <span className="wl-reading">Settled on: {h.reading}</span>
                              </td>
                              <td className="wl-r">{fmtPts(h.stake)}</td>
                              <td className="wl-result" data-r={h.result}>
                                {h.result === "won" ? "Won" : h.result === "lost" ? "Lost" : "Void"}
                              </td>
                              <td className="wl-r" data-tone={tone(h.pnl)}>
                                {signed(h.pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>

                <Limits key={wallet.limits.dailyLoss} wallet={wallet} player={player} token={token} onChange={refresh} />

                <footer className="wl-foot tl-num">
                  Granted {fmtPts(wallet.totals.granted)} · staked {fmtPts(wallet.totals.staked)} · returned {fmtPts(wallet.totals.returned)} ·
                  account {wallet.player.kycStatus}
                </footer>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  );
}
