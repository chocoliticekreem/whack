# Live trading pressure test, re-run on current code

2026-11-07 22:36 → 2026-11-08 00:31 UTC. Same harness as [README.md](README.md) (#79), on `origin/main` at 2d2244a.
That includes #72 (settle only on TfL readings stamped after close, weighted pruning) and #77 (hub race priced with
relative mean reversion, City Pulse line on the futures' median). Own city server on :8191 in live mode, scratch
ledger, real recorder data and the sim on :8449.

Bots traded for 65 min: 15 rounds settled (22:40–23:50) and 13 were reconciled by the runner. Seven strategy bots
and the live reader placed 280 + 34 bets. The first run had 19 rounds and 327 + 30 bets. Every call is logged in
`scratch/*.jsonl`; tables come from `report.py` and the new `rerun_check.py`.

**Verdict.** Settlement is now honest: all 30 crowding markets settled on readings TfL stamped after close, and the
recorder saw none of them before close. City Pulse no longer opens near certain. The frozen-price problem is gone.
But hub_surge is still beatable by the same "back the hub up most since open" rule: **6 of 10, +202%** (first run 9
of 11, +143%). The leak is closed, but the price still ignores public information. Two new problems turned up.
After midnight every vs-usual market voids (new #1). In a falling evening, City Pulse went **Under 14 of 15 times**.

## Before / after

| check | first run (#79, pre-#72/#77) | re-run (current main) | fixed? |
|---|---|---|---|
| **Bug 1** live reader on hub_surge (back the top riser since open, 20 s before close) | 9/11, **+143%** | 6/10, **+202% ± 108%** at a mean price of 0.21 | **no** (the leak is fixed, the edge isn't) |
| Bug 1: deciding reading TfL-stamped after closesAt | no: TfL `timeUtc` = close − 180 s on every settle | **30 of 30** stamped close + 120 s; first seen by the recorder at close + 543 s; snapshot equals the after-close values 30/30 | **yes** |
| **Bug 2** City Pulse favourite at open | up to **0.97** (0.97/0.03 at 21:10, and it lost) | 0.50–0.66, median 0.54, 0 of 13 above 0.85 | **yes** |
| Bug 2: City Pulse Brier at open (coin = 0.500) | **0.591** (n=13) | **0.518** (n=13) | better, still worse than a coin |
| City Pulse outcome balance | — | **Under 14/15**; the Over favourite at open lost 8 of 8 | new problem (#3 below) |
| **Bug 3** O/U markets frozen at the last sample before close | **20/28** (20 futures alive = filter stopped) | **0 real** out of 30. 2 had a flat price over the last 60 s, both pinned at the 0.03 / 0.97 bounds. 12/30 still had exactly 20 alive, but prices kept moving (city_pulse 5–8 moves a round) | **yes** |
| Side the live value was on at close won | — | 23/29 (city_pulse 10/15, london_vix 12/13) | — |
| Live reader on city_pulse / london_vix | −5% / — | +8% (8/10) / +2% (4/5) | no free lunch there |
| hub_surge Brier at open (coin = 0.800) | 0.766 | 0.736 | slightly better |
| london_vix Brier at open (coin = 0.500) | 0.437 | 0.508 | worse, n=12 |
| Voids | 0 | 1 in the traded window (london_vix-2255, VIX feed gap); 3 after it (hub_surge-2355, -0000, hub_hour-0000, new #1) | regression after midnight |
| Fills after close | 0 of 250 | **0 of 229** | yes |
| Bets on halted markets refused | 46/46 | 26/26 | yes |
| Bets 1–3 s after close refused | 45/47 (2 = restart) | 37/37 | yes |
| Ledger reconciliation | 120/120 | final 8/8. Per round 81/91: the 10 misses are one bet the client timed out on but the server filled (#4) | yes (server side) |
| Ledger audit | clean | clean: entries sum 9,000 = grants, every bet nets to zero | yes |
| House P&L vs players | **−1,408** | **−177** (final, after the late settles) | better |
| House vs AI traders (off ledger) | −1,387 | +309 (final) | better |
| Copycat (Tube Crowd's pick) ROI | +23% ± 27% | **+30% ± 20%** (city_pulse +59%, hub_surge +1%) | still positive |
| london_vix listed | 17/19 (skipped first round after each start) | 14/15 (skipped the first round; heathrow_stack stood in) | unchanged (bug 4d) |
| Settle delay after close (crowding) | +243 s | **+543 to +843 s**, so players wait 9–14 min for hub_surge and city_pulse | expected cost of the fix |

## Ranked bugs still open

| # | Sev | X breaks when Y | Evidence |
|---|---|---|---|
| 1 | **High** (new) | **Every vs-usual market voids after the midnight profile refresh, until a restart.** The recorder writes a new `crowding_profile` file at 00:03:05. The server's `Store.load_profile` loaded it at that moment and kept a profile missing 4 of the 5 hubs. It never re-reads the same path (`files[-1] == self.profile_file` returns early). `settled_vs` then finds no typical value and returns "missing", so hub_surge-2355 voided at 00:03:06, before its 20-min deadline. hub_surge-0000 and hub_hour-0000 voided at 00:08 (hub_hour-0000 carried 5 bot bets, refunded). Live `/markets` at 00:10 lists only Oxford Circus in the `vs_usual` hubs. The file itself has all 20 stations, and a fresh `Store` reads Sunday typicals for all 5 hubs. *Confirmed: the void readings and the 1-hub signal. Inferred: the read happened mid-write.* | `scratch/late_settled.jsonl` (hub_surge-2355, -0000, hub_hour-0000: "No data — void"); `/markets` at 00:10; `crowding_profile/2026-11-08/00.ndjson.gz` (20 lines, mtime 00:03) |
| 2 | **High** | **hub_surge still pays the public "top riser since open" rule.** The settle reading is now TfL's stamp 2 min after close, so nothing that decides the market is visible before close. But the riser at the mid-round publish (stamps 3–8 min before close) kept rising: the reader won 6/10 while paying a mean 0.21. The #77 hub model mean-reverts relative gains, which looks like the wrong sign at a 5–10 min horizon. **+202% ± 108%** is about 1.9 SE on 10 bets: not proven, but the first run's 9/11 points the same way. The other hub rules lose: signal/laggard −17%, longshot −100%, copycat +1%. | `scratch/reader.json`; report "Live reader" table (wins at 0.31, 0.35, 0.13, 0.23, 0.09, 0.28) |
| 3 | **High** | **City Pulse lines sit above the outcome when the city empties.** From 22:40 to 23:50 the pulse fell 22.7 → 16.6, about −0.5 a round. Each line is the futures' median, but the settle reading is stamped 12–20 min after the newest reading the market sees at open, and the futures do not carry the evening decline. Under won 14/15. The over-favourite lost 8 of 8 and the under-favourite won 5 of 5. Backing Under at the open price returns about **+80%** (odds approximated as 0.95/p). It also shows in the bots: Tube Crowd (+1,072 on city_pulse, Under 13 times), signal +58% and copycat +59%. One falling hour only, so the regime matters. | `rerun_check.py` §2; report "Live value vs price" (from 23:30 the latest reading was above the line, but the stamp 2 min after close was below it) |
| 4 | Medium (new) | **A server stall makes a filled bet look rejected.** From 22:53:56 to 22:54:50, POST /bet took 2.3–10 s and two calls hit the client's 10 s timeout. One of them (longshot, hub_surge-2300) was filled on the server. Any client that does not retry with the same idempotency key believes the bet failed while 10 points sit in escrow. In the same window the multiverse VIX had no frame from about 22:52:30 to 22:56:16, so london_vix-2255 voided ("No data", stakes refunded). *Cause inferred, not measured: machine load or a lock held during the 22:54 round open.* | `trades.jsonl` 335–352 (ms 2297–10089), RECON_MISMATCH lines 788 onwards; `prices.jsonl` london_vix-2255 |
| 5 | Low | london_vix is skipped in the first round after a start (first-run bug 4d, unchanged). | `/round` at 22:40: heathrow_stack stood in |
| 6 | Low | The daily loss limit counts gross losses, so a winner gets locked out (first-run #8). Kelly was refused 14 times. | report "Pending bets" |
| 7 | Low | The AI traders are still off the ledger: house +309 against them, not in `Ledger.house()`. | report "AI traders" |

Not re-tested here: restart (#4 in the first run), the pending desk ordering (#5) and the confirm bypass (#6). This run
did not use `--edge`.

## 1. Last-second reader and the settlement stamps

Each settled crowding market was rebuilt from the raw recorder files (`rerun_check.py`). For every station, it takes
the first row whose TfL `timeUtc` is after `closesAt`. That row is compared with the server's snapshot and with the
last poll before close.

- **Violations: 0 of 30.** No deciding reading was stamped at or before close. None was seen by the recorder before
  close (earliest: close + 543 s). Every snapshot equals the after-close values, not the pre-close poll.
- TfL stamps at :X1 and :X6 and the recorder polls at :X3:03 and :X8:03. Rows in one poll carry two stamps, so a
  market with all 20 City Pulse stations often waits for a second poll: settles land at close + 843 s.
- The reader's hub_surge bets (P&L on 10-point stakes): bank 2240 won +20, waterloo 2245 lost, oxford 2250 lost,
  bank 2255 won +17, kings-cross 2305 won +59, bank 2310 lost, kings-cross 2320 won +30, oxford 2325 lost,
  piccadilly 2330 won +93, oxford 2335 won +23.

## 2. City Pulse at open

| market | favourite at open | winner |
|---|---|---|
| 22:45 / 23:05 / 23:25 / 23:30 / 23:50 | Under 0.58 / 0.54 / 0.62 / 0.60 / 0.54 | Under (favourite won) |
| 22:50 / 22:55 / 23:10 / 23:15 / 23:20 / 23:35 / 23:40 / 23:45 | Over 0.66 / 0.52 / 0.52 / 0.64 / 0.50 / 0.52 / 0.52 / 0.54 | Under (favourite lost) |

The #77 fix did its job: opens sit near 50/50. Calibration did not improve, because the median is biased in a
trend (#3).

## 3. Frozen markets

The first run's measure (exactly 20 futures alive) no longer means frozen: after the 60% ruled-out cap the weights
keep moving the price (#72). Measured directly as "price unchanged over the last 60 s while the live value changed",
it hit 2 of 30, and both were at the price bounds:
- london_vix-2250 Over at 0.03, with the VIX at 4.96 against a 6.53 line;
- london_vix-2315 Over at 0.97, at 6.07 against 4.68.

Both were correct. The first run's example, Higher still at 42% with the VIX far above the line, did not recur.

## 4. P&L

| bot | won | staked | P&L | ROI ± 1 SE | first run |
|---|---|---|---|---|---|
| copycat | 18/28 | 420 | +126 | +30% ± 20% | +23% ± 27% |
| signal | 25/40 | 600 | +109 | +18% ± 16% | +38% ± 25% |
| favourite | 24/41 | 615 | +49 | +8% ± 16% | −16% ± 14% |
| latecomer | 7/7 | 105 | +37 | +35% ± 10% | 3/4, +11 |
| kelly | 11/20 | 973 | −25 | −3% ± 25% | +77% ± 102% |
| random | 16/41 | 410 | −115 | −28% ± 15% | +39% ± 43% |
| longshot | 10/41 | 410 | −209 | −51% ± 14% | +5% ± 43% |
| live reader | 19/26 | 260 | +211 | +81% (hub_surge +202%) | +99% (hub_surge +143%) |

| AI trader | bets | won | mean implied prob | P&L | first run |
|---|---|---|---|---|---|
| Tube Crowd | 30 | 19 (63%) | 0.43 | **+1,101** (city_pulse +1,072, hub_surge +29) | +1,288 |
| Camera Eye | 13 | 8 (62%) | 0.57 | +153 (london_vix) | +484 |
| Simulation | 30 | 12 (40%) | 0.39 | −281 | +1,060 |
| The Contrarian | 27 | 4 (15%) | 0.38 | −1,182 | −1,479 |

Tube Crowd beats its prices (63% won against 0.43 implied) almost entirely by backing Under on City Pulse (#3).
The Simulation trader is calibrated (40% against 0.39) and no longer feasts on 3%-floor hub longshots.

**Free lunch left?** Two edges are worth more than one hour of data, and neither is riskless: the hub_surge riser
(#2) and Under on City Pulse in a falling evening (#3). The copycat's +30% comes from the same Under edge. On
london_vix the side the VIX was already on 20 s before close returned +2%, which is fair.

## 5. Ledger, voids, fills, london_vix

- **Ledger.** The final wallet check reconciles for 8 of 8 accounts: cash + escrow = 1000 + P&L, history P&L =
  wallet P&L, and open stakes = escrow. The audit is clean. 257 bets: 130 won, 114 lost, 11 void, 2 open on
  clean_sheet-0030 until 00:30. The 10 per-round mismatches are all one bet (#4). The server was right; the harness
  had logged a timeout as a rejection.
- **Voids.** In the traded window only london_vix-2255 voided, from a VIX feed gap, and its stakes were refunded,
  including the reader's. After the window, hub_surge-2355, hub_surge-0000 and hub_hour-0000 voided (#1).
- **Fills after close: 0 of 229.** Median fill 5.3 s after placement, slowest 45.1 s (clean_sheet).
- **london_vix** listed 14 of 15 rounds. VIX range about 4.2–9.6, with 22 suspends on VIX moves of more than 0.3.

## How this run was made

```bash
CITY_PORT=8191 CITY_RAW=/Users/sonionring/orca/whack/data/raw \
  CITY_LEDGER_DB=$PWD/analysis/live_trading/scratch/ledger.db python3 services/city/server.py &
python3 analysis/live_trading/run.py --url http://127.0.0.1:8191 --minutes 65
python3 analysis/live_trading/reader.py --url http://127.0.0.1:8191 --minutes 65
python3 analysis/live_trading/prices.py --url http://127.0.0.1:8191 --minutes 75
python3 analysis/live_trading/report.py --url http://127.0.0.1:8191
python3 analysis/live_trading/rerun_check.py
```

Crowding markets now settle 9–14 min after close, so `run.py` and `reader.py` stop before the last rounds settle.
A side observer logged every settle from `/markets` until 00:31, and the results were merged into `state.json`
and `reader.json` before the report. Originals are kept as `*.orig.json` in scratch.
