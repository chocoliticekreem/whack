import type { SettleResponse } from "./city-types";

export const START_POINTS = 1000;
const KEY = "trade-london-v1";

export interface MyBet {
  market: string;
  option: string;
  label: string;
  stake: number;
  /** Points paid out if this option wins (stake × the odds locked at bet time). */
  payout: number;
  done?: "won" | "lost" | "void";
}

export interface Wallet {
  player: string;
  name: string | null;
  /** Mirrors the server ledger's cash (GET /wallet). Never persisted; only the demo mock counts it locally. */
  points: number;
  streak: number;
  bets: Record<string, MyBet>;
  seenIntro: boolean;
}

export type Settled = Extract<SettleResponse, { settled: true }> & { localVoid?: boolean };

export function freshWallet(): Wallet {
  const id = Math.random().toString(36).slice(2, 10);
  return { player: `p-${id}`, name: null, points: START_POINTS, streak: 0, bets: {}, seenIntro: false };
}

export function loadWallet(): Wallet {
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) ?? "null");
    if (raw && typeof raw.player === "string") return { ...freshWallet(), ...raw, points: START_POINTS };
  } catch {
    // Private mode or blocked storage: play with a fresh wallet this session.
  }
  return freshWallet();
}

export function saveWallet(w: Wallet) {
  try {
    const keep = Object.fromEntries(Object.entries(w.bets).slice(-40));
    const { points: _serverOwned, ...rest } = w;
    void _serverOwned;
    window.localStorage.setItem(KEY, JSON.stringify({ ...rest, bets: keep }));
  } catch {
    // Private mode or blocked storage: points live only in this tab.
  }
}

export const defaultName = (player: string) => `Player ${(parseInt(player.slice(2, 6), 36) % 900) + 100}`;

export const londonClock = (ms: number, seconds = false) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    timeZone: "Europe/London",
  }).format(ms);

export function countdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const fmtPts = (n: number) => Math.round(n).toLocaleString("en-GB");
