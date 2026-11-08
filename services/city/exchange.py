"""The exchange: 5-minute rounds of recurring city series, AI traders, player bets (LMSR), settlement, scores."""
from __future__ import annotations

import sys
import time
import uuid

import fan
import integrity
import local
import markets as mk
import pricing
import signals
import tops
import traders
from data import SOURCES, Store, load_hotspots, local_time
from ledger import Ledger, WalletError, as_points
from pricing import MIN_MS

ROUND_MS = 5 * MIN_MS
KEEP_SETTLED_MS = 60_000
DEFAULT_BANKROLL = 1000
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
    def __init__(self, store: Store, clock: Clock, hotspots: list[dict] | None = None, ledger: Ledger | None = None):
        self.store, self.clock = store, clock
        self.ledger = ledger or Ledger()  # wallet hook: player points live in the ledger, not here
        self.spots = hotspots or load_hotspots()
        self.by_id = {h["id"]: h for h in self.spots}
        self.markets: dict[str, mk.Market] = {}
        self.order: list[str] = []
        self.rounds: dict[int, dict] = {}
        self.rejected = 0
        self.record = {t["id"]: {"won": 0, "lost": 0, "points": DEFAULT_BANKROLL, "why": ""} for t in traders.TRADERS}
        for tid, rec in traders.load_records(self.ledger).items():
            if tid in self.record:
                self.record[tid] = rec
        self.restored: list[tuple[mk.Restored, list[str]]] = []
        self.stats = {s: {"real": 0, "void": 0} for s in mk.SERIES}
        self.open_favourites: dict[str, list[float]] = {s: [] for s in mk.SERIES}
        self.calibration: dict[str, list[tuple[float, bool]]] = {"price": [], "model": []}
        self.series_calibration: dict[str, list[tuple[float, bool]]] = {s: [] for s in mk.SERIES}
        self.sat_out: dict[str, tuple[int, str]] = {}
        self.sim_views: dict[str, tuple[int, dict | None]] = {}
        self.integrity = integrity.Integrity()
        self.tops = tops.Tops(self.ledger)
        self.local = local.LocalMarkets(self)
        self.ledger.ai_exposure = self.ai_exposure

    def ai_exposure(self) -> list[tuple]:
        """The AI traders' bets on unsettled markets, as the ledger's worst-case rows. An AI win is paid as
        round(stake × odds) (settle), so that is its payout here."""
        return [(m.id, m.settles_at, b["option"], b["stake"], round(b["stake"] * b["odds"]))
                for m in self.active() for b in m.ai_bets]

    # ------------------------------------------------------------ the loop

    def tick(self, now: int | None = None) -> None:
        now = self.clock.now() if now is None else now
        self.advance(now)
        self.open_round(now)
        self.trade(now)

    def advance(self, now: int) -> None:
        """Read new data, then move every listed market on: observe, lock, close, settle."""
        self.store.refresh(now - mk.HISTORY_MS, now)
        self.integrity.scan(self.store, self.active(), now)
        for m in self.active():
            mk.observe(m, self.store, now)
            self.lock_if_decided(m, now)
            if m.close_prices is None and (now >= m.closes_at or m.locked):
                m.close_prices, m.close_model = m.prices(), m.model()
            try:
                self.settle(m, now)
            except Exception as exc:  # noqa: BLE001 - the market stays unsettled and the next tick retries it
                print(f"settle {m.id} failed, will retry: {type(exc).__name__}: {exc}", file=sys.stderr)
        self.settle_restored(now)

    def trade(self, now: int) -> None:
        """Fill the pending bets that are due, then let the AI traders bet on the open markets."""
        self.integrity.fill_due(self, now)
        for m in self.active():
            if self.status(m, now) == "open":
                self.ai_bets(m, now)
        self.sim_views.clear()  # each view was for one instant; a later tick asks the simulation afresh

    def lock_if_decided(self, m: mk.Market, now: int) -> None:
        """Ask the series' settle function about the data so far. A final answer while bets are still open closes
        the market at once, so a sure thing can never be bought."""
        if m.locked is not None or m.result is not None or now >= m.closes_at:
            return
        got = mk.REGISTRY[m.sub].settle(m, self.store, now)
        if got[0] == "done":
            m.locked = got

    # ------------------------------------------------------------ restart

    def recover(self, now: int | None = None) -> dict:
        """After a restart: bets on a recorded market are settled from recorded data by the next ticks, like any
        other; only bets that cannot be settled (no record, or a series that no longer exists) are refunded now. A
        market that outlives the current round (an hourly headline) is listed again for trading, rebuilt from its
        record, with its bets on it."""
        now = self.clock.now() if now is None else now
        self.store.refresh(now - mk.HISTORY_MS, now)
        refunded = pending = 0
        for g in self.ledger.open_markets():
            rec = g["record"]
            if rec is None or rec.get("series") not in mk.REGISTRY:
                refunded += self.ledger.settle_bets(
                    g["bets"], None, "Exchange restarted and this market cannot be settled from data; stake refunded")
                continue
            pending += len(g["bets"])
            m = self.relist(rec, now) if rec["settlesAt"] > round_for(now) else None
            if m is None:
                self.restored.append((mk.Restored(rec), g["bets"]))
            else:
                m.bets = self.ledger.open_bets(g["bets"])
        return {"refunded": refunded, "pending": pending}

    def relist(self, rec: dict, now: int) -> mk.Market | None:
        """A tradable market rebuilt from its ledger record: futures from the data now, everything the settle rule
        reads (spec, options, opening time) from the record. None if the series cannot build one now."""
        series = mk.REGISTRY[rec["series"]]
        at = mk.headline_round(rec["settlesAt"]) if series.due else rec["settlesAt"]
        try:
            m = series.build(rec["id"], self.store, self.spots, now, at)
        except Exception as exc:  # noqa: BLE001 - it still settles from its record as a Restored market
            print(f"relist {rec['id']} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        if m is None or (m.id, m.settles_at, m.option_ids()) != (rec["id"], rec["settlesAt"],
                                                                 [o["id"] for o in rec["options"]]):
            return None
        m.opened_at, m.spec, m.options = rec["openedAt"], rec["spec"], rec["options"]
        self.tops.reattach(m)
        self.markets[m.id] = m
        self.order.append(m.id)
        return m

    def settle_restored(self, now: int) -> None:
        keep = []
        for m, bets in self.restored:
            got = mk.try_settle(m, self.store, now)
            if got[0] == "wait":
                keep.append((m, bets))
            elif got[0] == "done":
                self.ledger.settle_bets(bets, got[1], got[2])
                self.tops.settle(m.id, m.settles_at, got[1], now)
            else:
                self.ledger.settle_bets(bets, None, got[1])
                self.tops.settle(m.id, m.settles_at, None, now)
        self.restored = keep

    def active(self) -> list[mk.Market]:
        return [self.markets[i] for i in self.order if self.markets[i].result is None]

    def biddable(self, now: int) -> list[mk.Market]:
        return [m for m in self.active() if now < m.closes_at]

    def open_round(self, now: int) -> None:
        """Build every registered series once for the current round, all settling at the same 5-min mark. A bracket
        series whose favourite would still exceed 85% after its edges were placed is left out of the round."""
        if round_for(now) not in self.rounds:
            self.list_round(self.build_round(now))

    def build_round(self, now: int) -> dict:
        """The current round's markets, built but not listed, with the Simulation trader's view of each (its 4000
        draws were most of a round open). It reads only the store, and the markets are visible to no one yet, so the
        server runs it outside the request lock (the store is only written by the thread that calls this)."""
        at = round_for(now)
        built = []
        for sid, series in mk.SERIES.items():
            if series.due and not series.due(at):
                continue
            m = series.build(f"{sid}-{local_time(at, '%H%M')}", self.store, self.spots, now, at)
            if m is None:
                built.append((sid, series, None, (series.why_none and series.why_none(self.store, now))
                              or "no fresh reading", None))
            else:
                view = mk.sim_view(m, self.store, now) if m.sub in traders.PLAYS["sim"] else None
                m.tops_stances = tops.picks(m, self.store, now, view,
                                            lambda feed: feed not in SOURCES or self.store.fresh(feed, now))
                built.append((sid, series, m, None, view))
        return {"at": at, "now": now, "built": built}

    def list_round(self, plan: dict) -> None:
        """Lists the markets build_round made. If the round was listed meanwhile (an on-demand market opened it
        first), this one is dropped."""
        at = plan["at"]
        if at in self.rounds:
            return
        rnd = {"markets": [], "skipped": {}}
        self.rounds[at] = rnd
        for sid, series, m, why, view in plan["built"]:
            if m is None:
                rnd["skipped"][sid] = why
                continue
            same = self.markets.get(m.id)
            if same is not None and same.settles_at == m.settles_at:  # relisted after a restart: keep its bets
                rnd["markets"].append(m.id)
                continue
            fav = max(m.open_model.values())
            reason = series.skip(m) if series.skip else None
            if reason is None and fav > MAX_FAVOURITE and m.kind == "bracket":
                self.rejected += 1
                reason = f"favourite {fav:.0%} even after re-placing the brackets"
            if reason:
                rnd["skipped"][sid] = reason
                continue
            if m.tops_stances is not None:
                self.tops.open(m, m.tops_stances, plan["now"])
            self.open_favourites[sid].append(max(m.open_model.values()))
            self.markets[m.id] = m
            self.order.append(m.id)
            rnd["markets"].append(m.id)
            self.sim_views[m.id] = (plan["now"], view)

    def add_market(self, m: mk.Market, now: int) -> None:
        """Lists an on-demand market (local.py) in the current round, as if open_round had built it."""
        self.open_round(now)
        self.stats.setdefault(m.sub, {"real": 0, "void": 0})
        self.series_calibration.setdefault(m.sub, [])
        self.open_favourites.setdefault(m.sub, []).append(max(m.open_model.values()))
        self.markets[m.id] = m
        self.order.append(m.id)
        self.rounds[m.settles_at]["markets"].append(m.id)

    # ------------------------------------------------------------ status, betting

    def status(self, m: mk.Market, now: int) -> str:
        if m.result is not None:
            return "void" if m.result["winner"] is None else "settled"
        if now >= m.closes_at or m.locked is not None:
            return "closed"
        return "halted" if self.halt_reason(m, now) else "open"

    def halt_reason(self, m: mk.Market, now: int) -> str | None:
        if not self.store.fresh(m.feed, now):
            return "stale feed"
        return m.halt_reason or self.integrity.suspended(m, now)

    def stakes(self, m: mk.Market) -> dict[str, float]:
        out = {o: 0.0 for o in m.option_ids()}
        for b in m.bets + m.ai_bets:
            out[b["option"]] += b["stake"]
        return out

    @staticmethod
    def quote(m: mk.Market, option: str, stake: float) -> tuple[float, float]:
        """(shares, locked odds) for a stake, without touching the book. Odds never exceed the shares bought, so no
        price gives a bettor more than the market maker's own payout."""
        shares = m.book.quote(m.model(), option, stake)
        odds = pricing.locked_odds(shares, stake)
        if odds is None:
            raise WalletError("price_too_short", f"Price too short: {m.label(option)} pays under "
                                                 f"{pricing.MIN_ODDS}x right now")
        return shares, odds

    def bet(self, body: dict, now: int | None = None) -> dict:
        """Places a player's bet. `body['player']` must already be authenticated by the caller (the HTTP layer)."""
        now = self.clock.now() if now is None else now
        m = self.markets.get(str(body.get("market")))
        if m is None:
            raise LookupError("unknown market")
        option = str(body.get("option"))
        if option not in m.option_ids():
            raise ValueError(f"option must be one of {m.option_ids()}")
        stake = as_points(body.get("stake"))
        if stake <= 0:
            raise ValueError("stake must be a positive number")
        player = self.ledger.require(body.get("player"))
        # wallet hook (ledger.py): a replayed idempotency_key returns the original bet before anything moves.
        key = str(body.get("idempotency_key") or uuid.uuid4().hex)
        prior = self.ledger.find_bet(player, key, m.id, m.settles_at, option, stake)
        if prior:
            return self.bet_reply(m, prior)
        order = self.integrity.find(player, key, m, option, stake)
        if order:
            return self.integrity.reply(order, self.ledger, replayed=True)
        status = self.status(m, now)
        if status != "open":
            raise PermissionError(f"market {status}")
        bankroll = self.ledger.equity(player)  # wallet hook: cash + escrow from the ledger
        cap = max(0.0, self.integrity.cap(m.sub) * bankroll)
        # The ledger, not m.bets: bets booked before a restart count toward the cap of the market rebuilt after it.
        held = self.ledger.staked(player, m.id, m.settles_at) + self.integrity.held(player, m.id)
        if held + stake > cap:
            raise PermissionError(f"position cap: max W{cap:g} in this market (W{held:g} already staked)")
        if self.integrity.delay_ms(m.sub):
            self.quote(m, option, stake)  # a price already under the floor can never fill: refuse it now
            confirmed = self.ledger.precheck(player, key, stake, market=m.id, settles_at=m.settles_at, option=option,
                                             confirm_token=body.get("confirm_token"),
                                             reserved=self.integrity.pending_stakes(player))
            order = self.integrity.accept(player, key, m, option, stake, now, confirmed)
            return self.integrity.reply(order, self.ledger, False)
        return self.book(m, player, key, option, stake, now, body.get("confirm_token"),
                         reserved=self.integrity.pending_stakes(player))

    def book(self, m: mk.Market, player: str, key: str, option: str, stake: int, now: int, confirm_token,
             bet_id: str | None = None, reserved: int = 0) -> dict:
        """Books a bet at the price now: the immediate fill, or a pending bet's fill after its delay."""
        # wallet hook: checks, the quote and cash -> escrow run in one transaction; the book moves only after commit.
        quoted: dict[str, float] = {}

        def priced() -> tuple[float, float]:
            quoted["shares"], odds = self.quote(m, option, stake)
            return quoted["shares"], odds

        booked = self.ledger.place_bet(player, key, stake, market=m.id, question=m.question, option=option,
                                       label=m.label(option), settles_at=m.settles_at, price=priced,
                                       commit=lambda: m.book.commit(option, quoted["shares"]),
                                       confirm_token=confirm_token, record=mk.record_of(m), bet_id=bet_id,
                                       reserved=reserved)
        m.bets.append({"player": player, "option": option, "stake": float(stake), "odds": booked["odds"], "at": now,
                       "bet_id": booked["betId"]})
        return self.bet_reply(m, booked)

    def bet_reply(self, m: mk.Market, booked: dict) -> dict:
        return {"ok": True, "price": round(m.prices()[booked["option"]], 4), "shares": round(booked["shares"], 1),
                "odds": booked["odds"], "options": self.options_json(m), "betId": booked["betId"],
                "payout": booked["payout"], "balance": booked["balance"], "escrow": booked["escrow"],
                "replayed": booked["replayed"], "status": "filled"}

    def ai_bets(self, m: mk.Market, now: int) -> None:
        ctx = {"spots": self.by_id}
        seen = self.sim_views.pop(m.id, None)
        if seen is not None and seen[0] == now:  # build_round's view for this same instant, made off the lock
            ctx["sim_view"] = seen[1]
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
            open_stakes = self.ai_open_stakes(tid)
            stake = traders.stake_for(self.record[tid]["points"], open_stakes)
            if not stake:
                self.sat_out[tid] = (m.settles_at, f"Sitting out: W{self.record[tid]['points']} left, "
                                                   f"W{open_stakes:g} of it on unsettled bets, so a 10% stake of the "
                                                   f"rest is under the W{traders.MIN_STAKE} minimum")
                continue
            cap_pct = self.integrity.cap(m.sub)
            cap = cap_pct * max(0, self.record[tid]["points"])
            held = sum(b["stake"] for b in m.ai_bets if b["trader"] == tid)
            held += self.integrity.ai_pending(tid, m.id) * stake
            if held + stake > cap:
                self.sat_out[tid] = (m.settles_at, f"Sitting out: position cap W{cap:g} per market "
                                                   f"({cap_pct:.0%} of its W{self.record[tid]['points']})")
                continue
            if self.integrity.delay_ms(m.sub):
                self.integrity.accept_ai(tid, m, option, why, now)
            else:
                self.ai_fill(m, tid, option, why)

    def ai_open_stakes(self, tid: str) -> int:
        """The trader's stakes on markets that have not settled yet (points only move at settle)."""
        return sum(b["stake"] for m in self.active() for b in m.ai_bets if b["trader"] == tid)

    def ai_fill(self, m: mk.Market, tid: str, option: str, why: str) -> None:
        stake = traders.stake_for(self.record[tid]["points"], self.ai_open_stakes(tid))
        if not stake:
            return
        try:
            shares, odds = self.quote(m, option, stake)
        except WalletError:
            return
        if not self.ledger.within_cap((m.id, m.settles_at, option, stake, round(stake * odds))):
            return
        m.book.commit(option, shares)
        m.ai_bets.append({"trader": tid, "option": option, "why": why, "stake": stake, "odds": odds})

    # ------------------------------------------------------------ settlement

    def settle(self, m: mk.Market, now: int) -> dict:
        if m.result is not None:
            return m.result
        got = mk.try_settle(m, self.store, now)
        if got[0] == "wait":
            return {"settled": False, "waitMs": int(got[1])}
        winner, reading, snap = (got[1], got[2], got[3]) if got[0] == "done" else (None, got[1], got[2])
        # wallet hook: player bets settle first, so if the ledger fails nothing below has run and the market retries.
        self.ledger.settle_bets([b["bet_id"] for b in m.bets if "bet_id" in b], winner, reading)
        results = []
        for b in m.ai_bets:
            rec = self.record[b["trader"]]
            if winner is None:
                results.append({"trader": b["trader"], "won": None, "refunded": True})
                continue
            won = b["option"] == winner
            rec["won" if won else "lost"] += 1
            rec["points"] += round((b["stake"] * b["odds"] if won else 0) - b["stake"])
            rec["why"] = b["why"]
            results.append({"trader": b["trader"], "won": won})
        m.result = {"settled": True, "winner": winner, "reading": reading, "aiResults": results,
                    "futuresRuledOut": m.futures.ruled_out, "settlement": snap, "void": winner is None}
        m.settled_at = now
        self.tops.settle(m.id, m.settles_at, winner, now)
        touched = {r["trader"] for r in results if r["won"] is not None}
        if touched:
            traders.save_records(self.ledger, now, {tid: self.record[tid] for tid in sorted(touched)})
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
               "closesAt": m.closes_at, "settlesAt": m.settles_at,
               "futures": fan.futures_json(m, self.store, now) | {"fromSim": m.futures.from_sim},
               "options": self.options_json(m),
               "aiBets": [{k: b[k] for k in ("trader", "option", "why", "stake", "odds")} for b in m.ai_bets],
               "source": m.source, "rule": m.rule, "status": self.status(m, now),
               "series": m.sub, "round": local_time(m.settles_at), "roundSettlesAt": m.settles_at,
               "signals": signals.market(m, self.store, now)}
        out["integrity"], out["rule"] = integrity.meta(m.sub), f"{m.rule} {integrity.plain(m.sub)}"
        if m.tops:
            out["tops"] = {k: m.tops[k] for k in ("lambda", "weights", "contribution")}
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
                        {"record": {"won": rec["won"], "lost": rec["lost"]}, "points": rec["points"], "why": why,
                         "lastWhy": rec["why"], "trust": self.tops.trust(t["id"])})
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

    def leaderboard(self) -> dict:
        """Human points are each player's equity from the ledger; nothing a client posts can change them."""
        rows = self.ledger.leaderboard()
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
