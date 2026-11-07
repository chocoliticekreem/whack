"use client";

import { Bus, Check, Cloud, Footprints, House, PersonStanding, CloudRain, Sun, Users, X, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { snapOrigin, setCurrentOrigin, toLocal } from "@/twin/config";
import { useOrigin } from "@/twin/origin";
import { useCameraStore } from "@/twin/scene/cameraStore";
import { World } from "@/twin/World";
import { Futures3D, OXFORD_CIRCUS, type Reveal } from "./Futures3D";
import {
  aiPick,
  binMass,
  ctxOf,
  evidence,
  pct,
  pickQuestion,
  shortCam,
  surprise,
  uncertaintyWord,
  winningBin,
  type AiPick,
  type IconName,
  type Question,
} from "./futures";
import type { Geometry, MultiverseState } from "./types";

const ICONS: Record<IconName, LucideIcon> = {
  bus: Bus,
  users: Users,
  rain: CloudRain,
  sun: Sun,
  cloud: Cloud,
  home: House,
  walk: Footprints,
  run: PersonStanding,
};

const N_FUTURES = 50;
const REVEAL_S = 9;
const SHATTER_ZONES = 3;
// Why: east of Oxford Circus so the loaded OSM rectangle covers Bond Street to Soho Square.
const TWIN_ORIGIN = snapOrigin(OXFORD_CIRCUS.lat, OXFORD_CIRCUS.lon + 0.0036);

function roundSeconds(): number {
  const v = Number(new URLSearchParams(window.location.search).get("round"));
  return Number.isFinite(v) && v >= 3 ? v : 12;
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/multiverse/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
  return data as T;
}

type Score = { you: number; ai: number };
const SCORE_KEY = "which-london-score";

const gameIdOf = (s: MultiverseState) => `${s.seedAt}:${s.spawnMs}`;

function loadScore(id: string): Score {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SCORE_KEY) ?? "null");
    if (saved?.id === id) return { you: Number(saved.you) || 0, ai: Number(saved.ai) || 0 };
  } catch {
    // Private mode or blocked storage: the score just starts at zero.
  }
  return { you: 0, ai: 0 };
}

function saveScore(id: string, score: Score) {
  try {
    window.localStorage.setItem(SCORE_KEY, JSON.stringify({ id, ...score }));
  } catch {
    // Private mode or blocked storage: the score lives only in this tab.
  }
}

const firstSentence = (text: string) => text.split(/(?<=[.!?])\s+/)[0];

const framesLeft =(s: MultiverseState) => s.replay.frames - s.replay.cursor;

interface Round {
  n: number;
  q: Question;
  mass: number[];
  ai: AiPick;
  before: MultiverseState;
  titles: string[];
}

interface Result {
  winner: number;
  after: number[];
  you: boolean;
  ai: boolean;
  why: string;
}

type Phase = "loading" | "pick" | "countdown" | "reveal" | "over" | "error";

// Why: this module only loads in the browser (ssr: false), so the twin is re-centred before World first renders.
setCurrentOrigin(TWIN_ORIGIN);
useOrigin.setState({ origin: TWIN_ORIGIN });

const clockMs = () => Date.now();

function useTwinFraming() {
  useEffect(() => {
    const [x, z] = toLocal(OXFORD_CIRCUS.lat, OXFORD_CIRCUS.lon);
    const tx = x + 60;
    useCameraStore.getState().setCinematic({ pos: [tx + 100, 820, z + 720], target: [tx, 0, z + 90] });
  }, []);
}

function shatteredZones(prev: MultiverseState, next: MultiverseState): string[] {
  const after = new Map(next.consensusHorizon.map((s) => [s.zoneId, s.sd]));
  return prev.consensusHorizon
    .filter((s) => s.mean > 1)
    .map((s) => ({ id: s.zoneId, drop: (s.sd - (after.get(s.zoneId) ?? s.sd)) / Math.max(s.sd, 1) }))
    .filter((d) => d.drop > 0.02)
    .sort((a, b) => b.drop - a.drop)
    .slice(0, SHATTER_ZONES)
    .map((d) => d.id);
}

