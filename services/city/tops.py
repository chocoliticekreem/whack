"""TOPS loop: the AI traders' views feed back into the opening price, weighted by how right each has been.

Marshall Wace's TOPS collects ideas from many brokers, scores who has been right and leans on them by track record.
Here the brokers are the seven rule-based traders (traders.py). Per market:

1. At open, each trader that plays the series states a view: a probability per option. The Simulation trader's is
   its forecast (sim_view). The others only pick a side, so the pick gets a calibrated probability from that
   trader's own past picks in the series (the futures prior plus its shrunk excess hit rate) and the other options
   keep their prior shares of the rest. With no history the view equals the prior, so it moves nothing.
2. At settle, each view is scored: log p_view(winner) - log p_prior(winner), its skill against the futures prior.
   Scores are kept per trader per series as an exponentially decayed mean (half-life HALF_LIFE settled markets),
   shrunk toward 0 for small samples: shrunk = sum(w s) / (sum(w) + K_SHRINK).
3. Trust weights per series: softmax(shrunk / TEMP), mixed with a FLOOR so no trader is ignored, then capped at CAP
   so none dominates; what the cap cuts stays with the futures (weight FUTURES).
4. The opening prior is (1 - lam) * futures + lam * (sum_i w_i view_i + w_futures * futures), with
   lam = LAMBDA_MAX * S / (S + S_HALF) and S the summed positive shrunk skill of the traders with a view: no proven
   skill, lam = 0, the price is the futures. The blend is kept as a fixed tilt (blended / prior) on the futures as
   they learn in-round, held in the same [3%, 97%] band the futures use; the LMSR, the odds floor and SIM_WEIGHT are
   untouched.

The baseline is the futures prior, not the blended price: scored against a price it had already moved, a right
trader would look less skilled the more it was trusted, and its weight would oscillate.

Persisted in the ledger's SQLite (tops_markets, one row per listed market, written at open and updated at settle);
the records are rebuilt from it at start. ai_records cannot rebuild them: it keeps won/lost totals, not per-market
views or prices. No look-ahead: records only change at settle, so a market opening at t uses settles before t.
"""
from __future__ import annotations

import json
import math

import pricing

HALF_LIFE = 20
DECAY = 0.5 ** (1 / HALF_LIFE)
K_SHRINK = 10.0
K_CAL = 5.0
TEMP = 0.01
FLOOR = 0.05
CAP = 0.40
LAMBDA_MAX = 0.30
S_HALF = 0.05
P_LO, P_HI = pricing.P_FLOOR, pricing.P_CEIL
HISTORY_SHOWN = 200
FUTURES = "futures"

SCHEMA = """CREATE TABLE IF NOT EXISTS tops_markets (
  market     TEXT NOT NULL,
  settles_at INTEGER NOT NULL,
  series     TEXT NOT NULL,
  opened_at  INTEGER NOT NULL,
  prior      TEXT NOT NULL,
  views      TEXT NOT NULL,
  lam        REAL NOT NULL,
  weights    TEXT NOT NULL,
  tilt       TEXT NOT NULL,
  winner     TEXT,
  settled_at INTEGER,
  PRIMARY KEY (market, settles_at)
)"""


# ---------------------------------------------------------------- the maths (pure)


def new_record() -> dict:
    """Decayed sums for one trader on one series: s = sum(w score), n = sum(w), e = sum(w (hit - prior of the pick))
    and c = sum(w) over its side picks (calibration)."""
    return {"s": 0.0, "n": 0.0, "e": 0.0, "c": 0.0, "count": 0}


def update(rec: dict, score: float, excess: float | None) -> None:
    """One more settled view: every older one counts DECAY times as much as before."""
    rec["s"] = rec["s"] * DECAY + score
    rec["n"] = rec["n"] * DECAY + 1.0
    rec["count"] += 1
    if excess is not None:
        rec["e"] = rec["e"] * DECAY + excess
        rec["c"] = rec["c"] * DECAY + 1.0


