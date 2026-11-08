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
| `city_pulse` | Will Tube activity finish above the expected level (X) by HH:MM? | Above / Below | Average busyness of 20 TfL stations, first readings stamped after close | The line is the futures' median, on the nearest .0 or .5 (#77). Known: biased when the city is emptying in the evening (Below won 14 of 15, 22:40–23:50). Not fixed: no stable model in the data yet. |
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
