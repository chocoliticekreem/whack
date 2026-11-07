"""Small end-to-end check through the public stack (Vercel -> tunnel -> the laptop's city server): 3 players,
2 bets of 10 each (6 in total), pending -> filled, then wallet + history after settlement.

    python3 analysis/live_trading/public_check.py --url https://overwatchcapital.vercel.app/api/city

Logs to scratch/public.jsonl. Nicknames start with "test-".
"""
from __future__ import annotations

import argparse
import json
import time
import uuid

from client import SCRATCH, Api, Log, Player


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://overwatchcapital.vercel.app/api/city")
    a = ap.parse_args()
    api = Api(a.url, Log(SCRATCH / "public.jsonl"), timeout=25)
    out: dict = {"steps": []}

    def step(name, st, r, line, ok):
        out["steps"].append({"step": name, "status": st, "ok": ok, "line": line,
                             "resp": json.dumps(r)[:300]})
        print("OK " if ok else "BAD", name, st, json.dumps(r)[:160])

    t0 = time.time()
    st, h, ln = api.get("/round", actor="public")
    step("GET /round", st, h, ln, st == 200)
    st, ms, ln = api.get("/markets", actor="public")
    step("GET /markets", st, {"n": len(ms.get("markets", [])), "live": ms.get("live")}, ln, st == 200)
    opened = [m for m in ms.get("markets", []) if m["status"] == "open" and m["closesAt"] - ms["now"] > 45_000
              and m["series"] in ("hub_surge", "city_pulse", "london_vix", "heathrow_stack")]
    while len(opened) < 2:
        time.sleep(10)
        st, ms, _ = api.get("/markets", actor="public", quiet=True)
        opened = [m for m in ms.get("markets", []) if m["status"] == "open" and m["closesAt"] - ms["now"] > 45_000
                  and m["series"] in ("hub_surge", "city_pulse", "london_vix", "heathrow_stack")]
    players = [Player.new(api, f"test-lt-{c}-{uuid.uuid4().hex[:4]}") for c in "abc"]
    step("POST /player/new x3", 200, [p.name for p in players], 0, all(p.id for p in players))
    bets = []
    for i, p in enumerate(players):
        for m in opened[:2]:
            o = m["options"][i % len(m["options"])]["id"]
            st, r, ln = p.bet(m["id"], o, 10, f"pub-{uuid.uuid4().hex[:8]}")
            step(f"{p.name} bet 10 on {m['id']} {o}", st, r, ln, st == 200 and r.get("status") in ("pending",
                                                                                                   "filled"))
            bets.append((p, m, r.get("betId"), r))
    time.sleep(7)
    for p, m, bid, _ in bets:
        st, r, ln = p.get("/bet", {"id": bid})
        step(f"GET /bet {bid[:8] if bid else None}", st, r, ln, st == 200 and r.get("status") == "filled")
    for p in players:
        st, w, ln = p.get("/wallet")
        step(f"{p.name} wallet after fills", st, {"cash": w.get("cash"), "escrow": w.get("escrow")}, ln,
             st == 200 and w.get("cash", 0) + w.get("escrow", 0) == 1000)
    st, r, ln = api.get("/wallet", {"player": players[0].id}, token="nope", actor="public")
    step("wallet with a wrong token through the proxy", st, r, ln, st == 401)
    settle_by = max(m["settlesAt"] for _, m, _, _ in bets) / 1000 + 300
    while time.time() < settle_by:
        time.sleep(20)
        if all(p.get("/positions", quiet=True)[1].get("positions") == [] for p in players):
            break
    for p in players:
        st, w, ln = p.get("/wallet")
        st2, hh, ln2 = p.get("/history")
        res = [(x["market"], x["result"], x["pnl"]) for x in hh.get("history", [])]
        step(f"{p.name} after settlement", st, {"cash": w.get("cash"), "escrow": w.get("escrow"),
                                                "pnl": w.get("pnl"), "history": res}, ln2,
             st == 200 and w.get("escrow") == 0 and w.get("cash") == 1000 + sum(x[2] for x in res))
    st, lb, ln = api.get("/leaderboard", actor="public")
    names = {p.name for p in players}
    step("players on the public leaderboard", st, [r for r in lb.get("rows", []) if r["name"] in names], ln,
         st == 200)
    out["seconds"] = round(time.time() - t0)
    out["players"] = [p.name for p in players]
    (SCRATCH / "public_result.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
