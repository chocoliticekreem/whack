"""Quality bar for the brokers: fixture replay, brokers off vs on, over K rng seeds. Offline (rules brokers).

    python3 services/multiverse/eval.py [K=5] [n=50]

Truth is fixture.TRUE_ASSUMPTIONS: ten one-minute frames, five-minute horizon, so the market rolls into a
second epoch at +6. Bus delay passes when within 10 % of the truth or of the mean of the noisy bus reads
(what the data actually support). Rain is the probability that it is raining by the frame's epoch minute,
reported at the first frame after the onset (+4) and checked on every frame from +5. "insiderLeads" runs the
brokered replay again with an insider broker whose idea is the truth and checks it tops the leaderboard;
"rightLeads" checks that in both brokered runs the final leader's every view is within TOL of the truth.
"alpha" checks the brokers reading informative feeds (rain, bikes, tube, bus) end with a higher mean
cumulative P&L than those reading noise (aviation, grid, news, attention); the per-broker table at the end
is the "which data has alpha" ranking.
"""
from __future__ import annotations

import os
import sys
from multiprocessing import Pool

import brokers

os.environ.pop("ANTHROPIC_API_KEY", None)
brokers.load_env = lambda: None

import engine  # noqa: E402
import feeds  # noqa: E402
import fixture  # noqa: E402

DIMS = ("busDelayMin", "touristFactor", "movingShare", "capacityScale")
REL_TOL = 0.10
OFF_SLACK = 0.05
RAIN_MIN = 0.9
ESS_MIN = 10.0
DELIST_MAX = 0.6
TRUTH = fixture.TRUE_ASSUMPTIONS
RAIN_BROKER = "B-RAIN"
INFORMATIVE = ("B-RAIN", "B-BIKES", "B-TUBE", "B-BUS")
NOISE = ("B-AIR", "B-GRID", "B-NEWS", "B-WIKI")
TOL = {"busDelayMin": 1.0, "touristFactor": 0.2, "movingShare": 0.1, "capacityScale": 0.2, "rainAtMin": 1.5}
TRUTH_BROKER = {"id": "B-TRUTH", "persona": "Insider", "archetype": "resident", "voice": "knows", "conviction": 0.9,
                "lean": {k: TRUTH[k] for k in DIMS}}


def posterior(live: list[dict], minute: int) -> dict[str, float]:
    """Price-weighted means; rain = probability it is raining by `minute`."""
    total = sum(w["price"] for w in live) or 1.0
    out = {k: sum(w["price"] * float(w["assumptions"][k]) for w in live) / total for k in DIMS}
    out["rain"] = sum(w["price"] for w in live if w["assumptions"].get("rainAtMin") is not None
                      and w["assumptions"]["rainAtMin"] <= minute) / total
    return out


def rel_err(post: dict[str, float]) -> dict[str, float]:
    out = {k: abs(post[k] - float(TRUTH[k])) / float(TRUTH[k]) for k in DIMS}
    out["rain"] = 1.0 - post["rain"]
    return out


def consistent(idea: dict, rain_truth: float) -> bool:
    """Every view the idea holds is within TOL of the truth (rain onset in the current epoch's minutes)."""
    truth = dict(TRUTH, rainAtMin=rain_truth)
    for k, tol in TOL.items():
        v = idea.get(k)
        if v is None:
            continue
        if k == "rainAtMin" and v < 0:
            return False
        if abs(float(v) - float(truth[k])) > tol:
            return False
    return not idea.get("event")


def run(rng_seed: int, n: int, with_brokers: bool, roster: list[dict] | None = None) -> dict:
    seed = fixture.build_seed()
    bk = brokers.Brokerage(seed, fixture.HORIZON, roster) if with_brokers else None
    mv = engine.Multiverse(seed, n=n, horizon=fixture.HORIZON, crn_seed=fixture.CRN_SEED, rng_seed=rng_seed,
                           brokerage=bk)
    mv.spawn()
    frames = feeds.load_frames(fixture.RAW_DIR)
    ess, delist, ranks, posts, absmin = [], [], [], [], []
    for frame in frames:
        obs = mv.advance(frame)
        live = mv.live()
        ess.append(1.0 / sum(w["price"] ** 2 for w in live))
        delist.append(len(obs["delisted"]) / mv.n)
        posts.append(posterior(live, obs["minute"]))
        absmin.append((frame["ts"] - seed["at"]) / 60_000)
        if bk:
            ranks.append([row["id"] for row in bk.leaderboard(live)])
    onset = next(i for i, m in enumerate(absmin) if m > TRUTH["rainAtMin"])
    bus_mean = sum(f["busDelayMin"] for f in frames) / len(frames)
    rain_truth = max(0.0, TRUTH["rainAtMin"] - (mv.seed["at"] - seed["at"]) / 60_000)
    out = {"post": posts[-1], "err": rel_err(posts[-1]), "essMin": min(ess), "delistMax": max(delist),
           "busVsGold": abs(posts[-1]["busDelayMin"] - bus_mean) / bus_mean,
           "rainOnset": posts[onset]["rain"], "rainAfter": min(p["rain"] for p in posts[onset + 1:]),
           "vix": [round(v["vix"], 1) for v in mv.vix_history], "epochs": mv.epochs}
    if bk:
        board = bk.leaderboard(mv.live())
        out |= {"leader": board[0]["id"], "leaderConsistent": consistent(board[0]["idea"], rain_truth),
                "cumulative": {b["id"]: b["cumulative"] for b in board},
                "rainRank": ranks[min(onset + 1, len(ranks) - 1)].index(RAIN_BROKER) + 1,
                "minSlots": min(b["slots"] for b in board), "floor": getattr(brokers, "SLOT_FLOOR", 1),
                "mm": sum(1 for w in mv.live() if w.get("broker") is None),
                "maxW": max(b["weight"] for b in board), "minW": min(b["weight"] for b in board)}
    return out


