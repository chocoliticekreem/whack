"""The exchange: a rolling book of 3-5 open city markets, AI traders, player bets (LMSR), settlement, scores."""
from __future__ import annotations

import random
import time
import uuid

import markets as mk
import pricing
import traders
from data import SOURCES, STALE_S, Store, load_hotspots, local_time
from ledger import Ledger
from pricing import MIN_MS

MIN_OPEN, MAX_OPEN = 3, 5
FILL_TO = 4  # one slot above the floor, so a lopsided-candidate gap never drops the book below 3
QUICK_MS = 2 * MIN_MS
KEEP_SETTLED_MS = 60_000
DEFAULT_BANKROLL = 1000
POSITION_CAP = 0.25
PUBLISH_MS = 5 * MIN_MS
ROTATION = ("level", "busiest", "bus", "cam", "level", "rain", "bus", "line", "lines_any", "cam")
QUICK = ("bus", "cam", "lines_any", "line")
SLUG = {"busiest": "busiest", "level": "level", "bus": "bus", "cam": "cam", "lines_any": "lines", "line": "line",
        "rain": "rain"}
MAX_FAVOURITE = 0.85
HORIZON_MIN = (5, 15)
PER_SPOT = {"level": lambda h: True, "bus": lambda h: bool(h["busStops"]), "cam": lambda h: bool(h["cams"])}


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
    def __init__(self, store: Store, clock: Clock, hotspots: list[dict] | None = None, ledger: Ledger | None = None):
        self.store, self.clock = store, clock
        self.ledger = ledger or Ledger()  # wallet hook: player points live in the ledger, not here
        self.spots = hotspots or load_hotspots()
        self.by_id = {h["id"]: h for h in self.spots}
        self.markets: dict[str, mk.Market] = {}
        self.order: list[str] = []
        self.rot = 0
        self.quick_rot = 0
        self.spot_rot = {sub: 0 for sub in PER_SPOT}
        self.recent: dict[str, list] = {"busiest": [], "line": []}
        self.open_favourites: list[float] = []
        self.rejected = 0
        self.record = {t["id"]: {"won": 0, "lost": 0, "points": DEFAULT_BANKROLL} for t in traders.TRADERS}
        self.scores: dict[str, dict] = {}
        self.stats = {k: {"real": 0, "void": 0} for k in SLUG}
        self.calibration: dict[str, list[tuple[float, bool]]] = {"price": [], "model": []}

    # ------------------------------------------------------------ the loop

    def tick(self, now: int | None = None) -> None:
        now = self.clock.now() if now is None else now
        self.store.refresh(now - mk.HISTORY_MS, now)
        for m in self.active():
            mk.observe(m, self.store, now)
            if m.close_prices is None and now >= m.closes_at:
                m.close_prices, m.close_model = m.prices(), m.futures.probs()
            self.settle(m, now)
        self.spawn(now)
        for m in self.active():
            if self.status(m, now) == "open":
                self.ai_bets(m, now)

    def active(self) -> list[mk.Market]:
        return [self.markets[i] for i in self.order if self.markets[i].result is None]

    def biddable(self, now: int) -> list[mk.Market]:
        return [m for m in self.active() if now < m.closes_at]

    def spawn(self, now: int) -> None:
        for _ in range(MAX_OPEN):
            open_now = self.biddable(now)
            quick = any(m.closes_at - now <= QUICK_MS for m in open_now)
            if len(open_now) >= MAX_OPEN or (len(open_now) >= FILL_TO and quick):
                return
            m = self.new_market(now, quick_needed=not quick)
            if m is None:
                return
            self.markets[m.id] = m
            self.order.append(m.id)

    def new_market(self, now: int, quick_needed: bool) -> mk.Market | None:
        """The next market in rotation whose model favourite is at most 85% at open; lopsided candidates are
        dropped and the next one is tried."""
        if quick_needed:
            self.quick_rot = (self.quick_rot + 1) % len(QUICK)
            subs = QUICK[self.quick_rot:] + QUICK[:self.quick_rot]
        else:
            subs = ROTATION[self.rot:] + ROTATION[:self.rot]
        counts: dict[str, int] = {}
        for m in self.biddable(now):
            counts[m.sub] = counts.get(m.sub, 0) + 1
        for i, sub in enumerate(subs):
            if counts.get(sub, 0) >= 2:
                continue
            for m in self.candidates(sub, now, quick_needed):
                fav = max(m.futures.probs().values())
                if fav > MAX_FAVOURITE:
                    self.rejected += 1
                    continue
                self.accept(m, fav)
                if not quick_needed:
                    self.rot = (self.rot + i + 1) % len(ROTATION)
                return m
        return None

    def accept(self, m: mk.Market, fav: float) -> None:
        self.open_favourites.append(fav)
        if m.sub == "busiest":
            self.recent["busiest"].append(m.hotspots)
        elif m.sub == "line":
            self.recent["line"].append(m.spec["line"])
        elif m.sub in PER_SPOT:
            self.spot_rot[m.sub] += 1

    def settles_for(self, sub: str, now: int, quick: bool) -> int | None:
        if sub in ("busiest", "level", "rain"):
            return None if quick else self.after_publish("rain_now" if sub == "rain" else "crowding", now)
        rng = random.Random(now // MIN_MS * 31 + len(self.order))
        minutes = 2 if quick else rng.randint(*HORIZON_MIN)
        return mk.ceil_minute(now) + minutes * MIN_MS

    def candidates(self, sub: str, now: int, quick: bool):
        settles = self.settles_for(sub, now, quick)
        if settles is None:
            return
        mid = self.new_id(sub, settles)
        if sub in PER_SPOT:
            spots = [h for h in self.spots if PER_SPOT[sub](h)]
            start = self.spot_rot[sub]
            build = {"level": mk.build_level, "bus": mk.build_bus, "cam": mk.build_cam}[sub]
            for k in range(len(spots)):
                m = build(mid, self.store, spots[(start + k) % len(spots)], now, settles)
                if m:
                    yield m
            return
        if sub == "busiest":
            recent = {frozenset(x) for x in self.recent["busiest"][-2:]}
            m = mk.build_busiest(mid, self.store, self.spots, now, settles, recent)
        elif sub == "lines_any":
            m = mk.build_lines_any(mid, self.store, self.spots, now, settles)
        elif sub == "line":
            m = mk.build_line(mid, self.store, self.spots, now, settles, set(self.recent["line"][-2:]))
        else:
            m = mk.build_rain(mid, self.store, self.spots, now, settles)
        if m:
            yield m

    def after_publish(self, source: str, now: int) -> int | None:
        """settlesAt just after one of the feed's next 5-min publishes, 5-15 min away, so a fresh reading lands
        at or before it."""
        last = self.store.latest(source, now)
        if not last or now - last[0] > STALE_S[source] * 1000:
            return None
        options = [mk.ceil_minute(last[0] + k * PUBLISH_MS + 15_000) for k in range(1, 5)]
        options = [t for t in options if HORIZON_MIN[0] * MIN_MS <= t - now <= HORIZON_MIN[1] * MIN_MS]
        if not options:
            return None
        return random.Random(now // MIN_MS + len(self.order)).choice(options)

    def new_id(self, sub: str, settles: int) -> str:
        base = f"{SLUG[sub]}-{local_time(settles, '%H%M')}"
        mid, n = base, 2
        while mid in self.markets:
            mid, n = f"{base}-{n}", n + 1
        return mid

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
        if m.sub == "cam":
            last = self.store.latest("jamcams", now)
            if not last or m.spec["cam"] not in last[1]:
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
        # wallet hook (ledger.py): a replayed idempotency_key returns the original bet before anything moves.
        player = self.ledger.ensure_player(player, body.get("name"))
        key = str(body.get("idempotency_key") or uuid.uuid4().hex)
        prior = self.ledger.find_bet(player, key, m.id, option, stake)
        if prior:
            return self.bet_reply(m, prior)
        status = self.status(m, now)
        if status != "open":
            raise PermissionError(f"market {status}")
        bankroll = self.ledger.equity(player)  # wallet hook: cash + escrow from the ledger
        cap = max(0.0, POSITION_CAP * bankroll)
        held = sum(b["stake"] for b in m.bets if b["player"] == player)
        if held + stake > cap:
            raise PermissionError(f"position cap: max {cap:g} points in this market ({held:g} already staked)")
        # wallet hook: funds, limits and break are checked and the stake moves cash -> escrow in one transaction.
        def priced() -> tuple[float, float]:
            shares, _, odds = self.place(m, option, float(stake))
            return shares, odds

        label = next(o["label"] for o in m.options if o["id"] == option)
        booked = self.ledger.place_bet(player, key, stake, market=m.id, question=m.question, option=option,
                                       label=label, settles_at=m.settles_at, confirm=bool(body.get("confirm")),
                                       price=priced)
        m.bets.append({"player": player, "option": option, "stake": float(stake), "odds": booked["odds"], "at": now,
                       "bet_id": booked["betId"]})
        return self.bet_reply(m, booked)

    def bet_reply(self, m: mk.Market, booked: dict) -> dict:
        return {"ok": True, "price": round(m.prices()[booked["option"]], 4), "shares": round(booked["shares"], 1),
                "odds": booked["odds"], "options": self.options_json(m), "betId": booked["betId"],
                "payout": booked["payout"], "balance": booked["balance"], "escrow": booked["escrow"],
                "replayed": booked["replayed"]}

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
            pick = traders.DECIDE[tid](m, self.store, now, ctx)
            if pick is None:
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
        self.ledger.settle_bets([b["bet_id"] for b in m.bets if "bet_id" in b], winner, reading)  # wallet hook
        self.stats[m.sub]["void" if winner is None else "real"] += 1
        if winner is not None and m.close_prices:
            for key, probs in (("price", m.close_prices), ("model", m.close_model)):
                fav = max(probs, key=lambda o: probs[o])
                self.calibration[key].append((probs[fav], fav == winner))
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
               "source": m.source, "rule": m.rule, "status": self.status(m, now)}
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

    def traders_json(self) -> dict:
        rows = []
        for t in traders.TRADERS:
            rec = self.record[t["id"]]
            rows.append({k: t[k] for k in ("id", "name", "emoji", "watches", "style")} |
                        {"record": {"won": rec["won"], "lost": rec["lost"]}, "points": rec["points"]})
        return {"traders": rows}

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
        favs = self.open_favourites
        hist: dict[str, int] = {}
        for p in favs:
            lo = min(0.9, int(p * 20) / 20)
            key = f"{lo:.2f}-{lo + 0.05:.2f}"
            hist[key] = hist.get(key, 0) + 1
        mid = sum(0.40 <= p <= 0.75 for p in favs)
        return {"settled": self.stats, "calibration": {k: self.buckets(v) for k, v in self.calibration.items()},
                "openFavourite": {"n": len(favs), "share40to75": round(mid / len(favs), 3) if favs else None,
                                  "rejectedOver85": self.rejected, "histogram": dict(sorted(hist.items()))}}

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
