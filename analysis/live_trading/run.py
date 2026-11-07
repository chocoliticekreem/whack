"""Live trading pressure test: seven bot players trade every 5-minute round of a city markets server, and every
round each player's wallet and history are reconciled against the bot's own record.

    python3 analysis/live_trading/run.py --url http://127.0.0.1:8161 --minutes 60
    python3 analysis/live_trading/run.py --url http://127.0.0.1:8161 --minutes 60 --edge   # + edge-case suite

Everything goes to scratch/trades.jsonl (every call) and scratch/state.json (players, bets, markets, recon).
stdlib only. The bots never touch a server they were not pointed at.
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid

from client import SCRATCH, Api, Log, Player

ROUND_MS = 300_000
GRANT = 1000


def now_ms() -> int:
    return int(time.time() * 1000)


def opt(m: dict, oid: str) -> dict:
    return next(o for o in m["options"] if o["id"] == oid)


def sig(m: dict, sid: str) -> dict | None:
    return next((s.get("data") for s in m.get("signals") or [] if s.get("id") == sid), None)


# ---------------------------------------------------------------- strategies: market json -> option or None


def s_random(m, rng):
    return rng.choice(m["options"])["id"]


def s_favourite(m, rng):
    return max(m["options"], key=lambda o: o["prob"])["id"]


def s_longshot(m, rng):
    return min(m["options"], key=lambda o: o["prob"])["id"]


def s_signal(m, rng):
    """The documented clues (docs/questions.md): back the hub furthest below its usual, ride the usual curve on City
    Pulse, fade the last VIX / stack move, back the hour's leader, Line Watcher's clean-sheet rule."""
    sub = m["series"]
    if sub == "hub_surge":
        d = sig(m, "vs_usual")
        return d and d.get("laggard")
    if sub == "hub_hour":
        d = sig(m, "vs_usual")
        return d and d.get("leader")
    if sub == "city_pulse":
        d = sig(m, "pulse")
        if not d:
            return None
        if d.get("usualTrend") in ("up", "down"):
            return "over" if d["usualTrend"] == "up" else "under"
        c = d.get("change5")
        return None if not c else ("over" if c > 0 else "under")
    if sub == "london_vix":
        d = sig(m, "vix_driver")
        c = d and d.get("change5")
        return None if not c else ("under" if c > 0 else "over")
    if sub == "heathrow_stack":
        d = sig(m, "stacks")
        c = d and d.get("change10")
        return None if not c else ("under" if c > 0 else "over")
    if sub == "clean_sheet":
        d = sig(m, "lines")
        if not d:
            return None
        fresh = [x for x in d.get("disrupted", []) if x.get("sinceMin") is not None and x["sinceMin"] < 30]
        return "no" if fresh else "yes"
    return None


# Hit rates the docs measured for each clue; the Kelly bot uses max(price, rate) as its own probability.
CLUE_RATE = {"hub_surge": 0.36, "city_pulse": 0.69, "london_vix": 0.65, "heathrow_stack": 0.60, "hub_hour": 0.56,
             "clean_sheet": 0.57}


class Bot:
    def __init__(self, name: str, player: Player, pick, stake: int, when: str = "early"):
        self.name, self.p, self.pick, self.stake, self.when = name, player, pick, stake, when
        self.done: set[str] = set()       # market ids decided
        self.bets: dict[str, dict] = {}   # betId -> record
        self.rng = random.Random(hash(name) & 0xFFFF)
        self.wait_until: dict[str, int] = {}

    def size(self, m: dict, option: str) -> int:
        return self.stake


class Kelly(Bot):
    def size(self, m: dict, option: str) -> int:
        o = opt(m, option)
        p = max(o["prob"], CLUE_RATE.get(m["series"], o["prob"]))
        b = o["odds"] - 1
        f = (p * (b + 1) - 1) / b if b > 0 else 0
        stake = int(0.5 * f * self.equity)  # half Kelly
        cap = m["integrity"]["capPct"] / 100 * self.equity
        return max(0, min(stake, int(cap) - 1, 60))

    equity = GRANT


# ---------------------------------------------------------------- the run


class Run:
    def __init__(self, url: str, minutes: float, edge: bool, tag: str):
        self.log = Log(SCRATCH / "trades.jsonl")
        self.api = Api(url, self.log)
        self.end = now_ms() + int(minutes * 60_000)
        self.edge_on, self.tag = edge, tag
        self.markets: dict[str, dict] = {}   # id -> {open, openSeenLag, statuses, settled, ...}
        self.rounds_reconciled: set[int] = set()
        self.recon: list[dict] = []
        self.halts: list[dict] = []
        self.edge_log: list[dict] = []
        self.lock = threading.Lock()
        self.traders_start = None
        self.bots: list[Bot] = []
        self.edge_player: Player | None = None
        self.down_since: int | None = None

    def ev(self, kind: str, **data) -> int:
        return self.log.write({"actor": "observer", "event": kind} | data)

    def setup(self) -> None:
        mk = lambda n: Player.new(self.api, f"{self.tag}-{n}")
        spec = [("random", s_random, 10, "early"), ("favourite", s_favourite, 15, "early"),
                ("longshot", s_longshot, 10, "early"), ("signal", s_signal, 15, "early"),
                ("copycat", None, 15, "copy"), ("latecomer", s_favourite, 15, "late")]
        self.bots = [Bot(n, mk(n), f, st, w) for n, f, st, w in spec]
        self.bots.append(Kelly("kelly", mk("kelly"), s_signal, 0, "early"))
        self.edge_player = mk("edge")
        _, self.traders_start, _ = self.api.get("/traders", actor="observer")
        self.save()

    # ------------------------------------------------------------ observing

    def poll(self) -> list[dict]:
        st, d, _ = self.api.get("/markets", quiet=True)
        if st != 200:
            if self.down_since is None:
                self.down_since = now_ms()
                self.ev("server_down", status=st, resp=d)
            return []
        if self.down_since is not None:
            self.ev("server_up", downMs=now_ms() - self.down_since)
            self.down_since = None
        t = d["now"]
        for m in d["markets"]:
            rec = self.markets.get(m["id"])
            if rec is None:
                opens = m["settlesAt"] - ROUND_MS - 60_000 if m["series"] not in ("hub_hour", "clean_sheet") else None
                rec = self.markets[m["id"]] = {
                    "id": m["id"], "series": m["series"], "closesAt": m["closesAt"], "settlesAt": m["settlesAt"],
                    "question": m["question"], "firstSeen": t, "openLagMs": None if opens is None else t - opens,
                    "open": {o["id"]: o["prob"] for o in m["options"]}, "openAiBets": len(m["aiBets"]),
                    "status": m["status"], "statusLog": [[t, m["status"], m.get("haltReason")]], "aiBets": [],
                    "fromSim": m["futures"].get("fromSim"), "integrity": m["integrity"]}
                self.ev("market_seen", market=m["id"], series=m["series"], status=m["status"], open=rec["open"],
                        closesAt=m["closesAt"], settlesAt=m["settlesAt"], fromSim=rec["fromSim"],
                        signals=[s.get("text") for s in m.get("signals") or []])
            if m["status"] != rec["status"] or (m["status"] == "halted" and m.get("haltReason") != rec["statusLog"][-1][2]):
                rec["statusLog"].append([t, m["status"], m.get("haltReason")])
                line = self.ev("market_status", market=m["id"], status=m["status"], prev=rec["status"],
                               haltReason=m.get("haltReason"), closesAt=m["closesAt"])
                if m["status"] == "halted":
                    self.halts.append({"market": m["id"], "at": t, "reason": m.get("haltReason"), "line": line})
                    self.probe_halt(m)
                rec["status"] = m["status"]
            rec["aiBets"] = m["aiBets"]
            rec["last"] = {o["id"]: o["prob"] for o in m["options"]}
            if m["status"] in ("settled", "void") and "winner" not in rec:
                rec.update(winner=m.get("winner"), reading=m.get("reading"), settlement=m.get("settlement"),
                           settledSeen=t, futuresRuledOut=m.get("futuresRuledOut"))
                self.ev("market_settled", market=m["id"], status=m["status"], winner=m.get("winner"),
                        reading=m.get("reading"), settlement=m.get("settlement"), closesAt=m["closesAt"],
                        settlesAt=m["settlesAt"], aiBets=m["aiBets"])
        return d["markets"]

    # ------------------------------------------------------------ betting

    def place(self, bot: Bot, m: dict, option: str, stake: int) -> None:
        if stake < 10:
            self.ev("skip", bot=bot.name, market=m["id"], why=f"stake {stake} below 10")
            return
        key = f"{bot.name}-{m['id']}-{uuid.uuid4().hex[:6]}"
        t = now_ms()
        st, r, line = bot.p.bet(m["id"], option, stake, key)
        rec = {"bot": bot.name, "market": m["id"], "series": m["series"], "option": option, "stake": stake,
               "key": key, "placedAt": t, "closesAt": m["closesAt"], "settlesAt": m["settlesAt"],
               "priceSeen": opt(m, option)["prob"], "oddsSeen": opt(m, option)["odds"], "placeStatus": st,
               "placeLine": line, "status": r.get("status") if st == 200 else "rejected",
               "reason": None if st == 200 else r.get("error")}
        if st == 200:
            rec["betId"] = r["betId"]
            rec["fillsAt"] = r.get("fillsAt")
            if r.get("status") == "filled":
                rec.update(odds=r["odds"], payout=r["payout"], filledAt=t)
            bot.bets[r["betId"]] = rec
        else:
            bot.bets[f"rej-{key}"] = rec

    def check_pending(self) -> None:
        for bot in self.bots:
            for bid, b in list(bot.bets.items()):
                if b["status"] != "pending" or now_ms() < (b.get("fillsAt") or 0):
                    continue
                st, r, line = bot.p.get("/bet", {"id": bid})
                if st != 200:
                    b["lookupErr"] = [st, r, line]
                    if st == 404:
                        b["status"], b["reason"] = "lost", "GET /bet 404 (server forgot the pending bet)"
                    continue
                if r["status"] == "filled":
                    b.update(status="filled", odds=r["odds"], payout=r["payout"], filledAt=r["filledAt"],
                             fillLine=line, fillPrice=r.get("price"))
                    if r["filledAt"] >= b["closesAt"]:
                        self.ev("BUG_fill_after_close", bot=bot.name, betId=bid, filledAt=r["filledAt"],
                                closesAt=b["closesAt"], line=line)
                elif r["status"] == "cancelled":
                    b.update(status="cancelled", reason=r.get("reason"), fillLine=line)

    def decide(self, ms: list[dict]) -> None:
        t = now_ms()
        for m in ms:
            if m["status"] != "open":
                continue
            for bot in self.bots:
                if m["id"] in bot.done:
                    continue
                if bot.when == "early":
                    wait = bot.wait_until.setdefault(m["id"], t + bot.rng.randint(2, 20) * 1000)
                    if t < wait:
                        continue
                    bot.done.add(m["id"])
                    o = bot.pick(m, bot.rng)
                    if o:
                        if isinstance(bot, Kelly):
                            bot.equity = self.equity_of(bot)
                        self.place(bot, m, o, bot.size(m, o))
                elif bot.when == "late":
                    if t >= m["closesAt"] - 4000:
                        bot.done.add(m["id"])
                        self.place(bot, m, s_favourite(m, bot.rng), bot.stake)
                elif bot.when == "copy":
                    tube = [b for b in m["aiBets"] if b["trader"] == "tube"]
                    if tube:
                        bot.done.add(m["id"])
                        self.place(bot, m, tube[0]["option"], bot.stake)
                    elif t >= m["closesAt"] - 30_000:
                        bot.done.add(m["id"])

    def equity_of(self, bot: Bot) -> int:
        st, w, _ = bot.p.get("/wallet", quiet=True)
        return w.get("total", GRANT) if st == 200 else GRANT

    # ------------------------------------------------------------ edge probes during the run

    def probe_halt(self, m: dict) -> None:
        p = self.edge_player
        st, r, line = p.bet(m["id"], m["options"][0]["id"], 10, f"halt-{uuid.uuid4().hex[:6]}")
        self.edge_log.append({"case": "bet on a halted market", "market": m["id"], "haltReason": m.get("haltReason"),
                              "status": st, "resp": r, "line": line, "ok": st == 409})

    def probe_after_close(self) -> None:
        """Once per round: the edge player bets 1-3 s after a market closes."""
        t = now_ms()
        for rec in self.markets.values():
            if rec.get("closedProbe") or not (rec["closesAt"] + 1000 <= t <= rec["closesAt"] + 3000):
                continue
            rec["closedProbe"] = True
            p = self.edge_player
            st, r, line = p.bet(rec["id"], next(iter(rec["open"])), 10, f"late-{uuid.uuid4().hex[:6]}")
            self.edge_log.append({"case": "bet after close", "market": rec["id"], "afterCloseMs": t - rec["closesAt"],
                                  "status": st, "resp": r, "line": line, "ok": st == 409})

    # ------------------------------------------------------------ reconciliation

    def reconcile(self, round_at: int) -> None:
        """Each player's /wallet and /history against the bot's own record, to the point."""
        for bot in self.bots:
            st, w, wl = bot.p.get("/wallet")
            st2, h, hl = bot.p.get("/history", {"limit": 500})
            st3, pos, pl = bot.p.get("/positions")
            if st != 200 or st2 != 200 or st3 != 200:
                self.recon.append({"round": round_at, "bot": bot.name, "error": [st, st2, st3]})
                continue
            hist = {x["betId"]: x for x in h["history"]}
            filled = {bid: b for bid, b in bot.bets.items() if b["status"] == "filled"}
            issues = []
            for bid, b in filled.items():
                if bid in hist:
                    x = hist[bid]
                    b["result"], b["pnl"] = x["result"], x["pnl"]
                    want = self.expected(b)
                    if want is not None and want != x["result"]:
                        issues.append(f"{bid[:8]} {b['market']} {b['option']}: server says {x['result']}, "
                                      f"market winner says {want}")
                    paid = b["payout"] if x["result"] == "won" else b["stake"] if x["result"] == "void" else 0
                    if x["pnl"] != paid - b["stake"]:
                        issues.append(f"{bid[:8]} pnl {x['pnl']} != {paid - b['stake']}")
                    if x["result"] == "won" and b["payout"] != int(b["stake"] * b["odds"] + 1e-9):
                        issues.append(f"{bid[:8]} payout {b['payout']} != floor({b['stake']}x{b['odds']})")
            extra = [bid for bid in hist if bid not in filled]
            if extra:
                issues.append(f"history has bets the bot never saw fill: {extra}")
            open_ = [b for bid, b in filled.items() if bid not in hist]
            exp_escrow = sum(b["stake"] for b in open_)
            exp_pnl = sum(hist[bid]["pnl"] for bid in filled if bid in hist)
            exp_cash = GRANT - sum(b["stake"] for b in filled.values()) + sum(
                hist[bid]["pnl"] + filled[bid]["stake"] for bid in filled if bid in hist)
            got = {"cash": w["cash"], "escrow": w["escrow"], "pnl": w["pnl"]["allTime"],
                   "positions": len(pos["positions"]), "pending": len(pos.get("pending", []))}
            want = {"cash": exp_cash, "escrow": exp_escrow, "pnl": exp_pnl, "positions": len(open_)}
            for k in want:
                if got[k] != want[k]:
                    issues.append(f"{k}: server {got[k]} vs own record {want[k]}")
            if got["cash"] + got["escrow"] != GRANT + got["pnl"]:
                issues.append(f"cash+escrow {got['cash'] + got['escrow']} != grant+pnl {GRANT + got['pnl']}")
            row = {"round": round_at, "bot": bot.name, "got": got, "want": want, "issues": issues,
                   "lines": [wl, hl, pl]}
            self.recon.append(row)
            if issues:
                self.ev("RECON_MISMATCH", **row)
        st, tr, _ = self.api.get("/traders", actor="observer")
        st, lb, _ = self.api.get("/leaderboard", actor="observer")
        self.ev("round_reconciled", round=round_at)

    def expected(self, b: dict) -> str | None:
        rec = self.markets.get(b["market"])
        if not rec or "winner" not in rec:
            return None
        if rec["winner"] is None:
            return "void"
        return "won" if rec["winner"] == b["option"] else "lost"

    def maybe_reconcile(self) -> None:
        t = now_ms()
        for at in sorted({r["settlesAt"] for r in self.markets.values()}):
            if at in self.rounds_reconciled or t < at:
                continue
            ms = [r for r in self.markets.values() if r["settlesAt"] == at]
            done = all("winner" in r for r in ms)
            if done or t > at + 290_000:
                self.rounds_reconciled.add(at)
                self.reconcile(at)
                self.save()

    # ------------------------------------------------------------ loop

    def save(self) -> None:
        state = {"players": [b.p.json() | {"bot": b.name} for b in self.bots] +
                 ([self.edge_player.json() | {"bot": "edge"}] if self.edge_player else []),
                 "bets": {b.name: b.bets for b in self.bots}, "markets": self.markets, "recon": self.recon,
                 "halts": self.halts, "edge": self.edge_log, "tradersStart": self.traders_start,
                 "savedAt": now_ms()}
        tmp = SCRATCH / "state.json.tmp"
        tmp.write_text(json.dumps(state, default=str))
        tmp.replace(SCRATCH / "state.json")

    def loop(self) -> None:
        self.setup()
        if self.edge_on:
            import edge
            threading.Thread(target=edge.suite, args=(self,), daemon=True).start()
        last_save = 0
        while now_ms() < self.end or any(b["status"] == "pending" for bot in self.bots for b in bot.bets.values()):
            try:
                with self.lock:
                    ms = self.poll()
                    if now_ms() < self.end:
                        self.decide(ms)
                    self.check_pending()
                    self.probe_after_close()
            except Exception as exc:  # noqa: BLE001 - the run must survive a bad response and log it
                self.ev("runner_error", error=f"{type(exc).__name__}: {exc}")
            self.maybe_reconcile()
            if now_ms() - last_save > 30_000:
                self.save()
                last_save = now_ms()
            time.sleep(1)
        # let the final round settle and reconcile
        deadline = now_ms() + 330_000
        while now_ms() < deadline and len(self.rounds_reconciled) < len({r["settlesAt"] for r in self.markets.values()
                                                                        if r["settlesAt"] <= deadline}):
            with self.lock:
                self.poll()
                self.check_pending()
            self.maybe_reconcile()
            time.sleep(1)
        self.save()
        self.ev("run_done", rounds=len(self.rounds_reconciled))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8161")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--edge", action="store_true", help="also run the edge-case suite (edge.py) during the run")
    ap.add_argument("--tag", default="bot")
    a = ap.parse_args()
    Run(a.url, a.minutes, a.edge, a.tag).loop()


if __name__ == "__main__":
    main()
