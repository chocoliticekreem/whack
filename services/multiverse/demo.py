"""End-to-end replay on the fixture: spawn -> observations -> print the exchange.

    python3 services/multiverse/demo.py [observations=3] [n=50] [--brokers]

--brokers spawns with data-source brokers (Haiku when ANTHROPIC_API_KEY is set, rules otherwise) and
prints the broker leaderboard after each frame. Every frame prints the London VIX and what moved it.
"""
from __future__ import annotations

import sys
import time

from server import App


def show(state: dict, top: int = 6) -> None:
    print(f"  live {state['live']}  delisted {state['delisted']}  effective worlds {state['effectiveWorlds']}"
          f"  minute +{state['minute']}")
    v = state["vix_history"][-1] if state.get("vix_history") else None
    if v:
        print(f"  LONDON VIX {state['vix']:5.1f} ({v['delta']:+.1f}{' SPIKE' if v['spike'] else ''})  moved most by: "
              f"{state['vix_driver']} ({v['driverDelta']:+.1f})")
    for row in state["board"][:top]:
        spark = " ".join(f"{p:.3f}" for p in row["history"][-4:])
        print(f"   {row['id']:<6} {row['price']:.3f}  [{spark}]  {', '.join(row['labels'][:3])}")
    for row in [r for r in state["board"] if r["status"] == "delisted"][:3]:
        print(f"   x {row['id']:<6} delisted: {row['delistReason']}")


def show_brokers(app: App) -> None:
    info = app.brokers()
    latest = [c for c in info["calls"] if c["obs"] == info["calls"][-1]["obs"]] if info["calls"] else []
    for call in latest:
        print(f"  brokers via {call['model']}: {call.get('ms')} ms, {call.get('inputTokens')} in / "
              f"{call.get('outputTokens')} out tokens, rules fallbacks {len(call['fallbacks'])}/{len(call['brokers'])}"
              + (f", errors {call['errors'][:2]}" if call.get("errors") else ""))
    if not latest:
        print("  brokers via rules (no ANTHROPIC_API_KEY)")
    print(f"   {'broker':<11} {'data source':<11} {'persona':<24} {'track':>7} {'weight':>6} {'slots':>5} {'conv':>5}  pnl")
    for b in info["brokers"]:
        pnl = " ".join(f"{p:+.2f}" for p in b["pnl"][-4:])
        print(f"   {b['id']:<11} {b['glyph']} {b['source']:<9} {b['persona'][:24]:<24} {b['trackRecord']:>+7.2f} {b['weight']:>6.2f} "
              f"{b['slots']:>5} {b['conviction'] or 0:>5.2f}  [{pnl}]")
    for b in info["brokers"][:3]:
        print(f"     \"{b['rationale']}\" ({b['id']}, {b['ideaFrom']})")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_brokers = "--brokers" in sys.argv[1:]
    n_obs = int(args[0]) if len(args) > 0 else 3
    n = int(args[1]) if len(args) > 1 else 50
    app = App()
    t0 = time.perf_counter()
    state = app.spawn({"n": n, "mode": "replay", "brokers": "on" if with_brokers else "off"})
    print(f"spawned {state['n']} worlds in {time.perf_counter() - t0:.2f}s from seed '{state['seedSource']}' ({state['area']})")
    show(state)
    if with_brokers:
        show_brokers(app)
    out = state
    for _ in range(n_obs):
        out = app.observe({"next": True})
        obs = out["observation"]
        if obs is None:
            print("replay exhausted")
            break
        print(f"\nobservation {obs['index']} at +{obs['minute']} min: {obs['compared']} cams, "
              f"bus delay {obs['busDelayMin']} -> delisted {len(obs['delisted'])}, spawned {len(obs['spawned'])}")
        show(out)
        if with_brokers:
            show_brokers(app)
    print("\nLondon VIX history: " + " ".join(f"{h['vix']:.1f}" for h in out["vix_history"]))
    print("\nconsensus at horizon (zone: mean +/- sd, implied vol):")
    for z in sorted(out["consensusHorizon"], key=lambda z: -z["iv"])[:5]:
        print(f"   {z['name']:<30} {z['mean']:>6} +/- {z['sd']:<5} iv {z['iv']}")


if __name__ == "__main__":
    main()
