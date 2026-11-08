# City markets (:8051)

Live prediction markets across five central-London hotspots (Oxford Circus, King's Cross St Pancras,
Piccadilly Circus, Waterloo, Bank). Simulated futures set the prices; real recorded TfL and Open-Meteo data settles them.
Seven rule-based AI traders each read one feed and bet against the players. Each stakes quarter Kelly on the LMSR for
its own probability (its calibrated record in the series, or the Simulation's forecast) against the option's price,
after the 5% fee and the slippage, on the points not already staked on unsettled markets, at most 100; with no edge,
or a stake under 10, it sits out. A stake is never more than a quarter of the free points, so losing every open bet
still leaves 0 or more (sizing on settled points alone took the Contrarian to −181 live). A negative record on file
loads as 0 and is logged. Details: `docs/traders-and-tops.md`. Their records (points, won/lost, last reason) are appended to the ledger's SQLite
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

`GET /health /hotspots /traders /markets /round /leaderboard /stats /signals /tops`, `POST /settle {id}`,
`POST /bet {player, market, option, stake}` (with `X-Player-Token`, see Wallet below). The response shapes are in the city
contract (shared with the UI); `/stats` is the settled count per series (real reading vs void), calibration tables and
the favourite's price at open. `/round` lists the current round's markets, the open hourly `headlines` and the previous
round's results. Every market carries `signals` (1–2 visible clues, shapes in `signals.py`); `/signals` is the top
strip: the 5-hub vs-usual strip with the laggard flagged, the Tube line board with reasons, the latest football
result and the VIX line.

Wallet: `POST /player/new` (per client IP, which at a venue is the whole Wi-Fi behind NAT: 30 a minute,
`CITY_NEWPLAYER_PER_MIN`, and 500 a day, `CITY_NEWPLAYER_PER_DAY`) is the only way to get a player: it returns a
server-made id, a random token (stored hashed) and the W1000 grant. `GET /wallet /ledger /positions /history ?player=`,
`POST /bet /limits {dailyLoss} /break {hours} /player/name {name} /player/pin {pin}` all need `X-Player-Token`; reads never create a
player. `ledger.py` keeps player points in an append-only, double-entry SQLite ledger (`CITY_LEDGER_DB`, default
`services/city/data/ledger.db`, gitignored, one server per file via a lock file; `--replay-from` uses an in-memory
ledger). Balances are sums of entries. `/bet` takes an `idempotency_key` (scoped to the market round), a minimum stake
of 10, and moves the stake from cash to escrow; a payout never exceeds the market maker's shares (odds under 1.05 are
refused) and rounds down. A daily gross-loss limit, a server-issued 60 s confirm token above 25% of the balance, a
1 h / 24 h break and a house worst-case exposure cap (`CITY_HOUSE_BANKROLL`, `CITY_EXPOSURE_CAP`, 100000 each) are
enforced on the server. The exposure cap counts the AI traders' open bets as well as the players' (an AI fill past it
is skipped), though AI points stay off the player ledger. The confirm threshold and the loss-limit room count the
player's other pending bets, so firing several large bets at once needs a confirm token for each one past 25%. After a
restart, bets on recorded markets settle from data; only unresolvable ones are refunded. A market that outlives the
current round (an hourly headline) is listed again, rebuilt from its ledger record, with its bets on it; the position
cap reads a player's booked stakes from the ledger, so bets placed before a restart still count. On a live start the
server fetches the VIX once (5 s timeout) before the first round, after the first read of the recorder files (30-80 s
on a full day; a frame fetched before it was stale by the time the round was built), so london_vix lists in it. `/leaderboard` ranks players by ledger equity.