def mean(values: dict[str, float], ids: tuple[str, ...]) -> float:
    return sum(values[i] for i in ids) / len(ids)


def evaluate(args: tuple[int, int]) -> tuple[int, dict, dict, dict]:
    s, n = args
    return s, run(s, n, False), run(s, n, True), run(s, n, True, brokers.ROSTER + [TRUTH_BROKER])


def fmt(r: dict) -> str:
    errs = " ".join(f"{k[:5]} {v * 100:5.1f}%" for k, v in r["err"].items())
    return (f"{errs}  bus {r['post']['busDelayMin']:.2f}  rain@+4 {r['rainOnset']:.2f} rain>=+5 "
            f"{r['rainAfter']:.2f}  essMin {r['essMin']:5.1f}  delistMax {r['delistMax']:.2f}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    k = int(args[0]) if args else 5
    n = int(args[1]) if len(args) > 1 else 50
    with Pool(min(k, os.cpu_count() or 1)) as pool:
        results = pool.map(evaluate, [(s, n) for s in range(1, k + 1)])
    passes = 0
    tally: dict[str, int] = {}
    for s, off, on, insider in results:
        checks = {
            "within10%": on["err"]["touristFactor"] <= REL_TOL
            and min(on["err"]["busDelayMin"], on["busVsGold"]) <= REL_TOL,
            "rain>=0.9": on["rainAfter"] >= RAIN_MIN,
            "vsOff+5%": all(on["err"][d] <= off["err"][d] + OFF_SLACK for d in (*DIMS, "rain")),
            "ess>=10": on["essMin"] >= ESS_MIN,
            "delist<=60%": on["delistMax"] <= DELIST_MAX,
            "rightLeads": on["leaderConsistent"] and insider["leaderConsistent"],
            "insiderLeads": insider["leader"] == "B-TRUTH",
            "rainTop3": on["rainRank"] <= 3,
            "floor": on["minSlots"] >= on["floor"],
            "weights": on["maxW"] < 0.9 and on["minW"] >= brokers.UNIFORM_MIX / len(brokers.ROSTER) - 1e-9,
            "alpha": mean(on["cumulative"], INFORMATIVE) > mean(on["cumulative"], NOISE),
        }
        ok = all(checks.values())
        passes += ok
        for name, v in checks.items():
            tally[name] = tally.get(name, 0) + v
        print(f"seed {s}  OFF {fmt(off)}")
        print(f"        ON  {fmt(on)}")
        print(f"        leader {on['leader']}  insider-run leader {insider['leader']}  weather rank one frame after"
              f" onset {on['rainRank']}  minSlots {on['minSlots']}  mm {on['mm']}"
              f"  weights {on['minW']:.3f}-{on['maxW']:.2f}")
        print("        " + "  ".join(f"{name} {'ok' if v else 'FAIL'}" for name, v in checks.items())
              + f"  => {'PASS' if ok else 'FAIL'}")
    print("\nwhich data has alpha (cumulative P&L, mean over seeds):")
    ids = list(results[0][2]["cumulative"])
    for bid in sorted(ids, key=lambda b: -sum(r[2]["cumulative"][b] for r in results)):
        feed = next(b["feed"] for b in brokers.ROSTER if b["id"] == bid)
        kind = "informative" if bid in INFORMATIVE else "noise" if bid in NOISE else "-"
        print(f"   {bid:<9} {feed:<11} {kind:<12} {sum(r[2]['cumulative'][bid] for r in results) / k:+.2f}")
    print("VIX series (seed 1, brokers on): " + " ".join(f"{v:.1f}" for v in results[0][2]["vix"]))
    print("\nper criterion: " + "  ".join(f"{name} {v}/{k}" for name, v in tally.items()))
    print(f"{passes}/{k} seeds pass the bar")


if __name__ == "__main__":
    main()
