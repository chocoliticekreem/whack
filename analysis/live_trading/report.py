"""Tables for the README from a run's scratch files (state.json, trades.jsonl, the scratch ledger, restart files).

    python3 analysis/live_trading/report.py [--raw /Users/sonionring/orca/whack/data/raw]

Markdown to stdout. Read-only: the ledger is opened with mode=ro.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from client import SCRATCH

AI_STAKE = 100


def hm(ms) -> str:
    return "-" if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M:%S")


def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def tfl_time(raw: Path, fetched_ms: int) -> str | None:
    """TfL's own timeUtc inside the recorder line stamped `fetched_ms` (crowding only)."""
    d = datetime.fromtimestamp(fetched_ms / 1000, timezone.utc)
    for ext in (".ndjson.gz", ".ndjson"):
        p = raw / "crowding" / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}{ext}"
        if not p.is_file():
            continue
        opener = gzip.open if ext.endswith("gz") else open
        with opener(p, "rt") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
                if abs(ts.timestamp() * 1000 - fetched_ms) < 1000:
                    rows = rec.get("payload") or []
                    return rows[0].get("timeUtc") if rows else None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/Users/sonionring/orca/whack/data/raw")
    ap.add_argument("--url", help="also re-read every bot wallet from this server after the run (final check)")
    a = ap.parse_args()
    raw = Path(a.raw)
    st = json.loads((SCRATCH / "state.json").read_text())
    markets = st["markets"]
    bets = [b for bot in st["bets"].values() for b in bot.values()]

    # ---------------------------------------------------------- settlement
    print("## Settlement\n")
    rows, cnt = [], Counter()
    for m in sorted(markets.values(), key=lambda m: (m["settlesAt"], m["id"])):
        s = m.get("settlement") or {}
        status = "unsettled" if "winner" not in m else ("void" if m["winner"] is None else "settled")
        cnt[(m["series"], status)] += 1
        f = s.get("fetchedAt")
        tfl = tfl_time(raw, f) if f and s.get("source") == "TfL Crowding API" else None
        tfl_ms = int(datetime.fromisoformat(tfl.replace("Z", "+00:00")).timestamp() * 1000) if tfl else None
        rows.append([m["id"], status, m.get("winner"), s.get("value"), s.get("source"), hm(m["closesAt"]),
                     hm(m["settlesAt"]), hm(f), "-" if f is None else f"{(f - m['closesAt']) / 1000:+.0f}",
                     "-" if tfl_ms is None else f"{hm(tfl_ms)} ({(tfl_ms - m['closesAt']) / 1000:+.0f})",
                     (m.get("reading") or "")[:70]])
    print(table(["market", "status", "winner", "value", "source", "closes", "settles", "fetchedAt",
                 "fetched − close (s)", "TfL timeUtc (− close s)", "reading"], rows))
    print()
    series = sorted({k[0] for k in cnt})
    print(table(["series", "settled (real)", "void", "unsettled"],
                [[s, cnt[(s, "settled")], cnt[(s, "void")], cnt[(s, "unsettled")]] for s in series]))
    print()

    # ---------------------------------------------------------- pending bets
    print("## Pending bets\n")
    by = defaultdict(Counter)
    reasons = Counter()
    lat = []
    after_close = []
    for b in bets:
        by[b["bot"]][b["status"]] += 1
        by[b["bot"]]["placed"] += 1
        if b["status"] in ("cancelled", "rejected", "lost"):
            reasons[(b["status"], (b.get("reason") or "")[:60])] += 1
        if b["status"] == "filled" and b.get("filledAt") and b.get("placedAt"):
            lat.append(b["filledAt"] - b["placedAt"])
            if b["filledAt"] >= b["closesAt"]:
                after_close.append(b)
    head = ["bot", "placed", "filled", "cancelled", "rejected at /bet", "lost on restart", "fill rate"]
    rows = []
    for name, c in sorted(by.items()):
        rows.append([name, c["placed"], c["filled"], c["cancelled"], c["rejected"], c["lost"],
                     f"{c['filled'] / c['placed']:.0%}" if c["placed"] else "-"])
    tot = Counter()
    for c in by.values():
        tot.update(c)
    rows.append(["**all**", tot["placed"], tot["filled"], tot["cancelled"], tot["rejected"], tot["lost"],
                 f"{tot['filled'] / max(1, tot['placed']):.0%}"])
    print(table(head, rows))
    print()
    print(table(["outcome", "reason", "n"], [[k[0], k[1], n] for k, n in reasons.most_common()]))
    print()
    if lat:
        lat.sort()
        print(f"Fill latency placed→filled: median {lat[len(lat) // 2] / 1000:.1f} s, max {lat[-1] / 1000:.1f} s, "
              f"n={len(lat)}. Fills at or after close: **{len(after_close)}**.\n")
    late = [b for b in bets if b["bot"] == "latecomer"]
    lc = Counter((b["series"], b["status"], (b.get("reason") or "")[:40]) for b in late)
    print(table(["latecomer series", "status", "reason", "n"], [[*k, n] for k, n in sorted(lc.items())]))
    print()

    # ---------------------------------------------------------- halts
    print("## Halts and suspends\n")
    print(table(["market", "at", "reason", "log line"],
                [[h["market"], hm(h["at"]), h["reason"], h["line"]] for h in st["halts"]]))
    print()

    # ---------------------------------------------------------- reconciliation
    print("## Ledger reconciliation\n")
    rec = st["recon"]
    bad = [r for r in rec if r.get("issues") or r.get("error")]
    print(f"{len(rec)} wallet+history checks over {len({r['round'] for r in rec})} rounds; "
          f"**{len(bad)} with a discrepancy**.\n")
    for r in bad[:20]:
        print(f"- round {hm(r['round'])} {r['bot']}: {r.get('issues') or r.get('error')} (lines {r.get('lines')})")
    print()

    # ---------------------------------------------------------- P&L by strategy
    print("## P&L by strategy\n")
    rows = []
    final = {}
    for r in rec:
        if "got" in r:
            final[r["bot"]] = r["got"]
    for name in sorted(by):
        s = [b for b in bets if b["bot"] == name and b.get("result")]
        n = len(s)
        pnl = [b["pnl"] for b in s]
        staked = sum(b["stake"] for b in s if b["result"] != "void")
        tot_pnl = sum(pnl)
        sd = (sum((x - tot_pnl / n) ** 2 for x in pnl) / (n - 1)) ** 0.5 if n > 1 else 0
        won = sum(1 for b in s if b["result"] == "won")
        lost = sum(1 for b in s if b["result"] == "lost")
        roi = tot_pnl / staked if staked else 0
        se = sd * math.sqrt(n) / staked if staked else 0
        rows.append([name, n, f"{won}/{won + lost}", staked, tot_pnl, f"{roi:+.0%} ± {se:.0%}",
                     (final.get(name) or {}).get("cash"), (final.get(name) or {}).get("pnl")])
    print(table(["bot", "settled bets", "won", "staked", "P&L", "ROI ± 1 SE", "final cash", "server pnl"], rows))
    print()
    ser = defaultdict(lambda: [0, 0, 0])
    for b in bets:
        if b.get("result") in ("won", "lost"):
            k = (b["bot"], b["series"])
            ser[k][0] += 1
            ser[k][1] += b["stake"]
            ser[k][2] += b["pnl"]
    print(table(["bot", "series", "bets", "staked", "P&L", "ROI"],
                [[k[0], k[1], v[0], v[1], v[2], f"{v[2] / v[1]:+.0%}"] for k, v in sorted(ser.items())]))
    print()

    # ---------------------------------------------------------- AI traders
    print("## AI traders (100-point bets, in-memory record)\n")
    ai = defaultdict(lambda: Counter())
    for m in markets.values():
        if "winner" not in m:
            continue
        for b in m.get("aiBets") or []:
            c = ai[b["trader"]]
            c["bets"] += 1
            if m["winner"] is None:
                c["void"] += 1
                continue
            won = b["option"] == m["winner"]
            c["won"] += won
            c["pnl"] += round(b["stake"] * b["odds"] - b["stake"]) if won else -b["stake"]
            c[f"pnl:{m['series']}"] += round(b["stake"] * b["odds"] - b["stake"]) if won else -b["stake"]
    start = {t["id"]: t["points"] for t in (st.get("tradersStart") or {}).get("traders", [])}
    rows = []
    for tid, c in sorted(ai.items(), key=lambda kv: -kv[1]["pnl"]):
        per = ", ".join(f"{k[4:]} {v:+d}" for k, v in c.items() if k.startswith("pnl:"))
        rows.append([tid, c["bets"], f"{c['won']}/{c['bets'] - c['void']}", c["pnl"],
                     f"{c['pnl'] / max(1, (c['bets'] - c['void']) * AI_STAKE):+.0%}", per])
    print(table(["trader", "bets", "won", "P&L (observed markets)", "ROI", "by series"], rows))
    print()
    house_ai = -sum(c["pnl"] for c in ai.values())

    # ---------------------------------------------------------- house
    print("## House\n")
    db = sqlite3.connect(f"file:{SCRATCH / 'ledger.db'}?mode=ro", uri=True)
    house = db.execute("SELECT COALESCE(SUM(amount), 0) FROM entries WHERE account = 'house'").fetchone()[0]
    players = db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    nb = db.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
    open_b = db.execute("SELECT COUNT(*) FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id "
                        "WHERE s.bet_id IS NULL").fetchone()[0]
    outc = dict(db.execute("SELECT outcome, COUNT(*) FROM settlements GROUP BY outcome").fetchall())
    print(f"Scratch ledger: {players} players, {nb} bets ({open_b} open), settlements {outc}. House P&L vs players "
          f"**{house:+d}**; vs the AI traders (not on the ledger) **{house_ai:+d}** over the markets observed.\n")
    print("```\n" + "\n".join(audit(db)) + "\n```\n")

    # ---------------------------------------------------------- calibration
    print("## Calibration at open\n")
    pts, brier, brier_u, by_series = [], 0.0, 0.0, defaultdict(lambda: [0, 0.0, 0.0])
    for m in markets.values():
        if not m.get("winner") or m.get("openLagMs") is None or m["openLagMs"] > 15_000:
            continue
        k = len(m["open"])
        b = sum((p - (o == m["winner"])) ** 2 for o, p in m["open"].items())
        bu = sum((1 / k - (o == m["winner"])) ** 2 for o in m["open"])
        s = by_series[m["series"]]
        s[0] += 1
        s[1] += b
        s[2] += bu
        for o, p in m["open"].items():
            pts.append((p, o == m["winner"]))
    buckets = defaultdict(lambda: [0, 0, 0.0])
    for p, y in pts:
        lo = min(9, int(p * 10))
        buckets[lo][0] += 1
        buckets[lo][1] += y
        buckets[lo][2] += p
    print(table(["price bucket", "options", "won", "mean price", "win rate"],
                [[f"{lo / 10:.1f}-{(lo + 1) / 10:.1f}", v[0], v[1], f"{v[2] / v[0]:.2f}", f"{v[1] / v[0]:.2f}"]
                 for lo, v in sorted(buckets.items())]))
    print()
    print(table(["series", "markets", "Brier (open)", "Brier (uniform)"],
                [[s, v[0], f"{v[1] / v[0]:.3f}", f"{v[2] / v[0]:.3f}"] for s, v in sorted(by_series.items())]))
    print()

    # ---------------------------------------------------------- edge
    print("## Edge cases\n")
    print(table(["case", "status", "ok", "line", "response"],
                [[e["case"], e["status"], "yes" if e["ok"] else "**no**", e.get("line"),
                  json.dumps(e.get("resp"))[:150].replace("|", "/")] for e in st["edge"]]))
    print()
    stale(markets)
    reader()
    if a.url:
        final_check(a.url, st)
    vix = [m for m in markets.values() if m["series"] == "london_vix"]
    sim = [(m["id"], b["option"], m.get("winner"), b["odds"]) for m in markets.values() for b in m.get("aiBets") or []
           if b["trader"] == "sim"]
    print(f"london_vix listed {len(vix)} times; Simulation trader bets: {len(sim)}: {sim[:20]}")


