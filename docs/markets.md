---
title: OverWatch markets — every bet we list, and how to change them
date: 2026-11-08
status: current on main (through #101), hub_surge model updated on algo/hub-surge
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
| `hub_surge` | Which area will see the biggest rise in busyness relative to usual by HH:MM? | Oxford Circus / King's Cross / Piccadilly Circus / Waterloo / Bank | TfL Crowding: each hub's first reading TfL stamped **after** close, compared with its usual-day level | Result in about 14 min (fair settlement). Priced by a relative mean-reversion model refitted every round on today's readings, re-drawn after each mid-round TfL publish. See [hub_surge: the model](#hub_surge-the-model). |
| `city_pulse` | Will Tube activity finish above the expected level (X) by HH:MM? | Above / Below | Average busyness of 20 TfL stations, first readings stamped after close | The line is the futures' median, on the coarsest of .5/.25/.1/.05/.01 no wider than their spread: .0/.5 by day, finer on a quiet night (#77; a fixed .5 opened 03:20 on 8 Nov at 3%/97%). The futures follow the usual day plus 0.30 of the last 10 min's trend vs usual (see [City Pulse model](#city-pulse-model)). Known: the Tube Sigma "pulse leak" is still open in the evening. |
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
| `local_station` | Will it get busier or quieter near {station} by HH:MM? | That station's TfL Crowding reading after close | Within 400 m of any of the 20 TfL Crowding stations; a click farther away opens the nearest one's market, shown as "Detected area" |
| `local_cam` | People on the {camera} camera at HH:MM: over or under N? | Median YOLO count from the first TfL image after close | Any of about 13 JamCams with fresh counts. 45 s delay, 10% cap |

### Disabled
- `oxford-busy`: the old single-station bracket ladder, replaced by the lineup above.

## City Pulse model

**Model.** Each of the 50 futures ends at

`log P_settle = log P0 + [log U(t1) − log U(t0)] + 0.30 · [d(t0) − d(t0 − 10 min)] + σ·√h·ε`

`P0` is City Pulse at the open reading. `t0` is that reading's TfL stamp, and `t1` is the first stamp after close (`h` = 15–20 min). `U` is the usual-day pulse. `d = log(P / U)` is City Pulse vs usual at a TfL stamp. `σ` is the one-step sigma fitted on today's changes (unchanged), and `ε` is a normal capped at 2.5σ. The line is the futures' median, rounded to the coarsest step in .5/.25/.1/.05/.01 no wider than the futures' spread (IQR / 1.349), so it sits at most half an SD from the median and the favourite opens at most ~69% (`markets.pulse_line`).

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

**Pulse leak** (10 points on Tube Sigma's pick at the opening quote, empty book, 95% bootstrap CI over markets): old +43.0% [+22%, +63%], new +41.3% [+22%, +61%]. **Not closed.**

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
1. **The leak is an evening regime, and one afternoon can't teach it.** In the fit window the same leak has no edge: old −7.9% [−29%, +14%], new −14.3% [−35%, +7%]. After 18:00 Tube Sigma's pick wins about 75%. Its pick is mostly the sign of the usual curve's next 15-min step.

   The script prints fits it never prices with. On 18:00–23:30 opens the city moved 1.62× the usual curve's log change, against 1.09× on 12:00–18:00, and the trend coefficient is 0.79 against 0.30. So on this Saturday evening the city moved far more than TfL's typical day says. Betting the usual slope's sign beats any line that carries the slope only once.

   A coefficient refitted on the last 2 h at each open narrows the leak only to +38% [+19%, +58%]. The likely fix is fitting the amplitude and trend by hour of day across several days. The TOPS loop already leans on Tube Sigma's record in the meantime.

   Fits that include the 23:30–00:00 opens collapse (trend −0.08, amplitude 0.29) because of limit 3.
2. **The futures are too narrow.** 39% of outcomes land outside their 90% band. Sizing sigma on real 15–20 min changes over the last 2 h cuts that to 27% and improves the log score of the spread from +0.48 to −0.40. It does not improve Brier or log loss at the listed line, so it is not live.
3. **Midnight.** `data.band_of` reads the new weekday's profile from 00:00. After midnight on Saturday the live pulse tracked TfL's SAT 00:00–02:00 bands: 12.3 live at 00:21 against SAT 00:15 = 10.25, and 4.8 at 01:41 against SAT 01:30 = 4.3. It did not track the SUN bands the code reads (4.2). That much is confirmed from the data. That TfL's profile day runs past midnight is inferred. Walks that cross 00:00 dive towards 4. The fix belongs in `data.py`, which hub_surge shares, so it is not in this change.
4. **OU and measurement lag were tried.** Reversion of vs usual toward 0 fits κ ≈ 0. The ~10 min TfL publish lag is already priced: the walk runs from the open reading's TfL stamp to the first stamp after close. The 10-min trend is that lag's footprint.

## hub_surge: the model

Reproduce: `python3 analysis/judge/hub_model.py data/raw 2026-11-07T12:00:00Z 2026-11-08T02:35:00Z 4000` (about
45 s; read-only). Code: `pricing.fit_relative_ou`, `pricing.RelativeRaceSim`, `markets.build_hub_surge`.

**What the race really is.** TfL stamps every hub every 5 minutes and publishes a reading about 7 minutes after it
measures it. At the open (6 minutes before the mark) the newest reading is 8 minutes old, and 42% of hub readings are a
publish older (14 minutes). The settle reading is stamped 2 minutes after close. So each hub's race runs 3 or 4 TfL
steps (15-20 min) from its open reading, not 5 minutes. A stale open reading was not mispriced (legacy price 0.197,
won 0.200, n=350), so the lag shows up in the horizon, not as a leak.

**Model.** Per hub *i* and race horizon *k* (TfL steps from its open reading to the settle slot):

    x_i(settle) = x_i + [u_i(settle) − u_i(open)] − b_k·(d_i − ℓ_i) + σ_ik·z_i,   z ~ N(0, R_k)
    d_i = (x_i − u_i) − mean over hubs,   winner = argmax_i  e^{x_i(settle)} / typical_i(settle band) − vs_i(open)

*x* = log live busyness, *u* = log of TfL's usual day (smooth between 15-min bands). Every term is refitted each round
on that day's readings polled up to the open, binned by TfL stamp:
- *b_k*: pooled least squares of the hubs' k-step relative moves on *d − ℓ*, at the race's own horizon.
- *ℓ_i*, hub level: the mean of *d_i* over the day, shrunk toward 0 with a prior worth 36 slots (3 h). Bank sat 0.15
  below the other hubs all day and did not catch up; the old model pulled it toward the mean every round.
- *σ_ik*: each hub's residual sd over the last 3 h, partially pooled across hubs in logs (empirical Bayes).
- *R_k*: the sample correlation of the residuals.
- Cold start (fewer than 8 joint steps): b = 1 − e^{−0.0161·5k}, no levels, the old fixed correlation.

The 50 futures are a stratified sample of 2,000 joint draws (each winner gets its share of the 50), so the price
carries no ±0.06 sampling noise. Open prices are tempered, p ∝ p^0.7 (`HUB_POWER`); after an in-round publish the
model re-draws from that reading for the steps left, untempered.

**Diagnosis of the old model** (per-minute OU at κ = 0.0161, fixed correlation, 50 plain futures, power 0.5), 166
races, 12:14-02:10:
- Per hub: King's Cross priced 0.154, won 0.241; Bank priced 0.279, won 0.241; Waterloo priced 0.215, won 0.163. The
  pull toward the five-hub mean is the cause: it assumes every hub's normal level is the same.
- Leader vs riser: the furthest-below hub (Tube Sigma's pick) won 59/166 at 0.29 (+18% ROI); the leader 21/166 at
  0.13 (−10%). Split by hub, Bank as laggard was fair (36/113 at 0.30, −1%) and every other laggard won 23/53 at 0.26
  (+58%).
- Reversion: b over k steps is 0.054, 0.100, 0.164, 0.216 (± 0.015-0.029) for k = 1-4, close to a 1-step rate
  compounded, so the horizon matters more than the curve. It is flat across the day (k = 3: 0.16, 0.12, 0.17 ± 0.04
  for 12-17, 17-21, 21-03) but not across hubs (Piccadilly 0.03 ± 0.04, King's Cross 0.39 ± 0.08). Shrunk per-hub b
  still lost out of sample.
- Residual autocorrelation of 5-min relative moves at 5/10/15/20 min: +0.02/−0.22/−0.22/+0.28 (12-17),
  −0.16/−0.26/+0.36/+0.05 (17-21), +0.30/−0.04/−0.03/−0.09 (21-03). No stable sign, so no momentum term (one lost).
- Shocks: excess kurtosis 7.1, from volatility doubling after 21:00 (sd 0.032 → 0.071). The rolling 3-h sd handles
  that; Student-t shocks added nothing.

**Out of sample.** Each model is refitted at every round on readings up to its open; 133 races opened 15:00-02:10.
Paired differences vs the old model, mean ± 1 SE (negative is better). The old model's constants came from
12:00-17:00, so 15:00-17:00 is in sample for it.

| model | log loss | Brier | Δ log loss | Δ Brier | Δ LL 15-20 (n=60) | Δ LL 20-02 (n=73) |
|---|---|---|---|---|---|---|
| old model | 1.498 | 0.756 | | | | |
| **deployed** (`build_hub_surge`) | **1.458** | **0.738** | **−0.039 ± 0.024** | **−0.017 ± 0.011** | +0.005 ± 0.034 | **−0.076 ± 0.032** |
| uniform (ln 5) | 1.609 | 0.800 | +0.112 ± 0.029 | +0.044 ± 0.013 | +0.053 ± 0.047 | +0.160 ± 0.036 |
| frequency each hub has won so far | 1.650 | 0.816 | +0.153 ± 0.029 | +0.060 ± 0.013 | +0.120 ± 0.048 | +0.180 ± 0.035 |
| OU at the race horizon (4,000 draws) | 1.502 | 0.760 | +0.004 ± 0.029 | +0.005 ± 0.013 | +0.040 ± 0.037 | −0.026 ± 0.043 |
| + per-hub b, shrunk | 1.505 | 0.767 | +0.008 ± 0.034 | +0.011 ± 0.015 | +0.034 ± 0.044 | −0.014 ± 0.049 |
| + Ledoit-Wolf correlation | 1.518 | 0.763 | +0.021 ± 0.030 | +0.008 ± 0.013 | +0.031 ± 0.032 | +0.012 ± 0.048 |
| + Student-t shocks | 1.519 | 0.763 | +0.021 ± 0.032 | +0.007 ± 0.013 | +0.026 ± 0.034 | +0.017 ± 0.052 |
| + momentum term | 1.536 | 0.768 | +0.038 ± 0.034 | +0.012 ± 0.014 | +0.038 ± 0.042 | +0.038 ± 0.053 |
| + hub levels, Ledoit-Wolf | 1.470 | 0.746 | −0.028 ± 0.037 | −0.010 ± 0.017 | +0.040 ± 0.050 | −0.083 ± 0.053 |
| + hub levels, sample correlation (deployed spec) | 1.451 | 0.742 | −0.046 ± 0.036 | −0.014 ± 0.018 | +0.050 ± 0.052 | −0.125 ± 0.048 |

Hub levels are the one addition that wins; the simplest model with them is the deployed one. Ledoit-Wolf shrinkage
lost 0.019 of log loss to the sample correlation (1.470 vs 1.451): residual variance triples at night, which breaks its iid
intensity, and at 01:00 it shrank all the way to the constant correlation (δ = 1.0 with 90 joint steps). The
walk-forward Platt power ran 0.25 early in the day to 0.75 at the end; 0.7 is the end value. In-round, at the first
publish after the open, the deployed model matches the old one (Δ log loss −0.011 ± 0.031, n=126).

**Leak rules** (10 points at the open model price, odds 0.95/p, 2,000-resample bootstrap 95% CI; 133 out-of-sample
races; the in-round rule at the price after the first in-round publish):

| rule | old model | deployed |
|---|---|---|
| copycat Tube Sigma (hub furthest below usual) | 48/133 at 0.28: +24.0% [−4%, +55%] | 48/133 at 0.30: +18.2% [−9%, +48%] |
| back the leader (furthest above usual) | 17/133 at 0.14: −19.0% [−54%, +21%] | 17/133 at 0.13: −12.6% [−51%, +30%] |
| back the top riser at open (last 5-min move) | 24/100 at 0.20: +18.5% [−24%, +64%] | 24/100 at 0.18: +23.9% [−19%, +75%] |
| back the top riser since open, at the first in-round publish | 58/126 at 0.42: +0.4% [−21%, +22%] | 58/126 at 0.42: +2.6% [−19%, +24%] |

Every CI includes 0 for both models. With the full exchange (`replay_score.py`, 12:00-02:30, AI staking and locked
odds), Tube Sigma's real hub_surge record goes from +2.8% [−20%, +27%] to −3.2% [−26%, +20%] on 170 bets.

**Known limits.**
- One recorded day (Saturday 12:00 to Sunday 02:35, 133 scored races, heavily overlapping). The log-loss gain is
  1.6 SE overall and 2.4 SE on the last 73 races; it is a lean, not proof.
- The copycat leak is shaved, not closed: a laggard other than Bank still won 21/43 out of sample at a 0.31 price
  (0.25 before); Bank as laggard is fair (27/90 at 0.29). Per-hub reversion would price that, but it lost out of
  sample with the data we have.
- Hub levels need history: with under 3 h of readings the model leans on the cold-start prior, and on the 15:00-20:00
  block it did no better than the old model.
- Residual sds are fully pooled across hubs late at night (the empirical-Bayes spread falls below the noise), and
  the residuals include the city-wide move, so σ and R are dominated by night readings.
- The typical-day profile is static per weekday and the newest file wins, so a Saturday race read the Sunday file's
  Saturday profile. TfL's profiles are historical averages, so this is not look-ahead in the race outcome.
- A hub without a reading or a usual-day value at open is dropped (the race needs 3 hubs). Settlement needs every
  listed hub's first reading after close, so a hub already missing would most likely void the market for everyone.
  This listed one extra race on 2026-11-07 (13:05, Waterloo missing) that the old builder skipped.
- `hub_hour` uses its own level race (`RaceSim`), not this model, and is unchanged.

## Who trades what (AI traders, `traders.py`)

| Trader | Feed | Plays |
|---|---|---|
| Tube Sigma | TfL Crowding | hub_surge, city_pulse, hub_hour |
| Camadel | London VIX | london_vix |
| Line-ennium | TfL line status | clean_sheet |
| Rain Street | Open-Meteo rain | city_pulse (only when it rains) |
| Runway72 | Heathrow stacks | heathrow_stack |
| Simulation | Central-London agent sim | hub_surge, city_pulse, hub_hour, london_vix |
| Fadewater | Other traders' bets | everything |

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
