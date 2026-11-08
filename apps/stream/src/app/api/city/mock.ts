/**
 * Contract-shaped stand-in for the city markets backend, served at /api/city/*?mock=1 and used as the
 * demo fallback when the real engine is offline. Every number here is synthetic.
 */
import type {
  AiBet,
  Feed,
  Hotspot,
  Integrity,
  LeaderRow,
  Market,
  MarketOption,
  SettleResponse,
  Trader,
} from "@/app/multiverse/city-types";

const HOTSPOTS: Hotspot[] = [
  { id: "oxford-circus", name: "Oxford Circus", lat: 51.5152, lon: -0.1418, naptan: "940GZZLUOXC" },
  { id: "kings-cross", name: "King's Cross", lat: 51.5304, lon: -0.1238, naptan: "940GZZLUKSX" },
  { id: "piccadilly", name: "Piccadilly Circus", lat: 51.5098, lon: -0.1342, naptan: "940GZZLUPCC" },
  { id: "waterloo", name: "Waterloo", lat: 51.5033, lon: -0.1134, naptan: "940GZZLUWLO" },
  { id: "bank", name: "Bank", lat: 51.5133, lon: -0.0886, naptan: "940GZZLUBNK" },
];

const TRADERS: Trader[] = [
  { id: "tube", name: "Tube Sigma", kind: "AI hedge fund", emoji: "🚇", watches: "Live busyness at 20 Tube stations (TfL)", style: "Backs whichever station is running hottest against its usual.", record: { won: 3, lost: 2 }, points: 1180 },
  { id: "cams", name: "Camadel", kind: "AI hedge fund", emoji: "📷", watches: "People counted on TfL JamCams (YOLO)", style: "Counts heads on the street cameras and extrapolates.", record: { won: 4, lost: 3 }, points: 1090 },
  { id: "lines", name: "Line Watch", kind: "AI hedge fund", emoji: "🚦", watches: "Tube line status board (TfL)", style: "Bets on trouble the moment a line flickers amber.", record: { won: 2, lost: 2 }, points: 990 },
  { id: "rain", name: "Rain Street", kind: "AI hedge fund", emoji: "🌧️", watches: "Rain right now across London (Open-Meteo)", style: "Rain means people go underground.", record: { won: 3, lost: 1 }, points: 1240 },
  { id: "bus", name: "Bus Clock", kind: "AI hedge fund", emoji: "🚌", watches: "Live bus arrivals at 5 hubs (TfL)", style: "Reads the countdown boards, nothing else.", record: { won: 1, lost: 3 }, points: 870 },
  { id: "contrarian", name: "Contrarian", kind: "AI hedge fund", emoji: "🦊", watches: "The other traders' bets", style: "Fades whatever everyone else is backing.", record: { won: 2, lost: 4 }, points: 940 },
];

const FEEDS = ["crowding", "line_status", "jamcams", "rain_now", "bus"] as const;
type FeedId = (typeof FEEDS)[number];

const LOCK_MS = 60_000;
const OPEN_TARGET = 4;
const KEEP_SETTLED_MS = 45_000;
const B = 1200;

interface MockMarket extends Market {
  feed: FeedId;
  q: number[];
  base: number[];
}

interface State {
  markets: MockMarket[];
  results: Map<string, { res: Extract<SettleResponse, { settled: true }>; at: number }>;
  scores: Map<string, { name: string; points: number }>;
  seq: number;
  lastDrift: number;
}

interface PendingOrder {
  betId: string;
  market: string;
  option: number;
  stake: number;
  placedAt: number;
  fillsAt: number;
  status: "pending" | "filled" | "cancelled";
  reason?: string;
  odds?: number;
  payout?: number;
}

const g = globalThis as unknown as { __cityMock?: State; __cityMockOrders?: Map<string, PendingOrder> };
const state: State = (g.__cityMock ??= { markets: [], results: new Map(), scores: new Map(), seq: 0, lastDrift: 0 });
const orders: Map<string, PendingOrder> = (g.__cityMockOrders ??= new Map());