def final_check(url: str, st: dict) -> None:
    """Every bot's wallet and history once the last markets settled: cash + escrow = grant + realised P&L, the
    history's P&L sums to the wallet's, and open positions match escrow."""
    from client import Api, Log, Player
    api = Api(url, Log(SCRATCH / "final.jsonl"))
    rows = []
    for pj in st["players"]:
        p = Player(api, pj["name"], pj["id"], pj["token"])
        _, w, wl = p.get("/wallet")
        _, h, _ = p.get("/history", {"limit": 500})
        _, pos, _ = p.get("/positions")
        hist_pnl = sum(x["pnl"] for x in h.get("history", []))
        held = sum(x["stake"] for x in pos.get("positions", []))
        ok = (w["cash"] + w["escrow"] == 1000 + w["pnl"]["allTime"] and hist_pnl == w["pnl"]["allTime"]
              and held == w["escrow"])
        hs = [x for x in h.get("history", []) if x["result"] in ("won", "lost")]
        staked = sum(x["stake"] for x in hs)
        won = sum(1 for x in hs if x["result"] == "won")
        pnls = [x["pnl"] for x in hs]
        n = len(pnls)
        sd = (sum((x - sum(pnls) / n) ** 2 for x in pnls) / (n - 1)) ** 0.5 if n > 1 else 0
        roi = f"{sum(pnls) / staked:+.0%} ± {sd * math.sqrt(n) / staked:.0%}" if staked else "-"
        rows.append([pj["bot"], f"{won}/{n}", staked, roi, w["cash"], w["escrow"], w["pnl"]["allTime"], hist_pnl,
                     held, "yes" if ok else "**no**", wl])
    print("## Final wallet check\n")
    print(table(["bot", "won", "staked (settled)", "ROI ± 1 SE", "cash", "escrow", "wallet pnl", "history pnl",
                 "open stakes", "reconciles", "line"], rows))
    print()