Every request and tick shares one lock, so a tick never holds it for the slow part of a round open: it settles under
the lock, builds the next round's markets (and the Simulation trader's 4000-draw view of each) outside it, then lists
them under it. The VIX poller hands its fetch to the ticker, which keeps every store write on one thread. Any holder
that keeps the lock past 500 ms logs `lock held N ms by <tick part | METHOD /path>` to stderr. **A `/bet` that times
out on the client may still have filled.** Retry with the same `idempotency_key`: the server returns the original bet
(`"replayed": true`, same `betId`, filled, pending or cancelled) and moves no more points. A new key is a new bet. The server binds 127.0.0.1 (`CITY_HOST`) and refuses bodies
over 16 KB. `Ledger.audit()` re-checks every invariant from the raw entries. A `/bet` is accepted as pending and fills
after the series' delay (see Integrity below); `GET /bet?player=&id=` shows it pending, filled or cancelled, and
`/positions` lists the pending ones under `pending`.

Players see the currency as W (play money, no real value); the ledger stores whole W as integers, and every new player
gets `GRANT` = 1000 once.

Signing in on another device (no logins): nicknames are unique, case-insensitively (`/player/new {name}` and
`/player/name` answer 409 `name_taken`). `POST /player/pin {player, pin}` (token required) sets or changes a 4-digit
PIN, stored as salted PBKDF2 in `player_pins`; the player needs a name first. `POST /player/recover {name, pin}`
returns `{player, token, nickname}` with a NEW token (kept hashed in `player_tokens`); the device's old token stays
valid, so both devices keep playing. A wrong name, a wrong PIN and a player without a PIN all answer the same 401
"Name or PIN didn't match". Five misses on one name within 15 minutes lock that name's recovery for 15 minutes (409
`locked`, in memory, so a restart clears it), and each network gets 20 tries per 10 minutes (429).

## Backups and restore

`backup.py` runs as a thread of the live server: on start and then every 5 minutes it copies the ledger with the
sqlite3 backup API (consistent while the server writes) to `services/city/data/backups/ledger-<UTC time>.db`
(gitignored) and keeps the newest 48 (4 hours). If `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set, each copy is
also uploaded to the Supabase Storage bucket `ledger-backups` (create it once, private); without them it stays local.
`python3 services/city/backup.py` takes one backup now.

To restore: stop the city server (it holds `ledger.db.lock`), move the live files aside, copy a backup into place, start
again. The ledger replays its own tables, so balances, PINs and tokens come back as of that copy.

    mv services/city/data/ledger.db services/city/data/ledger.db.broken
    rm -f services/city/data/ledger.db-wal services/city/data/ledger.db-shm
    cp services/city/data/backups/ledger-<time>.db services/city/data/ledger.db
    python3 -c "import sys; sys.path.insert(0, 'services/city'); from ledger import Ledger; print(Ledger('services/city/data/ledger.db').audit())"   # [] = healthy
    python3 services/city/server.py

From Supabase: download the object from the `ledger-backups` bucket in the dashboard (or
`curl -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" "$SUPABASE_URL/storage/v1/object/ledger-backups/<name>" -o ledger.db`) and copy it into place the same way.

## Rounds and series

Markets come in rounds on a fixed 5-minute wall clock (:00, :05 … Europe/London), Kalshi-style. Every market in round
R opens when round R−5 closes, closes at R−60 s (a series may close earlier) and settles at R, so there is always one
round to bet on. Each round lists every registered series, so a player can learn them.

The series registry is in `markets.py`. Adding one is self-contained: a `build` and a `settle` function, one
`register(Series(id, feed, build, settle, close_before_ms=60_000, skip=None, due=None, settles_early=False,
enabled=True))`, and a `PLAYS` entry in `traders.py` for each trader whose feed informs it. `due(round)` limits a series
to some rounds (the hourly headlines), `settles_early` lets it resolve before settlesAt, `enabled=False` keeps it
registered but out of the lineup. The lineup is the data-backed one from `docs/questions.md`; *vs usual* = live TfL %
÷ TfL's typical % for that weekday and 15-min band (`crowding_profile`). The recorder writes a new profile file each
day, one station per append (about 00:03 UTC). The store re-reads a profile file whenever its mtime or size changes,
reads a cut gzip member up to the cut and tries again on the next tick, and per station and weekday uses the newest
file that has it, so a station the new day's file does not have yet keeps yesterday's profile.

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
with a refund. Every settlement snapshot is `{value, source, measuredAt, fetchedAt}`: `measuredAt` is the source's
own time for the value (TfL's `timeUtc`, the earliest one used when several stations count; the image time for a
camera; the frame or poll time elsewhere) and `fetchedAt` is when the recorder fetched it. A crowding or camera market
that is not locked early settles only when `measuredAt` > `closesAt`; any other answer voids it.