/** Mirrors services/city/integrity.py: a line board is visible from a platform, so it waits longest. */
const INTEGRITY: Record<FeedId, Integrity> = {
  line_status: { delayS: 45, suspendsOn: ["any Tube line's status changes"], coolOffS: 20, capPct: 10 },
  crowding: { delayS: 5, suspendsOn: ["a hub's TfL crowding reading moves more than 10%"], coolOffS: 15, capPct: 25 },
  jamcams: { delayS: 2, suspendsOn: [], coolOffS: 10, capPct: 25 },
  rain_now: { delayS: 5, suspendsOn: [], coolOffS: 15, capPct: 25 },
  bus: { delayS: 5, suspendsOn: [], coolOffS: 15, capPct: 25 },
};

const plainIntegrity = (i: Integrity) =>
  `Bets fill ${i.delayS} s after you place them, at the price then; if the market pauses or closes first the bet is ` +
  `cancelled and your stake is untouched.` +
  (i.suspendsOn.length ? ` The market pauses for ${i.coolOffS} s whenever ${i.suspendsOn.join(" or ")}.` : "") +
  ` Max ${i.capPct}% of your balance in this market.`;

const clock = (ms: number, seconds = false) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    timeZone: "Europe/London",
  }).format(ms);
const name = (id: string) => HOTSPOTS.find((h) => h.id === id)?.name ?? id;
const rand = (a: number, b: number) => a + Math.random() * (b - a);
const oddsOf = (p: number) => Math.min(20, Math.max(1.05, Math.round((0.95 / p) * 10) / 10));
const normalise = (ps: number[]) => {
  const s = ps.reduce((a, b) => a + b, 0);
  return ps.map((p) => p / s);
};

interface Draft {
  kind: Market["kind"];
  question: string;
  hotspots: string[];
  feed: FeedId;
  source: string;
  rule: string;
  options: { id: string; label: string; hotspot?: string }[];
  probs: number[];
  bets: AiBet[];
}

type Template = (settlesAt: number) => Draft;

