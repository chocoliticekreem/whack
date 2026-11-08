---
title: OverWatch markets — every bet we list, and how to change them
date: 2026-11-08
status: current on main (through #96)
---

# Markets catalogue

## Are the bets hard-coded?
Partly. Each bet is a **series**, and three places together define it:

| Layer | File | What it defines |
|---|---|---|
| Backend series | `services/city/markets.py` (`register(Series(...))`, lines ~872–880) | When it opens, how it's priced (the 50 futures), the line or options, and how it settles |
| Plain wording | `apps/stream/src/app/multiverse/plain.ts` (`COPY`) | The question and answer labels players see, and the result sentence |
| Integrity | `services/city/integrity.py` | Bet delay, pause-on-change triggers, position cap |
| AI traders | `services/city/traders.py` (`PLAYS`) | Which computer trader bets on which series, and why |

**Adding a series:** write a `build_*` and a `settle_*` function in `markets.py` and add one `register(...)` line. Then add wording in `plain.ts`, an integrity row, and optionally a `PLAYS` entry. Read `docs/questions.md` first: every series should pass its tests. Nobody on the street can know the answer, the result is genuinely uncertain at open, and a visible signal helps.

## Live series

### Every 5-minute round

| Series | Question players see | Answers | Settles on | Notes |
|---|---|---|---|---|
| `hub_surge` | Which area will see the biggest rise in busyness relative to usual by HH:MM? | Oxford Circus / King's Cross / Piccadilly Circus / Waterloo / Bank | TfL Crowding: each hub's first reading TfL stamped **after** close, compared with its usual-day level | Result in about 14 min (fair settlement). Priced with relative mean reversion plus correlated shocks (#77), and re-simulated after each mid-round TfL publish (#86). Known: the "top riser" rule is narrowed (+6% out of sample) but not proven closed. |
| `city_pulse` | Will Tube activity finish above the expected level (X) by HH:MM? | Above / Below | Average busyness of 20 TfL stations, first readings stamped after close | The line is the futures' median, on the nearest .0 or .5 (#77). The futures follow the usual day plus 0.30 of the last 10 min's trend vs usual (see [City Pulse model](#city-pulse-model)). Known: the Tube Crowd "pulse leak" is still open in the evening. |
| `london_vix` | Will the model become more uncertain by HH:MM? | More uncertain / Less uncertain | The Oxford Circus multiverse engine's uncertainty index (VIX) at settle | Needs the engine live with about 1 h of warm-up. When it's unavailable, `heathrow_stack` stands in. |
| `heathrow_stack` (stand-in) | More or fewer planes circling Heathrow at HH:MM? | More planes / Fewer planes | adsb.lol: aircraft in the 4 Heathrow holding stacks | Listed only when the VIX is down. Poisson count around a mean-reverting level, line centred (#86). |

### Hourly headlines

| Series | Question | Answers | Settles on | Notes |
|---|---|---|---|---|
| `hub_hour` (opens before :00) | Which area has the busiest evening by HH:00? | 5 hubs | Each hub vs its usual level at the hour | The favourite at open is held to 70% or less. Small sample so far. |
| `clean_sheet` (opens before :30) | Will every Tube line keep running smoothly until HH:30? | Yes, all smooth / No, a line hits trouble | TfL line status. Resolves No the moment a Good line breaks | Betting closes with its first round, 45 s bet delay, 10% cap: a single event that people on the Tube can see first (courtsiding). |

### Click-to-trade (created on demand)

| Series | Question | Settles on | Notes |
|---|---|---|---|
| `local_station` | Will it get busier or quieter near {station} by HH:MM? | That station's TfL Crowding reading after close | Within 400 m of any of the 20 TfL Crowding stations |
| `local_cam` | People on the {camera} camera at HH:MM: over or under N? | Median YOLO count from the first TfL image after close | Any of about 13 JamCams with fresh counts. 45 s delay, 10% cap |

### Disabled
- `oxford-busy`: the old single-station bracket ladder, replaced by the lineup above.

## City Pulse model

**Model.** Each of the 50 futures ends at

`log P_settle = log P0 + [log U(t1) − log U(t0)] + 0.30 · [d(t0) − d(t0 − 10 min)] + σ·√h·ε`

`P0` is City Pulse at the open reading. `t0` is that reading's TfL stamp, and `t1` is the first stamp after close (`h` = 15–20 min). `U` is the usual-day pulse. `d = log(P / U)` is City Pulse vs usual at a TfL stamp. `σ` is the one-step sigma fitted on today's changes (unchanged), and `ε` is a normal capped at 2.5σ. The line is the futures' median on .0/.5.

The drift used to be half the last hour's mean change in `d`, capped at σ/5. It is now 0.30 of the last 10 min's change (`pricing.fit_pulse`). Without the usual profile at `t0` or at `t0 − 10 min`, the builder falls back to the old drift.

**Data and method.** The data is 2026-11-07 12:00 to 2026-11-08 02:30 UTC, a Saturday. `analysis/judge/pulse_model.py` builds every 5-min round with the live builder at its real open and settles it with the live settle function.

- The 0.30 coefficient is fitted on the 63 markets opened 12:00–18:00 (fitted value 0.299).
- Every candidate is scored on the 72 markets opened 18:00–00:00.
- One random draw of 50 futures moved a model's Brier by ±0.02 (line rounding), so every score is the expected score over 40 seeded draws per market. SEs are over markets.
- Run: `python3 analysis/judge/pulse_model.py data/raw` (about 1.5 min).

**Diagnosis (old model, by hour of open).** Above won 2.0/12 at 14h, 9.9/12 at 15h, 9.3/12 at 17h, 2.2/12 at 19h, 7.8/12 at 21h and 3.0/12 at 23h. The bias is not an evening-only constant. It is a wave: the error to the futures' median runs −3.7% to +2.6% per hour before 23:00. Errors persist because the city's move away from its usual day keeps going for 15–20 min. The 23h and 00h hours (+6.9%, −10.8%) are a separate problem, the midnight profile switch (limit 3 below).

**Out of sample (72 markets, 18:00–00:00).**

| Model | Brier | vs old | Log loss | vs old | Futures' 90% band missed |
|---|---|---|---|---|---|
| Old: walk drift | 0.2487 | | 0.6909 | | 40% |
| Usual curve only | 0.2645 | +0.016 ± 0.010 | 0.7236 | +0.033 ± 0.020 | 41% |
| Usual + OU on vs usual (κ = 0.0005/min) | 0.2631 | +0.014 ± 0.010 | 0.7207 | +0.030 ± 0.020 | 40% |
| Usual + 2 h horizon sigma | 0.2573 | +0.009 ± 0.009 | 0.7082 | +0.017 ± 0.019 | 29% |
| **Usual + 10-min trend (live)** | **0.2403** | **−0.008 ± 0.009** | **0.6741** | **−0.017 ± 0.018** | 39% |
| Usual + trend + 2 h sigma | 0.2413 | −0.007 ± 0.008 | 0.6757 | −0.015 ± 0.016 | 27% |
| Usual + trend refitted on the last 2 h | 0.2418 | −0.007 ± 0.009 | 0.6769 | −0.014 ± 0.019 | 36% |
| 50/50 | 0.2500 | | 0.6931 | | |
| Climatology (Above's 12:00–18:00 rate, 0.55) | 0.2663 | | 0.7259 | | |

The trend model is the only candidate that beats the old one, 50/50 and climatology. It is also the simplest of the three trend variants. The gain is about 1 SE, from one day of data.

**Pulse leak** (10 points on Tube Crowd's pick at the opening quote, empty book, 95% bootstrap CI over markets): old +43.0% [+22%, +63%], new +41.3% [+22%, +61%]. **Not closed.**

**Reliability of the opening P(Above), out of sample** (2,880 market × draw pairs):

| P(Above) bin | Old: n | Old: mean P | Old: Above hit | New: n | New: mean P | New: Above hit |
|---|---|---|---|---|---|---|
| 0.1–0.2 | 13 | 0.18 | 0.38 | 2 | 0.14 | 1.00 |
| 0.2–0.3 | 143 | 0.26 | 0.51 | 112 | 0.26 | 0.40 |
| 0.3–0.4 | 504 | 0.35 | 0.37 | 516 | 0.35 | 0.28 |
| 0.4–0.5 | 784 | 0.44 | 0.32 | 836 | 0.44 | 0.29 |
| 0.5–0.6 | 896 | 0.55 | 0.28 | 840 | 0.55 | 0.36 |
| 0.6–0.7 | 379 | 0.65 | 0.56 | 441 | 0.65 | 0.53 |
| 0.7–0.8 | 141 | 0.75 | 0.76 | 121 | 0.75 | 0.72 |
| 0.8–0.9 | 20 | 0.82 | 0.95 | 12 | 0.83 | 0.83 |

Both models are miscalibrated in the middle: the 0.5–0.6 bin hits 28% for the old model and 36% for the new one. The new model is closer there, but the evening's Below tilt is still in it.

**Known limits.**
1. **The leak is an evening regime, and one afternoon can't teach it.** In the fit window the same leak has no edge: old −7.9% [−29%, +14%], new −14.3% [−35%, +7%]. After 18:00 Tube Crowd's pick wins about 75%. Its pick is mostly the sign of the usual curve's next 15-min step.

   The script prints fits it never prices with. On 18:00–23:30 opens the city moved 1.62× the usual curve's log change, against 1.09× on 12:00–18:00, and the trend coefficient is 0.79 against 0.30. So on this Saturday evening the city moved far more than TfL's typical day says. Betting the usual slope's sign beats any line that carries the slope only once.

   A coefficient refitted on the last 2 h at each open narrows the leak only to +38% [+19%, +58%]. The likely fix is fitting the amplitude and trend by hour of day across several days. The TOPS loop already leans on Tube Crowd's record in the meantime.

   Fits that include the 23:30–00:00 opens collapse (trend −0.08, amplitude 0.29) because of limit 3.
2. **The futures are too narrow.** 39% of outcomes land outside their 90% band. Sizing sigma on real 15–20 min changes over the last 2 h cuts that to 27% and improves the log score of the spread from +0.48 to −0.40. It does not improve Brier or log loss at the listed line, so it is not live.
3. **Midnight.** `data.band_of` reads the new weekday's profile from 00:00. After midnight on Saturday the live pulse tracked TfL's SAT 00:00–02:00 bands: 12.3 live at 00:21 against SAT 00:15 = 10.25, and 4.8 at 01:41 against SAT 01:30 = 4.3. It did not track the SUN bands the code reads (4.2). That much is confirmed from the data. That TfL's profile day runs past midnight is inferred. Walks that cross 00:00 dive towards 4. The fix belongs in `data.py`, which hub_surge shares, so it is not in this change.
4. **OU and measurement lag were tried.** Reversion of vs usual toward 0 fits κ ≈ 0. The ~10 min TfL publish lag is already priced: the walk runs from the open reading's TfL stamp to the first stamp after close. The 10-min trend is that lag's footprint.

## Who trades what (AI traders, `traders.py`)

| Trader | Feed | Plays |
|---|---|---|
| Tube Crowd | TfL Crowding | hub_surge, city_pulse, hub_hour |
| Camera Eye | London VIX | london_vix |
| Line Watcher | TfL line status | clean_sheet |
| Rain Radar | Open-Meteo rain | city_pulse (only when it rains) |
| Plane Spotter | Heathrow stacks | heathrow_stack |
| Simulation | Central-London agent sim | hub_surge, city_pulse, hub_hour, london_vix |
| The Contrarian | Other traders' bets | everything |

Their track records feed opening prices through the TOPS loop (#93). The blend is 0 until a trader has proven skill.

## Backlog: ideas and why they're not live yet

| Idea | From | Verdict |
|---|---|---|
| Will the current rise in Tube activity continue over the next 5 minutes? | James | Not yet. It's the mirror of the reversion question, so listing both is one bet twice. Our data shows the momentum sign flips between 15 and 20 min, so it would be mispriced. |
| Will Tube activity move closer to normal over the next 5 minutes? | James | Same as above (the reversion side). |
| Will the quieter areas catch up over the next 5 minutes? | James | Already priced inside hub_surge (relative reversion). A standalone yes/no needs a precise "catch up" rule. Candidate if we define it as "the laggard's gap to the hub average shrinks by settle". |
| Rain markets | questions.md | Not listed: it rained 0 mm on every reading. They come back automatically if rain appears in the futures. |
| Single camera or single bus stop | questions.md | Killed for the round lineup (local edge, noise). Kept only as click-to-trade `local_cam`. |
| Busiest station, raw | questions.md | Killed: the leader won 98.5%. |

## Checklist for tweaking or adding a series
1. Write the question in plain words in `plain.ts`. No jargon on the card face; the exact rule goes in Details.
2. Check it against today's recorded data: the favourite at open should be 30–70%, and the outcome should change often.
3. Fit on early data and score on later data (Brier and log loss). Run `analysis/judge/replay_score.py` for copycat and leak rules.
4. Give it an integrity row: bet delay, pause trigger, cap. Single-event or locally visible series get stricter settings.
5. Settle only on readings measured **after** betting closes.
6. Run `python3 -m unittest discover -s services/city` and the bot pressure test (`analysis/live_trading/run.py`).
