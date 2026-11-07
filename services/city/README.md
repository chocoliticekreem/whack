# City markets (:8051)

Live prediction markets across five central-London hotspots (Oxford Circus, King's Cross St Pancras,
Piccadilly Circus, Waterloo, Bank). Simulated futures set the prices; real recorded TfL and Open-Meteo data settles them.
Seven rule-based AI traders each read one feed and bet against the players. Each stakes min(100, 10% of its points)
and sits out under 100 points; their records (points, won/lost, last reason) are appended to the ledger's SQLite
(`ai_records`) on every settle and reloaded on start. The web app proxies `/api/city/*` here.

```bash
python3 services/city/server.py                                   # live: reads the recorder's newest data
python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z # replay: 1 recorded minute per 5 s
python3 -m unittest discover -s services/city
```

Env: `CITY_PORT` (8051), `CITY_RAW` (recorder root; default `data/raw` of the main checkout), `CITY_LOG=1` logs requests,
`MULTIVERSE_URL` (default `http://127.0.0.1:8349`; live mode polls its `GET /vix` every 15 s for the London VIX; the
multiverse server itself defaults to :8049, so start it with `MULTIVERSE_PORT=8349` or set this),
`SIM_URL` (default `http://127.0.0.1:8449`, the central-London agent simulation; live mode polls its `GET /state` every
30 s), `SIM_WEIGHT` (default 0: the share of each market's 50 futures sampled from the simulation; the sim loses to
persistence, so by default it only trades, as the Simulation trader), `CITY_LEDGER_DB`.
CORS allows localhost:3047, :3349 and :3849.

## Endpoints

`GET /health /hotspots /traders /markets /round /leaderboard /stats /signals`, `POST /settle {id}`,
`POST /bet {player, market, option, stake}` (with `X-Player-Token`, see Wallet below). The response shapes are in the city
contract (shared with the UI); `/stats` is the settled count per series (real reading vs void), calibration tables and
the favourite's price at open. `/round` lists the current round's markets, the open hourly `headlines` and the previous
round's results. Every market carries `signals` (1–2 visible clues, shapes in `signals.py`); `/signals` is the top
strip: the 5-hub vs-usual strip with the laggard flagged, the Tube line board with reasons, the latest football
result and the VIX line.

Wallet: `POST /player/new` (per client IP, which at a venue is the whole Wi-Fi behind NAT: 30 a minute,
`CITY_NEWPLAYER_PER_MIN`, and 500 a day, `CITY_NEWPLAYER_PER_DAY`) is the only way to get a player: it returns a
server-made id, a random token (stored hashed) and the 1000-point grant. `GET /wallet /ledger /positions /history ?player=`,
`POST /bet /limits {dailyLoss} /break {hours} /player/name {name}` all need `X-Player-Token`; reads never create a
player. `ledger.py` keeps player points in an append-only, double-entry SQLite ledger (`CITY_LEDGER_DB`, default
`services/city/data/ledger.db`, gitignored, one server per file via a lock file; `--replay-from` uses an in-memory
ledger). Balances are sums of entries. `/bet` takes an `idempotency_key` (scoped to the market round), a minimum stake
of 10, and moves the stake from cash to escrow; a payout never exceeds the market maker's shares (odds under 1.05 are
refused) and rounds down. A daily gross-loss limit, a server-issued 60 s confirm token above 25% of the balance, a
1 h / 24 h break and a house worst-case exposure cap (`CITY_HOUSE_BANKROLL`, `CITY_EXPOSURE_CAP`, 100000 each) are
enforced on the server. After a restart, bets on recorded markets settle from data; only unresolvable ones are
refunded. `/leaderboard` ranks players by ledger equity. The server binds 127.0.0.1 (`CITY_HOST`) and refuses bodies
over 16 KB. `Ledger.audit()` re-checks every invariant from the raw entries. A `/bet` is accepted as pending and fills
after the series' delay (see Integrity below); `GET /bet?player=&id=` shows it pending, filled or cancelled, and
`/positions` lists the pending ones under `pending`.

## Rounds and series

Markets come in rounds on a fixed 5-minute wall clock (:00, :05 … Europe/London), Kalshi-style. Every market in round
R opens when round R−5 closes, closes at R−60 s (a series may close earlier) and settles at R, so there is always one
round to bet on. Each round lists every registered series, so a player can learn them.

The series registry is in `markets.py`. Adding one is self-contained: a `build` and a `settle` function, one
`register(Series(id, feed, build, settle, close_before_ms=60_000, skip=None, due=None, settles_early=False,
enabled=True))`, and a `PLAYS` entry in `traders.py` for each trader whose feed informs it. `due(round)` limits a series
to some rounds (the hourly headlines), `settles_early` lets it resolve before settlesAt, `enabled=False` keeps it
registered but out of the lineup. The lineup is the data-backed one from `docs/questions.md`; *vs usual* = live TfL %
÷ TfL's typical % for that weekday and 15-min band (`crowding_profile`).