const TEMPLATES: Template[] = [
  (at) => {
    const picks = ["kings-cross", "oxford-circus", "waterloo", "bank"].sort(() => Math.random() - 0.5).slice(0, 3);
    const probs = normalise(picks.map(() => rand(0.5, 1.5)));
    const fav = picks[probs.indexOf(Math.max(...probs))];
    return {
      kind: "versus",
      question: `Which station is busiest at ${clock(at)}?`,
      hotspots: picks,
      feed: "crowding",
      source: `TfL Crowding API · ${picks.map(name).join(", ")} · settles ${clock(at, true)} Europe/London`,
      rule: `Settles to the station with the highest % of usual busyness in the first TfL Crowding reading within ±2 min of ${clock(at, true)}. No reading → void, stakes refunded.`,
      options: picks.map((h) => ({ id: h, label: name(h), hotspot: h })),
      probs,
      bets: [{ trader: "tube", option: fav, why: `${name(fav)} is at ${Math.round(rand(108, 126))}% of its usual Saturday` }],
    };
  },
  (at) => {
    const n = Math.round(rand(10, 22));
    const p = rand(0.35, 0.65);
    return {
      kind: "overunder",
      question: `Oxford Circus camera at ${clock(at)}: more than ${n} people in frame?`,
      hotspots: ["oxford-circus"],
      feed: "jamcams",
      source: `TfL JamCam Oxford St / Regent St · settles ${clock(at, true)} Europe/London`,
      rule: `Median YOLO person count over the JamCam frames in the 60 s before ${clock(at, true)}. Over ${n} means ${n + 1} or more; exactly ${n} settles Under. No frame → void.`,
      options: [
        { id: "over", label: `Over ${n}`, hotspot: "oxford-circus" },
        { id: "under", label: `${n} or fewer` },
      ],
      probs: [p, 1 - p],
      bets: [{ trader: "cams", option: p > 0.5 ? "over" : "under", why: `Counted ${n + Math.round(rand(-4, 4))} people on the last frame` }],
    };
  },
  (at) => {
    const p = rand(0.2, 0.45);
    return {
      kind: "yesno",
      question: `Will the Northern line show delays at ${clock(at)}?`,
      hotspots: ["kings-cross", "bank"],
      feed: "line_status",
      source: `TfL Line Status · Northern line · settles ${clock(at, true)} Europe/London`,
      rule: `Yes if the TfL status for the Northern line at ${clock(at, true)} is anything other than Good Service.`,
      options: [
        { id: "yes", label: "Delays", hotspot: "bank" },
        { id: "no", label: "Good service", hotspot: "kings-cross" },
      ],
      probs: [p, 1 - p],
      bets: [{ trader: "lines", option: "yes", why: "Minor delays flagged on the Bank branch 6 min ago" }],
    };
  },
  (at) => {
    const p = rand(0.15, 0.4);
    return {
      kind: "yesno",
      question: `Will it be raining at Waterloo at ${clock(at)}?`,
      hotspots: ["waterloo"],
      feed: "rain_now",
      source: `Open-Meteo rain · Waterloo · settles ${clock(at, true)} Europe/London`,
      rule: `Yes if Open-Meteo's current precipitation at Waterloo is above 0.1 mm/h in the reading nearest ${clock(at, true)}.`,
      options: [
        { id: "yes", label: "Raining", hotspot: "waterloo" },
        { id: "no", label: "Dry" },
      ],
      probs: [p, 1 - p],
      bets: [{ trader: "rain", option: "no", why: `${rand(0, 0.3).toFixed(1)} mm/h right now, radar clearing west` }],
    };
  },
  (at) => {
    const m = Math.round(rand(3, 6));
    const p = rand(0.4, 0.7);
    return {
      kind: "overunder",
      question: `Next 25 bus at Bank after ${clock(at)}: here within ${m} min?`,
      hotspots: ["bank"],
      feed: "bus",
      source: `TfL bus arrivals · Bank stop K · settles ${clock(at, true)} Europe/London`,
      rule: `Yes if the first route 25 prediction at Bank (stop K) at ${clock(at, true)} is ${m} min or less.`,
      options: [
        { id: "under", label: `Within ${m} min`, hotspot: "bank" },
        { id: "over", label: `Over ${m} min` },
      ],
      probs: [p, 1 - p],
      bets: [{ trader: "bus", option: p > 0.5 ? "under" : "over", why: `Countdown board shows the next 25 at ${m - 1} min` }],
    };
  },
  (at) => {
    const probs = normalise([rand(0.6, 1.4), rand(0.6, 1.4)]);
    return {
      kind: "versus",
      question: `Busier at ${clock(at)}: Piccadilly Circus or Oxford Circus?`,
      hotspots: ["piccadilly", "oxford-circus"],
      feed: "crowding",
      source: `TfL Crowding API · Piccadilly Circus, Oxford Circus · settles ${clock(at, true)} Europe/London`,
      rule: `Settles to the station with the higher % of usual busyness in the first TfL Crowding reading within ±2 min of ${clock(at, true)}.`,
      options: [
        { id: "piccadilly", label: "Piccadilly Circus", hotspot: "piccadilly" },
        { id: "oxford-circus", label: "Oxford Circus", hotspot: "oxford-circus" },
      ],
      probs,
      bets: [{ trader: "tube", option: probs[0] > probs[1] ? "piccadilly" : "oxford-circus", why: "Piccadilly Circus at 104% of usual, Oxford Circus at 97%" }],
    };
  },
];

function pricesOf(m: MockMarket): number[] {
  const w = m.base.map((p, i) => p * Math.exp(m.q[i] / B));
  return normalise(w).map((p) => Math.min(0.97, Math.max(0.03, p)));
}

function reprice(m: MockMarket) {
  const p = pricesOf(m);
  m.options = m.options.map((o, i) => ({
    ...o,
    prob: Number(p[i].toFixed(3)),
    odds: oddsOf(p[i]),
    history: [...(o.history ?? []), Number(p[i].toFixed(3))].slice(-40),
  }));
}