def stale(markets: dict) -> None:
    """From scratch/prices.jsonl: each over/under market's last sample before close, the live value vs the line,
    the price of the side the value is on, how many futures were still alive, and the result."""
    path = SCRATCH / "prices.jsonl"
    if not path.exists():
        return
    last: dict[str, dict] = {}
    for line in open(path):
        r = json.loads(line)
        m = markets.get(r["m"])
        if m and r["t"] < m["closesAt"] and r["status"] in ("open", "halted"):
            last[r["m"]] = r
    rows = []
    hit = n = 0
    for mid, r in sorted(last.items()):
        m = markets[mid]
        if m["series"] not in ("london_vix", "city_pulse", "heathrow_stack") or "winner" not in m:
            continue
        v = r["clue"].get("vix", r["clue"].get("pulse", r["clue"].get("total")))
        line = re.findall(r"(\d+(?:\.\d+)?)\?$", m["question"])
        if v is None or not line or m["winner"] is None:
            continue
        line = float(line[0])
        side = "over" if v > line else "under"
        n += 1
        hit += m["winner"] == side
        rows.append([mid, hm(r["t"]), v, line, side, f"{r['p'][side]:.2f}", r["alive"], m["winner"],
                     "yes" if m["winner"] == side else "no"])
    print("## Live value vs price at the last sample before close\n")
    print(table(["market", "sampled", "live value", "line", "side it is on", "price of that side", "futures alive",
                 "winner", "side won"], rows))
    if n:
        print(f"\nThe side the live value was already on won {hit}/{n}.\n")