def shrunk(rec: dict | None) -> float:
    """Decayed mean skill shrunk toward 0: a normal prior at 0 worth K_SHRINK markets."""
    return rec["s"] / (rec["n"] + K_SHRINK) if rec else 0.0


def normalise(p: dict[str, float]) -> dict[str, float]:
    s = sum(p.values())
    return {k: v / s for k, v in p.items()}


def clamp(p: dict[str, float]) -> dict[str, float]:
    """The futures' [3%, 97%] band (pricing.smooth), held after renormalising too: an option pushed out of the band
    is fixed at its edge and the rest share what is left, until none is out. The tilt can move the sum well off 1,
    where a single clamp-then-renormalise would let the floor slip."""
    w = normalise(p)
    fixed: dict[str, float] = {}
    for _ in range(len(w)):
        out = {k: (P_LO if v < P_LO else P_HI) for k, v in w.items()
               if k not in fixed and not P_LO - 1e-12 <= v <= P_HI + 1e-12}
        if not out:
            break
        fixed |= out
        free = {k: v for k, v in w.items() if k not in fixed}
        room, total = 1 - sum(fixed.values()), sum(free.values())
        w = fixed | ({k: v * room / total for k, v in free.items()} if total > 0 else {})
    return normalise(w)


def pick_view(prior: dict[str, float], pick: str, rec: dict | None) -> dict[str, float]:
    """A side pick as probabilities: the pick at its prior plus the trader's shrunk excess hit rate on past picks in
    this series, the rest in their prior proportions. No history, or a record no better than the prior: exactly the
    prior. A trader that keeps losing is trusted less, never faded."""
    excess = max(0.0, rec["e"] / (rec["c"] + K_CAL)) if rec else 0.0
    p = min(P_HI, max(P_LO, prior[pick] + excess))
    rest = 1 - prior[pick]
    return {o: p if o == pick else (prior[o] * (1 - p) / rest if rest > 0 else (1 - p) / (len(prior) - 1))
            for o in prior}


def score(view: dict[str, float], prior: dict[str, float], winner: str) -> float:
    """Log score of the view minus the prior's, on the realised outcome."""
    return math.log(view[winner]) - math.log(prior[winner])


def weights(skill: dict[str, float]) -> dict[str, float]:
    """Trust weights over the traders plus the futures' own share (key FUTURES), summing to 1: softmax(skill / TEMP)
    mixed with a floor of min(FLOOR, 1/m) each, then each trader capped at CAP. What the cap cuts goes to the futures,
    not to the other traders: with two or three traders, spreading it would hand the best one's excess to the worst."""
    if not skill:
        return {FUTURES: 1.0}
    m = len(skill)
    lo = min(FLOOR, 1 / m)
    top = max(skill.values())
    soft = normalise({k: math.exp((v - top) / TEMP) for k, v in skill.items()})
    w = {k: min(CAP, lo + (1 - m * lo) * v) for k, v in soft.items()}
    return w | {FUTURES: max(0.0, 1 - sum(w.values()))}


def lam_of(skill: dict[str, float]) -> float:
    """How far the price leans on the traders: grows with their summed positive skill, 0 without any, at most
    LAMBDA_MAX."""
    total = sum(max(0.0, v) for v in skill.values())
    return LAMBDA_MAX * total / (total + S_HALF)


def blend(prior: dict[str, float], views: dict[str, dict[str, float]], w: dict[str, float],
          lam: float) -> dict[str, float]:
    """(1 - lam) * prior + lam * (the weighted views + the futures' share on the prior): a convex mix of
    distributions, so still one."""
    if not views or lam <= 0:
        return dict(prior)
    pool = {o: sum(w[t] * v[o] for t, v in views.items()) + w.get(FUTURES, 0.0) * prior[o] for o in prior}
    return {o: (1 - lam) * prior[o] + lam * pool[o] for o in prior}


