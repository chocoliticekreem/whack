"use client";

import { Flame, Trophy, WalletIcon, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CityMapGL, flyToHotspots, type Pop } from "./CityMapGL";
import {
  START_POINTS,
  defaultName,
  fmtPts,
  loadWallet,
  londonClock,
  saveWallet,
  type MyBet,
  type Settled,
  type Wallet,
} from "./city-store";
import type { BetResponse, CitySignals, Hotspot, LeaderRow, Market, MarketsResponse, SettleResponse, Trader } from "./city-types";
import { CityStrip } from "./Clues";
import { HowItWorks } from "./HowItWorks";
import { LocalMarket } from "./LocalMarket";
import { Badge, MarketCard, phaseOf, type TipTarget } from "./MarketCard";
import { cancelNote, usePendingFills, type Fill } from "./pending-bets";
import { plainSettle, plainWatches, recordText } from "./plain";
import { Scenario, type WhatIfOdds } from "./Scenario";
import { WalletPanel, createPlayer, setNickname, useServerWallet, useStakeConfirm } from "./Wallet";

const POLL_MS = 3000;
const SETTLE_POLL_MS = 2500;
// Only used when the exchange is unreachable; while it answers, its own void rule decides.
const GIVE_UP_MS = 600_000;
const SHOW_SETTLED_MS = 25_000;
const HISTORY_CAP = 200;

const FEED_NAMES: Record<string, string> = {
  crowding: "TfL Crowding",
  line_status: "TfL line status",
  jamcams: "TfL JamCams",
  rain_now: "Open-Meteo rain",
  bus: "TfL bus arrivals",
};

const forcedMock = () => new URLSearchParams(window.location.search).get("mock") === "1";

