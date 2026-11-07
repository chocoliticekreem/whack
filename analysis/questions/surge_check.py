"""Is the 'hub surge' edge (furthest below its usual Saturday catches up) more than 'always pick Bank'?

    python3 analysis/questions/surge_check.py
"""
from __future__ import annotations

import series as S
from evaluate import value_at
from load import MIN


def main():
    hr = S.hub_ratio()
    picks, hits, per = {}, 0, {}
    n = 0
    t = hr[0][0] - hr[0][0] % (5 * MIN) + 35 * MIN
    while t + 5 * MIN <= hr[-1][0]:
        a, b = value_at(hr, t, 6 * MIN), value_at(hr, t + 5 * MIN, 6 * MIN)
        if a and b:
            pick = min(a, key=a.get)
            win = max(a, key=lambda o: b[o] - a[o])
            picks[pick] = picks.get(pick, 0) + 1
            per.setdefault(pick, [0, 0])
            per[pick][0] += pick == win
            per[pick][1] += 1
            hits += pick == win
            n += 1
        t += 5 * MIN
    print(f"opens={n} furthest-below wins {hits}/{n} = {hits / n:.0%} (chance 20%)")
    print("by pick:", {k: f"{h}/{m}" for k, (h, m) in per.items()})
    always = {o: 0 for o in S.HUBS}
    t = hr[0][0] - hr[0][0] % (5 * MIN) + 35 * MIN
    while t + 5 * MIN <= hr[-1][0]:
        a, b = value_at(hr, t, 6 * MIN), value_at(hr, t + 5 * MIN, 6 * MIN)
        if a and b:
            always[max(a, key=lambda o: b[o] - a[o])] += 1
        t += 5 * MIN
    print("winners (= 'always pick X' hit counts):", always)


if __name__ == "__main__":
    main()