def tilted(probs: dict[str, float], tilt: dict[str, float] | None) -> dict[str, float]:
    """The futures' probabilities with the open's TOPS tilt carried on top (identity without one)."""
    if not tilt:
        return probs
    return clamp({o: p * tilt[o] for o, p in probs.items()})


# ---------------------------------------------------------------- views at open


def picks(m, store, now: int, sim: dict | None, fresh) -> dict[str, tuple]:
    """Each trader's stance on a market at open, before it is listed: ('probs', {option: p}) for the Simulation
    trader, ('pick', option) for the rest; the same rules and freshness checks the AI bets use (exchange.ai_bets)."""
    import traders  # traders imports markets, which imports this module

    out: dict[str, tuple] = {}
    ctx = {"stakes": {o: 0.0 for o in m.option_ids()}, "sim_view": sim}
    for t in traders.TRADERS:
        tid = t["id"]
        if tid != "contrarian" and m.sub not in traders.PLAYS[tid]:
            continue
        if not fresh(t["feed"]):
            continue
        if tid == "sim":
            if sim and all(o in sim["probs"] for o in m.option_ids()):
                out[tid] = ("probs", {o: float(sim["probs"][o]) for o in m.option_ids()})
            continue
        got = traders.decide(tid, m, store, now, ctx)
        if got is not None and got[0] in m.option_ids():
            out[tid] = ("pick", got[0])
    return out


# ---------------------------------------------------------------- state + persistence