function open(now: number, lockIn: number) {
  const settlesAt = Math.round((now + lockIn + LOCK_MS) / 1000) * 1000;
  const d = TEMPLATES[state.seq % TEMPLATES.length](settlesAt);
  state.seq++;
  const m: MockMarket = {
    id: `m${state.seq}-${settlesAt}`,
    kind: d.kind,
    question: d.question,
    hotspots: d.hotspots,
    closesAt: settlesAt - LOCK_MS,
    settlesAt,
    source: d.source,
    rule: `${d.rule} ${plainIntegrity(INTEGRITY[d.feed])}`,
    integrity: INTEGRITY[d.feed],
    status: "open",
    feed: d.feed,
    base: d.probs,
    q: d.probs.map(() => 0),
    options: d.options.map((o, i) => {
      const history = Array.from({ length: 30 }, (_, k) => Math.min(0.95, Math.max(0.03, d.probs[i] + (Math.random() - 0.5) * 0.1 * (1 - k / 30))));
      return { ...o, prob: d.probs[i], odds: oddsOf(d.probs[i]), history };
    }),
    aiBets: [],
  };
  reprice(m);
  const fav = m.options.reduce((a, b) => (b.prob > a.prob ? b : a));
  const dog = m.options.filter((o) => o.id !== fav.id).reduce((a, b) => (b.prob > a.prob ? b : a));
  m.aiBets = [...d.bets, { trader: "contrarian", option: dog.id, why: `Everyone is on ${fav.label}; ×${dog.odds} is too good to pass` }];
  state.markets.push(m);
}

function settle(m: MockMarket, now: number) {
  const isVoid = Math.random() < 0.08;
  let r = Math.random();
  const win = m.options.find((o) => (r -= o.prob) <= 0) ?? m.options[m.options.length - 1];
  const aiResults = isVoid ? [] : m.aiBets.map((b) => ({ trader: b.trader, won: b.option === win.id }));
  for (const a of aiResults) {
    const t = TRADERS.find((x) => x.id === a.trader);
    if (!t) continue;
    if (a.won) t.record.won++;
    else t.record.lost++;
    t.points += a.won ? 130 : -100;
  }
  const at = clock(m.settlesAt - Math.round(rand(2, 40)) * 1000, true);
  const reading = isVoid
    ? "No data — void"
    : m.kind === "versus"
      ? `${m.options.map((o) => `${o.label} ${Math.round(o.id === win.id ? rand(110, 125) : rand(85, 108))}%`).join(" · ")} of usual · TfL Crowding · ${at}`
      : m.feed === "jamcams"
        ? `${win.id === "over" ? Math.round(rand(23, 30)) : Math.round(rand(6, 12))} people · JamCam Oxford St · ${at}`
        : `${win.label} · ${m.source?.split(" · ")[0]} · ${at}`;
  m.status = isVoid ? "void" : "settled";
  state.results.set(m.id, {
    at: now,
    res: { settled: true, winner: isVoid ? null : win.id, reading, aiResults, futuresRuledOut: Math.round(rand(20, 40)), void: isVoid },
  });
}

const rainStale = (now: number) => Math.floor(now / 75_000) % 4 === 0;

function tick(now: number) {
  for (const m of state.markets) {
    if (m.status === "settled" || m.status === "void") continue;
    if (now >= m.settlesAt + 4_000) settle(m, now);
    else if (now >= m.closesAt) m.status = "closed";
    else m.status = m.feed === "rain_now" && rainStale(now) ? "halted" : "open";
  }
  state.markets = state.markets.filter((m) => {
    const r = state.results.get(m.id);
    return !r || now - r.at < KEEP_SETTLED_MS;
  });
  const live = () => state.markets.filter((m) => m.closesAt > now);
  if (!live().some((m) => m.closesAt - now < 120_000)) open(now, rand(40_000, 90_000));
  while (live().length < OPEN_TARGET) open(now, rand(100_000, 280_000));
  if (now - state.lastDrift > 6_000) {
    state.lastDrift = now;
    for (const m of live()) {
      m.base = normalise(m.base.map((p) => Math.max(0.03, p + (Math.random() - 0.5) * 0.04)));
      reprice(m);
    }
  }
  for (const [id, r] of state.results) if (now - r.at > 10 * 60_000) state.results.delete(id);
}

