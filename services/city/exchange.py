"""The exchange: 5-minute rounds of recurring city series, AI traders, player bets (LMSR), settlement, scores."""
from __future__ import annotations

import time

import markets as mk
import pricing
import signals
import traders
from data import SOURCES, Store, load_hotspots, local_time
from pricing import MIN_MS

ROUND_MS = 5 * MIN_MS
KEEP_SETTLED_MS = 60_000
DEFAULT_BANKROLL = 1000
POSITION_CAP = 0.25
MAX_FAVOURITE = 0.85


def round_for(now: int) -> int:
    """settlesAt of the round you can bet on at `now`: the next 5-min wall-clock mark whose 60 s close is still
    ahead. Round R opens when round R-5 closes, so there is always exactly one."""
    return (now + mk.CLOSE_MS) // ROUND_MS * ROUND_MS + ROUND_MS


class Clock:
    """Live: wall time. Replay: starts at `start_ms` and runs 12x (1 recorded minute per 5 s)."""

    SPEED = 12

    def __init__(self, start_ms: int | None = None):
        self.start, self.wall0 = start_ms, time.time()

    @property
    def live(self) -> bool:
        return self.start is None

    def now(self) -> int:
        if self.start is None:
            return int(time.time() * 1000)
        return int(self.start + (time.time() - self.wall0) * 1000 * self.SPEED)


