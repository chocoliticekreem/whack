"""The "live reader": 20 s before each market closes, back the side the live reading is already on (over/under
markets) or the hub that has risen most since open (hub_surge). Tests whether a stale price is a free lunch.

    python3 analysis/live_trading/reader.py --url http://127.0.0.1:8161 --minutes 60

Logs to scratch/reader.jsonl; final record in scratch/reader.json.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid

from client import SCRATCH, Api, Log, Player

MARGIN = {"london_vix": 0.10, "city_pulse": 0.30, "heathrow_stack": 0.0}
VALUE = {"london_vix": ("vix_driver", "vix"), "city_pulse": ("pulse", "pulse"), "heathrow_stack": ("stacks", "total")}


def now_ms() -> int:
    return int(time.time() * 1000)


def line_of(q: str) -> float | None:
    got = re.findall(r"(\d+(?:\.\d+)?)\?$", q)
    return float(got[0]) if got else None


def pick(m: dict, open_vs: dict) -> tuple[str, str] | None:
    sub = m["series"]
    sig = {s["id"]: s.get("data") or {} for s in m.get("signals") or []}
    if sub in VALUE:
        sid, k = VALUE[sub]
        v, line = sig.get(sid, {}).get(k), line_of(m["question"])
        if v is None or line is None or abs(v - line) <= MARGIN[sub]:
            return None
        return ("over" if v > line else "under"), f"{k} {v} vs line {line}"
    if sub == "hub_surge" and m["id"] in open_vs:
        hubs = {h["id"]: h["vsUsual"] for h in sig.get("vs_usual", {}).get("hubs", [])}
        gains = {h: hubs[h] - open_vs[m["id"]][h] for h in hubs if h in open_vs[m["id"]]}
        if not gains or max(gains.values()) <= 0:
            return None
        best = max(gains, key=gains.get)
        return best, f"{best} up {gains[best]:+.3f} since open"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8161")
    ap.add_argument("--minutes", type=float, default=60)
    a = ap.parse_args()
    api = Api(a.url, Log(SCRATCH / "reader.jsonl"))
    p = Player.new(api, "bot-reader")
    end = now_ms() + int(a.minutes * 60_000)
    done, bets, open_vs = set(), {}, {}
    while now_ms() < end + 420_000:
        st, d, _ = api.get("/markets", quiet=True)
        if st == 200:
            for m in d["markets"]:
                if m["series"] == "hub_surge" and m["id"] not in open_vs:
                    hubs = next((s["data"]["hubs"] for s in m["signals"] if s["id"] == "vs_usual"), [])
                    open_vs[m["id"]] = {h["id"]: h["vsUsual"] for h in hubs}
                left = m["closesAt"] - d["now"]
                if m["id"] in done or m["status"] != "open" or not 15_000 <= left <= 22_000 or now_ms() > end:
                    continue
                done.add(m["id"])
                got = pick(m, open_vs)
                if not got:
                    continue
                o, why = got
                price = next(x["prob"] for x in m["options"] if x["id"] == o)
                st2, r, ln = p.bet(m["id"], o, 10, f"reader-{uuid.uuid4().hex[:8]}")
                bets[r.get("betId") or f"rej-{ln}"] = {"market": m["id"], "series": m["series"], "option": o,
                                                       "why": why, "price": price, "status": r.get("status"),
                                                       "error": r.get("error"), "line": ln}
        for bid, b in bets.items():
            if b["status"] == "pending":
                st3, r, _ = p.get("/bet", {"id": bid}, quiet=True)
                if st3 == 200 and r["status"] != "pending":
                    b.update(status=r["status"], odds=r.get("odds"), reason=r.get("reason"))
        if now_ms() > end and not any(b["status"] == "pending" for b in bets.values()):
            _, pos, _ = p.get("/positions", quiet=True)
            if not pos.get("positions"):
                break
        time.sleep(1)
    _, h, _ = p.get("/history", {"limit": 500})
    _, w, _ = p.get("/wallet")
    for x in h.get("history", []):
        if x["betId"] in bets:
            bets[x["betId"]].update(result=x["result"], pnl=x["pnl"])
    (SCRATCH / "reader.json").write_text(json.dumps({"player": p.json(), "bets": bets, "wallet": w}, indent=1))


if __name__ == "__main__":
    main()
