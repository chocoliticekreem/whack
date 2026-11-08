# OverWatch market design: what to ask

Measured on Saturday 7 Nov 2026, about 12:06 to 18:25, from `data/raw`. Scripts are in `analysis/questions/` (stdlib
only, read-only). Re-run them with `python3 analysis/questions/<script>.py`. Their outputs go to
`analysis/questions/out/`.

**Caveat on the numbers.** This is one quiet, dry Saturday afternoon. Opens are every 5 min, so a 5-min series gets
about 66 independent windows. A 60-min series gets only about 16 overlapping windows, which is about 5 independent
hours. Treat any hit rate built on fewer than 20 calls as a direction, not a proof.

## 1. Why the current questions don't make people want to bet

The current questions fail in four ways:

- **They are local.** One camera, one bus stop or one station. Anyone standing there, or with the TfL app open,
  already knows the answer.
- **At 5 minutes they are either already decided or pure dice.** "Which station is busiest" went to Piccadilly
  Circus 63 times out of 66, so the leader at open won 98.5% of the time. The number of disrupted lines did not move
  in 90% of 5-min windows. Rain was 0.0 mm on every reading and every nowcast step all day. Camera and bus counts are
  noise: the 13-camera YOLO total moved back toward its 20-min mean in 10 of 14 windows.
- **Nothing on screen helps you form a view.** The obvious cue, "it's been going up", is a coin flip on crowding:
  47–54% across 1,200+ station-windows. The one cue that does work is the typical-Saturday curve. It calls a
  station's direction right 66% of the time at 15 min, 72% at 30 min and 79% at 60 min. It is not shown anywhere.
- **There is no story.** Times like 18:04, lines like "43.5% busyness" with nothing to compare against ("is that a
  lot?"), and no race, streak or rivalry to follow.

So the player either already knows the answer or has nothing to reason with. Both kill the urge to bet.

## 2. Candidate series, measured

How to read the numbers in every card:

- **Fav at open.** The house's probability for the likelier side at open. The house prices from the past only: the
  empirical distribution of h-minute changes seen before the open (`evaluate.py`). Shown as p10/p50/p90 and the
  share of opens that fall inside 30–70%.
- **Moved.** How often the settle value differs from the value at open.
- **Persist.** How often "it stays as it is now" wins.
- **Signal.** The hit rate of the visible cue over the windows where it made a call, given as hits and n.
- **Void.** The share of opens with no settle reading.

### Kept

#### S1 `hub_surge`: which hub surges most (heartbeat, 5 min)

- **Question:** "Which hub surges most by 18:35?"
- **Format:** a 5-way race between Oxford Circus, King's Cross, Piccadilly Circus, Waterloo and Bank.
- **Settles on:** TfL Crowding. Each hub's value is *vs usual* = live `percentageOfBaseline` ÷ the typical-Saturday
  % for the same 15-min band (`crowding_profile`, SAT). The winner is the hub with the biggest rise in *vs usual*
  between the reading frozen at open and the last reading at or before T. That reading must be at most 2 min old;
  otherwise use the first reading up to 3 min after T. If two hubs tie for the top, the first in the fixed order
  wins. If no hub moved, the market is void and stakes are refunded.
- **Signal:** "furthest below its usual Saturday catches up". The hub with the lowest *vs usual* at open won
  **23/66 = 35%**, against 20% for a random pick and 24% for the best fixed pick (Bank, 16/66). When that hub was
  Waterloo it won **9/19 = 47%**. Momentum is worthless here: 17%.
- **No street-corner edge:** you would need to stand in five stations at once and know their typical Saturday.
- **Measured:** fav p10/p50/p90 = 0.24/0.27/0.31, which is right for a 5-way race. Winners were spread evenly
  (13/10/12/15/16). Moved 85%. Persist does not apply. Top tied in 3/76 windows, every hub flat in 2/76, void 1/67.
- **Verdict: KEEP.** It is genuinely uncertain and the cue really works.

#### S2 `city_pulse`: the whole Tube's busyness (heartbeat, 5 min)

- **Question:** "City Pulse at 18:35: over or under 34.6?"
- **Format:** over/under.
- **Settles on:** City Pulse = 100 × the mean live `percentageOfBaseline` over the 20 TfL Crowding stations, in the
  last reading at or before T. At least 18 stations must report, otherwise the market is void. A value exactly on the
  line counts as Under.
- **Line:** the value at open plus the house's median 5-min change, to 2 decimals.
- **Signals:**
  - The last 5-min change: 56% (38/68).
  - The typical-Saturday slope when the 15-min band rolls over: 57% (12/21). With the line set at the value at open
    instead, the typical slope hits 81% (17/21).
- **No street-corner edge:** it is the average of 20 stations.
- **Measured:** fav 0.50/0.51/0.56, 100% in band. Moved 100%. Persist 50%. Void 0.
- **Verdict: KEEP.** The edge is modest but honest, and the series gives the city a pulse to watch.

#### S3 `london_vix`: the multiverse uncertainty index (heartbeat, 5 min)

- **Question:** "London VIX at 18:35: higher or lower than 2.31?"
- **Format:** over/under, with the line at the VIX value at open.
- **Settles on:** the `vix` that `services/multiverse` publishes at the last frame at or before T. A value exactly
  equal to the line counts as Lower. The market is void when there is no frame within 2 min of T, and during the
  first 60 min after a spawn. That warm-up exclusion matters: the VIX falls from 15.8 to about 2.3 over the first
  hour.
- **Signal:** "fade the last move". If the VIX rose over the last 5 min, bet Lower: **65% (26/40)**. Following the
  move ("momentum") hits 30% (13/43).
- **No street-corner edge:** it is a model quantity nobody can see.
- **Measured** on a 13:30–18:20 replay (`vix_replay.py`, 289 frames):
  - After warm-up the median is 2.3 and the range 1.73–4.9.
  - The median 5-min move is 0.2 points, and half of all moves are smaller than that.
  - Fav 0.57/0.61/0.69, 93% in band. Moved 98%. Void 0 in replay.
  - Live void risk = the multiverse service being down.
- **Weakness:** the VIX covers the Oxford Circus area only. Its biggest drivers were bike docks (61), the market
  maker's refills (57, the engine's own resampling) and tube crowding (54). Rain radar showed up 14 times on a dry
  day. A meaningful part of each move is the engine's own randomness.