class Tops:
    def __init__(self, ledger):
        self.ledger = ledger
        self.records: dict[tuple[str, str], dict] = {}
        self.history: dict[str, list[dict]] = {}
        self.open_rows: dict[tuple[str, int], dict] = {}
        with ledger.lock:
            ledger.db.execute(SCHEMA)
            rows = ledger.db.execute("SELECT market, settles_at, series, prior, views, winner, settled_at FROM "
                                     "tops_markets ORDER BY COALESCE(settled_at, 0), opened_at, market").fetchall()
        for mid, at, series, prior, views, winner, settled_at in rows:
            row = {"series": series, "prior": json.loads(prior), "views": json.loads(views)}
            if settled_at is None:
                self.open_rows[(mid, at)] = row
            elif winner is not None:
                self.learn(row, winner, settled_at)

    def skill(self, tid: str, series: str) -> float:
        return shrunk(self.records.get((tid, series)))

    def series_weights(self, series: str, tids) -> dict[str, float]:
        return weights({t: self.skill(t, series) for t in tids})

    def open(self, m, stances: dict[str, tuple], now: int) -> None:
        """Turn the stances into views, blend them into the market's opening price and record the open."""
        prior = clamp(m.futures.probs())
        views = {}
        for tid, (kind, val) in stances.items():
            views[tid] = clamp(val) if kind == "probs" else pick_view(prior, val, self.records.get((tid, m.sub)))
        skill = {t: self.skill(t, m.sub) for t in views}
        w = weights(skill)
        lam = lam_of(skill)
        blended = blend(prior, views, w, lam)
        tilt = {o: blended[o] / prior[o] for o in prior}
        self.attach(m, prior, views, w, lam, tilt)
        picked = {t: stances[t][1] for t in views if stances[t][0] == "pick"}
        stored = {t: {"probs": v, "pick": picked.get(t)} for t, v in views.items()}
        with self.ledger.lock:
            self.ledger.db.execute("INSERT OR REPLACE INTO tops_markets (market, settles_at, series, opened_at, prior, "
                                   "views, lam, weights, tilt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (m.id, m.settles_at, m.sub, now, json.dumps(prior), json.dumps(stored), lam,
                                    json.dumps(w), json.dumps(tilt)))
        self.open_rows[(m.id, m.settles_at)] = {"series": m.sub, "prior": prior, "views": stored}

    @staticmethod
    def attach(m, prior, views, w, lam, tilt) -> None:
        m.tilt = tilt if lam > 0 else None
        model = m.model()
        m.open_model = model
        m.tops = {"lambda": round(lam, 4), "weights": {t: round(v, 4) for t, v in w.items() if t != FUTURES},
                  "futuresWeight": round(w.get(FUTURES, 1.0), 4),
                  "contribution": {o: round(model[o] - prior[o], 4) + 0.0 for o in prior}, "prior": prior,
                  "views": views}

    def reattach(self, m) -> None:
        """A market relisted after a restart keeps the tilt it opened with."""
        with self.ledger.lock:
            row = self.ledger.db.execute("SELECT prior, views, lam, weights, tilt FROM tops_markets WHERE market = ? "
                                         "AND settles_at = ?", (m.id, m.settles_at)).fetchone()
        if row is None:
            return
        prior, views, w, tilt = (json.loads(row[i]) for i in (0, 1, 3, 4))
        if set(tilt) != set(m.option_ids()):
            return
        self.attach(m, prior, {t: v["probs"] for t, v in views.items()}, w, row[2], tilt)

    def settle(self, mid: str, settles_at: int, winner: str | None, now: int) -> None:
        """Score every view on the market (a void scores nothing) and update the records."""
        row = self.open_rows.pop((mid, settles_at), None)
        if row is None:
            return
        with self.ledger.lock:
            self.ledger.db.execute("UPDATE tops_markets SET winner = ?, settled_at = ? WHERE market = ? AND "
                                   "settles_at = ?", (winner, now, mid, settles_at))
        if winner is not None and winner in row["prior"]:
            self.learn(row, winner, now)

    def learn(self, row: dict, winner: str, at: int) -> None:
        series, prior = row["series"], row["prior"]
        for tid, v in row["views"].items():
            rec = self.records.setdefault((tid, series), new_record())
            pick = v.get("pick")
            update(rec, score(v["probs"], prior, winner), None if pick is None else (pick == winner) - prior[pick])
        tids = self.tids(series)
        w = self.series_weights(series, tids)
        self.history.setdefault(series, []).append(
            {"ts": at, "traders": {t: {"score": round(self.skill(t, series), 4), "weight": round(w[t], 4)}
                                   for t in tids}})

    # ------------------------------------------------------------ JSON views

    def tids(self, series: str) -> list[str]:
        """The traders with a settled view on the series: the set its trust weights are taken over."""
        return sorted(t for t, s in self.records if s == series)

    def trust(self, tid: str) -> float:
        """A trader's trust: its mean weight over the series it has settled views on (0 before the first)."""
        got = [self.series_weights(s, self.tids(s))[tid] for t, s in self.records if t == tid]
        return round(sum(got) / len(got), 4) if got else 0.0

    def json(self) -> dict:
        """GET /tops: per series, each trader's shrunk score, settled-view count (n), decayed sample size (nEff) and
        trust weight now, the lean (lambda) those scores give, and the score and weight after every settle."""
        out = {}
        for s in sorted({s for _, s in self.records}):
            tids = self.tids(s)
            skill = {t: self.skill(t, s) for t in tids}
            w = weights(skill)
            out[s] = {"lambdaNow": round(lam_of(skill), 4),
                      "traders": {t: {"score": round(skill[t], 4), "n": self.records[(t, s)]["count"],
                                      "nEff": round(self.records[(t, s)]["n"], 2), "weight": round(w[t], 4)}
                                  for t in tids},
                      "futuresWeight": round(w[FUTURES], 4),
                      "history": self.history.get(s, [])[-HISTORY_SHOWN:]}
        return {"halfLife": HALF_LIFE, "shrinkK": K_SHRINK, "temperature": TEMP, "floor": FLOOR, "cap": CAP,
                "lambdaMax": LAMBDA_MAX, "series": out}
