"""End-to-end replay on the fixture: spawn -> observations -> print the exchange.

    python3 services/multiverse/demo.py [observations=3] [n=50]
"""
from __future__ import annotations

import sys
import time

from server import App


def show(state: dict, top: int = 6) -> None:
    print(f"  live {state['live']}  delisted {state['delisted']}  effective worlds {state['effectiveWorlds']}"
          f"  minute +{state['minute']}")
    for row in state["board"][:top]:
        spark = " ".join(f"{p:.3f}" for p in row["history"][-4:])
        print(f"   {row['id']:<6} {row['price']:.3f}  [{spark}]  {', '.join(row['labels'][:3])}")
    for row in [r for r in state["board"] if r["status"] == "delisted"][:3]:
        print(f"   x {row['id']:<6} delisted: {row['delistReason']}")


def main() -> None:
    n_obs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    app = App()
    t0 = time.perf_counter()
    state = app.spawn({"n": n, "mode": "replay"})
    print(f"spawned {n} worlds in {time.perf_counter() - t0:.2f}s from seed '{state['seedSource']}' ({state['area']})")
    show(state)
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
    print("\nconsensus at horizon (zone: mean +/- sd, implied vol):")
    for z in sorted(out["consensusHorizon"], key=lambda z: -z["iv"])[:5]:
        print(f"   {z['name']:<30} {z['mean']:>6} +/- {z['sd']:<5} iv {z['iv']}")


if __name__ == "__main__":
    main()