- **Verdict: KEEP** for the Marshall Wace angle. Show the driver line next to it ("moved most by: bike docks") so
  each move has a reason. If the multiverse service is down, swap in S4.

#### S4 `heathrow_stack`: planes holding over Heathrow (bench heartbeat, 5 min)

- **Question:** "Planes holding over Heathrow at 18:35: over or under 4.5?"
- **Format:** over/under, with the line at the count at open + 0.5 ("more than now?").
- **Settles on:** adsb.lol `summary.holding_total`, the sum of the BNN, LAM, BIG and OCK stacks, in the last
  reading at or before T. It must be at most 2 min old, otherwise the market is void. Cadence is 1 min and the
  largest gap was 1 min.
- **Signal:** "stacks drain after they build". If the count rose over the last 10 min, bet Under: **60% (28/47)**.
  Reversion to the 30-min mean: 54%.
- **No street-corner edge:** the stacks circle at 7–15k ft over the Home Counties.
- **Measured:** fav 0.53/0.60/0.61, 100% in band. Moved 72%. Persist 65%. Void 0. The hourly mean fell from 10.9
  (14:00) to 3.3 (18:00), so the evening stack runs 0–8.
- **Verdict: KEEP** as the fallback for S3. It also works as the "airport" flavour round.

#### S5 `hub_hour`: furthest above its usual Saturday (headline, 60 min, opens at :00)

- **Question:** "Which hub ends the hour furthest above its usual Saturday? Settles 19:00."
- **Format:** a 5-way race.
- **Settles on:** the highest *vs usual* (as in S1) in the last TfL Crowding reading at or before T. Ties go to the
  first in the fixed order.
- **Signals:**
  - The current leader won **9/16 = 56%**.
  - The typical-Saturday curve: it predicts station direction 79% at 60 min (n=741, pooled across all 20 stations).
  - Fixtures, shown as an anecdote, not a proof. Arsenal 1-0 Hull ended at about 16:55 (estimated egress, TheSportsDB
    via `scrape_fixtures`). King's Cross then went from 0.91–0.99 of a normal Saturday (16:33–16:53) to 1.08 at 17:31
    and 1.37 at 18:08. That was the biggest climb of the five hubs; the others moved +0.0 to +0.2. n=1, and
    evening rail departures could explain part of it.
- **No street-corner edge:** five stations, each against its own baseline.
- **Measured:** winners were Oxford Circus 10, King's Cross 5 and Piccadilly 1. The house bootstrap was
  overconfident: its fav p50 was 0.89 against a real hit rate of 56%. The price needs a wider simulation (see §5).
  Void 0.
- **Verdict: KEEP.** This is the "evening rush" story market.