async function call<T>(mock: boolean, path: string, body?: unknown, token?: string | null): Promise<{ ok: boolean; status: number; data: T }> {
  const res = await fetch(`/api/city/${path}${mock ? "?mock=1" : ""}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json", ...(token ? { "x-player-token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = (await res.json()) as T;
  return { ok: res.ok, status: res.status, data };
}

const isPhone = () => window.matchMedia("(max-width: 720px)").matches;

/** A portrait phone cannot fit all of central London; frame the three hubs nearest the middle instead. */
function core(hubs: Hotspot[]): Hotspot[] {
  if (hubs.length <= 3) return hubs;
  const lat = hubs.reduce((s, h) => s + h.lat, 0) / hubs.length;
  const lon = hubs.reduce((s, h) => s + h.lon, 0) / hubs.length;
  const d = (h: Hotspot) => (h.lat - lat) ** 2 + ((h.lon - lon) * 0.62) ** 2;
  return [...hubs].sort((a, b) => d(a) - d(b)).slice(0, 3);
}

function frame(hubs: Hotspot[]) {
  flyToHotspots(isPhone() ? core(hubs) : hubs);
}

function useNow(ms = 500) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

type Health = "green" | "amber" | "red";

interface Book {
  markets: Record<string, Market>;
  hist: Record<string, Record<string, number[]>>;
}

const NO_HISTORY: Record<string, number[]> = {};

/** Merges fresh market copies in and appends each option's price to its history for the sparkline. */
function absorb(b: Book, markets: Market[]): Book {
  const next: Book = { markets: { ...b.markets }, hist: { ...b.hist } };
  for (const m of markets) {
    next.markets[m.id] = m;
    const per = { ...(next.hist[m.id] ?? {}) };
    for (const o of m.options) {
      const prev = per[o.id] ?? (o.history?.length ? [...o.history] : []);
      per[o.id] = (prev[prev.length - 1] === o.prob ? prev : [...prev, o.prob]).slice(-HISTORY_CAP);
    }
    next.hist[m.id] = per;
  }
  return next;
}

function healthOf(feeds: MarketsResponse["feeds"], offline: boolean): Health {
  if (offline) return "red";
  const list = Object.values(feeds ?? {});
  if (!list.length) return "green";
  const bad = list.filter((f) => !f.ok).length;
  return bad === 0 ? "green" : bad === list.length ? "red" : "amber";
}

export function City() {
  const now = useNow();
  const [mock, setMock] = useState(forcedMock);
  const [offline, setOffline] = useState(false);
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [traders, setTraders] = useState<Trader[]>([]);
  const [feed, setFeed] = useState<MarketsResponse | null>(null);
  const [signals, setSignals] = useState<CitySignals | null>(null);
  const [results, setResults] = useState<Record<string, Settled & { at: number }>>({});
  const [wallet, setWallet] = useState<Wallet>(loadWallet);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ market: string; option: string } | null>(null);
  const [stake, setStake] = useState<number | "all">(100);
  const [askName, setAskName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [board, setBoard] = useState<LeaderRow[] | null>(null);
  const [boardOpen, setBoardOpen] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [whatIf, setWhatIf] = useState<Record<string, WhatIfOdds> | null>(null);
  const [tip, setTip] = useState<TipTarget | null>(null);
  const [toast, setToast] = useState<{ key: number; text: string; sub: string; good: boolean } | null>(null);
  const [pop, setPop] = useState<Pop | null>(null);
  const [glow, setGlow] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);
  const [betNote, setBetNote] = useState<string | null>(null);
  const [book, setBook] = useState<Book>({ markets: {}, hist: {} });
  const settling = useRef(new Set<string>());
  // Wallet hook: the server ledger owns the balance; wallet.points mirrors it outside the demo mock.
  const [walletOpen, setWalletOpen] = useState(false);
  const mirrorCash = useCallback((cash: number) => setWallet((w) => (w.points === cash ? w : { ...w, points: cash })), []);
  // Why: a reply to a request sent with the old token can land after a new player is saved; only that old token is dropped.
  const dropToken = useCallback((stale: string | null) => setWallet((w) => (w.token && w.token === stale ? { ...w, token: null } : w)), []);
  const { wallet: server, refresh: refreshWallet } = useServerWallet(wallet.player, wallet.token, !mock && !!wallet.token, mirrorCash, dropToken);
  const [askStake, stakeDialog] = useStakeConfirm();

  const nameRef = useRef(wallet.name);
  useEffect(() => {
    nameRef.current = wallet.name;
  }, [wallet.name]);

  // The server issues the player id and token once; both live in localStorage from then on.
  useEffect(() => {
    if (mock || wallet.token) return;
    let live = true;
    void createPlayer().then((r) => {
      if (!live) return;
      if ("error" in r) {
        setBetError(r.error);
        return;
      }
      setBetError(null);
      if (nameRef.current) void setNickname(r.player, r.token, nameRef.current);
      setWallet((w) => ({ ...w, player: r.player, token: r.token, streak: 0, bets: {} }));
    });
    return () => {
      live = false;
    };
  }, [mock, wallet.token]);

  useEffect(() => saveWallet(wallet), [wallet]);

  const traderMap = useMemo(() => new Map(traders.map((t) => [t.id, t])), [traders]);
  const hubMap = useMemo(() => new Map(hotspots.map((h) => [h.id, h])), [hotspots]);

  const pollMarkets = useCallback(async () => {
    // Why: the strip is optional context, so a failed /signals clears it without touching the markets.
    if (mock) setSignals(null);
    else
      void call<CitySignals>(false, "signals")
        .then((r) => setSignals(r.ok ? r.data : null))
        .catch(() => setSignals(null));
    try {
      const r = await call<MarketsResponse & { error?: string }>(mock, "markets");
      if (!r.ok) throw new Error(r.data.error ?? `HTTP ${r.status}`);
      setBook((b) => absorb(b, r.data.markets));
      setFeed(r.data);
      setOffline(false);
    } catch {
      if (!mock) setMock(true);
      else setOffline(true);
    }
  }, [mock]);

  useEffect(() => {
    let live = true;
    const loadStatic = async () => {
      try {
        const [h, t] = await Promise.all([
          call<{ hotspots: Hotspot[] }>(mock, "hotspots"),
          call<{ traders: Trader[] }>(mock, "traders"),
        ]);
        if (!live) return;
        if (h.ok) setHotspots(h.data.hotspots);
        if (t.ok) setTraders(t.data.traders);
      } catch {
        // pollMarkets flips to the mock if the engine is down; static lists reload with it.
      }
    };
    const first = window.setTimeout(() => {
      void loadStatic();
      void pollMarkets();
    }, 0);
    const id = window.setInterval(() => void pollMarkets(), POLL_MS);
    const tid = window.setInterval(() => void loadStatic(), 15_000);
    return () => {
      live = false;
      window.clearTimeout(first);
      window.clearInterval(id);
      window.clearInterval(tid);
    };
  }, [mock, pollMarkets]);

  useEffect(() => {
    // Why: the 15 s hotspot reload would otherwise pull the camera off the street a what-if is exploring.
    if (hotspots.length && !selected && !scenarioOpen) frame(hotspots);
  }, [hotspots, selected, scenarioOpen]);

  const applyResult = useCallback(
    (m: Market, res: Settled) => {
      setResults((r) => ({ ...r, [m.id]: { ...res, at: Date.now() } }));
      const isVoidResult = res.void || res.winner == null;
      const winHub = isVoidResult ? null : (m.options.find((o) => o.id === res.winner)?.hotspot ?? m.hotspots[0] ?? null);
      if (winHub) {
        setGlow(winHub);
        window.setTimeout(() => setGlow((g) => (g === winHub ? null : g)), 6000);
      }
      setWallet((w) => {
        const bet = w.bets[m.id];
        if (!bet || bet.done || bet.pending) return w;
        const isVoid = res.void || res.winner == null;
        const won = !isVoid && bet.option === res.winner;
        const done: MyBet["done"] = isVoid ? "void" : won ? "won" : "lost";
        const next: Wallet = {
          ...w,
          points: mock ? w.points + (isVoid ? bet.stake : won ? bet.payout : 0) : w.points,
          streak: isVoid ? w.streak : won ? w.streak + 1 : 0,
          bets: { ...w.bets, [m.id]: { ...bet, done } },
        };
        const key = Date.now();
        setToast({
          key,
          good: won,
          text: isVoid ? `Refunded ${fmtPts(bet.stake)}` : won ? `+${fmtPts(bet.payout)}` : `−${fmtPts(bet.stake)}`,
          sub: plainSettle(m, res, isVoid ? "void" : won ? "won" : "lost", fmtPts(isVoid || !won ? bet.stake : bet.payout)),
        });
        window.setTimeout(() => setToast((t) => (t?.key === key ? null : t)), 4500);
        if (winHub) setPop({ key, hotspot: winHub, text: won ? `+${fmtPts(bet.payout)}` : `−${fmtPts(bet.stake)}`, good: won });
        return next;
      });
      if (!mock) void refreshWallet();
    },
    [mock, refreshWallet],
  );

  const due = useMemo(
    () =>
      Object.values(book.markets).filter(
        (m) => !results[m.id] && (now >= m.settlesAt || m.status === "settled" || m.status === "void"),
      ),
    [now, results, book],
  );

  useEffect(() => {
    for (const m of due) {
      if (settling.current.has(m.id)) continue;
      settling.current.add(m.id);
      const attempt = async () => {
        let reachable = false;
        try {
          const r = await call<SettleResponse>(mock, "settle", { id: m.id });
          reachable = r.ok;
          if (r.ok && r.data.settled) {
            applyResult(m, r.data);
            return;
          }
        } catch {
          // Network blip: retry below until the give-up deadline.
        }
        if (!reachable && Date.now() > m.settlesAt + GIVE_UP_MS) {
          applyResult(m, { settled: true, winner: null, reading: "No data — void", aiResults: [], void: true, localVoid: true });
          return;
        }
        window.setTimeout(attempt, SETTLE_POLL_MS);
      };
      void attempt();
    }
  }, [due, mock, applyResult]);

  const shown = useMemo(() => {
    const live = new Set(feed?.markets.map((m) => m.id) ?? []);
    return Object.values(book.markets)
      .filter((m) => {
        const r = results[m.id];
        if (r) return now - r.at < SHOW_SETTLED_MS;
        // Why: local markets open on the map card; the list shows one only once you hold a bet on it.
        return (live.has(m.id) && !m.id.startsWith("local_")) || (wallet.bets[m.id] && !wallet.bets[m.id].done);
      })
      .sort((a, b) => rank(a) - rank(b) || a.closesAt - b.closesAt);

    // Why: your own result is the payoff, so it leads; then what you can still bet on, then markets already locked.
    function rank(m: Market) {
      if (results[m.id]) return wallet.bets[m.id] ? 0 : 3;
      if (wallet.bets[m.id] || now < m.closesAt) return 1;
      return 2;
    }
  }, [book, feed, results, now, wallet.bets]);

  const selectedMarket = shown.find((m) => m.id === selected) ?? null;
  const marketOf = useCallback((id: string) => book.markets[id], [book]);
  const addLocal = useCallback((m: Market) => setBook((b) => absorb(b, [m])), []);
  const selectedHubs = selected ? (book.markets[selected]?.hotspots.join(",") ?? "") : "";
  useEffect(() => {
    const hubs = selectedHubs
      .split(",")
      .map((h) => hubMap.get(h))
      .filter((h): h is Hotspot => !!h);
    if (hubs.length) frame(hubs);
  }, [selectedHubs, hubMap]);

  const placeBet = useCallback(
    async (w: Wallet) => {
      if (!draft) return;
      const m = book.markets[draft.market];
      const opt = m?.options.find((o) => o.id === draft.option);
      if (!m || !opt) return;
      const amount = Math.round(stake === "all" ? w.points : Math.min(stake, w.points));
      if (amount <= 0) return;
      if (!mock && !w.token) {
        setBetError("Setting up your player. Try again in a moment.");
        return;
      }
      setBetError(null);
      try {
        const body = { player: w.player, market: m.id, option: opt.id, stake: amount, idempotency_key: crypto.randomUUID() };
        let r = await call<BetResponse>(mock, "bet", body, w.token);
        if (r.data.code === "confirm_required") {
          if (!(await askStake(r.data.error ?? "Confirm this stake."))) return;
          r = await call<BetResponse>(mock, "bet", { ...body, confirm_token: r.data.confirmToken }, w.token);
        }
        if (r.status === 401) {
          dropToken(w.token);
          setBetError("Your player was not recognised. A new one is being set up.");
          return;
        }
        if (!r.ok || !r.data.ok) {
          setBetError(r.data.error ?? "Bet rejected");
          return;
        }
        setBetNote(null);
        if (r.data.status === "pending" && r.data.betId && r.data.fillsAt) {
          const pending = { betId: r.data.betId, fillsAt: r.data.fillsAt };
          const bet: MyBet = { market: m.id, option: opt.id, label: opt.label, stake: amount, payout: 0, pending };
          setWallet((cur) => ({ ...cur, bets: { ...cur.bets, [m.id]: bet } }));
          setDraft(null);
          return;
        }
        const odds = r.data.odds ?? opt.odds;
        const payout = r.data.payout ?? Math.floor(amount * odds);
        if (r.data.options) {
          const updated: Market = {
            ...m,
            options: m.options.map((o) => {
              const u = r.data.options?.find((x) => x.id === o.id);
              return u ? { ...o, prob: u.prob, odds: u.odds } : o;
            }),
          };
          setBook((b) => absorb(b, [updated]));
        }
        setWallet((cur) => ({
          ...cur,
          points: r.data.balance ?? cur.points - amount,
          bets: { ...cur.bets, [m.id]: { market: m.id, option: opt.id, label: opt.label, stake: amount, payout } },
        }));
        setDraft(null);
      } catch {
        setBetError("Could not reach the exchange");
      }
    },
    [draft, stake, mock, book, askStake, dropToken],
  );

  const onFill = useCallback(
    (market: string, fill: Fill) => {
      setWallet((cur) => {
        const bet = cur.bets[market];
        if (!bet?.pending) return cur;
        const filled = { ...bet, payout: fill.payout, pending: undefined };
        return { ...cur, points: mock ? cur.points - bet.stake : cur.points, bets: { ...cur.bets, [market]: filled } };
      });
      if (!mock) void refreshWallet();
    },
    [mock, refreshWallet],
  );
  const onCancelled = useCallback(
    (market: string, reason: string | undefined) => {
      const bet = wallet.bets[market];
      if (!bet?.pending) return;
      setWallet((cur) => {
        const { [market]: _gone, ...bets } = cur.bets;
        void _gone;
        return { ...cur, bets };
      });
      setDraft({ market, option: bet.option });
      setBetNote(cancelNote(reason));
    },
    [wallet.bets],
  );
  usePendingFills(wallet.bets, wallet.player, wallet.token, mock, onFill, onCancelled);

  const confirm = () => {
    if (!wallet.name) {
      setNameInput(defaultName(wallet.player));
      setAskName(true);
      return;
    }
    void placeBet(wallet);
  };

  const saveName = () => {
    const name = nameInput.trim().slice(0, 18) || defaultName(wallet.player);
    const w = { ...wallet, name };
    setWallet(w);
    setAskName(false);
    if (!mock && w.token) void setNickname(w.player, w.token, name);
    void placeBet(w);
  };

  useEffect(() => {
    if (!boardOpen) return;
    let live = true;
    const load = () =>
      call<{ rows: LeaderRow[] }>(mock, "leaderboard")
        .then((r) => live && r.ok && setBoard(r.data.rows))
        .catch(() => undefined);
    void load();
    const id = window.setInterval(load, 5000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [boardOpen, mock]);

  const health = healthOf(feed?.feeds, offline);
  const liveMode = feed?.live ?? false;
  const clockMs = liveMode ? now : (feed?.asOf ?? now);
  const openBets = Object.values(wallet.bets).filter((b) => !b.done).length;
  const broke = wallet.points < 1 && openBets === 0;
  const active = selectedMarket?.hotspots ?? [];
  const lastWhy = useMemo(() => {
    const out = new Map<string, string>();
    for (const m of feed?.markets ?? []) for (const b of m.aiBets) out.set(b.trader, b.why);
    return out;
  }, [feed]);

  const hasStrip = !!(signals?.hubs?.hubs.length || signals?.lines || signals?.football);

  const tipTrader = tip ? traderMap.get(tip.trader) : null;
  const tipRect = tip?.el.getBoundingClientRect();

  const boardRows = useMemo(() => {
    const myName = wallet.name ?? defaultName(wallet.player);
    const rows = (board ?? []).filter((r) => !(r.kind === "human" && r.name === myName));
    rows.push({ name: myName, points: server?.total ?? wallet.points, kind: "human" });
    return rows.sort((a, b) => b.points - a.points);
  }, [board, wallet, server]);

  return (
    <div className="tl" data-strip={hasStrip || undefined} data-scenario={scenarioOpen || undefined} onClick={() => setTip(null)}>
      <div className="tl-world">
        <CityMapGL hotspots={hotspots} active={active} winner={glow} pop={pop} vsUsual={signals?.hubs ?? null} />
      </div>

      <header className="tl-bar">
        <div className="tl-brand">
          <span className="tl-title">
            <span className="tl-mark" aria-hidden />
            OverWatch
          </span>
          <span className="tl-live" data-live={liveMode || undefined}>
            <span className="tl-live-dot" />
            {liveMode ? "Live" : "Replay"}
            <span className="tl-clock">
              <span className="tl-data">{londonClock(clockMs)}</span>
              <span className="tl-desk"> London</span>
            </span>
          </span>
        </div>
        <div className="tl-wallet tl-num" aria-label="Your points">
          <span className="tl-pts">
            <b className="tl-data">{fmtPts(wallet.points)}</b> pts
          </span>
          <span className="tl-streak" data-hot={wallet.streak > 0 || undefined} aria-label={`Win streak ${wallet.streak}`}>
            <Flame size={14} strokeWidth={2.5} />
            <span className="tl-data">{wallet.streak}</span>
          </span>
          {broke && mock && (
            <button type="button" className="tl-refill" onClick={() => setWallet((w) => ({ ...w, points: START_POINTS, streak: 0 }))}>
              Refill
            </button>
          )}
        </div>
        <div className="tl-bar-right">
          <HowItWorks />
          <button type="button" className="tl-board-btn tl-whatif" aria-pressed={scenarioOpen} onClick={() => setScenarioOpen((o) => !o)}>
            What if…
          </button>
          <button
            type="button"
            className="tl-health"
            data-h={health}
            aria-label={`Data health: ${health}`}
            aria-expanded={healthOpen}
            onClick={(e) => {
              e.stopPropagation();
              setHealthOpen((o) => !o);
            }}
          >
            <span className="tl-health-dot" />
            <span className="tl-desk">Data</span>
          </button>
          <button type="button" className="wl-open" onClick={() => setWalletOpen(true)}>
            <WalletIcon size={16} />
            <span className="tl-desk">Wallet</span>
          </button>
          <button type="button" className="tl-board-btn" onClick={() => setBoardOpen(true)}>
            <Trophy size={16} />
            <span className="tl-desk">Leaderboard</span>
          </button>
        </div>
        {healthOpen && (
          <div className="tl-pop tl-health-list" onClick={(e) => e.stopPropagation()}>
            <b>Live feeds{mock ? ", demo data" : ""}</b>
            {offline && <span className="tl-bad">Exchange unreachable</span>}
            {Object.entries(feed?.feeds ?? {}).map(([id, f]) => (
              <span key={id} className="tl-feed" data-ok={f.ok || undefined}>
                <span className="tl-health-dot" />
                {FEED_NAMES[id] ?? id}
                <span className="tl-muted tl-data">{f.ageS == null ? "no data" : f.ageS < 90 ? `${f.ageS}s ago` : `${Math.round(f.ageS / 60)} min ago`}</span>
              </span>
            ))}
          </div>
        )}
      </header>

      <CityStrip signals={signals} />

      <aside className="tl-ai">
        <div className="tl-ai-head">
          <b>{traders.length || 6} computer traders.</b> Each one watches one live London data feed and bets against you.
        </div>
        <div className="tl-ai-list">
          {traders.map((t) => (
            <button
              key={t.id}
              type="button"
              className="tl-trader"
              onMouseEnter={(e) => setTip({ trader: t.id, why: lastWhy.get(t.id), el: e.currentTarget })}
              onMouseLeave={() => setTip(null)}
              onClick={(e) => {
                e.stopPropagation();
                setTip({ trader: t.id, why: lastWhy.get(t.id), el: e.currentTarget });
              }}
            >
              <Badge id={t.id} name={t.name} />
              <span className="tl-trader-text">
                <b>{t.name}</b>
                <span className="tl-muted">{plainWatches(t.id, t.watches)}</span>
              </span>
              <span className="tl-rec">
                <span>{recordText(t.record.won, t.record.lost)}</span>
                <span>
                  <span className="tl-data">{fmtPts(t.points)}</span> pts
                </span>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="tl-markets" aria-live="polite">
        <div className="tl-intro">
          <h1>Bet on what London does next.</h1>
          {mock && <span className="tl-demo">Demo data</span>}
        </div>
        {betError && (
          <div className="tl-error" onClick={() => setBetError(null)}>
            {betError}
          </div>
        )}
        {betNote && (
          <div className="tl-error" data-quiet onClick={() => setBetNote(null)}>
            {betNote}
          </div>
        )}
        {shown.length === 0 && <div className="tl-market tl-empty">Opening markets…</div>}
        {shown.map((m) => (
          <MarketCard
            key={m.id}
            market={m}
            now={now}
            traders={traderMap}
            bet={wallet.bets[m.id]}
            result={results[m.id]}
            selected={selected === m.id}
            draft={draft?.market === m.id ? draft.option : null}
            stake={stake}
            points={wallet.points}
            history={book.hist[m.id] ?? NO_HISTORY}
            onSelect={() => setSelected((s) => (s === m.id ? null : m.id))}
            onPick={(option) => {
              if (phaseOf(m, Date.now(), results[m.id]) !== "open") return;
              setSelected(m.id);
              setDraft({ market: m.id, option });
              setBetError(null);
            }}
            onStake={setStake}
            onConfirm={confirm}
            onCancel={() => setDraft(null)}
            onTip={setTip}
            whatIf={scenarioOpen ? whatIf?.[m.id] : undefined}
          />
        ))}
      </section>

      <LocalMarket
        mock={mock}
        player={wallet.player}
        token={wallet.token}
        disabled={scenarioOpen}
        error={betError}
        marketOf={marketOf}
        onMarket={addLocal}
        card={(m) => (
          <MarketCard
            market={m}
            now={now}
            traders={traderMap}
            bet={wallet.bets[m.id]}
            result={results[m.id]}
            selected
            draft={draft?.market === m.id ? draft.option : null}
            stake={stake}
            points={wallet.points}
            history={book.hist[m.id] ?? NO_HISTORY}
            onSelect={() => undefined}
            onPick={(option) => {
              if (phaseOf(m, Date.now(), results[m.id]) !== "open") return;
              setDraft({ market: m.id, option });
              setBetError(null);
            }}
            onStake={setStake}
            onConfirm={confirm}
            onCancel={() => setDraft(null)}
            onTip={setTip}
          />
        )}
      />

      {tip && tipTrader && tipRect && (
        <div
          className="tl-tip"
          style={{
            left: Math.max(8, Math.min(window.innerWidth - 288, tipRect.left)),
            top: tipRect.bottom + 6 + 150 > window.innerHeight ? undefined : tipRect.bottom + 6,
            bottom: tipRect.bottom + 6 + 150 > window.innerHeight ? window.innerHeight - tipRect.top + 6 : undefined,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="tl-tip-head">
            <Badge id={tipTrader.id} name={tipTrader.name} />
            <b>{tipTrader.name}</b>
            <span className="tl-rec">{recordText(tipTrader.record.won, tipTrader.record.lost)}</span>
          </div>
          <div>
            <span className="tl-muted">Reads</span> {tipTrader.watches}
          </div>
          {tip.why && <q>{tip.why}</q>}
          <div className="tl-muted">{tipTrader.style}</div>
        </div>
      )}

      {scenarioOpen && <Scenario hotspots={hotspots} markets={feed?.markets ?? []} now={now} onOdds={setWhatIf} onClose={() => setScenarioOpen(false)} />}

      <WalletPanel player={wallet.player} token={wallet.token} wallet={server} open={walletOpen} onClose={() => setWalletOpen(false)} refresh={() => void refreshWallet()} />
      {stakeDialog}

      {toast && (
        <div key={toast.key} className="tl-toast" data-good={toast.good || undefined}>
          <span className="tl-toast-big tl-num">{toast.text}</span>
          <span className="tl-toast-sub">{toast.sub}</span>
        </div>
      )}

      {askName && (
        <div className="tl-modal-bg" onClick={() => setAskName(false)}>
          <form
            className="tl-modal"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              saveName();
            }}
          >
            <h2>Your trader name</h2>
            <p className="tl-muted">Shown on the leaderboard next to the computer traders.</p>
            <input value={nameInput} onChange={(e) => setNameInput(e.target.value)} maxLength={18} autoFocus aria-label="Trader name" />
            <button type="submit" className="tl-confirm">
              Place my bet
            </button>
          </form>
        </div>
      )}

      {boardOpen && (
        <div className="tl-modal-bg" onClick={() => setBoardOpen(false)}>
          <div className="tl-modal tl-board" onClick={(e) => e.stopPropagation()}>
            <div className="tl-board-head">
              <h2>Leaderboard</h2>
              <button type="button" className="tl-stake-x" aria-label="Close" onClick={() => setBoardOpen(false)}>
                <X size={18} />
              </button>
            </div>
            <ol>
              {boardRows.map((r, i) => {
                const t = r.kind === "ai" ? traders.find((x) => x.name === r.name) : undefined;
                const me = r.kind === "human" && r.name === (wallet.name ?? defaultName(wallet.player));
                return (
                  <li key={`${r.kind}-${r.name}`} data-me={me || undefined} data-kind={r.kind}>
                    <span className="tl-rank tl-data">{i + 1}</span>
                    <Badge id={t?.id ?? r.name} name={r.name} kind={r.kind === "ai" ? "ai" : me ? "me" : "human"} />
                    <span className="tl-board-name">
                      {r.name}
                      {me && " (you)"}
                    </span>
                    <span className="tl-kind">{r.kind === "ai" ? "Computer trader" : "Player"}</span>
                    <b className="tl-data">{fmtPts(r.points)}</b>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
