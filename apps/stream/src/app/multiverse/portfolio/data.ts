"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fmtPts, londonClock, loadWallet } from "../city-store";
import type { ServerWallet } from "../Wallet";

/** Shapes served by services/city/ledger.py: GET /positions /history /ledger /versus. */
export interface Position {
  betId: string;
  market: string;
  question: string;
  option: string;
  label: string;
  stake: number;
  odds: number;
  toWin: number;
  profit: number;
  placedAt: number;
  settlesAt: number;
}

export interface Settled {
  betId: string;
  market: string;
  question: string;
  option: string;
  label: string;
  stake: number;
  odds: number;
  payout: number;
  result: "won" | "lost" | "void";
  pnl: number;
  reading: string;
  placedAt: number;
  settledAt: number;
}

export interface Entry {
  id: number;
  ts: number;
  account: "player_cash" | "player_escrow" | "house";
  amount: number;
  kind: string;
  market: string | null;
  betId: string | null;
  idempotencyKey: string;
}

export interface Versus {
  trader: string;
  markets: number;
  won: number;
  lost: number;
  tied: number;
  pnl: number;
  theirPnl: number;
}

interface LiveMarket {
  id: string;
  series: string;
  source: string;
  status: string;
  settlesAt: number;
  options: { id: string; prob: number; odds: number }[];
}

export interface Portfolio {
  wallet: ServerWallet;
  positions: Position[];
  history: Settled[];
  series: [number, number][];
  entries: Entry[];
  versus: Versus[] | null;
  traders: Record<string, string>;
  markets: Record<string, LiveMarket>;
  sources: Record<string, string>;
}

export type State =
  | { kind: "loading" }
  | { kind: "no-player" }
  | { kind: "offline"; error: string }
  | { kind: "ready"; data: Portfolio };

const WALLET_MS = 5000;
const MARKETS_MS = 10_000;
export const HISTORY_LIMIT = 500;

async function get<T>(path: string, token: string | null): Promise<{ status: number; data: T | null }> {
  try {
    const res = await fetch(`/api/city/${path}`, { cache: "no-store", headers: token ? { "x-player-token": token } : {} });
    return { status: res.status, data: res.ok ? ((await res.json()) as T) : null };
  } catch {
    return { status: 0, data: null };
  }
}

/** A market id is `<series>-<HHMM>`. */
export const seriesOf = (market: string) => market.replace(/-\d{4}$/, "");

/** "TfL Crowding API · 20 stations · settles 20:45:00 Europe/London" -> the part that names the data. */
const sourceName = (source: string) => source.split(" · settles")[0];

/** Polls the wallet; reloads bets and the ledger only when the wallet moves, and live prices on their own clock. */
export function usePortfolio(): State {
  const [state, setState] = useState<State>({ kind: "loading" });
  const id = useRef<{ player: string; token: string | null } | null>(null);
  const lastKey = useRef("");
  const rest = useRef<Omit<Portfolio, "wallet" | "markets" | "sources"> | null>(null);
  const live = useRef<Pick<Portfolio, "markets" | "sources">>({ markets: {}, sources: {} });
  const wallet = useRef<ServerWallet | null>(null);

  const publish = useCallback(() => {
    if (wallet.current && rest.current) setState({ kind: "ready", data: { wallet: wallet.current, ...rest.current, ...live.current } });
  }, []);

  const loadMarkets = useCallback(async () => {
    const m = await get<{ markets: LiveMarket[] }>("markets", null);
    if (m.data) {
      const sources = { ...live.current.sources };
      for (const x of m.data.markets) sources[x.series] = sourceName(x.source);
      live.current = { markets: Object.fromEntries(m.data.markets.map((x) => [x.id, x])), sources };
    }
    publish();
  }, [publish]);

  const loadRest = useCallback(
    async (player: string, token: string | null) => {
      const q = `player=${encodeURIComponent(player)}`;
      const [p, h, l, v, t] = await Promise.all([
        get<{ positions: Position[] }>(`positions?${q}`, token),
        get<{ history: Settled[]; series: [number, number][] }>(`history?${q}&limit=${HISTORY_LIMIT}`, token),
        get<{ entries: Entry[] }>(`ledger?${q}&limit=500`, token),
        get<{ versus: Versus[] }>(`versus?${q}`, token),
        rest.current && Object.keys(rest.current.traders).length ? Promise.resolve(null) : get<{ traders: { id: string; name: string }[] }>("traders", null),
      ]);
      const prev = rest.current;
      rest.current = {
        positions: p.data?.positions ?? prev?.positions ?? [],
        history: h.data?.history ?? prev?.history ?? [],
        series: h.data?.series ?? prev?.series ?? [],
        entries: l.data?.entries ?? prev?.entries ?? [],
        versus: v.data?.versus ?? prev?.versus ?? null,
        traders: t?.data ? Object.fromEntries(t.data.traders.map((x) => [x.id, x.name])) : (prev?.traders ?? {}),
      };
    },
    [],
  );

  const tick = useCallback(async () => {
    const me = id.current;
    if (!me) return;
    const r = await get<ServerWallet>(`wallet?player=${encodeURIComponent(me.player)}`, me.token);
    if (r.status === 401 || r.status === 404) return setState({ kind: "no-player" });
    if (!r.data) {
      if (!wallet.current) setState({ kind: "offline", error: r.status === 0 ? "Could not reach the exchange." : `The exchange answered ${r.status}.` });
      return;
    }
    wallet.current = r.data;
    const t = r.data.totals;
    const key = `${t.betsPlaced}:${t.betsSettled}:${r.data.cash}:${r.data.escrow}`;
    if (key !== lastKey.current) {
      lastKey.current = key;
      await loadRest(me.player, me.token);
    }
    publish();
  }, [loadRest, publish]);

  useEffect(() => {
    const w = loadWallet();
    if (!w.token) {
      const t = window.setTimeout(() => setState({ kind: "no-player" }), 0);
      return () => window.clearTimeout(t);
    }
    id.current = { player: w.player, token: w.token };
    const first = window.setTimeout(() => void tick().then(loadMarkets), 0);
    const a = window.setInterval(() => void tick(), WALLET_MS);
    const b = window.setInterval(() => void loadMarkets(), MARKETS_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(a);
      window.clearInterval(b);
    };
  }, [tick, loadMarkets]);

  return state;
}

export const signed = (n: number) => (n > 0 ? `+${fmtPts(n)}` : n < 0 ? `−${fmtPts(-n)}` : "0");
export const tone = (n: number) => (n > 0 ? "up" : n < 0 ? "down" : undefined);
export const pct = (n: number | null) => (n == null ? "–" : `${Math.round(n * 100)}%`);

export function when(ms: number, seconds = false) {
  const d = new Date(ms);
  const today = new Date().toDateString() === d.toDateString();
  const day = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Europe/London" });
  return today ? londonClock(ms, seconds) : `${day} ${londonClock(ms, seconds)}`;
}

export const iso = (ms: number) => new Date(ms).toISOString();

export function downloadCsv(name: string, header: string[], rows: (string | number)[][]) {
  const cell = (v: string | number) => {
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(cell).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