Daily loss limit counts **gross** losses (stakes of bets lost today plus stakes in play), so wins never buy back room:
a player up +593 can still be refused for the limit, as the live test's Kelly bot was. That is deliberate. A net limit
lets a winning streak fund unlimited further risk, so the limit would stop binding exactly when a player is chasing.
A gross limit caps how much a player can put at risk in a day whatever their running P&L, matching the responsible-play
rule it stands in for; a player who wants more room can raise it, which takes effect after 24 h.

## Pricing

Each market runs 50 futures. Every future starts from the last real reading:
- city_pulse: a log random walk of City Pulse from TfL's own stamp on the open reading to the expected settle
  reading. Sigma is fitted on today's changes around TfL's usual day; the drift carries 0.30 of the last 10 min's
  change in City Pulse vs usual (`pricing.fit_pulse`, fitted out of sample by `analysis/judge/pulse_model.py`; half
  the last hour's trend, capped, when the usual profile is missing). The walk adds the usual day's slope (a smooth curve through the 15-min bands) over its horizon; without it,
  copying the usual curve beat the house by 32-48% a bet. The line is the half point (x.0 or x.5) nearest the futures'
  median at the settle reading, so the market opens near 50/50 (the old line, open + the median 5-min change, ignored that slope and opened
  evening markets at a 94% favourite). Steps are capped at 2.5σ.
- hub_surge: refitted every round on today's readings up to the open, binned by TfL stamp (`markets.hub_fits`,
  `pricing.fit_relative_ou`). Each hub runs 3-4 TfL steps from its open reading to the settle reading (TfL publishes
  ~7 min after it measures). Its settle value is drawn in one step at that horizon: its usual-day slope, a pull of
  b_k toward its own level against the other hubs (Bank sits 0.15 below them and does not catch up), and correlated
  shocks with each hub's last-3-h residual sd. The 50 futures are a stratified sample of 2,000 draws; open shares are
  tempered, p ∝ share^0.7. A TfL publish stamped after the open re-draws every future from that reading for the steps
  left, untempered. Out of sample (133 races, 15:00–02:10) log loss 1.458 vs 1.498 for the previous per-minute walk
  (−0.039 ± 0.024), Brier 0.738 vs 0.756. A hub without a reading at open is dropped from the race (3 needed).
  Details, rejected candidates and leak ROIs: docs/markets.md "hub_surge: the model", `analysis/judge/hub_model.py`.
- hub_hour: the same walk and usual slope with sigma fitted on 60-min changes, widened until the favourite at open is
  ≤ 70% (the 5-min fit priced it at 89% against a real 56%).
- london_vix: a bootstrap of today's own h-minute changes (post-warm-up frames only).
- heathrow_stack: each future's count is a Poisson draw around the open count pulled 47% of the way per 5 min toward
  its trailing 30-min mean (`pricing.CountSim`, `analysis/judge/fit_heathrow.py`, fitted 13:24–17:00). The line is
  the half point next to the futures' median whose Over share is nearest 50%. Each in-round reading resets the
  weights to the same model run from that reading, since the count is close to a fresh draw every minute. The old
  bootstrap of raw changes ignored where the count stood against its mean, and fading a stack that had just built
  won +53% out of sample.
- clean_sheet: P(break) from a rolling 3-h per-line hazard × the Good lines, held to 30–70% at open (the whole-day
  rate priced 85% against a real 43%); each future draws its break time, so a clean hour drifts toward YES.