export function Game() {
  useTwinFraming();
  const [state, setState] = useState<MultiverseState | null>(null);
  const [cams, setCams] = useState<Geometry["cams"]>([]);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState<Round | null>(null);
  const [pick, setPick] = useState<number | null>(null);
  const [deadline, setDeadline] = useState(0);
  const [now, setNow] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [score, setScore] = useState<Score>({ you: 0, ai: 0 });
  const [details, setDetails] = useState(false);
  const [intro, setIntro] = useState(false);
  const pending = useRef<Promise<MultiverseState> | null>(null);
  const answered = useRef<MultiverseState | null>(null);
  const gameId = useRef("");

  useEffect(() => {
    if (gameId.current) saveScore(gameId.current, score);
  }, [score]);

  const startRound = useCallback((s: MultiverseState, previous: string | null) => {
    const q = pickQuestion(s, previous);
    const mass = binMass(s, q);
    const c = ctxOf(s);
    const n = s.replay.cursor + 1;
    setRound({ n, q, mass, ai: aiPick(s, q, mass), before: s, titles: q.bins.map((b) => b.title(c)) });
    setPick(null);
    setResult(null);
    setReveal((r) => r && { ...r, label: null });
    setPhase("pick");
  }, []);

  const newGame = useCallback(async () => {
    setPhase("loading");
    setScore({ you: 0, ai: 0 });
    setReveal(null);
    try {
      const s = await api<MultiverseState>("spawn", { n: N_FUTURES, mode: "replay", brokers: "on" });
      const g = await api<Geometry>("geometry");
      gameId.current = gameIdOf(s);
      setCams(g.cams);
      setState(s);
      startRound(s, null);
    } catch (e) {
      setError((e as Error).message);
      setPhase("error");
    }
  }, [startRound]);

  useEffect(() => {
    let live = true;
    api<MultiverseState>("state")
      .then(async (s) => {
        if (!live) return;
        if (s.mode !== "replay" || framesLeft(s) < 3) {
          await newGame();
          return;
        }
        const g = await api<Geometry>("geometry");
        gameId.current = gameIdOf(s);
        setScore(loadScore(gameId.current));
        setCams(g.cams);
        setState(s);
        startRound(s, null);
      })
      .catch(() => {
        if (live) void newGame();
      });
    return () => {
      live = false;
    };
  }, [newGame, startRound]);

  const choose = (i: number) => {
    if (phase !== "pick") return;
    setPick(i);
    setDeadline(clockMs() + roundSeconds() * 1000);
    setNow(clockMs());
    setPhase("countdown");
    answered.current = null;
    const before = round?.before.replay.cursor ?? -1;
    // Why: several phones share one engine; whoever picks first moves it a minute and the rest reuse that minute.
    pending.current = api<MultiverseState>("state").then((s) =>
      s.replay.cursor > before ? s : api<MultiverseState>("observe", { next: true }),
    );
    pending.current.then(
      (s) => (answered.current = s),
      (e: Error) => {
        setError(e.message);
        setPhase("error");
      },
    );
  };

  useEffect(() => {
    if (phase !== "countdown" || !round || pick == null) return;
    const id = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      const next = answered.current;
      if (t < deadline || !next) return;
      window.clearInterval(id);
      const after = binMass(next, round.q);
      const winner = winningBin(next, round.q, after);
      const obs = next.observations[next.observations.length - 1];
      const cam = obs ? surprise(round.before, obs) : null;
      const you = pick === winner;
      const ai = round.ai.bin === winner;
      setScore((sc) => ({ you: sc.you + (you ? 1 : 0), ai: sc.ai + (ai ? 1 : 0) }));
      setResult({ winner, after, you, ai, why: evidence(round.before, next, round.q) });
      setReveal({
        key: Date.now(),
        camId: cam?.camId ?? null,
        label: cam ? `${shortCam(cam.name)}: ${Math.round(cam.observed)} people` : null,
        shattered: shatteredZones(round.before, next),
      });
      setState(next);
      setPhase("reveal");
    }, 200);
    return () => window.clearInterval(id);
  }, [phase, round, pick, deadline]);

  const advance = useCallback(() => {
    if (!state || !round) return;
    if (state.done || framesLeft(state) <= 0) setPhase("over");
    else startRound(state, round.q.id);
  }, [state, round, startRound]);

  useEffect(() => {
    if (phase !== "reveal") return;
    const id = window.setTimeout(advance, REVEAL_S * 1000);
    return () => window.clearTimeout(id);
  }, [phase, advance]);

  const left = Math.max(0, Math.ceil((deadline - now) / 1000));
  const totalRounds = state ? state.replay.frames : 0;

  return (
    <div className="mvg">
      <div className="mvg-world">
        <World bare>{state && <Futures3D state={state} cams={cams} reveal={reveal} />}</World>
      </div>

      <div className="mvg-top">
        <header className="mvg-card mvg-title" data-open={intro || undefined} onClick={() => setIntro(false)}>
          <h1>Which London is real?</h1>
          <p>
            We simulate {state?.n ?? N_FUTURES} possible futures of Oxford Circus, 5 minutes ahead. Every minute, real
            London data rules out the wrong ones.
          </p>
        </header>

        {round && phase !== "loading" && (
          <div className="mvg-score mvg-num" aria-label="Score">
            <span className="mvg-round">
              Round {round.n} of {totalRounds}
            </span>
            <span className="mvg-dot mvg-round" />
            <span>
              You <b>{score.you}</b>
            </span>
            <span className="mvg-dot" />
            <span>
              AI<span className="mvg-desk-only"> forecasters</span> <b>{score.ai}</b>
            </span>
          </div>
        )}

        {state && (
          <aside className="mvg-card mvg-meter" aria-label="City uncertainty">
            <span className="mvg-muted">City uncertainty</span>
            <span className="mvg-meter-row">
              <strong className="mvg-num">{state.vix == null ? "–" : state.vix.toFixed(1)}</strong>
              <span className="mvg-accent">{uncertaintyWord(state)}</span>
            </span>
            <span className="mvg-caption">How much the futures disagree · London VIX</span>
            <span className="mvg-caption">Columns on the map: crowd in 5 min, pinker where the futures disagree</span>
          </aside>
        )}

        <button
          type="button"
          className="mvg-help"
          onClick={() => setIntro((o) => !o)}
          aria-expanded={intro}
          aria-label="What is this game?"
        >
          ?
        </button>
      </div>

      <section className="mvg-card mvg-panel" aria-live="polite">
        {phase === "loading" && (
          <div className="mvg-center">
            <h2>Building {N_FUTURES} futures of Oxford Circus…</h2>
            <p className="mvg-muted">Each one is a full simulation of the next 5 minutes. About 20 seconds.</p>
          </div>
        )}
        {phase === "error" && (
          <div className="mvg-center">
            <h2>The simulator is offline</h2>
            <p className="mvg-muted">{error}</p>
            <button type="button" className="mvg-btn" onClick={newGame}>
              Try again
            </button>
          </div>
        )}
        {round && (phase === "pick" || phase === "countdown" || phase === "reveal") && (
          <>
            <div className="mvg-head">
              {phase === "pick" && (
                <>
                  <h2>
                    <span className="mvg-desk-only">Which future happens next? Pick one.</span>
                    <span className="mvg-phone-only">{round.q.ask}</span>
                  </h2>
                  <p className="mvg-muted mvg-desk-only">
                    {round.q.ask} You are playing against {round.ai.name}. Real London decides.
                  </p>
                </>
              )}
              {phase === "countdown" && (
                <>
                  <h2>
                    Real London answers in <span className="mvg-num">0:{String(left).padStart(2, "0")}</span>
                  </h2>
                  <p className="mvg-muted mvg-desk-only">
                    {round.ai.name} picked “{round.titles[round.ai.bin]}”. Waiting for the next minute of camera and bus data.
                  </p>
                </>
              )}
              {phase === "reveal" && result && (
                <>
                  <h2 className={result.you ? "mvg-win" : undefined}>
                    {result.you ? <Check size={22} strokeWidth={2.5} /> : <X size={22} strokeWidth={2.5} />}
                    {result.you ? (
                      "You called it"
                    ) : (
                      <span>
                        Not this time<span className="mvg-desk-only">: {round.titles[result.winner].toLowerCase()}</span>
                      </span>
                    )}
                  </h2>
                  <p className="mvg-muted mvg-desk-only">
                    {result.why} {round.ai.name} {result.ai ? "got it right" : "got it wrong"}.
                  </p>
                  <p className="mvg-muted mvg-phone-only mvg-evidence">{firstSentence(result.why)}</p>
                </>
              )}
            </div>
            <div className="mvg-cards">
              {round.q.bins.map((b, i) => {
                const Icon = ICONS[b.icon];
                const m = phase === "reveal" && result ? result.after[i] : round.mass[i];
                const look =
                  phase === "reveal" && result ? (i === result.winner ? "won" : "lost") : pick === i ? "picked" : "idle";
                return (
                  <button
                    key={b.id}
                    type="button"
                    className="mvg-future"
                    data-state={look}
                    disabled={phase !== "pick"}
                    onClick={() => choose(i)}
                  >
                    <Icon size={22} strokeWidth={1.8} className="mvg-icon" />
                    <span className="mvg-future-title">{round.titles[i]}</span>
                    <span className="mvg-muted mvg-num">
                      {phase === "reveal" && <span className="mvg-desk-only">now </span>}
                      {pct(m)}
                      <span className="mvg-desk-only"> likely</span>
                    </span>
                    <span className="mvg-tags">
                      {pick === i && <span className="mvg-tag-you">You</span>}
                      {phase !== "pick" && round.ai.bin === i && <span className="mvg-tag-ai">AI</span>}
                      {phase === "reveal" && result?.winner === i && <span className="mvg-tag-real">{round?.q.id === "bus" ? "What happened" : "Data says"}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
            {phase === "reveal" && (
              <div className="mvg-foot">
                <span className="mvg-muted mvg-desk-only">Next round starts in a few seconds</span>
                <button type="button" className="mvg-btn" onClick={advance}>
                  Next round
                </button>
              </div>
            )}
          </>
        )}
        {phase === "over" && (
          <div className="mvg-center">
            <h2>
              Final score: You {score.you} · AI forecasters {score.ai}
            </h2>
            <p className="mvg-muted">{score.you >= score.ai ? "You read London better than the AI." : "The AI read London better this time."}</p>
            <button type="button" className="mvg-btn" onClick={newGame}>
              Play again
            </button>
          </div>
        )}
      </section>

      <button type="button" className="mvg-details" onClick={() => setDetails((d) => !d)} aria-expanded={details}>
        {details ? "Close details" : "Details"}
      </button>
      {details && (
        <div className="mvg-drawer">
          <iframe src="/multiverse/board" title="All futures and forecasters" />
        </div>
      )}
    </div>
  );
}
