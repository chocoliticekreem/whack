"""Edge cases fired deliberately at a city markets server. Run by run.py --edge (in a thread, once, on the first
round with 3+ minutes to close) or alone:

    python3 analysis/live_trading/edge.py --url http://127.0.0.1:8161
    python3 analysis/live_trading/edge.py --url http://127.0.0.1:8161 --restart-before   # then restart, then:
    python3 analysis/live_trading/edge.py --url http://127.0.0.1:8161 --restart-after

Each case is {case, expect, status, resp, line, ok}: `ok` is whether the server did the right thing.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid

from client import SCRATCH, Api, Log, Player


def now_ms() -> int:
    return int(time.time() * 1000)


def key() -> str:
    return f"edge-{uuid.uuid4().hex[:10]}"


def fresh_round(api: Api, min_left_ms: int = 180_000) -> dict[str, dict]:
    """{series: market json} for the open round once every market in it has min_left_ms to close."""
    while True:
        st, d, _ = api.get("/markets", quiet=True)
        if st == 200:
            ms = {m["series"]: m for m in d["markets"] if m["status"] == "open"
                  and m["closesAt"] - d["now"] > min_left_ms and m["series"] in ("hub_surge", "city_pulse",
                                                                               "london_vix", "heathrow_stack")}
            if "hub_surge" in ms and "city_pulse" in ms:
                st, d2, _ = api.get("/markets", quiet=True)
                return ms | {m["series"]: m for m in d2["markets"] if m["status"] == "open"
                             and m["series"] in ("hub_hour", "clean_sheet")}
        time.sleep(2)


def wait_fill(p: Player, bet_id: str, timeout_s: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st, r, line = p.get("/bet", {"id": bet_id})
        if st == 200 and r.get("status") != "pending":
            return r | {"_line": line}
        time.sleep(1)
    return {"status": "timeout"}


class Cases:
    def __init__(self, api: Api, out: list):
        self.api, self.out = api, out

    def add(self, case: str, expect: str, st: int, r: dict, line: int, ok: bool, **extra) -> None:
        row = {"case": case, "expect": expect, "status": st, "resp": r, "line": line, "ok": bool(ok)} | extra
        self.out.append(row)
        self.api.log.write({"actor": "edge", "event": "edge_case"} | row)


def suite(run=None, url: str | None = None, part: str = "all") -> list:
    api = run.api if run else Api(url, Log(SCRATCH / "edge.jsonl"))
    out = run.edge_log if run else []
    c = Cases(api, out)
    tag = run.tag if run else "edge"
    ms = fresh_round(api)
    hub, pulse = ms["hub_surge"], ms["city_pulse"]
    third = ms.get("london_vix") or ms.get("heathrow_stack")
    e = run.edge_player if run else Player.new(api, f"{tag}-edge")
    if part == "money":
        money(c, api, tag, hub, pulse, third, ms)
        return out

    # --- validation
    for stake, exp in ((5, 400), (9, 400), (0, 400), (-10, 400), (10.5, 400), ("10", 400), (True, 400),
                       (1e12, 409)):
        st, r, ln = e.bet(hub["id"], hub["options"][0]["id"], stake, key())
        c.add(f"stake {stake!r}", f"{exp} refused", st, r, ln, st == exp)
    st, r, ln = e.bet(hub["id"], "atlantis", 10, key())
    c.add("unknown option", "400 option must be one of", st, r, ln, st == 400)
    st, r, ln = e.bet("hub_surge-0000", "bank", 10, key())
    c.add("unknown market", "404", st, r, ln, st == 404)
    st, r, ln = api.post("/bet", {"player": e.id, "market": hub["id"], "option": "bank", "stake": 10,
                                  "idempotency_key": key()}, token="wrong-token", actor="edge")
    c.add("wrong token", "401", st, r, ln, st == 401)
    st, r, ln = e.bet(hub["id"], "bank", 10, "k" * 129)
    c.add("idempotency key 129 chars", "400", st, r, ln, st == 400)

    # --- position cap (25% of 1000 = 250 on hub_surge)
    st, r, ln = e.bet(hub["id"], "bank", 260, key())
    c.add("stake 260 over the 25% cap", "409 position cap", st, r, ln, st == 409 and "cap" in r.get("error", ""))
    if ms.get("clean_sheet"):
        cs = ms["clean_sheet"]
        st, r, ln = e.bet(cs["id"], "yes", 110, key())
        c.add("stake 110 over clean_sheet's 10% cap", "409 position cap", st, r, ln, st == 409)

    # --- idempotency
    k = key()
    st1, r1, l1 = e.bet(pulse["id"], "over", 10, k)
    st2, r2, l2 = e.bet(pulse["id"], "over", 10, k)
    c.add("same key twice (pending)", "same betId, replayed", st2, r2, l2,
          st1 == st2 == 200 and r1.get("betId") == r2.get("betId") and r2.get("replayed") is True, first=r1)
    st3, r3, l3 = e.bet(pulse["id"], "over", 20, k)
    c.add("same key, different stake", "400 already used", st3, r3, l3, st3 == 400)
    st3, r3, l3 = e.bet(pulse["id"], "under", 10, k)
    c.add("same key, different option", "400 already used", st3, r3, l3, st3 == 400)
    if r1.get("betId"):
        f = wait_fill(e, r1["betId"])
        st4, r4, l4 = e.bet(pulse["id"], "over", 10, k)
        c.add("same key after the fill", "same betId, filled, replayed", st4, r4, l4,
              st4 == 200 and r4.get("betId") == r1["betId"] and r4.get("status") == "filled" and r4.get("replayed"),
              fill=f)
        st5, w5, l5 = e.get("/positions")
        n = sum(1 for p in w5.get("positions", []) if p["market"] == pulse["id"])
        c.add("same key: one position", "exactly 1 position on the market", st5, w5, l5, n == 1)
    st, r, ln = e.bet(hub["id"], "bank", 10, k)
    c.add("same key on another market", "200, a separate bet (keys are per market round)", st, r, ln, st == 200)

    # --- someone else's bet id
    other = Player.new(api, f"{tag}-other")
    st, r, ln = other.get("/bet", {"id": r1.get("betId")})
    c.add("GET /bet for another player's bet", "404", st, r, ln, st == 404)
    st, r, ln = other.get("/bet", {"id": "nope"})
    c.add("GET /bet unknown id", "404", st, r, ln, st == 404)

    # --- concurrency: 20 distinct bets of 20 at once on one market (cap 250 → at most 12)
    conc = Player.new(api, f"{tag}-conc")
    res: list = []

    def fire(stake: int, k: str, m: dict, option: str):
        res.append(conc.bet(m["id"], option, stake, k))

    ts = [threading.Thread(target=fire, args=(20, key(), hub, "waterloo")) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    acc = [r for st, r, _ in res if st == 200]
    rej = [r for st, r, _ in res if st != 200]
    fills = [wait_fill(conc, r["betId"]) for r in acc]
    filled = [f for f in fills if f.get("status") == "filled"]
    staked = sum(f["stake"] for f in filled)
    st, w, ln = conc.get("/wallet")
    c.add("20 concurrent bets of 20 (cap 250)", "<= 12 accepted, escrow = filled stakes, cash+escrow = 1000", st,
          {"accepted": len(acc), "rejected": [r.get("error") for r in rej][:3], "filled": len(filled),
           "staked": staked, "wallet": {k2: w.get(k2) for k2 in ("cash", "escrow", "total")}}, ln,
          len(acc) <= 12 and staked <= 250 and w.get("escrow") == staked and w.get("total") == 1000)

    res.clear()
    same = key()
    ts = [threading.Thread(target=fire, args=(10, same, pulse, "under")) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = {r.get("betId") for st, r, _ in res if st == 200}
    c.add("10 concurrent bets with one key", "1 betId", 200, {"betIds": list(ids),
                                                             "statuses": [st for st, _, _ in res]}, 0, len(ids) == 1)

    money(c, api, tag, hub, pulse, third, ms)
    if run:
        run.save()
    return out


def money(c: Cases, api: Api, tag: str, hub: dict, pulse: dict, third: dict | None, ms: dict) -> None:
    # --- spread across markets while nothing is held: pending bets hold no escrow, so all pass precheck
    over = Player.new(api, f"{tag}-over")
    rows = []
    for m in [x for x in (hub, pulse, third, ms.get("hub_hour")) if x]:
        cap = int(m["integrity"]["capPct"] / 100 * 1000)
        st, r, ln = over.bet(m["id"], m["options"][0]["id"], min(cap, 250), key())
        rows.append((m["id"], st, r, ln))
    outcomes = [(mid, st, wait_fill(over, r["betId"]) if st == 200 else r) for mid, st, r, ln in rows]
    st, w, ln = over.get("/wallet")
    filled_n = sum(1 for _, _, f in outcomes if f.get("status") == "filled")
    c.add("4 x 250 at once across markets (loss limit 500)", "the limit holds: escrow <= 500; over-limit bets "
          "refused (ideally at accept, not cancelled after 'pending')", st,
          {"accepted": sum(1 for _, s, _, _ in rows if s == 200), "filled": filled_n,
           "outcomes": [(mid, s, f.get("status"), f.get("reason") or f.get("error")) for mid, s, f in outcomes],
           "wallet": {k2: w.get(k2) for k2 in ("cash", "escrow")}}, ln, w.get("escrow", 0) <= 500)
    big = [f for _, _, f in outcomes if f.get("status") == "filled"]
    c.add("confirm token bypass: 2nd 250 fills at > 25% of remaining cash without a confirm",
          "a stake over 25% of cash needs a confirm token", st, {"filled": [(f.get("market"), f.get("stake"))
                                                                         for f in big]}, ln, len(big) < 2)

    # --- confirm-token flow on a player holding escrow: cash 760, stake 200 > 25% of cash
    cf = Player.new(api, f"{tag}-confirm")
    st, r, ln = cf.bet(hub["id"], "kings-cross", 240, key())
    if st == 200:
        wait_fill(cf, r["betId"])
    k2 = key()
    st, r, ln = cf.bet(pulse["id"], "under", 200, k2)
    tok = r.get("confirmToken")
    c.add("stake 200 at 26% of cash", "409 confirm_required with a token", st, r, ln,
          st == 409 and r.get("code") == "confirm_required" and tok)
    st, r, ln = cf.bet(pulse["id"], "under", 200, k2, confirm_token=tok)
    c.add("same stake with the confirm token", "200 pending", st, r, ln, st == 200)
    st, r, ln = cf.bet(pulse["id"], "under", 200, key(), confirm_token="1.abc")
    c.add("forged confirm token", "409", st, r, ln, st == 409)
    if third:
        st, r, ln = cf.bet(third["id"], third["options"][0]["id"], 100, key())
        c.add("over the 500 daily loss limit (440 in play)", "409 loss_limit", st, r, ln,
              st == 409 and r.get("code") in ("loss_limit", "confirm_required"))


# ---------------------------------------------------------------- restart


def restart_before(url: str) -> None:
    """Places bets that will be filled, and one that is still pending, on the open round; saves them."""
    api = Api(url, Log(SCRATCH / "restart.jsonl"))
    ms = fresh_round(api, 60_000)
    p = Player.new(api, "edge-restart")
    bets = []
    for m in [ms["hub_surge"], ms["city_pulse"]] + [ms[s] for s in ("london_vix", "heathrow_stack") if s in ms]:
        st, r, ln = p.bet(m["id"], m["options"][0]["id"], 20, key())
        bets.append({"market": m["id"], "betId": r.get("betId"), "line": ln})
    for b in bets:
        b["state"] = wait_fill(p, b["betId"])
    st, r, ln = p.bet(ms["hub_surge"]["id"], "bank", 15, key())  # left pending: kill within 5 s
    bets.append({"market": ms["hub_surge"]["id"], "betId": r.get("betId"), "line": ln, "pendingAtKill": True})
    _, w, wl = p.get("/wallet")
    _, tr, _ = api.get("/traders", actor="edge")
    (SCRATCH / "restart.json").write_text(json.dumps({"player": p.json(), "bets": bets, "wallet": w,
                                                      "traders": tr, "at": now_ms()}))
    print("placed; kill the server now", json.dumps([(b["market"], (b.get("state") or {}).get("status"))
                                                     for b in bets]))


def restart_after(url: str) -> None:
    api = Api(url, Log(SCRATCH / "restart.jsonl"))
    saved = json.loads((SCRATCH / "restart.json").read_text())
    pj = saved["player"]
    p = Player(api, pj["name"], pj["id"], pj["token"])
    rows = []
    for b in saved["bets"]:
        st, r, ln = p.get("/bet", {"id": b["betId"]})
        rows.append({"market": b["market"], "pendingAtKill": b.get("pendingAtKill", False), "status": st,
                     "bet": r, "line": ln})
    _, w, wl = p.get("/wallet")
    _, h, hl = p.get("/history", {"limit": 50})
    _, pos, pl = p.get("/positions")
    _, tr, _ = api.get("/traders", actor="edge")
    out = {"bets": rows, "walletBefore": saved["wallet"], "walletAfter": w, "history": h, "positions": pos,
           "tradersBefore": [(t["name"], t["points"]) for t in saved["traders"]["traders"]],
           "tradersAfter": [(t["name"], t["points"]) for t in tr["traders"]], "lines": [wl, hl, pl]}
    api.log.write({"actor": "edge", "event": "restart_check"} | out)
    (SCRATCH / "restart_after.json").write_text(json.dumps(out))
    print(json.dumps({k: out[k] for k in ("tradersBefore", "tradersAfter")}))
    for r in rows:
        print(r["market"], r["pendingAtKill"], r["status"], r["bet"].get("status"), r["bet"].get("result"),
              r["bet"].get("error"))
    print("wallet", w.get("cash"), w.get("escrow"), w.get("pnl"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8161")
    ap.add_argument("--restart-before", action="store_true")
    ap.add_argument("--restart-after", action="store_true")
    ap.add_argument("--part", default="all", choices=["all", "money"])
    a = ap.parse_args()
    if a.restart_before:
        restart_before(a.url)
    elif a.restart_after:
        restart_after(a.url)
    else:
        for row in suite(url=a.url, part=a.part):
            print("OK " if row["ok"] else "BAD", row["case"], row["status"], json.dumps(row["resp"])[:200])


if __name__ == "__main__":
    main()