def reader() -> None:
    path = SCRATCH / "reader.json"
    if not path.exists():
        return
    d = json.loads(path.read_text())
    bets = list(d["bets"].values())
    rows = [[b["market"], b["option"], b["why"], f"{b['price']:.2f}", b.get("odds"), b["status"],
             b.get("result"), b.get("pnl")] for b in bets]
    print("## Live reader bot (bets 20 s before close on the side the live reading is on)\n")
    print(table(["market", "option", "why", "price", "odds", "status", "result", "pnl"], rows))
    by = defaultdict(lambda: [0, 0, 0])
    for b in bets:
        if b.get("result") in ("won", "lost"):
            x = by[b["series"]]
            x[0] += 1
            x[1] += b["result"] == "won"
            x[2] += b["pnl"]
    print()
    print(table(["series", "bets", "won", "P&L (10-pt stakes)", "ROI"],
                [[k, v[0], v[1], v[2], f"{v[2] / (10 * v[0]):+.0%}"] for k, v in sorted(by.items())]))
    print(f"\nreader wallet: cash {d['wallet'].get('cash')}, pnl {d['wallet'].get('pnl')}\n")


def audit(db) -> list[str]:
    """Ledger.audit()'s invariants, re-run read-only."""
    bad = []
    for r in db.execute("SELECT bet_id, SUM(amount) FROM entries WHERE bet_id IS NOT NULL GROUP BY bet_id "
                        "HAVING SUM(amount) <> 0"):
        bad.append(f"bet {r[0]} entries sum to {r[1]}")
    for r in db.execute("SELECT player, account, SUM(amount) FROM entries WHERE account IN ('player_cash', "
                        "'player_escrow') GROUP BY player, account HAVING SUM(amount) < 0"):
        bad.append(f"{r[0]} {r[1]} negative {r[2]}")
    total = db.execute("SELECT SUM(amount) FROM entries").fetchone()[0]
    grants = db.execute("SELECT SUM(amount) FROM entries WHERE kind = 'grant'").fetchone()[0]
    if total != grants:
        bad.append(f"entries sum {total} != grants {grants}")
    return bad or [f"audit clean: entries sum {total} = grants {grants}, every bet nets to zero, no negative balance"]


if __name__ == "__main__":
    main()