function feeds(now: number): Record<string, Feed> {
  return Object.fromEntries(
    FEEDS.map((f) => {
      const stale = f === "rain_now" && rainStale(now);
      return [f, { ageS: stale ? 412 : Math.round(rand(4, 50)), ok: !stale }];
    }),
  );
}

/** A pending order cancels if its market stops being open, else fills at the price once its delay is up. */
function advance(o: PendingOrder, now: number) {
  if (o.status !== "pending") return;
  const m = state.markets.find((x) => x.id === o.market);
  if (!m || m.status !== "open") {
    o.status = "cancelled";
    o.reason = m?.status === "halted" ? "stale feed" : "market closed";
    return;
  }
  if (now < o.fillsAt) return;
  const p0 = m.options[o.option].prob;
  const shares = B * Math.log((Math.exp(o.stake / B) - (1 - p0)) / p0);
  m.q[o.option] += shares;
  reprice(m);
  o.status = "filled";
  o.odds = Number((shares / o.stake).toFixed(2));
  o.payout = Math.floor(o.stake * o.odds);
}

const publicMarket =({ feed: _f, q: _q, base: _b, ...m }: MockMarket): Market => m;

export function mockHandle(route: string, method: string, body: Record<string, unknown>): { status: number; data: unknown } {
  const now = Date.now();
  tick(now);
  const ok = (data: unknown) => ({ status: 200, data });
  switch (route) {
    case "hotspots":
      return ok({ hotspots: HOTSPOTS });
    case "traders":
      return ok({ traders: TRADERS });
    case "markets":
      return ok({ asOf: now - 4_000, live: false, markets: state.markets.map(publicMarket), feeds: feeds(now) });
    case "settle": {
      const id = String(body.id ?? "");
      const done = state.results.get(id);
      if (done) return ok(done.res);
      const m = state.markets.find((x) => x.id === id);
      if (!m) return ok({ settled: true, winner: null, reading: "No data — void", aiResults: [], void: true });
      return ok({ settled: false, waitMs: Math.max(1000, m.settlesAt + 4_000 - now) });
    }
    case "bet": {
      if (method === "GET") {
        const o = orders.get(String(body.id ?? ""));
        if (!o) return { status: 404, data: { ok: false, error: "unknown bet" } };
        advance(o, now);
        return ok({ ok: true, ...o, option: state.markets.find((x) => x.id === o.market)?.options[o.option]?.id });
      }
      const m = state.markets.find((x) => x.id === body.market);
      if (!m || m.status !== "open") return { status: 409, data: { ok: false, error: m ? `market ${m.status}` : "market closed" } };
      const i = m.options.findIndex((o) => o.id === body.option);
      const stake = Math.max(1, Math.round(Number(body.stake) || 0));
      if (i < 0) return { status: 409, data: { ok: false, error: "unknown option" } };
      const delay = (m.integrity?.delayS ?? 5) * 1000;
      const o: PendingOrder = { betId: crypto.randomUUID(), market: m.id, option: i, stake, placedAt: now, fillsAt: now + delay, status: "pending" };
      orders.set(o.betId, o);
      return ok({ ok: true, ...o, option: m.options[i].id });
    }
    case "score": {
      if (method !== "POST") return { status: 405, data: { ok: false } };
      state.scores.set(String(body.player), { name: String(body.name), points: Math.round(Number(body.points) || 0) });
      return ok({ ok: true });
    }
    case "leaderboard": {
      const rows: LeaderRow[] = [
        ...[...state.scores.values()].map((s) => ({ name: s.name, points: s.points, kind: "human" as const })),
        ...TRADERS.map((t) => ({ name: t.name, points: t.points, kind: "ai" as const })),
      ];
      return ok({ rows: rows.sort((a, b) => b.points - a.points) });
    }
    default:
      return { status: 404, data: { error: "unknown city route" } };
  }
}
