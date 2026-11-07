# City markets (:8051)

Live prediction markets across five central-London hotspots (Oxford Circus, King's Cross St Pancras,
Piccadilly Circus, Waterloo, Bank). Simulated futures set the prices; real recorded TfL and Open-Meteo data settles them.
Six rule-based AI traders each read one feed and bet against the players. The web app proxies `/api/city/*` here.

```bash
python3 services/city/server.py                                   # live: reads the recorder's newest data
python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z # replay: 1 recorded minute per 5 s
python3 -m unittest discover -s services/city
```

Env: `CITY_PORT` (8051), `CITY_RAW` (recorder root; default `data/raw` of the main checkout), `CITY_LOG=1` logs requests,
`MULTIVERSE_URL` (default `http://127.0.0.1:8349`; live mode polls its `GET /vix` every 15 s for the London VIX).
CORS allows localhost:3047, :3349 and :3849.

## Endpoints

`GET /hotspots /traders /markets /round /leaderboard /stats /signals`, `POST /settle {id}`,
`POST /bet {player, market, option, stake}`, `POST /score {player, name, points}`. The response shapes are in the city
contract (shared with the UI); `/stats` is the settled count per series (real reading vs void), calibration tables and
the favourite's price at open. `/round` lists the current round's markets, the open hourly `headlines` and the previous
round's results. Every market carries `signals` (1–2 visible clues, shapes in `signals.py`); `/signals` is the top
strip: the 5-hub vs-usual strip with the laggard flagged, the Tube line board with reasons, the latest football
result and the VIX line.

Wallet: `GET /wallet /ledger /positions /history ?player=`, `POST /limits {player, dailyLoss}`, `POST /break {player, hours}`.
`ledger.py` keeps player points in an append-only, double-entry SQLite ledger (`CITY_LEDGER_DB`, default
`services/city/data/ledger.db`, gitignored). Balances are sums of entries. `/bet` takes an `idempotency_key` and moves the
stake from cash to escrow; settlement pays it out exactly once. A daily loss limit, a stake confirmation above 25% of the
balance and a 1 h / 24 h break are enforced on the server. `Ledger.audit()` re-checks every invariant from the raw entries.

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
| hub_surge (S1) | every round | versus (5 hubs) | Which hub surges most by 18:35? | biggest rise in vs usual, open → first TfL reading after close; no move → void |
| city_pulse (S2) | every round | over/under | City Pulse at 18:35: over or under 34.62? | 100 × mean % of baseline over the 20 stations, first reading after close; < 18 → void |
| london_vix (S3) | every round | over/under | London VIX at 18:35: higher or lower than 2.31? | multiverse VIX, last frame ≤ T (≤ 2 min old); void in the engine's first hour |
| heathrow_stack (S4) | in place of S3 | over/under | Planes holding over Heathrow at 18:35: over or under 4.5? | adsb.lol holding_total, last reading ≤ T (≤ 2 min old) |
| hub_hour (S5) | round spanning :00 | versus (5 hubs) | Which hub ends the hour furthest above its usual Saturday? | highest vs usual at the next :00 |
| clean_sheet (S6) | round spanning :30 | yes/no | Clean sheet: does every Tube line on Good Service at 18:30 stay Good until 19:30? | NO the moment a Good line breaks; YES at the bell; > 5 min of gaps → void |
| oxford-busy | retired (`enabled=False`) | bracket | Oxford Circus busyness at 17:50 | first TfL reading after close |

S4 is listed only when S3 can't be: no VIX frame in 2 min (engine down or not polled, as in replay), the engine
spawned under 60 min ago, or too few post-warm-up moves. Replay never polls the multiverse, so replays list S4.
Hourly markets open with the round whose betting window spans :00 / :30 (at hh:59 / hh:29) and settle at the next
:00 / :30; `/round` shows them under `current.headlines`.

Bracket ladders: three equal-width edges on whole numbers, placed on the futures so the favourite is a middle bracket
near 40% and no bracket is under 5%; lower edges are inclusive and `rule` says so. A bracket series whose favourite
would still exceed 85% is skipped for the round. Crowding settles on the first reading published after bets close,
so nobody bets on a reading that is already out. A market halts on a stale feed (3 min; 6 min for the 5-minutely
crowding) or on a 3σ jump in a crowding series, and resumes after 2 clean ticks. With no reading in time it voids
with a refund.

## Pricing

Each market runs 50 futures. Every future starts from the last real reading:
- crowding (hub_surge, city_pulse): a log random walk per station (or of City Pulse). Per-minute sigma comes from
  today's real changes, drift is half the last hour's trend (capped), and every step is capped at 2.5σ.
- hub_hour: the same walk with sigma fitted on 60-min changes, widened until the favourite at open is ≤ 70% (the
  5-min fit priced it at 89% against a real 56%).
- london_vix, heathrow_stack: a bootstrap of today's own h-minute changes (VIX: post-warm-up frames only).
- clean_sheet: P(break) from a rolling 3-h per-line hazard × the Good lines, held to 30–70% at open (the whole-day
  rate priced 85% against a real 43%); each future draws its break time, so a clean hour drifts toward YES.

Today's replay (15:05–18:30, 42 rounds): favourite at open in 30–70% for city_pulse 86%, heathrow_stack 82%, both
headlines 100%; hub_surge sits at 0.25–0.45 as a 5-way race should (Tube Crowd's furthest-below pick won 15/42 = 36%).

Each new observation rules out the futures that disagree with it, never more than 60% of them in total.
prob = share of surviving futures, clamped to [3%, 97%]. Bets move prices through LMSR with b = 1200, with the futures'
probabilities as the base (prob_i ∝ model_i · e^(q_i/b)), so a 100-point bet on a 50% option moves it about 4 points.
A 5% fee keeps the locked payout near the displayed `odds = 0.95/prob`.

## Files

`data.py` reads the recorder files into compact snapshots and never returns data after `now`. `pricing.py` holds the
fits, simulators, particle filter and LMSR. `markets.py` holds the series registry and builds and settles each series. `traders.py` holds the
six traders (Tube Crowd → S1/S2/S5, Camera Eye → S3, Plane Spotter → S4, Line Watcher → S6, Rain Radar → S2 only in
rain, Contrarian → all). `signals.py` builds the per-market clues and `/signals`. `exchange.py` runs the rounds, betting, settlement and scores. `server.py` is the HTTP layer.
`hotspots.json` lists the hotspots with their JamCams, bus stops and lines; the recorder adds these cams to its
YOLO list.
