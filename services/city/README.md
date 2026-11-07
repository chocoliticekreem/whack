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

`GET /hotspots /traders /markets /leaderboard /stats`, `POST /settle {id}`, `POST /bet {player, market, option, stake}`,
`POST /score {player, name, points}`. The response shapes are in the city contract (shared with the UI); `/stats` is
the settled count per market kind (real reading vs void) plus a calibration table.

## Markets

| kind | question | feed | settles on |
|---|---|---|---|
| versus | Which station is busiest at 17:50? | TfL Crowding (% of usual) | each station's last reading at or before T (≤2 min old) |
| overunder | Over/under 12 people on the Oxford Circus camera at 17:52? | JamCam + YOLO | median count in [T−60 s, T]; the neighbouring cam is the fallback |
| yesno | Will any Tube line get worse by 17:22? | TfL line status | the last reading at or before T compared with the snapshot at open |
| yesno | Will the Central line be back to Good Service by 17:22? | TfL line status | the last reading at or before T |
| yesno | Rain in central London before 17:25? | Open-Meteo | any reading > 0.1 mm in [open, T] |

The exchange keeps 3–5 markets open at all times. At least one closes within 2 min, and a new market opens as an old one
closes. Horizons are 2–10 min. Crowding and rain markets settle just after TfL's or Open-Meteo's 5-min publish, so the
settling reading is fresh. Trading closes 60 s before T (90 s for cameras). A market halts on a stale feed (3 min; 6 min
for the 5-minutely crowding and rain) or on a 3σ jump in its own series, and resumes after 2 clean ticks. With no
reading by T+3 min, "by T" yes/no markets resolve No and the others void with a refund.

## Pricing

Each market runs 50 futures. Every future starts from the last real reading:
- crowding: a log random walk per station. Per-minute sigma comes from today's real changes, drift is half the last
  hour's trend (capped), and every step is capped at 2.5σ;
- cameras: sticky counts. The per-minute chance of a change and the size of a change are fitted from today's counts
  for that camera;
- lines: worsening and recovery hazards per line-minute, fitted from today's status changes;
- rain: one wet/dry draw per 15-min slot, using the odds of that slot's nowcast.

Each new observation rules out the futures that disagree with it, never more than 60% of them in total.
prob = share of surviving futures, clamped to [3%, 97%]. Bets move prices through LMSR with b = 1200, with the futures'
probabilities as the base (prob_i ∝ model_i · e^(q_i/b)), so a 100-point bet on a 50% option moves it about 4 points.
A 5% fee keeps the locked payout near the displayed `odds = 0.95/prob`.

## Files

`data.py` reads the recorder files into compact snapshots and never returns data after `now`. `pricing.py` holds the
fits, simulators, particle filter and LMSR. `markets.py` builds and settles each market kind. `traders.py` holds the
six traders. `exchange.py` runs the rolling book, betting, settlement and scores. `server.py` is the HTTP layer.
`hotspots.json` lists the hotspots with their JamCams, bus stops and lines; the recorder adds these cams to its
YOLO list.