| series | when | kind | question | settles on |
|---|---|---|---|---|
| hub_surge (S1) | every round | versus (5 hubs) | Which hub surges most by 18:35? | biggest rise in vs usual, open → each hub's first reading TfL stamps after close; no move → void |
| city_pulse (S2) | every round | over/under | City Pulse at 18:35: over or under 34.62? | 100 × mean % of baseline over the 20 stations, each at its first reading TfL stamps after close; < 18 → void |
| london_vix (S3) | every round | over/under | London VIX at 18:35: higher or lower than 2.31? | multiverse VIX, last frame ≤ T (≤ 2 min old); void in the engine's first hour |
| heathrow_stack (S4) | in place of S3 | over/under | Planes holding over Heathrow at 18:35: over or under 4.5? | adsb.lol holding_total, last reading ≤ T (≤ 2 min old) |
| hub_hour (S5) | round spanning :00 | versus (5 hubs) | Which hub ends the hour furthest above its usual Saturday? | highest vs usual at the next :00 |
| clean_sheet (S6) | round spanning :30 | yes/no | Clean sheet: does every Tube line on Good Service at 18:30 stay Good until 19:30? | betting closes at :35 with the opening round; NO the moment a Good line breaks; YES at the bell; > 5 min of gaps → void |
| oxford-busy | retired (`enabled=False`) | bracket | Oxford Circus busyness at 17:50 | first reading TfL stamps after close |

S4 is listed only when S3 can't be: no VIX frame in 3 min (engine down or not polled, as in replay), the engine
spawned under 60 min ago, or too few post-warm-up moves (10 five-minute moves, so about 75 min after a spawn).
`/round` `skipped.london_vix` says which. Replay never polls the multiverse, so replays list S4.
Hourly markets open with the round whose betting window spans :00 / :30 (at hh:59 / hh:29) and settle at the next
:00 / :30; `/round` shows them under `current.headlines`.

Bracket ladders: three equal-width edges on whole numbers, placed on the futures so the favourite is a middle bracket
near 40% and no bracket is under 5%; lower edges are inclusive and `rule` says so. A bracket series whose favourite
would still exceed 85% is skipped for the round. Crowding settles on each station's first reading that TfL
stamps (`timeUtc`) after bets close, not on our poll time: TfL stamps a row a median 7 min before we poll it and rows
in one poll are up to 10 min apart, so that reading arrives about 14 min after the close (void after 20 min). A market halts on a stale feed (3 min; 6 min for the 5-minutely
crowding) or on a 3σ jump in a crowding series, and resumes after 2 clean ticks. With no reading in time it voids
with a refund.

## Pricing

Each market runs 50 futures. Every future starts from the last real reading:
- crowding (hub_surge, city_pulse): a log random walk per station (or of City Pulse) from TfL's own stamp on the
  open reading to the expected settle reading. Sigma and drift (half the last hour's trend, capped) are fitted on
  today's changes around TfL's usual day, and the walk adds the usual day's slope (a smooth curve through the 15-min
  bands) over its horizon; without it, copying the usual curve beat the house by 32-48% a bet. hub_surge also pulls
  each hub toward the five hubs' mean vs usual (0.01 per min, fitted on today's replay). Steps are capped at 2.5σ.
- hub_hour: the same walk and usual slope with sigma fitted on 60-min changes, widened until the favourite at open is
  ≤ 70% (the 5-min fit priced it at 89% against a real 56%).
- london_vix, heathrow_stack: a bootstrap of today's own h-minute changes (VIX: post-warm-up frames only).
- clean_sheet: P(break) from a rolling 3-h per-line hazard × the Good lines, held to 30–70% at open (the whole-day
  rate priced 85% against a real 43%); each future draws its break time, so a clean hour drifts toward YES.