Today's replay (15:05–18:30, 42 rounds): favourite at open in 30–70% for city_pulse 86%, heathrow_stack 82%, both
headlines 100%; hub_surge sits at 0.25–0.45 as a 5-way race should (Tube Crowd's furthest-below pick won 15/42 = 36%).

Out of sample (hub_surge and City Pulse fitted on 12:00–17:00, scored on markets opened 17:00–21:30, 54 each):
hub_surge open Brier 0.752 (was 0.797; uniform 0.800), log loss 1.501 (was 1.561; uniform 1.609). Copying Tube Crowd
(10 points on the furthest-below hub at the open quote) returns +13%, 95% bootstrap CI −27% to +55% (was +32%,
−17% to +86%): 54 races can't pin it to ±5%. City Pulse opens with the favourite in 40–65% on 78% of markets with
nearest-half lines (52% with x.5-only lines; was 13%), since the futures often span less than a point. Its P(over) runs high in the evening
(over won 21/54). Other series are unchanged.

Each new observation weights every surviving future by its likelihood exp(-(e/tol)²/2), e = its error in sigmas,
tempered so the effective sample size stays at half the survivors or more; futures off by more than tol are also
ruled out, never more than 60% of them in total. After that cap the weights still move the price (on main, 96 of the
96 capped markets that got later readings stopped learning; now 42 do). prob = weighted share of the surviving
futures, clamped to [3%, 97%]. Bets move prices through LMSR with b = 1200, with the futures'
probabilities as the base (prob_i ∝ model_i · e^(q_i/b)), so a 100-point bet on a 50% option moves it about 4 points.
A 5% fee keeps the locked payout near the displayed `odds = 0.95/prob`.
Where the model has a closed form (clean_sheet, heathrow_stack, hub_hour from a full reading) the price uses it in
place of the futures' share, so those opens carry no Monte Carlo error; the filter audit, LMSR invariants and their
tests are in `docs/pricing-engine.md`.

## TOPS loop

Marshall Wace's TOPS collects ideas from brokers, scores who has been right and leans on them by track record. Here the
brokers are the seven traders, and the loop (`tops.py`) feeds their records back into the opening price:

1. **Views at open.** Before a market is listed, every trader that plays the series states a view, with the same rules
   and feed-freshness checks as its bets. The Simulation trader's is its forecast (`sim_view`). The others pick a side;
   the pick becomes a probability from the trader's own record in that series: the futures' price for the pick plus its
   shrunk excess hit rate on past picks (never below the price: a losing trader is trusted less, never faded). With no
   record, the view is the futures' price, so it moves nothing.
2. **Track record.** At settle, each view scores `x = log p_view(winner) − log p_futures(winner)`: its skill over the
   futures prior. The prior, not the blended price, is the baseline, so a right trader doesn't look worse the more it
   is trusted. Skill model per trader per series: `x ~ N(θ, σ²)`, `θ ~ N(0, τ²)`, with a discounted likelihood (half-life
   20 settled markets, ≈100 min of 5-minute rounds, so it follows the day's regimes). Posterior
   `θ ~ N(Σw·x / (Σw + K), σ² / (Σw + K))`, `K = σ²/τ²`. σ² is the series' pooled second moment of the scores; K is
   empirical Bayes, from how much the traders' mean scores spread beyond noise, capped at the decayed window (29.4).
   On the replay it settles at K ≈ 23 markets: the records barely differ beyond noise.
3. **Trust weights** per series ∝ `P(θ > 0)` under that posterior (no temperature to tune), mixed with a 5% floor each
   so no trader is ignored, each capped at 40%. What the cap cuts stays with the futures (`futuresWeight`), not spread
   to the other traders: with two or three per series, that would hand the best one's excess to the worst.
4. **The blend.** Opening prior = `(1 − λ)·futures + λ·pool`, `pool = Σ w_i·view_i + w_futures·futures`. λ comes from
   the pool's own record: at every settle where the pool differed from the futures, `r = pool(winner)/futures(winner) − 1`
   is kept per series (same decay), and λ maximises `Σw·log(1 + λr)` to second order, `λ = Σw·r / Σw·r²`, shrunk by
   `n/(n + 10)` and held in [0, 0.3]. A pool that hasn't beaten the futures on log loss gets λ = 0 and the price is the
   futures. The blend rides on the futures as a fixed tilt (blended ÷ prior) while they learn in-round, held in the
   futures' [3%, 97%] band. The LMSR, the odds floor, the caps and `SIM_WEIGHT` (0) are unchanged.
5. **Kept.** One row per listed market in the ledger's SQLite (`tops_markets`: prior, views, λ, weights, tilt, then the
   winner at settle); the records are rebuilt from it at start, a relisted headline keeps its tilt, and a market settled
   after a restart is still scored. `ai_records` can't rebuild them: it keeps won/lost totals, not per-market views.
   No look-ahead: records change only at settle, so a market opening at t only sees settles before t.

`/markets` carries `tops {lambda, weights, contribution}` per market (`contribution` = each option's change at open),
`/traders` a `trust` per trader (its mean weight over the series it has settled views on), and `GET /tops` per series
each trader's posterior `score` (mean), `sd`, `pSkill`, `n`, `nEff` and `weight`, `futuresWeight`, `lambdaNow` and the
history after every settle, plus the empirical-Bayes `priorK`.

**Does it help? A little, not significantly.** `analysis/judge/tops_replay.py` logs every open and settle of a replay
(12:40 Nov 7 – 02:35 Nov 8 UTC) and walks each variant of the loop over the same log from an empty record: paired,
walk-forward, nothing fitted on the window. Log loss vs the futures prior (negative is better), new − old paired:

| window | n | old (PR #93) | new | new − old |
|---|---|---|---|---|
| all | 509 | −0.0006 ± 0.0004 | −0.0010 ± 0.0009 | −0.0004 ± 0.0006 |
| opened ≥ 17:00 | 354 | −0.0009 ± 0.0005 | −0.0018 ± 0.0012 | −0.0009 ± 0.0008 |
| opened ≥ 00:55 (fresh) | 54 | −0.0006 ± 0.0009 | +0.0003 ± 0.0024 | +0.0008 ± 0.0018 |

The rule set before the run (keep new if new − old ≤ 0 over the whole walk) keeps the new loop; the pooled differences
are inside 1.2 SE (per series: clean_sheet −2.1 SE on 13 markets, hub_hour +1.5 SE on 12). All of the gain is the λ
rule (mean λ 0.03 → 0.16); the new weights alone change nothing, since with two traders per series both rules mostly
sit on the cap and the floor. The Tube Crowd leaks are shaved, not closed (after
17:00: City Pulse +19.3% → +16.8%, copycat +15.1% → +13.9%). Multiple testing (`tops_permutation.py`, 200 re-walks
with the outcomes shuffled): of 10 trader-series, 0.5 would pass p < 0.05 by chance and 2 do (Tube Crowd on City
Pulse, Plane Spotter on Heathrow); none survives Bonferroni or Benjamini–Hochberg, and the posterior calls no one
skilled (P(θ > 0) > 0.95: 0).
Full write-up: `docs/traders-and-tops.md`.

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
  loss limit, break, confirm token, each net of the player's other pending stakes) and the price now is still above
  the 1.05x floor (else 409 "price too short"), but holds no escrow. Fills run in placement order and keep the
  player's earlier pending stakes aside, so a later bet on a faster market cannot spend an earlier bet's room. A fill
  past 25% of cash goes through only if the player confirmed that bet; otherwise it is cancelled. At `fillsAt` the bet is booked through the ledger at the price
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

## Parallel exchange venue (:8062)

The Kalshi-style exchange in `docs/exchange-design.md`, running beside this server: no house, one order book per
binary contract, and about 2,200 AI accounts (12 market makers, 184 hedge funds in three tiers, 2,000 retail) trading
against players. It reads the same recorder files read-only and keeps its books, ledger and accounts in memory, so
the live LMSR exchange is untouched and a restart starts it clean.

```bash
python3 services/city/venue_server.py                                    # live, beside server.py
python3 services/city/venue_server.py --replay-from 2026-11-07T15:00:00Z  # replay at 12x
python3 -m unittest discover -s services/city -p test_venue.py
python3 services/city/venue_demo.py      # walkthrough + pass/fail health report (generated data; --replay-from for recorded)
```

Env: `CITY_VENUE_PORT` (8062), `CITY_VENUE_HOST` (127.0.0.1), plus `CITY_RAW` and `MULTIVERSE_URL` as above.
Flags: `--retail`, `--market-makers`, `--seed`. Endpoints: `GET /health /contracts /book?contract= /stats
/leaderboard /positions?account=` (X-Player-Token), `POST /account/new {name}`, `POST /order {account, contract, side,
price, qty, tif, postOnly}`, `POST /cancel {account, order}`.

| Piece | File | Rules |
|---|---|---|
| Contracts | `venue.py` | A two-way market is one contract (YES = its first option); a race or ladder is one contract per option. A YES pays 100 points if it happens |
| Order book | `venue_book.py` | One book per contract: a NO bid at 40 is a YES offer at 60. Price-time priority, trades at the resting price, post-only and immediate-or-cancel orders, self-trade prevention by firm |
| Gateway | `venue_gateway.py` | The one path for every order, human or AI: the series' speed bump from `integrity.py` on any order that can take liquidity (post-only quotes skip it, as on TSX Alpha; AI also carries 300 ms of latency), cancels never delayed, 1 s minimum rest, a 25-point price collar on orders that can trade on arrival, the 25% / 10% position limit on net risk (closing a position is always allowed), per-tick message budgets |
| Money | `venue_ledger.py` | Double-entry in whole points; full collateral (a matched pair puts exactly 100 in the pool); a YES+NO pair is paid 100 at once; void returns the pool pro rata to cost; `audit()` rechecks every invariant. **Fees are off by default** (a points game has no exchange to fund); `--fees kalshi` or `CITY_VENUE_FEE_X100=7` turns on Kalshi's taker fee ceil(0.07·C·P·(1−P)) with free makers, refunds on void and market-maker rebates |
| Lifecycle | `venue.py` | A shadow copy of the live exchange lists, observes and settles the markets. Halts and suspensions cancel every order; close stops trading; settlement pays 100 per winning contract |
| Market makers | `venue_agents.py` | Two-sided post-only quotes around the forecast, inventory skew, fair value moved by fills, wider before close, on forecast jumps and as the flow against them turns one-sided (Glosten & Milgrom), inventory limits. Obligations for designated market makers: spread ≤ 6 (≤ 12 while the venue flags a **fast market**: one-sided flow or a 3-point forecast jump in the last minute), size ≥ 10, 90% uptime after a 10 s grace; with fees on, those that meet them share 25% of the contract's taker fees |
| Hedge funds | `venue_agents.py` | Tier 1 platforms run every rule as pods; tier 2 and 3 run one. Each blends its rule's measured hit rate with the forecast, trades only when the edge beats price + fee + a margin, sizes with fractional Kelly on its own equity, and is wound down at half its capital; its tier's investors relaunch a fund when they can |
| Retail | `venue_agents.py` | Random arrivals with documented biases (long shots, favourites, herding, momentum, home hub, none) and small immediate-or-cancel orders |

Cost: every agent is rules, so there are no language-model calls; data comes from the recorder files plus the same
local VIX and simulation polls as `server.py`. Measured: 12.4 ms of CPU per one-second tick and about 60 MB with 2,188
AI accounts. `/stats` reports P&L by group, market-maker uptime, fees, calibration (Brier of the closing price
against the forecast alone and an uninformed guess) and the ledger audit.

## Files

`data.py` reads the recorder files into compact snapshots and never returns data after `now`. `pricing.py` holds the
fits, simulators, particle filter and LMSR. `markets.py` holds the series registry and builds and settles each series. `traders.py` holds the
seven traders (Tube Crowd → S1/S2/S5, Camera Eye → S3, Plane Spotter → S4, Line Watcher → S6, Rain Radar → S2 only in
rain, Contrarian → all, Simulation → S1/S2/S5). `tops.py` is the TOPS loop (above). `eval_sim.py` scores the simulation (below). `signals.py` builds the per-market clues and `/signals`. `exchange.py` runs the rounds, betting, settlement and scores. `integrity.py` holds the bet delay, suspend-on-change and caps. `server.py` is the HTTP layer. `backup.py` copies the ledger every 5 minutes (above).
`hotspots.json` lists the hotspots with their JamCams, bus stops and lines; the recorder adds these cams to its
YOLO list.
