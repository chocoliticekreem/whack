# City markets (:8051)

Live prediction markets across five central-London hotspots (Oxford Circus, King's Cross St Pancras,
Piccadilly Circus, Waterloo, Bank). Simulated futures set the prices; real recorded TfL and Open-Meteo data settles them.
Six rule-based AI traders each read one feed and bet against the players. The web app proxies `/api/city/*` here.

```bash
python3 services/city/server.py                                   # live: reads the recorder's newest data
python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z # replay: 1 recorded minute per 5 s
python3 -m unittest discover -s services/city
```

Env: `CITY_PORT` (8051), `CITY_RAW` (recorder root; default `data/raw` of the main checkout), `CITY_LOG=1` logs requests.
CORS allows localhost:3047, :3349 and :3849.

## Endpoints

`GET /hotspots /traders /markets /round /leaderboard /stats`, `POST /settle {id}`, `POST /bet {player, market, option, stake}`,
`POST /score {player, name, points}`. The response shapes are in the city contract (shared with the UI); `/stats` is
the settled count per series (real reading vs void), calibration tables and the favourite's price at open.
`/round` lists the current round's markets and the previous round's results.

## Rounds and series

Markets come in rounds on a fixed 5-minute wall clock (:00, :05 … Europe/London), Kalshi-style. Every market in round
R opens when round R−5 closes, closes at R−60 s (a series may close earlier) and settles at R, so there is always one
round to bet on. Each round lists every registered series, so a player can learn them.

The series registry is in `markets.py`. Adding one is self-contained: a `build` and a `settle` function, one
`register(Series(id, feed, build, settle, close_before_ms=60_000, skip=None))`, and a `PLAYS` entry in `traders.py`
for each trader whose feed informs it. Today's book:

| series | kind | question | settles on |
|---|---|---|---|
| oxford-busy | bracket | Oxford Circus busyness at 17:50 (4 brackets of % of usual) | first TfL Crowding reading after bets close |

Bracket ladders: three equal-width edges on whole numbers, placed on the futures so the favourite is a middle bracket
near 40% and no bracket is under 5%; lower edges are inclusive and `rule` says so. A bracket series whose favourite
would still exceed 85% is skipped for the round. Crowding settles on the first reading published after bets close,
so nobody bets on a reading that is already out. A market halts on a stale feed (3 min; 6 min for the 5-minutely
crowding) or on a 3σ jump in a crowding series, and resumes after 2 clean ticks. With no reading in time it voids
with a refund.

## Pricing

Each market runs 50 futures. Every future starts from the last real reading:
- crowding: a log random walk per station. Per-minute sigma comes from today's real changes, drift is half the last
  hour's trend (capped), and every step is capped at 2.5σ.

Each new observation rules out the futures that disagree with it, never more than 60% of them in total.
prob = share of surviving futures, clamped to [3%, 97%]. Bets move prices through LMSR with b = 1200, with the futures'
probabilities as the base (prob_i ∝ model_i · e^(q_i/b)), so a 100-point bet on a 50% option moves it about 4 points.
A 5% fee keeps the locked payout near the displayed `odds = 0.95/prob`.

## Files

`data.py` reads the recorder files into compact snapshots and never returns data after `now`. `pricing.py` holds the
fits, simulators, particle filter and LMSR. `markets.py` holds the series registry and builds and settles each series. `traders.py` holds the
six traders. `exchange.py` runs the rounds, betting, settlement and scores. `server.py` is the HTTP layer.
`hotspots.json` lists the hotspots with their JamCams, bus stops and lines; the recorder adds these cams to its
YOLO list.
