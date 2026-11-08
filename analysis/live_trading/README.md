# Live trading pressure test: OverWatch city markets

2026-11-07, 20:57–22:31 UTC (Europe/London = UTC). Bot players traded live London markets (real TfL, multiverse
VIX, the central-London sim) on an isolated city server on :8161, with its own scratch ledger. A small check also
ran through the public stack. Every call is logged in `scratch/*.jsonl`. Line numbers below refer to those files.

**Verdict.** Real data works end to end: 0 voids and 0 fills after close. The ledger reconciled exactly in 120 of
120 checks, and the public Vercel → tunnel → laptop path works. The markets do not hold up. One public rule is
close to a free lunch on hub_surge (9 of 11 wins, +143%). City Pulse opens at 97/3 and prices worse than a coin.
The house lost to every kind of player.

**Re-run:** [RERUN.md](RERUN.md) (Sat 22:36–Sun 00:31, on code with the #72 and #77 pricing fixes).

## How to run

```bash
CITY_PORT=8161 CITY_RAW=/Users/sonionring/orca/whack/data/raw \
  CITY_LEDGER_DB=$PWD/analysis/live_trading/scratch/ledger.db python3 services/city/server.py &
python3 analysis/live_trading/run.py --url http://127.0.0.1:8161 --minutes 60 --edge   # 7 bots + edge cases
python3 analysis/live_trading/reader.py --url http://127.0.0.1:8161 --minutes 60       # the "live reader" bot
python3 analysis/live_trading/prices.py --url http://127.0.0.1:8161 --minutes 70       # price sampler
python3 analysis/live_trading/edge.py --restart-before   # then kill + restart the :8161 server, wait for settle:
python3 analysis/live_trading/edge.py --restart-after
python3 analysis/live_trading/public_check.py            # 3 players, 6 x 10-point bets via Vercel
python3 analysis/live_trading/report.py --url http://127.0.0.1:8161   # all tables below
```

Stdlib only. `scratch/` is gitignored. It holds the ledger, player tokens and logs: `trades.jsonl` (runner, 1782
lines), `reader.jsonl`, `edge.jsonl`, `restart.jsonl`, `public.jsonl`, `prices.jsonl`, `final.jsonl`.

Run used: 75 min, 19 rounds traded (21:00–22:30), 16 reconciled by the runner, plus a final wallet check after the
22:30 clean sheet settled. Seven strategy bots plus the reader: 327 bets placed by the runner and 30 by the reader.

## Ranked bugs

| # | Sev | X breaks when Y | Evidence |
|---|---|---|---|
| 1 | **High** | **hub_surge pays out on information published while betting is open.** The settle delta runs from the reading at open to the first reading after close. TfL's mid-round publish (recorder fetch at :X3:03 / :X8:03) lands about 57 s before close and counts toward that delta. The price barely moves. A bot backing "the hub up most since open" 20 s before close won **9/11 at a mean price of 0.33, +143% ROI** (+157 on 110 staked). The 10% suspend only fires on big moves and then reprices from frozen futures (#3). | `reader.jsonl` (bets on hub_surge-2110…2210); report "Live reader" table. Settlements show the winner is the mid-round mover, e.g. Bank 80% → 89% after the 21:03 publish, `trades.jsonl` 87, 301 |
| 2 | **High** | **City Pulse opens near-certain, and sometimes wrong.** The line is `floor(pulse + median 5-min change) + 0.5`. With the pulse at about 17–20 and a 5-min sd of about 0.3, the line lands up to 1 point from the expected value. Over/under has no favourite cap (`exchange.open_round` caps only `kind == "bracket"`). city_pulse-2110 opened **Over 97%** and settled **Under** (18.45 vs 18.5). A public test player won 170 on a 10-point Under at 18.03. Bets on the 97% side are accepted, then cancelled "price too short" (#5). | `trades.jsonl` 126 (open 0.97/0.03), 403; `public.jsonl` 9, 13, 15; Brier for city_pulse **0.591 vs 0.500 for a coin** (n=13). Random +205% and longshot +119% on city_pulse |
| 3 | **High** | **Prices freeze once 30 of 50 futures are ruled out.** `RULED_OUT_CAP = 0.6` (`pricing.Futures.observe`) stops the filter. After that the price ignores every new reading. At the last pre-close sample, 20 of 28 over/under markets had exactly 20 futures alive. Example: london_vix-2105 was still priced Higher at 42% at 21:02, with the VIX at 3.50 against a 2.96 line. It settled Higher (3.68). This is the mechanism behind #1. | `prices.jsonl` (`alive: 20`); `trades.jsonl` 14, 299; report "Live value vs price" table |
| 4 | Medium | **A restart breaks the open round.** (a) The **position cap is bypassed**: restored bets aren't in the rebuilt market's `m.bets`, so a player holding 20 on hub_surge-2125 added 235, for 255 against the 250 cap. (b) The **hourly headline disappears**: hub_hour-2200 left `/markets` and `headlines` for the rest of the hour, and a late bet got 404 "unknown market". Its 6 bets still settled from data at 22:03. (c) **AI records reset** to 1000. Simulation went from 2872 to 1000; the AI leaderboard is wiped. (d) **london_vix is skipped** ("no fresh reading") in the first round after every start, because `main()` ticks before the VIX poller's first fetch. Seen at 21:00 and 21:25. (e) `/round.previous` is null. (f) Pending orders are forgotten, so GET /bet returns 404 (documented debt). | `restart.jsonl` 17–27; `server.log` (37 open bets restored, 0 refunded); `trades.jsonl` 613–614 (7 s down), 644, 1365; `scratch/restart_after.json` |
| 5 | Medium | **A bet is accepted as "pending" when it can never fill.** Accept-time checks ignore the price and other pending stakes. 49 of the runner's bets were cancelled after acceptance: 32 for "market closed" (late bets, correct), 14 for "price too short", 2 for "market paused" and 1 for the loss limit. **Fills run in delay order, not placement order.** A 200 bet the player had explicitly confirmed (city_pulse, 5 s delay) was cancelled for the loss limit, because a later 100 bet on london_vix (2 s delay) filled first. | `edge.jsonl` (confirm player: city_pulse-2115 200 "cancelled: Daily loss limit…", london_vix-2115 100 filled); report "Pending bets" |
| 6 | Medium | **The confirm guard is bypassed by firing bets together.** `Integrity.fill_due` mints the confirm token on the server at fill time. Four 250 bets placed at once with 1000 cash all pass precheck. The second one fills at 33% of the remaining cash with no confirmation. The loss limit still held: escrow stayed at 500. | `edge.jsonl` "over" player: hub_surge-2115 250 + london_vix-2115 250 filled, 2 cancelled |
| 7 | Low–Med | **The settlement snapshot hides when TfL measured the value.** `fetchedAt` is the recorder fetch (always close + 243 s), but TfL's `timeUtc` for that value is always **close − 180 s**. "City Pulse at 21:00" is TfL's 20:56 number. It was not in the recorder's fetch 57 s before close, so TfL's publish lag is somewhere between 2 and 7 min. If that lag is ever under 3 min, someone polling TfL directly sees the settling value before close. *Inferred, not measured: I did not poll TfL directly.* | report "Settlement" table (`TfL timeUtc` column, read from the raw recorder files) |
| 8 | Low | **The daily loss limit counts gross losses, so a winner gets locked out.** The Kelly bot was up +593 and still had 28 bets refused for "Daily loss limit: you can risk 1 more points". | `trades.jsonl` (kelly, 409 loss_limit) |
| 9 | Low | **The AI traders sit outside the ledger.** The house lost **−1,387** to them on the markets observed. It does not show in `Ledger.house()`, exposure or the cap. Already noted in market-structure.md. | report "AI traders" |

Harness notes, not product bugs. Three edge rows read **no** but the server behaved correctly:
- "same key after the fill": the bet was cancelled for price too short, and the replay correctly returned the same
  betId as cancelled.
- Two "bet after close → 404": these were markets the restart had dropped (#4b).

## Did every market settle on a real reading?

Yes: **0 voids**. The runner observed 46 settled markets: city_pulse 15, hub_surge 15, london_vix 15,
heathrow_stack 1. Another 9 settled while the observer was down or after it stopped, and the ledger confirms each
one settled from data:
- 4 after the restart: city_pulse-2120, hub_surge-2120, london_vix-2125, hub_hour-2200;
- 4 after the runner stopped: city_pulse and hub_surge for 22:20 and 22:25;
- clean_sheet-2230, which settled YES at 22:30 ("All 6 lines stayed Good").

hub_hour-2300 had not settled when the server stopped; 5 bets on it remain open in the scratch ledger.

| source | fetchedAt − closesAt | data after close? |
|---|---|---|
| TfL Crowding (hub_surge, city_pulse) | +243 s every round (fetch at :X3:03 / :X8:03) | the fetch is after close, but TfL's `timeUtc` is close − 180 s (#7) |
| multiverse VIX | +3 s in 16 of 17; −57 s once (london_vix-2200: no frame between 21:58:03 and T) | yes: last frame ≤ T, where T is 60 s after close |
| adsb.lol stacks (heathrow_stack-2125) | +3 s | yes |
| TfL Line Status (clean_sheet-2230) | last reading 22:29:03 | yes |

Sample snapshots: `{"value": 19.46, "source": "TfL Crowding API", "fetchedAt": 21:03:03}` for city_pulse-2100
(closes 20:59:00, TfL timeUtc 20:56:00); `{"value": 3.68, "source": "OverWatch multiverse London VIX", "fetchedAt":
21:04:03}` for london_vix-2105 (closes 21:04:00). The full table is in `report.py` output.

## Pending bets, halts, suspends

| bot | placed | filled | cancelled | refused at /bet | fill rate |
|---|---|---|---|---|---|
| random / favourite / longshot / signal | 52 each | 50 / 50 / 51 / 50 | 2 / 2 / 1 / 2 | 0 | 96–98% |
| copycat | 31 | 31 | 0 | 0 | 100% |
| kelly | 43 | 14 | 1 | 28 (loss limit) | 33% |
| latecomer (bets at close − 4 s) | 45 | 4 | 41 | 0 | 9% |
| **all** | **327** | **250** | **49** | **28** | **76%** |

- **Fills after close: 0 of 250.** The median fill was 5.5 s after placement and the slowest 45.5 s (clean_sheet's
  45 s delay).
- **Latecomer.** All 30 hub_surge and city_pulse late bets (5 s delay) were cancelled "market closed", which is
  correct. On london_vix (2 s delay), 4 filled before close, 7 were cancelled "price too short" and 1 "market paused".
- **Halts.** 46 halt events. 40 were the VIX moving more than 0.30 in one frame; it jumped around a lot (3.59 → 5.48
  → 4.29 …). 3 were City Pulse moving more than 3%, 2 were Bank's crowding at +12% (one move suspending hub_surge-2105
  and hub_hour-2200 at 21:03:07), and 1 was a 3σ jump halt (Waterloo +3.8σ on hub_hour-2300).
- **Bets on halted markets.** The edge player bet on each halt as it happened: **46 of 46 refused** with 409
  "market halted".
- **Suspends cancel pending bets.** Seen 2 times: "market paused: London VIX 4.56 → 4.02".

## Ledger reconciliation

**120 of 120 checks reconciled to the point: 112 per-round checks plus 8 at the end.** Each per-round check read
`/wallet`, `/history` and `/positions` for every bot after every round. It compared them with the bot's own record:
cash = 1000 − filled stakes + returns, escrow = open filled stakes, realised P&L, payout = floor(stake × odds), and
each result against the market's published winner. Final check: cash + escrow = 1000 + P&L, history P&L = wallet
P&L, and open stakes = escrow, for all 8 accounts. The scratch ledger audit is clean: entries sum to 17,000, which
equals the grants; every bet nets to zero; no balance is negative. There were 292 bets and 287 settlements
(130 won, 157 lost).

## P&L by strategy (final wallets; small samples, ±1 SE)

| bot | won | staked | P&L | ROI ± 1 SE | notes |
|---|---|---|---|---|---|
| kelly (half-Kelly on the clues) | 5/14 | 774 | **+593** | +77% ± 102% | one 60-point Under at ~18x on city_pulse-2110 is +739 by itself |
| signal (documented clues) | 27/49 | 735 | **+277** | +38% ± 25% | city_pulse +93%, hub_surge +2%, london_vix −1% |
| random | 23/49 | 490 | +191 | +39% ± 43% | all from city_pulse underdogs (#2) |
| copycat (Tube Crowd's actual pick) | 14/30 | 450 | +105 | +23% ± 27% | city_pulse +26%, hub_surge ~0% |
| longshot | 13/50 | 500 | +27 | +5% ± 43% | city_pulse +119%, hub_surge −59% |
| latecomer | 3/4 | 60 | +11 | — | 41 of 45 cancelled |
| favourite | 24/49 | 735 | **−119** | −16% ± 14% | the only clear loser |
| live reader (`reader.py`) | 14/17 | 170 | **+169** | +99% | **hub_surge 9/11, +143%** (#1); city_pulse −5% |

**Is the copycat still profitable?** Barely. +23% ± 27% is about 1 SE from zero. Tube Crowd itself made +48% on its
100-point bets, but the copycat buys after Tube Crowd's own 100 points have moved the price about 4 points. That
erases the hub_surge edge (−0%) and leaves city_pulse (+26%).

**Free lunch?** hub_surge is close to one for anyone who reads the mid-round TfL publish (#1). City Pulse underdogs
win because the opens are miscalibrated (#2). Nothing is riskless: the price floor (0.97 / odds under 1.05 refused)
blocks buying the near-certain side.

## AI traders and the house (markets the observer saw settle)

| trader | bets | won | P&L | ROI |
|---|---|---|---|---|
| Tube Crowd | 27 | 13 | **+1,288** | +48% (hub_surge +435, city_pulse +853) |
| Simulation | 18 | 7 | **+1,060** | +59% (hub_surge +826 from longshots, e.g. Piccadilly at 12.66x; city_pulse +234) |
| Camera Eye | 15 | 9 | +484 | +32% (london_vix) |
| Plane Spotter | 1 | 1 | +34 | |
| The Contrarian | 32 | 8 | **−1,479** | −46% |

House P&L: **−1,408 against players** (from the ledger, 292 bets, including the edge-case players). It was also
**−1,387 against the AI traders**, which is off the ledger.

## Calibration at open (first price seen at most 15 s after open; 40 markets)

| price bucket | options | won | mean price | win rate |
|---|---|---|---|---|
| 0.0–0.1 | 7 | 1 | 0.07 | 0.14 |
| 0.1–0.2 | 31 | 3 | 0.15 | 0.10 |
| 0.2–0.3 | 32 | 13 | 0.24 | 0.41 |
| 0.3–0.4 | 10 | 2 | 0.35 | 0.20 |
| 0.4–0.5 | 12 | 2 | 0.44 | 0.17 |
| 0.5–0.6 | 12 | 10 | 0.56 | 0.83 |
| 0.6–0.7 | 5 | 4 | 0.63 | 0.80 |
| 0.7–0.8 | 5 | 1 | 0.75 | 0.20 |
| 0.8–1.0 | 5 | 4 | 0.93 | 0.80 |

| series | markets | Brier at open | Brier for a coin (uniform) |
|---|---|---|---|
| city_pulse | 13 | **0.591** | 0.500 |
| hub_surge | 13 | 0.766 | 0.800 |
| london_vix | 14 | 0.437 | 0.500 |

## Edge cases

| case | response | right? |
|---|---|---|
| bet after close (47 probes, 1–3 s after close) | 409 "market closed" (45); 404 on 2 markets the restart dropped | yes |
| bet on a halted market (46 probes) | 409 "market halted" | yes |
| stake 260 over the 25% cap; 110 over clean_sheet's 10% cap; 1e12 | 409 "position cap: max 250…" | yes |
| stake 5, 9 / 0, −10 / 10.5, "10", true | 400 "at least 10" / "positive number" / "whole number" | yes |
| unknown option / unknown market / wrong token / 129-char key | 400 / 404 / 401 / 400 | yes |
| same key twice | same betId, `replayed: true`; a different stake or option gives 400 "already used" | yes |
| same key on another market | a separate bet (keys are scoped per market round) | yes |
| GET /bet for another player's bet | 404 | yes |
| 20 concurrent bets of 20 from one player | 12 accepted (240 ≤ 250), 8 refused for the cap, escrow 240, total 1000 | yes |
| 10 concurrent bets with one key | 1 betId | yes |
| confirm flow: stake at 26% of cash | 409 `confirm_required` with a token; with the token, 200 | yes |
| forged or expired confirm token | 409 `confirm_required` (`edge.jsonl` 56–57) | yes |
| 4 × 250 at once across markets | escrow capped at 500 (loss limit holds), but see #5 and #6 | partly |
| restart mid-round | filled bets settled from data (3/3); pending bet 404; cap bypass, headline gone, AI reset (#4) | partly |

## Did london_vix list? Did the Simulation trader bet?

london_vix listed in **17 of 19 rounds**. It was skipped only in the first round after each server start (#4d).
heathrow_stack stood in at 21:25. It settled 15 times as observed, plus london_vix-2125 after the restart, with 0
voids. The VIX ran from 2.96 to 7.42 with many suspends.

The Simulation trader made 21 bets, all on hub_surge, city_pulse or hub_hour. It never bets london_vix, by design,
because `MULTIVERSE_URL` is the Oxford Circus engine and not the sim. It won 7/18 settled for **+1,060**, mostly
from hub_surge longshots priced near the 3% floor. Its in-memory record had reached 2,872 when the restart reset it.

## Public stack (target B)

3 players (`test-lt-a/b/c-*`), 6 bets of 10, via `https://overwatchcapital.vercel.app/api/city`:
- **Works end to end.** Calls took about 270–450 ms. Bets returned `pending`, then GET /bet showed 4 filled and 2
  cancelled ("price too short" on the 97% city_pulse-2110 Over, #2).
- A wrong token got 401 through the proxy.
- After settlement, every wallet reconciled (cash = 1000 + P&L, escrow 0) and all 3 players were on the public
  leaderboard (test-lt-b at 1210).

Evidence: `public.jsonl` 1–21, `scratch/public_result.json`.

## Recommendations

1. **hub_surge (#1).** Measure the rise from the last reading published before close, or close betting before the
   :X3 / :X8 publish. Re-run `reader.py`: done when its hub_surge ROI is about −5% (the fee).
2. **Futures (#3).** Let the filter keep following the data after the cap: re-seed futures around the live reading
   instead of holding 20 stale ones, or lift the cap late in the round.
3. **City Pulse line (#2).** Place the line on the simulated median rather than floor + 0.5. Use a finer step (0.1),
   or skip any over/under whose favourite is above 85%, as brackets already are.
4. **Restart (#4).** Rebuild open markets from the ledger's `markets` records, so restored bets count toward the cap
   and the headline stays listed. Persist AI records. Poll the VIX once before the first tick.
5. **Pending desk (#5, #6).** Quote the price and count other pending stakes at accept time. Fill in placement order.
   Do not mint the confirm token at fill time; count pending stakes against the 25% threshold.
6. **Settlement (#7).** Store TfL `timeUtc` in the settlement snapshot and show it. Decide whether "published after
   close" means the recorder fetch time or TfL's own time.