Today's replay (15:05–18:30, 42 rounds): favourite at open in 30–70% for city_pulse 86%, heathrow_stack 82%, both
headlines 100%; hub_surge sits at 0.25–0.45 as a 5-way race should (Tube Crowd's furthest-below pick won 15/42 = 36%).

Each new observation weights every surviving future by its likelihood exp(-(e/tol)²/2), e = its error in sigmas,
tempered so the effective sample size stays at half the survivors or more; futures off by more than tol are also
ruled out, never more than 60% of them in total. After that cap the weights still move the price (on main, 96 of the
96 capped markets that got later readings stopped learning; now 42 do). prob = weighted share of the surviving
futures, clamped to [3%, 97%]. Bets move prices through LMSR with b = 1200, with the futures'
probabilities as the base (prob_i ∝ model_i · e^(q_i/b)), so a 100-point bet on a 50% option moves it about 4 points.
A 5% fee keeps the locked payout near the displayed `odds = 0.95/prob`.

## The central-London simulation

Live mode reads the agent simulation (`services/multiverse`, central seed, `SIM_URL`) every 30 s with a 3 s timeout and
keeps the last good forecast; it counts only while at most 3 min old (`data.SimFeed`), otherwise the sim is off and
everything prices exactly as above. The forecast is each station's % of usual (TfL typical day) at +5, +15 and +60 min
as mean, p10, p90, read as a normal (sd = (p90 − p10) / 2.563) and interpolated to the expected settle reading.
Stations share one city-wide shock (correlation 0.5) because the sim publishes marginals only.

- Pricing: for hub_surge, hub_hour and city_pulse, the last `round(SIM_WEIGHT × 50)` futures (0 by default; 25 at `SIM_WEIGHT=0.5`) are replaced
  by draws from the sim, each a bridge from the live reading to the drawn value so the particle filter scores it like any
  other. hub_surge / hub_hour read the hub station's vs usual directly; City Pulse takes the drawn vs usual × the typical
  day for the 13 simulated stations and holds the other stations' vs usual. `futures.fromSim` on each market counts them.
- Simulation trader (`sim`): the option whose probability under 4 000 sim draws beats its price by the most, only if by
  5+ points, through the same quote, odds floor and position cap as the other traders (25% of its points per market).
  london_vix only when `MULTIVERSE_URL` is the simulation itself; the Oxford Circus multiverse VIX is a different number.

`python3 services/city/eval_sim.py record SIM_LOG` logs forecasts forward from now (there was no sim history);
`eval_sim.py score SIM_LOG --traders http://127.0.0.1:8121` rebuilds each recorded round statistical-only and blended
from the recorder's data, settles it, and prints Brier, log loss, favourite calibration and the Simulation trader's P&L.

## Integrity: courtsiding

Courtsiding (latency arbitrage): someone on the spot sees the outcome before the TfL feed that settles the market
publishes it. A passenger on a Central line train that has just stopped knows the clean sheet is lost before Line Status
says so, and bets while the market is still open. `integrity.py` adds three defences, configured per series in one
table (`TABLE`):

| series | delay_s | pauses when | cool-off | cap |
|---|---|---|---|---|
| clean_sheet (and any line_status series) | 45 | any Tube line's status changes | 20 s | 10% |
| hub_surge, hub_hour | 5 | a hub's TfL crowding reading moves > 10% | 15 s | 25% |
| city_pulse | 5 | City Pulse moves > 3% in one reading | 15 s | 25% |
| london_vix | 2 | the VIX moves > 0.30 in one frame | 10 s | 25% |
| heathrow_stack | 2 | holding changes by 4+ aircraft | 10 s | 25% |

- **Bet delay.** `POST /bet` returns `{"status": "pending", "fillsAt", "betId", …}` once the player checks pass (funds,
  loss limit, break, confirm token) but holds no escrow. At `fillsAt` the bet is booked through the ledger at the price
  then, under the same `betId` and idempotency key. If the market halted, closed or was suspended in between, or the
  wallet refuses the fill, the bet is cancelled and nothing has moved, so the ledger invariants never see a pending bet.
  Replaying the key returns the pending, filled or cancelled bet. AI traders, the Simulation trader included, queue
  through the same delay and are dropped by the same cancels (a dropped trader may decide again once the market reopens).
- **Suspend on change.** Each tick compares every new reading of a watched feed with the one before. A change past the
  threshold halts the market (`haltReason: "suspended: Central now Severe Delays; reopens 15:41:20"`), cancels its
  pending bets, resets the LMSR book so the price restarts from the futures, and reopens after the cool-off. Thresholds
  are set from 2026-11-07's recordings: 7 of 100 crowding publishes move a hub > 10%, 8 of 100 move City Pulse > 3%; 4+
  aircraft is the 95th percentile of one-minute holding changes.
- **Exposure cap.** 10% of equity per market on locally observable series, 25% elsewhere; pending stakes count.

Each market in `/markets` carries `integrity: {delayS, suspendsOn, coolOffS, capPct}` and the rule text says the same in
plain English ("Bets fill 45 s after you place them … The market pauses for 20 s whenever any Tube line's status
changes.").

Measured in `CourtsidingTest`: a courtsider stakes 100 on "No, a line breaks" T s before TfL publishes a Central line
break. Undefended (no delay, no suspend), every bet fills at odds 4.05 and returns +305. Defended, for T = 5, 15, 30 and
44 s, every bet is cancelled: edge 0. The delay only covers a lead shorter than itself: with T = 50 s the bet fills
before the feed changes and still pays +305, so on clean_sheet the 10% cap bounds what a longer lead can take. Pending
bets live in memory, so a restart forgets them (no points move).

## Files

`data.py` reads the recorder files into compact snapshots and never returns data after `now`. `pricing.py` holds the
fits, simulators, particle filter and LMSR. `markets.py` holds the series registry and builds and settles each series. `traders.py` holds the
seven traders (Tube Crowd → S1/S2/S5, Camera Eye → S3, Plane Spotter → S4, Line Watcher → S6, Rain Radar → S2 only in
rain, Contrarian → all, Simulation → S1/S2/S5). `eval_sim.py` scores the simulation (below). `signals.py` builds the per-market clues and `/signals`. `exchange.py` runs the rounds, betting, settlement and scores. `integrity.py` holds the bet delay, suspend-on-change and caps. `server.py` is the HTTP layer.
`hotspots.json` lists the hotspots with their JamCams, bus stops and lines; the recorder adds these cams to its
YOLO list.