#### S6 `clean_sheet`: does the Tube stay clean (headline, 60 min, opens at :30)

- **Question:** "Clean sheet: does every Tube line on Good Service at 18:30 stay Good until 19:30?"
- **Format:** yes/no.
- **Settles on:** TfL Line Status for the 11 Tube-mode lines. YES = every line at severity 10 at open still reads 10
  on every reading in (open, T]. The first break resolves the market NO immediately, which is a live moment ("the
  Central line just broke the clean sheet"). If readings are missing for more than 5 min in total, the market is void.
- **Signal:** line status reasons ("train cancellations" vs "points failure") and the minutes since the last change.
  Neither is proven: there were only 19 status changes in 6 h. The naive cues failed. "Two or more changes in the
  last hour" hit 36% (5/14), and "unplanned delays on the board" hit 43% (6/14).
- **No street-corner edge:** 11 lines across the city.
- **Measured** at 60 min: broken 6/14 = 43%. The house fav p50 was 0.85 (overconfident, because the early afternoon
  was busy and the late afternoon quiet); the honest price is about 50/50. At 30 min: broken 6/27 = 22%, house fav
  0.53/0.62/0.69. Void 0.
- **Verdict: KEEP** for drama and a crisp settle. Price it from a rolling 3-hour hazard, not the whole day.

#### S7 `lines_count_hour`: how many lines disrupted (alternate headline, 60 min)

- **Question:** "How many Tube lines will be disrupted at 19:00? Fewer / same / more than now (3)."
- **Format:** brackets.
- **Settles on:** the count of Tube-mode lines at severity below 10 in the last reading at or before T.
- **Measured:** fav 0.37/0.47/1.00, 69% in band. Moved 50%. There is no tested signal.
- **Verdict: BENCH.** Swap it in for S6 when every line is already disrupted or every line is Good, when the clean
  sheet has nothing to say.

#### S8 `rush_peak`: when the evening rush peaks (daily special, one-shot)

- **Question:** "Does the evening rush peak before 18:30?"
- **Format:** yes/no.
- **Settles on:** the time of the maximum City Pulse (S2) between 16:00 and 20:00.
- **Signal:** the typical Saturday peaks in the 17:30 band. 18 of the 20 stations have their SAT pm peak band
  starting between 16:00 and 17:15. Euston (14:00) and Paddington (13:45) peak earlier.
- **Measured:** one day, so no distribution. Today the live peak so far came at **18:08** (35.27), 40 min after the
  typical peak, and the value had dropped to 33.61 by 18:23.
- **Verdict: KEEP** as one daily market only. It can't be tested further until there are more days.

### Killed

| # | Series | Why it dies (measured) |
|---|---|---|
| K1 | `busiest` raw, 5 min ("Which station is busiest?") | Leader won 98.5% (65/66); Piccadilly 63/66; fav p50 1.00, 5% of opens in band |
| K2 | `busiest vs usual`, 5 min | Leader won 89%; 80% at 15 min and 70% at 30 min. Only the 60-min version (S5) is uncertain |
| K3 | `lines_any` / any line change, 5 min | YES only 19%; fav 0.72–0.84, 6% in band. The 15-min version is in band (36% YES), but no cue beat always-No (61% vs 64%) |
| K4 | `cam_total`, 13-camera YOLO total | Only ~40 min of 13-camera fresh data; n=3 windows with 12+ cams, n=14 with 8+. Coverage fell to 1 fresh cam 16:30–16:50. Moves are YOLO noise (reverted 10/14). Revisit after a full day of fresh counts |
| K5 | `bikes_city`, Santander bikes docked | Momentum 42%, reversion 53%: van rebalancing noise at 5 min; 56% at 60 min (n=16). No story |
| K6 | `rain` | 0.0 mm on all 62 readings and every nowcast step. ~97% No. Open it only when the nowcast shows ≥ 0.1 mm within 30 min |
| K7 | Single-camera / single-stop `cam`, `bus`, one-station `level` | Street-corner edge by construction (owner feedback) |
| K8 | `vix_spike` | A spike (≥ 2 pts and ≥ 20%) within 15 min happened 2% of the time |
| K9 | `grid` demand, `road_status` | Grid is GB-wide (not London) and a smooth predictable curve; road status flipped 3 times in 6 h, all on the A4 within 2 min |

## 3. Recommended lineup

**Every 5-min round (heartbeat), same wording every round:**

1. **S1 `hub_surge`**: "Which hub surges most by HH:MM?"
2. **S2 `city_pulse`**: "City Pulse at HH:MM: over or under X?"
3. **S3 `london_vix`**: "London VIX at HH:MM: higher or lower than X?" If the multiverse feed is down, swap in
   **S4 `heathrow_stack`**.

**Two headlines per hour, overlapping so there is always one building and one about to settle:**

- **S5 `hub_hour`** opens at :00 and settles at the next :00.
- **S6 `clean_sheet`** opens at :30 and settles at the next :30. Swap in S7 when the clean sheet has nothing to say.

Plus **S8 `rush_peak`** once a day, opening at 16:00.

All six series pass the no-street-corner check. Each one has a cue on screen that measurably beats luck, except
S6, which is there for drama and needs its reason-text cue proven on more days. Each one settles on one recorded
number.

### Demo opener: S1 `hub_surge`

A judge sees five bars, one per hub, each showing how busy it is against a normal Saturday. One bar is visibly sagging,
for example "Waterloo 92% of a normal Saturday". Tube Sigma, one of the AI traders, has already bet it, with its
reason in one line: "Waterloo is the furthest below its usual — it catches up".

The judge has a real choice: back the trader at about 4–5x, or pick a hub that is surging now. It settles in 5 min
on a real TfL reading. Nobody in London could know the answer from where they stand. The cue is learnable and true:
furthest-below wins 35% against 20% for luck, and 47% when it's Waterloo.

The judge leans in for three reasons: there is a visible underdog, there is an opponent with a stated thesis, and
the payoff comes before the conversation moves on. S5 frames it: "King's Cross has climbed from 0.95 to 1.37 since
the Arsenal final whistle — does it hold the hour?"

## 4. New signals the UI should show next to the markets

1. **Typical-Saturday ghost line.**
   - *Source:* `crowding_profile`, SAT, 15-min bands, already recorded at 12:06.
   - *Render:* a dashed line behind each hub's and City Pulse's last-hour chart, plus one text line: "KX 133% of a
     normal Saturday · usual curve rises next 15 min ▲".
   - *Evidence:* direction hit rate 66% at 15 min, 72% at 30 and 79% at 60 (`signals.py`, pooled across 20
     stations, n=482/697/741).
2. **"vs usual" strip for the five hubs.**
   - *Source:* live `crowding` ÷ the typical value above.
   - *Render:* five mini bars sorted by *vs usual*, with the lowest tagged "furthest below". One line: "Ox 1.12 · KX
     1.33 · Picc 1.11 · Wloo 0.98 · Bank 0.98".
   - *Evidence:* it drives S1 (35% vs 20%) and S5 (leader 56%).
3. **Heathrow stack gauge.**
   - *Source:* `aircraft` `summary.holding` and `holding_total`.
   - *Render:* "Stacks 4 (BNN 0 · LAM 1 · BIG 1 · OCK 2) · 30-min avg 3.5 · ▲ vs 10 min ago".
   - *Evidence:* "fade the build" hit 60% (n=47) for S4. It also feeds the VIX broker B-AIR.
4. **Line and fixtures ticker.**
   - *Source:* `line_status` (reason and minutes since the last change) plus `scrape_fixtures`.
   - *Render:* "Bakerloo · Part Suspended 21 min · points failure at Elephant & Castle" | "Arsenal 1-0 Hull · FT ·
     fans heading for Finsbury Park / King's Cross".
   - *Evidence:* this is the story feed for S5 and S6. Its edge is not proven on one day.

The VIX also needs its driver line, "moved most by: bike docks (+0.4)". It comes from multiverse `vix_driver` and
`vix_history`.

## 5. Spec for the backend

Rules shared by every series:

- Times are Europe/London.
- Reuse `markets.reading_at` (≤ 2 min before T, else first reading ≤ 3 min after, else void or the stated default)
  and the existing LMSR, 5% fee and halt rules.
- Freeze the open reading into `spec` so settlement never re-reads it.
- The fixed hub order for tie-breaks: Oxford Circus, King's Cross, Piccadilly Circus, Waterloo, Bank.
- `vs_usual(h, t)` = TfL `percentageOfBaseline`(h) ÷ the typical-Saturday %(h, the 15-min band of the reading's
  timestamp). The typical value is from `crowding_profile` `daysOfWeek[SAT].timeBands`.

| id | kind | question template | line / bracket placement | settle rule | trader and signal |
|---|---|---|---|---|---|
| `hub_surge` | versus (5) | `Which hub surges most by {HH:MM}?` | none; options = the 5 hubs in fixed order | winner = argmax(vs_usual(T) − vs_usual(open)); top tie → fixed order; all five unchanged → void, refund | **Tube Sigma**: picks argmin vs_usual(open). "{hub} is at {x:.0%} of a normal Saturday, the furthest below — it catches up" |
| `city_pulse` | overunder | `City Pulse at {HH:MM}: over or under {line}?` | line = pulse(open) + median 5-min change over the last 3 h (2 dp); price with `LevelSim` on log(pulse) | pulse(T) = 100 × mean percentageOfBaseline over the stations reporting; fewer than 18 → void; equal → Under | **Tube Sigma**: if the typical-Saturday band changes before T, bet the typical slope; else bet the sign of the last 5-min change |
| `london_vix` | overunder | `London VIX at {HH:MM}: higher or lower than {vix0}?` | line = VIX at open (2 dp); simulate from the last 3 h of 5-min VIX changes | the multiverse `vix` at the last frame ≤ T (≤ 2 min old); equal → Lower; no frame, or within 60 min of spawn → void. Needs the city service to read multiverse `/state` (or a recorder source `vix`) each minute | **Vol Desk** (repoint the idle `cams` trader to feed `vix`): fades the last 5-min move. "VIX jumped 2.1→2.6 on bike docks; spikes fade" |
| `heathrow_stack` | overunder | `Planes holding over Heathrow at {HH:MM}: over or under {n}.5?` | n = holding_total at open; price with `CountSim` fitted on today's holding series | `aircraft.summary.holding_total` last ≤ 2 min before T, else void | **Runway72** (repoint the idle `bus` trader to feed `aircraft`): if the total rose over the last 10 min → Under, fell → Over, flat → no bet |
| `hub_hour` | versus (5) | `Which hub ends the hour furthest above its usual Saturday? Settles {HH:00}` | opens at :00, T = next :00; price with the level-race sim but fit sigma on **60-min** changes and cap the favourite at 70% at open (the 5-min fit gave 0.89 against a real 56%) | winner = argmax vs_usual(T); tie → fixed order; no reading → void | **Tube Sigma**: backs the leader unless a fixture with `egress_est_utc` in the last 90 min lists a station on a hub's lines (Arsenal → King's Cross); then backs that hub with the fixture as its reason |
| `clean_sheet` | yesno | `Clean sheet: does every Tube line on Good Service at {HH:30} stay Good until {HH+1:30}?` | opens at :30; P(break) from a rolling 3-h per-line hazard (`fit_line_hazards` on a 3-h window, not the whole day) × the number of Good lines | YES iff every Tube-mode line at severity 10 at open reads 10 on every reading in (open, T]; the first break → NO at once (settle early, show which line); gaps over 5 min in total → void | **Line-ennium**: NO if any unplanned reason on the board matches `cancellations|signal|faulty|points|customer|incident` and it changed under 30 min ago; else YES |
| `lines_count_hour` (bench) | versus (3) | `Tube lines disrupted at {HH:00}: fewer, same or more than {n}?` | n = count at open | count of Tube-mode lines at severity < 10 at T | **Line-ennium**: "same" unless a delay changed under 15 min ago (recent delays clear) → "fewer" |
| `rush_peak` (daily) | yesno | `Does the evening rush peak before 18:30?` | opens 16:00, T = 20:00 | YES iff argmax of `city_pulse` over the readings 16:00–20:00 is before 18:30 | **Tube Sigma**: YES if the live pulse is already above the typical 17:30 level by 17:30, else NO |

The **Fadewater** stays as is and plays every market. **Rain Street** sits out unless `rain` is gated open (nowcast
≥ 0.1 mm within 30 min). The `cam`, `bus`, single-station `level` and 5-min `busiest` and `lines_any` builders should
stop opening.

## Analysis files

| File | What it does |
|---|---|
| `analysis/questions/load.py` | recorder reader, percentiles, correlation |
| `analysis/questions/series.py` | the city-scale settle series (City Pulse, vs usual, line counts, stacks, cams, bikes …) |
| `analysis/questions/evaluate.py` | per-candidate fav-at-open, moved, persist, signal hit rates and void → `out/results.json`, `out/evaluate.txt` |
| `analysis/questions/signals.py` | pooled 20-station signal test, the Arsenal → King's Cross trace, VIX after warm-up → `out/signals.json` |
| `analysis/questions/vix_replay.py` | in-process multiverse replay (no server, brokers off, no network) → `out/vix.json` |
| `analysis/questions/extras.py` | tie rates, feed cadence, peak bands, holding by hour, camera coverage |
| `analysis/questions/surge_check.py` | the `hub_surge` cue against fixed picks |