class Exchange:
    def __init__(self, store: Store, clock: Clock, hotspots: list[dict] | None = None):
        self.store, self.clock = store, clock
        self.spots = hotspots or load_hotspots()
        self.by_id = {h["id"]: h for h in self.spots}
        self.markets: dict[str, mk.Market] = {}
        self.order: list[str] = []
        self.rounds: dict[int, dict] = {}
        self.rejected = 0
        self.record = {t["id"]: {"won": 0, "lost": 0, "points": DEFAULT_BANKROLL} for t in traders.TRADERS}
        self.scores: dict[str, dict] = {}
        self.stats = {s: {"real": 0, "void": 0} for s in mk.SERIES}
        self.open_favourites: dict[str, list[float]] = {s: [] for s in mk.SERIES}
        self.calibration: dict[str, list[tuple[float, bool]]] = {"price": [], "model": []}
        self.series_calibration: dict[str, list[tuple[float, bool]]] = {s: [] for s in mk.SERIES}
        self.sat_out: dict[str, tuple[int, str]] = {}

    # ------------------------------------------------------------ the loop

    def tick(self, now: int | None = None) -> None:
        now = self.clock.now() if now is None else now
        self.store.refresh(now - mk.HISTORY_MS, now)
        for m in self.active():
            mk.observe(m, self.store, now)
            if m.close_prices is None and now >= m.closes_at:
                m.close_prices, m.close_model = m.prices(), m.futures.probs()
            self.settle(m, now)
        self.open_round(now)
        for m in self.active():
            if self.status(m, now) == "open":
                self.ai_bets(m, now)

    def active(self) -> list[mk.Market]:
        return [self.markets[i] for i in self.order if self.markets[i].result is None]

    def biddable(self, now: int) -> list[mk.Market]:
        return [m for m in self.active() if now < m.closes_at]

    def open_round(self, now: int) -> None:
        """Build every registered series once for the current round, all settling at the same 5-min mark. A bracket
        series whose favourite would still exceed 85% after its edges were placed is left out of the round."""
        at = round_for(now)
        if at in self.rounds:
            return
        rnd = {"markets": [], "skipped": {}}
        self.rounds[at] = rnd
        for sid, series in mk.SERIES.items():
            if series.due and not series.due(at):
                continue
            m = series.build(f"{sid}-{local_time(at, '%H%M')}", self.store, self.spots, now, at)
            if m is None:
                rnd["skipped"][sid] = "no fresh reading"
                continue
            fav = max(m.open_model.values())
            reason = series.skip(m) if series.skip else None
            if reason is None and fav > MAX_FAVOURITE and m.kind == "bracket":
                self.rejected += 1
                reason = f"favourite {fav:.0%} even after re-placing the brackets"
            if reason:
                rnd["skipped"][sid] = reason
                continue
            self.open_favourites[sid].append(fav)
            self.markets[m.id] = m
            self.order.append(m.id)
            rnd["markets"].append(m.id)

    # ------------------------------------------------------------ status, betting

    def status(self, m: mk.Market, now: int) -> str:
        if m.result is not None:
            return "void" if m.result["winner"] is None else "settled"
        if now >= m.closes_at:
            return "closed"
        return "halted" if self.halt_reason(m, now) else "open"

    def halt_reason(self, m: mk.Market, now: int) -> str | None:
        if not self.store.fresh(m.feed, now):
            return "stale feed"
        return m.halt_reason

    def stakes(self, m: mk.Market) -> dict[str, float]:
        out = {o: 0.0 for o in m.option_ids()}
        for b in m.bets + m.ai_bets:
            out[b["option"]] += b["stake"]
        return out

    def place(self, m: mk.Market, option: str, stake: float) -> tuple[float, float, float]:
        """(shares, new price, locked odds). The payout multiple is held to the displayed 1.05..20 range, so a
        bet on a 97% favourite never pays back less than its stake."""
        shares = m.book.buy(m.futures.probs(), option, stake)
        return shares, m.prices()[option], max(1.05, min(20.0, round(shares / stake, 2)))

    def bet(self, body: dict, now: int | None = None) -> dict:
        now = self.clock.now() if now is None else now
        m = self.markets.get(str(body.get("market")))
        if m is None:
            raise LookupError("unknown market")
        option, player = str(body.get("option")), str(body.get("player") or "")
        if option not in m.option_ids():
            raise ValueError(f"option must be one of {m.option_ids()}")
        if not player:
            raise ValueError("player is required")
        stake = body.get("stake")
        if not isinstance(stake, (int, float)) or isinstance(stake, bool) or stake <= 0:
            raise ValueError("stake must be a positive number")
        status = self.status(m, now)
        if status != "open":
            raise PermissionError(f"market {status}")
        bankroll = self.scores.get(player, {}).get("points", DEFAULT_BANKROLL)
        cap = max(0.0, POSITION_CAP * bankroll)
        held = sum(b["stake"] for b in m.bets if b["player"] == player)
        if held + stake > cap:
            raise PermissionError(f"position cap: max {cap:g} points in this market ({held:g} already staked)")
        shares, price, odds = self.place(m, option, float(stake))
        m.bets.append({"player": player, "option": option, "stake": float(stake), "odds": odds, "at": now})
        return {"ok": True, "price": round(price, 4), "shares": round(shares, 1), "odds": odds,
                "options": self.options_json(m)}

    def ai_bets(self, m: mk.Market, now: int) -> None:
        ctx = {"spots": self.by_id}
        for t in traders.TRADERS:
            tid = t["id"]
            if tid in m.decided:
                continue
            if tid == "contrarian" and now < m.opened_at + (m.closes_at - m.opened_at) / 2:
                continue
            m.decided.add(tid)
            if t["feed"] in SOURCES and not self.store.fresh(t["feed"], now):
                continue
            ctx["stakes"] = self.stakes(m)
            pick = traders.decide(tid, m, self.store, now, ctx)
            if pick is None:
                round_markets = [self.markets[i] for i in self.rounds.get(m.settles_at, {}).get("markets", [])]
                self.sat_out[tid] = (m.settles_at, traders.sit_out(tid, self.store, now, round_markets))
                continue
            option, why = pick
            _, _, odds = self.place(m, option, traders.STAKE)
            m.ai_bets.append({"trader": tid, "option": option, "why": why, "stake": traders.STAKE, "odds": odds})

    # ------------------------------------------------------------ settlement

    def settle(self, m: mk.Market, now: int) -> dict:
        if m.result is not None:
            return m.result
        got = mk.try_settle(m, self.store, now)
        if got[0] == "wait":
            return {"settled": False, "waitMs": int(got[1])}
        winner, reading, snap = (got[1], got[2], got[3]) if got[0] == "done" else (None, got[1], got[2])
        results = []
        for b in m.ai_bets:
            rec = self.record[b["trader"]]
            if winner is None:
                results.append({"trader": b["trader"], "won": None, "refunded": True})
                continue
            won = b["option"] == winner
            rec["won" if won else "lost"] += 1
            rec["points"] += round((b["stake"] * b["odds"] if won else 0) - b["stake"])
            results.append({"trader": b["trader"], "won": won})
        m.result = {"settled": True, "winner": winner, "reading": reading, "aiResults": results,
                    "futuresRuledOut": m.futures.ruled_out, "settlement": snap, "void": winner is None}
        m.settled_at = now
        self.stats[m.sub]["void" if winner is None else "real"] += 1
        if winner is not None and m.close_prices:
            for key, probs in (("price", m.close_prices), ("model", m.close_model)):
                fav = max(probs, key=lambda o: probs[o])
                self.calibration[key].append((probs[fav], fav == winner))
        if winner is not None:
            fav = max(m.open_model, key=lambda o: m.open_model[o])
            self.series_calibration[m.sub].append((m.open_model[fav], fav == winner))
        return m.result

    def settle_request(self, mid: str, now: int | None = None) -> dict:
        now = self.clock.now() if now is None else now
        m = self.markets.get(mid)
        if m is None:
            raise LookupError("unknown market")
        return self.settle(m, now)

    # ------------------------------------------------------------ JSON views

    def options_json(self, m: mk.Market) -> list[dict]:
        prices = m.prices()
        return [dict(o, prob=round(prices[o["id"]], 4), odds=pricing.odds_of(prices[o["id"]])) for o in m.options]

    def market_json(self, m: mk.Market, now: int) -> dict:
        out = {"id": m.id, "kind": m.kind, "question": m.question, "hotspots": m.hotspots,
               "closesAt": m.closes_at, "settlesAt": m.settles_at, "futures": pricing.N_FUTURES,
               "options": self.options_json(m),
               "aiBets": [{k: b[k] for k in ("trader", "option", "why", "stake", "odds")} for b in m.ai_bets],
               "source": m.source, "rule": m.rule, "status": self.status(m, now),
               "series": m.sub, "round": local_time(m.settles_at), "roundSettlesAt": m.settles_at,
               "signals": signals.market(m, self.store, now)}
        if out["status"] == "halted":
            out["haltReason"] = self.halt_reason(m, now)
        if m.result:
            out.update(winner=m.result["winner"], reading=m.result["reading"], settlement=m.result["settlement"],
                       futuresRuledOut=m.result["futuresRuledOut"])
        return out

    def markets_json(self, now: int | None = None) -> dict:
        now = self.clock.now() if now is None else now
        shown = [self.markets[i] for i in self.order
                 if self.markets[i].result is None or now - (self.markets[i].settled_at or now) <= KEEP_SETTLED_MS]
        return {"asOf": self.store.newest_ts(now), "live": self.clock.live, "now": now,
                "feeds": self.feeds_json(now), "markets": [self.market_json(m, now) for m in shown]}

    def feeds_json(self, now: int) -> dict:
        out = {}
        for s in SOURCES:
            age = self.store.age_s(s, now)
            out[s] = {"ageS": None if age is None else round(age), "ok": self.store.fresh(s, now)}
        return out

    def hotspots_json(self) -> dict:
        keys = ("id", "name", "lat", "lon", "naptan", "cams")
        return {"hotspots": [{k: h[k] for k in keys} for h in self.spots]}

    def traders_json(self, now: int | None = None) -> dict:
        now = self.clock.now() if now is None else now
        current = [self.markets[i] for i in self.rounds.get(round_for(now), {}).get("markets", [])]
        rows = []
        for t in traders.TRADERS:
            rec = self.record[t["id"]]
            bets = [b for m in current for b in m.ai_bets if b["trader"] == t["id"]]
            at, said = self.sat_out.get(t["id"], (None, None))
            why = (bets[-1]["why"] if bets else said if at == round_for(now)
                   else traders.sit_out(t["id"], self.store, now, current))
            rows.append({k: t[k] for k in ("id", "name", "emoji", "watches", "style")} |
                        {"record": {"won": rec["won"], "lost": rec["lost"]}, "points": rec["points"], "why": why})
        return {"traders": rows}

    def round_json(self, now: int | None = None) -> dict:
        now = self.clock.now() if now is None else now
        at = round_for(now)
        cur = self.rounds.get(at, {"markets": [], "skipped": {}})
        past = [r for r in self.rounds if r <= now]
        out = {"current": {"round": local_time(at), "closesAt": at - mk.CLOSE_MS, "settlesAt": at,
                           "markets": [i for i in cur["markets"] if self.markets[i].settles_at == at],
                           "skipped": cur["skipped"],
                           "headlines": [m.id for m in self.active() if m.settles_at > at]}, "previous": None}
        if past:
            prev = max(past)
            results = []
            for mid in self.rounds[prev]["markets"]:
                m = self.markets[mid]
                if m.settles_at != prev:
                    continue
                res = m.result or {}
                results.append({"market": mid, "series": m.sub, "status": self.status(m, now),
                                "winner": res.get("winner"), "reading": res.get("reading")})
            out["previous"] = {"round": local_time(prev), "settlesAt": prev, "results": results}
        return out

    def score(self, body: dict) -> dict:
        player, points = str(body.get("player") or ""), body.get("points")
        if not player or not isinstance(points, (int, float)) or isinstance(points, bool):
            raise ValueError("player and numeric points are required")
        self.scores[player] = {"name": str(body.get("name") or player)[:32], "points": int(points)}
        return {"ok": True}

    def leaderboard(self) -> dict:
        rows = [{"name": s["name"], "points": s["points"], "kind": "human"} for s in self.scores.values()]
        rows += [{"name": t["name"], "points": self.record[t["id"]]["points"], "kind": "ai"} for t in traders.TRADERS]
        return {"rows": sorted(rows, key=lambda r: -r["points"])}

    def stats_json(self) -> dict:
        favs = [f for s in mk.SERIES for f in self.open_favourites[s]]
        mid = sum(0.40 <= p <= 0.75 for p in favs)
        series = {}
        for s in mk.SERIES:
            fs = self.open_favourites[s]
            rounds = sum(any(self.markets[i].sub == s for i in r["markets"]) for r in self.rounds.values())
            series[s] = {"rounds": rounds,
                         "settled": self.stats[s], "openFavourite": self.histogram(fs),
                         "share30to70": round(sum(0.30 <= p <= 0.70 for p in fs) / len(fs), 3) if fs else None,
                         "calibrationAtOpen": self.buckets(self.series_calibration[s])}
        return {"settled": self.stats, "calibration": {k: self.buckets(v) for k, v in self.calibration.items()},
                "openFavourite": {"n": len(favs), "share40to75": round(mid / len(favs), 3) if favs else None,
                                  "share30to70": round(sum(0.30 <= p <= 0.70 for p in favs) / len(favs), 3)
                                  if favs else None,
                                  "rejectedOver85": self.rejected, "histogram": self.histogram(favs)},
                "rounds": len(self.rounds), "series": series}

    @staticmethod
    def histogram(favs: list[float]) -> dict[str, int]:
        hist: dict[str, int] = {}
        for p in favs:
            lo = min(0.9, int(p * 20) / 20)
            key = f"{lo:.2f}-{lo + 0.05:.2f}"
            hist[key] = hist.get(key, 0) + 1
        return dict(sorted(hist.items()))

    @staticmethod
    def buckets(rows: list[tuple[float, bool]]) -> dict:
        """Favourite's price at close vs how often it won, in 10-point price buckets."""
        buckets = {}
        for p, won in rows:
            lo = min(0.9, int(p * 10) / 10)
            b = buckets.setdefault(f"{lo:.1f}-{lo + 0.1:.1f}", {"n": 0, "favWon": 0, "meanPrice": 0.0})
            b["n"] += 1
            b["favWon"] += int(won)
            b["meanPrice"] += p
        for b in buckets.values():
            b["meanPrice"] = round(b["meanPrice"] / b["n"], 3)
            b["winRate"] = round(b["favWon"] / b["n"], 3)
        return dict(sorted(buckets.items()))
