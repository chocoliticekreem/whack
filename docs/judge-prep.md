---
title: Judge prep — a sceptical Marshall Wace panel vs OverWatch
date: 2026-11-07
status: prep for Sunday judging · numbers checked against main at 99a6b30 (#55) and the live API, Sat 20:19–20:40
---

# Judge prep: what Marshall Wace will ask, and the true answers

The panel: two quant traders, a market-structure specialist and a quant researcher. They get 3 minutes of demo plus
Q&A, and they will press on whatever sounds like a claim. This doc gives the hardest questions they will ask, the
best answer that is also true, and the weak spots to fix before Sunday 09:00.

**Ground rule for the Q&A:** say "we measured X on n=Y, here's the caveat" before they ask. MW people respect a
team that knows where its edge isn't. They'll shred a team that oversells.

Confidence ratings: **strong** = we can defend it with code and a number. **ok** = the answer is true but the
evidence is thin. **weak** = the honest answer hurts. Fix it, or say it first.

## 0. The numbers (verified tonight; how to re-run them is in the appendix)

| What | Number | Source |
|---|---|---|
| LMSR liquidity | b = 1200, fee 5% of stake, odds 1.05x–20x, model prob clamped to [3%, 97%] | `services/city/pricing.py` |
| Price impact | 100 points on a 50% option moves it +3.8 pts; the max player stake (250 = 25% of the 1000 grant) moves it about +9 pts | LMSR formula, `Book.quote` |
| LMSR worst case with a fixed prior | b·ln(n): 832 points (binary), 1,931 (5-way). With a 3% floor prior: b·ln(1/0.03) ≈ 4,200 per market | arithmetic; the prior moves (see Q1.2) |
| House limits | bankroll 100,000; worst-case open-bet loss capped at min(100,000, bankroll + P&L); position cap 25% of equity per market | `ledger.py` `_worst_case`, `exchange.py` `POSITION_CAP` |
| Replay of the real Exchange, 12:40–20:10 | 91 rounds, 273 markets, 1 void | `analysis/judge/replay_score.py` |
| Skill of the open price vs uniform (Brier improvement) | hub_surge +0.034 ± 0.026 (n=87), city_pulse +0.039 ± 0.047 (n=89), heathrow_stack +0.023 ± 0.027 (n=76). **No series is significant (all t < 2)** | same |
| Out of sample, 18:30–20:35 (after the cue-discovery window ended 18:25) | city_pulse +0.141 ± 0.063 (t = 2.2, n=24); hub_surge t = 1.1; heathrow_stack t = 0.0 | same, run on 18:30–20:35 |
| Tube Crowd vs the house (100-point bets at the real quote) | full day: hub_surge 31/87, ROI **+44%**; city_pulse 59/86, **+32%**. Out of sample: hub_surge 7/24, +7%; city_pulse **21/23, +48%** | same |
| Plane Spotter (fade the stack) | full day 41/65, +17%; out of sample 13/19, +26% | same |
| Backing the open favourite | loses 9–16% per series (fee plus spread) | same |
| Pruning cap | 218 of 273 markets (80%) hit the 60% ruled-out cap, after which no reading can move the model | same |
| Favourite-longshot | in sample, options priced under 10% won 13.4% (priced 6.0%, n=97) and options over 70% won 71% (priced 79%, n=52). **Out of sample the pattern vanishes** (1/27 longshots won) | same |
| Oxford Circus sim vs persistence | people per camera, 96 counts over 59 min: MAE market 1.68, equal-weight worlds 1.70, persistence 1.67; 83% inside the 80% range. Bus delay: market 0.38 vs persistence 0.24 | `services/multiverse/eval_real.py --windows 12` |
| Central-London sim vs persistence | station busyness, 919 readings over 7 windows: MAE **4.14 vs 3.61** (sim loses by 15%). With events switched on: 4.14 vs 3.94 with them off (events make it worse) | `eval_real.py --central` (pr-central numbers; I re-ran 3 windows: 4.24 vs 3.61) |
| Event layer | line_disruption vs control 8/18 (44%), crowding_surge 7/17 (41%), line_displacement 1/6; base rate 50%. **No skill** | `services/multiverse/eval_events.py` |
| Settlement timing | crowding settles on our recorder's first poll after close. TfL stamps the settling row a median **7 min before** our poll, and in 1,979 of 1,979 station-rounds the stamp is before bets close | `analysis/judge/crowd_latency.py` |
| TfL typical-day precision | 2 decimals. Bank's Saturday typical is 0.06 → 0.05 at 20:45, so Bank's "vs usual" jumps about 20% at that band edge on rounding alone | `analysis/judge/profile_precision.py` |
| Tests | city 129, multiverse 127, recorder 16, all passing | `python3 -m unittest discover -s services/<x>` |
| Live, 20:35 | leaderboard Tube Crowd 1297, Plane Spotter 1194, Simulation 1182, Contrarian 918; london_vix skipped every round so far ("no fresh reading"); 25 of 50 futures in hub/pulse markets come from the central sim (`fromSim: 25`) | `GET /leaderboard`, `/round`, `/markets` |

## 1. The 10 most likely, most dangerous questions

Memorise the one-liners. The full answers are in section 2.

| # | Question | One-line answer | Conf. |
|---|---|---|---|
| 1 | "Who's on the other side, and what's your worst case?" | "An automated market maker, LMSR with b = 1200 over the model's probabilities. The ledger caps the house's worst-case open loss at 100,000 points, and each player at 25% of equity per market." | ok |
| 2 | "Your forecast ties 'nothing changes'. Why should anyone trust these prices?" | "They shouldn't trust them as a forecast yet. On one day the open prices beat a uniform guess by a margin that isn't significant, except City Pulse out of sample (t = 2.2). What we built is a market structure that measures that honestly." | ok |
| 3 | "Your own Tube Crowd trader beats your market maker by 30–45% per bet. Isn't the house a piggy bank?" | "Yes, today it is, and that's the finding. The usual-Saturday curve is information the house doesn't price yet. TOPS exists for exactly this: you learn which signal beats your book and fold it in. That's our next step." | weak |
| 4 | "Someone on a platform sees a train stop before TfL does. Or a TfL controller knows. Isn't clean sheet an insider market?" | "Yes. Clean sheet breaks our own 'emergent only' rule: a signal failure is one event that staff see first. It's our drama market, not our integrity showcase. The fix is a bet delay and closing the market early." | weak |
| 5 | "You settle on the 'first reading published after bets close'. Is that number already public?" | "TfL stamps it before the close. We settle on our first poll after the close, and TfL's stamp on that row is about 7 minutes older. We haven't measured when TfL makes it public, so it might be. The fix is to settle on TfL's own timestamp." | weak |
| 6 | "What does the agent simulation actually add? Evidence, not adjectives." | "Nothing measurable yet. Oxford Circus ties persistence, central London loses to it by 15%, and the event layer makes it worse. We left a Simulation trader in so the market scores the model live against the data-only traders." | weak |
| 7 | "How is this TOPS? Where's the track-record weighting?" | "Today it's half of TOPS: every trader has a public, settled track record, and a losing trader's stake shrinks under the 25% cap. The other half isn't built yet: weighting their picks into the price by that record." | weak |
| 8 | "Why LMSR rather than an order book? Is this an exchange or a bookmaker?" | "Today it's a bookmaker with a published model. With a 250-point cap and b = 1200, players move prices about 9 points at most. We chose LMSR because a thin market of strangers needs guaranteed liquidity. The real-money version is peer-to-peer." | ok |
| 9 | "What is the London VIX, and why is it a fear index?" | "The spread of predicted crowds across 50 simulated worlds of Oxford Circus. It covers one area, needs an hour to burn in, and its moves are mostly the engine's own noise, which is why fading it works. Call it an uncertainty gauge, not a fear index." | weak |
| 10 | "Real money: which regulator, and who on earth hedges footfall?" | "Consumers: the UK Gambling Commission, or the CFTC route Kalshi took in the US. The paying customer we'd test first is a data buyer, not a hedger. Footfall hedging is a hypothesis we haven't validated with a single customer." | ok |

## 2. The full Q&A bank

### 2.1 Is it a real market or a game?

**Q1.1 Who's on the other side of my bet?** *(ok)*
The house, through an automated market maker: prob_i ∝ model_i · e^(q_i/b), with b = 1200 (`pricing.py` `Book`). The
six AI traders bet through the same book, but their points are scored in memory, not in the ledger, so the only real
counterparty to a player is the house. Say it plainly: it's a house-banked market today.

**Q1.2 What's the house's worst case? Give me b·ln(n).** *(ok)*
With a fixed prior, LMSR loses at most b·ln(n): 832 points on a binary market and 1,931 on a 5-way one. With our
prior clamped at a 3% floor, the bound is b·ln(1/0.03) ≈ 4,200 per market. The textbook bound doesn't strictly hold
here, because the prior moves as futures are ruled out. So the hard limit is the ledger:
the worst-case loss over all open bets must stay under min(100,000, bankroll + P&L), or the bet is refused
(`ledger.py` `_worst_case`, `house_limit`). The randomised 1000-bet invariant test checks that the books still
balance (`test_ledger.py`).

**Q1.3 What's the market maker's P&L expectation?** *(weak)*
If the model were calibrated, the house would keep about the 5% fee. It isn't calibrated against informed flow. In
the replay, a "back the open favourite" bettor loses 9–16%, but a bettor copying Tube Crowd makes +44% on hub_surge
and +32% on City Pulse. Out of sample, City Pulse still gives +48% (21/23). Against a reader of the usual-Saturday
curve, the house expects to lose. The fix is in section 4.

**Q1.4 Why LMSR and not an order book?** *(ok)*
A demo room of 20 strangers can't sustain a two-sided book every 5 minutes. LMSR always quotes, has a known loss bound
for a fixed prior, and lets the model set the opening price. The cost: with b = 1200 and a 250-point position cap, a
player moves a price by about 9 points at most, so there's no real price discovery. It's a bookmaker quoting a
published model. The peer-to-peer exchange is roadmap (business.md stage 2), and "no house risk" only applies there.

**Q1.5 Fees?** *(strong)*
5% of every stake comes off before shares are bought (`Book.quote`). Locked odds round down to 2 dp and never exceed
the shares bought, so no price ever pays more than the market maker's own payout (`pricing.locked_odds`). Bets under
1.05x are refused.

### 2.2 Pricing

**Q2.1 Where do the probabilities come from?** *(ok)*
Each market starts 50 futures from the last real reading. For crowding, that's a log random walk per station, with
sigma fitted to today's changes, drift set to half the last hour's trend (capped at σ/5), and steps capped at 2.5σ.
The VIX and Heathrow use a bootstrap of today's own h-minute changes. Clean sheet uses a per-line break hazard
(`pricing.py`). Since #55, when the central sim is up, 25 of the 50 futures in hub_surge, hub_hour and City Pulse are
draws from the agent simulation (`SIM_WEIGHT` 0.5; live `/markets` shows `fromSim: 25`). Don't call these "simulated
futures of the city": half are statistical walks, and the sim half comes from a model that loses to persistence.

**Q2.2 The particle filter: what's the likelihood, and what happens when everything's wrong?** *(weak)*
It's not a weighted filter. A future is ruled out when its error, in sigmas, exceeds a tolerance (1.5σ), with never
more than 60% ruled out in total (`Futures.observe`). There's no resampling, so once 30 of the 50 futures are gone,
pruning stops. In the replay, **218 of 273 markets hit that cap**. After that, the model price can't respond to data.
Only bets move it, and bets barely move it.

**Q2.3 Calibration evidence: numbers and sample sizes.** *(ok)*
On the replay of the real Exchange (12:40–20:10): hub_surge's open-price Brier is 0.766 against 0.800 for uniform
(n=87), City Pulse 0.461 against 0.500 (n=89), Heathrow 0.477 against 0.500 (n=76). None of the improvements is
significant: t = 1.3, 0.8 and 0.9. Out of sample (18:30–20:35), only City Pulse is significant: t = 2.2, n=24. One
dry Saturday. Say "directional".

**Q2.4 Is it overconfident?** *(ok)*
In sample, yes, mildly. Options priced over 70% won 71% (priced 79%, n=52), and options under 10% won 13% (priced 6%,
n=97). Out of sample, that pattern doesn't reproduce: 1 of 27 longshots won. So there's no stable bias on this sample.
The structural causes to admit: only 50 futures (2% granularity), the 3% clamp, and the capped pruning.

**Q2.5 How are strikes chosen?** *(weak for the headlines)*
- City Pulse puts its line at the value at open plus the median 5-min change, on a .5.
- Heathrow's line is the count at open + 0.5. The VIX line is its value at open.
- Brackets are placed on the futures, with no bracket under 5%.
- For **hub_hour**, we widen sigma ×1.3 until the favourite is at most 70%.
- For **clean sheet**, we clamp P(break) to 30–70%.

The last two choose the price, not the strike. We made the model less confident so the market looks uncertain. Say
it: "on hub_hour the 5-min fit priced 89% against a real 56%, so we widened it. That's a patch, not a model."

**Q2.6 Is the favourite-longshot bias present?** *(ok)*
In the model, there's no stable bias: in sample it leans the reverse way (longshots underpriced), and out of sample
it's gone. Among players, we can't tell: one human has bet, and players barely move the price.

### 2.3 Edge and information

**Q3.1 Courtsiding: can I beat your feed?** *(weak)*
- **Crowding:** we settle on our first 5-minutely poll after the close, and TfL's stamp on that row is a median 7 min
  older (1,979/1,979 station-rounds stamped before the close; `crowd_latency.py`). Our poll at hh:03 sees TfL's hh:56
  stamp, never hh:01, so TfL publishes 2–7 min after its stamp. We haven't polled fast enough to know whether the
  settling number is public before the close.
- **Clean sheet:** someone on a platform sees a line stop before TfL Line Status shows it, and the market stays open
  for about 59 minutes. Fully exposed.
- **Heathrow stacks:** a home ADS-B receiver beats adsb.lol by seconds. With a 60 s close, that's minor.

**Q3.2 Insider trading: TfL staff?** *(weak)*
The business.md rule is "only outcomes that emerge from many people" and it lists "unplanned disruption" as
allowed. That's wrong. A signal failure is one event, and TfL's controllers know it before the status page does, so
clean sheet is insider-exposed. Crowding isn't: no one person knows a station's live busyness ahead of TfL's own
measurement. TfL staff could still see it before publication, which is a data-agreement issue, not a design one.
Corrected rule: "list aggregates of many people's behaviour; never discrete operational events".

**Q3.3 Manipulation: can one person move a hub?** *(weak)*
Untested. Bank is the softest target: its Saturday-evening typical is 5–6% of baseline, quoted to 2 decimals, so its
vs-usual already jumps about 20% at band edges on rounding alone. A few hundred people tapping in at a near-empty
Bank could plausibly move it a few points, and hub_surge is often won by under 3 points. The limits: a 250-point
position cap, 3σ jump halts, and settlement on TfL's number, not ours. The fix is to drop low-typical stations, or to
use unrounded baselines (roadmap).

**Q3.4 Does an AI trader front-run players?** *(strong)*
No. Traders are deterministic rules on public feeds and decide once per market (`exchange.ai_bets`). The Contrarian
waits until halfway and fades where the points are, so it trades after players, against them, at the price they
left. It never sees an unfilled order, because there are none. It does move the price a player gets (100 points ≈
4 pts), which the card shows.

**Q3.5 Is there a guaranteed-profit strategy?** *(ok)*
There's no riskless one:
- buying every outcome loses the 5% fee;
- odds round down;
- a market closes the moment its outcome is decided (`lock_if_decided`);
- double settlement is blocked by exactly-once settlement and idempotent bets (tests, including an 8-thread hammer).

There is a positive-expectation one: copy Tube Crowd on City Pulse (+32% full day, +48% out of sample). And in a
points game, an uncapped one: `X-Forwarded-For` can be spoofed past the 5-players-per-hour limit, which farms
1000-point grants (a `debt:` note in `server.py`).

### 2.4 Accuracy

**Q4.1 The forecast ties persistence. Why trust the prices?** *(ok, if said first)*
Rerun tonight: on 59 minutes of Oxford Circus (96 fresh counts), MAE was 1.68 for the market and 1.67 for
persistence, with 83% inside the 80% range. It ties because every world is anchored to the last real count. Pruning
adds about 1% over unpruned equal weights (1.70). The prices are honest about uncertainty, not clever about
direction. The claim we can make: "the structure measures skill, and it tells us ours is near zero on one day."

**Q4.2 What does the simulation add? Evidence.** *(weak)*
- **Central London (#54):** station MAE 4.14 against persistence 3.61, on 919 readings, so the sim loses by 15%. It
  does beat the typical day (25.1).
- **Events:** switched on, they make it worse (4.14 vs 3.94 off).
- **Cameras:** sim 1.83, persistence 1.77, typical 1.72.
- **Sim blend in prices (#55):** n = 2 settled markets.

The sim feeds half the futures of the hub and Pulse markets live, so it may be making prices worse. Answer: "it
loses today, and the Simulation trader is the live A/B test". Better: set `SIM_WEIGHT=0` for the demo so that claim
is clean (section 4).

**Q4.3 The event layer: any skill?** *(weak, so say so)*
No. Line disruptions predicted the direction of busyness 10/18 raw and 8/18 against a no-event control, with a 50%
base rate. Crowding surges: 7/17 against control (`eval_events.py`). Fixtures, road closures and rain have no
station ground truth today. What would prove it: about 200 episodes per rule family (roughly a week of recording),
a significant hit rate against control at p < 0.05, and sim-with-events beating sim-without on held-out days.

**Q4.4 Which signals actually work?** *(ok)*
- The usual-Saturday curve: it calls station direction 66/72/79% at 15/30/60 min (n=482/697/741, `docs/questions.md`).
  As a trader on City Pulse, it won 21/23 out of sample.
- Fade the Heathrow stack: 13/19 out of sample.
- Furthest-below hub catches up: 35% in sample against 20% by chance, and 29% (7/24) out of sample.
- Momentum: worthless (17%).

### 2.5 Data and oracle

**Q5.1 Single source of truth? Revisions?** *(ok)*
Each market names one source and one rule, and freezes the settled reading (`settlement` snapshot). TfL Crowding
live values aren't revised. If no reading arrives in the window, the market is void and refunded: 1 void in 273
replay markets. There's no backup source, and real money would need a data agreement and a second source
(business.md).

**Q5.2 TfL outages?** *(ok)*
A stale feed halts the market: 3 min, or 6 min for the 5-minutely crowding feed. A missing settle reading voids it
after the grace period. Clean sheet voids if readings are missing for more than 5 min in total. The recorder runs
under a supervisor that restarts it after 3 min of stall. Its host is one laptop (see 2.8).

**Q5.3 Settlement windows?** *(weak, as worded in our docs)*
The rule text says "first TfL reading published after bets close". The code means our first poll after the close
(`crowd_values` keys off the recorder's poll timestamp, `recorder/record.py` `poll_crowding`). See Q3.1 for the fix.

**Q5.4 How accurate are the YOLO counts, and are they biased?** *(ok)*
No market settles on YOLO. We killed `cam_total`: moves were YOLO noise, reverting 10/14 (`questions.md` K4). The
counts only feed the Oxford Circus sim and the VIX. We have no ground truth: low-res 10 s JamCam clips, 13 cameras,
and an unknown bias with occlusion and night. Say "counts are an input, never an oracle".

**Q5.5 Privacy?** *(strong)*
Public TfL JamCam clips. The YOLO server writes each clip to a temp file and discards it. Only counts are stored,
there's no face or identity processing, and the counts are aggregated across cameras.

### 2.6 The TOPS analogy

**Q6.1 Is the TOPS analogy real?** *(weak)*
Half of it. TOPS collects broker ideas, scores them on outcomes, and weights trust by track record. We have the first
two: each trader reads one feed, states a reason, and settles publicly. The weighting loop isn't built. Traders'
picks don't feed the price, and their records live in memory: **a server restart resets every trader to 1000**
(`Exchange.record`). The live leaderboard reset between 20:19 and 20:35 tonight. Don't say "TOPS-style" without
saying which half.

**Q6.2 How are traders scored? Decay?** *(weak)*
Points at locked odds, 100 per bet. Since #55, a trader's stake is capped at 25% of its points, so a losing trader
gets benched: the Contrarian fell to 279 and placed only 27 bets in the full-day replay. That's crude decay by
bankroll, not a time-decayed track record. There's no Sharpe and no per-series weighting.

**Q6.3 Is "which data has alpha" actually measured? With what sample?** *(ok)*
Yes, on one day, against the house. The replay results are in section 0 (Tube Crowd, Plane Spotter). Line Watcher
went 2/6 and Rain Radar never traded on a dry day. The rules were found on 12:06–18:25 data, so only the 18:30–20:35
slice is out of sample (n ≈ 24 per series). That's a measurement design, not a result.

### 2.7 Scale and business

**Q7.1 Real money: which regulator?** *(ok)*
- **UK consumer betting:** the Gambling Commission. Betfair runs a licensed exchange, and a house-banked LMSR is
  bookmaking.
- **US:** the CFTC designated-contract-market route Kalshi took.
- **Business hedging:** possibly FCA-regulated derivatives, or an insurance partner (parametric cover).

Not legally verified (business.md says so). "Points to pounds is a licence, not a rewrite" is too strong. The ledger
is ready, but the no-house-risk model needs an order book we haven't built.

**Q7.2 Who pays?** *(weak)*
In order of believability:
1. data buyers (live probabilities and the uncertainty index as alt data);
2. sponsors for free-entry prizes;
3. hedgers.

We have no customer conversations.

**Q7.3 Why would anyone hedge footfall?** *(weak)*
The steelman: a West End retailer or an event operator has revenue that's highly sensitive to footfall, and weather
derivatives and parametric insurance show that index cover can sell. The honest part: 5-minute contracts are useless
for hedging. A hedger needs daily or weekly notional on a trusted index, not "Bank vs Waterloo by 20:40". The
5-minute game is the engagement and calibration engine; the hedge would be a different contract.

**Q7.4 Liquidity bootstrapping and cold start?** *(ok)*
That's exactly why we chose LMSR: the house always quotes, and the AI traders make the board look alive (and expose
mispricings) with zero users. The cost is house risk, capped by the exposure limit.

### 2.8 Engineering

**Q8.1 What runs where?** *(ok)*
Everything runs on one laptop: recorder, YOLO, both sims and the exchange. The front end is on Vercel, reaching the
laptop through a Cloudflare quick tunnel whose URL changes on restart (build.md gaps 1–2). If the laptop sleeps, the
site falls back to demo data.

**Q8.2 Persistence and failure modes?** *(ok)*
- **Player money:** an append-only double-entry SQLite ledger. After a restart, bets on recorded markets settle from
  recorded data, and unresolvable ones are refunded (`Exchange.recover`).
- **Lost on restart:** open markets (tonight's hub_hour-2100 vanished in a restart), the AI records, and the
  calibration stats.

**Q8.3 Test coverage?** *(strong)*
129 city, 127 multiverse and 16 recorder tests, all passing tonight. They include a randomised 1000-bet ledger
invariant, an 8-thread hammer, idempotency, exactly-once settlement, and halts and voids.

**Q8.4 What breaks under load?** *(ok)*
Every HTTP request and every 1 s tick share one global lock (`server.py` `LOCK`). `/markets` takes about 10 ms and
returns 78 KB, so the ceiling is about 100 req/s: fine for a judging room, not for launch. SQLite with one writer is
the next limit.

**Q8.5 Security review results?** *(ok)*
The red team found that anyone could claim any player id, and that a price near certainty could pay more than the
market maker's shares. Both are fixed: server-issued hashed tokens, and an odds floor that rounds down. Other
protections: the server binds 127.0.0.1, rejects bodies over 16 KB, and confirms stakes over 25%. Open: the
`X-Forwarded-For` spoof past the new-player rate limit (Sybil grants, a `debt:` note), and the quick-tunnel URL.

### 2.9 Ethics

**Q9.1 Betting on crowds? Surveillance?** *(ok)*
Points only. Counts only, no faces, aggregated. The settlement sources are public TfL feeds, not our cameras.

**Q9.2 Harm markets?** *(ok)*
There are none: no accidents, crime, injuries or protests (business.md). Clean sheet is the edge case: a "No" pays
when a line fails, which can mean a casualty on the line. Consider excluding "customer incident" reasons from what
counts as a break, or drop the market for real money.

**Q9.3 Responsible play?** *(ok)*
Built: a daily gross-loss limit (500 default; raising it takes 24 h), confirmation over 25% of the balance, and
1 h / 24 h breaks. Not built: reality checks and self-exclusion. A 5-minute cadence is a known harm vector in betting
(fast repeat play), so say that real money would need slower contracts or a cooling-off design.

## 3. Claims to stop making

| Where | Claim now | Why a quant shreds it | Say instead |
|---|---|---|---|
| idea.md ★, devpost tagline | "Every price is 50 simulated futures of the city" | Half are statistical random walks. The other half come from a sim that loses to persistence by 15% | "Every price comes from 50 futures: statistical walks fitted to today's data, half of them drawn from our agent simulation when it's live" |
| idea.md, devpost | "ruled out live by real TfL data" | 80% of markets hit the 60% cap, and then pruning stops | "each reading rules out the futures that disagree, up to 30 of the 50" |
| business.md | "betting stops 60 s before settlement, and crowding markets settle on the first reading published after bets close", so no fast-feed edge | TfL stamps every settling row before the close (1,979/1,979). We settle on our poll time, not TfL's | "crowding settles on our first poll after bets close; settling on TfL's own timestamp is next" |
| business.md, devpost "What we learned" | "List only outcomes that emerge from many people … Then there is no insider to police", with "unplanned disruption" allowed | Clean sheet is one discrete event that TfL staff see first | "aggregates of many people's behaviour only; clean sheet is the exception and gets a bet delay" |
| idea.md, devpost | "TOPS-style" (unqualified) | No weighting by track record, and records reset on restart | "each trader builds a public track record, the first half of TOPS; weighting their picks into the price is next" |
| idea.md, devpost | "The London VIX is the city's fear index" | One area (Oxford Circus), a 1 h burn-in from ~17 to ~2.3, moves dominated by engine noise. It listed 0 rounds live tonight | "an uncertainty gauge for Oxford Circus: the spread of 50 simulated worlds" |
| idea.md 100 words | "Nobody knows the outcome in advance. The edge is reading the data better." | The house doesn't read it: a public rule beats the book by 32–48% on City Pulse | Keep the line, and add: "today our own Tube Crowd trader proves the house can be read better. That's the leaderboard's job" |
| devpost | "Strikes are placed so the favourite opens well under 85%" | For hub_hour we inflate vol; for clean sheet we clamp the probability | "lines and brackets are placed so the favourite opens under 85%; on the hourly markets we widen the model until it does" |
| devpost | "an odds floor (a payout never exceeds the price)" | Garbled | "a payout never exceeds the market maker's shares, and bets under 1.05x are refused" |
| devpost | "118 backend tests" | Out of date | "129 city, 127 simulation and 16 recorder tests" |
| idea.md Q&A, devpost | "MAE 2.4 vs 2.4 over 30 minutes, 77% inside its 80% range" | Stale | "1.68 vs 1.67 over 59 minutes (96 counts), 83% inside the 80% range". Add: "central London loses to persistence by 15%" |
| business.md pitch line, devpost | "going from points to pounds is a licence, not a rewrite" | A house-banked LMSR is bookmaking; "no house risk" needs an order book that doesn't exist | "the ledger is real-money grade; the peer-to-peer exchange and licensing are the work left" |
| business.md | "City-scale aggregates one person can't move" | Untested; Bank sits at 5–6% of baseline with 2-dp typicals | "five busy hubs against their own baselines; low-traffic stations are the soft spot we'd exclude" |
| idea.md, devpost "Mapped to criteria" | "Kalshi-style integrity rules" (as a whole) | Kalshi settles on the official source's timestamp and runs surveillance | "Kalshi-style rules: source and rule on every market, close before settle, void and refund, frozen reading" |
| devpost | "London VIX: higher or lower" listed as an every-round market | heathrow_stack stood in for every round we observed | "London VIX when the engine is warm; Heathrow stacks stand in otherwise" |

## 4. Fix tonight (ranked by value to the pitch per hour)

`services/` and `apps/` are being edited by other agents, so the code items below are recommendations for their
owners. Re-score every pricing change with `python3 analysis/judge/replay_score.py <raw> 2026-11-07T12:40:00Z
2026-11-07T20:10:00Z 30`.

| # | Fix | Why | Effort | Tonight? |
|---|---|---|---|---|
| 1 | Apply section 3 to devpost.md, idea.md and business.md | Every item there is a live grenade in Q&A; docs only | 30–40 min | yes |
| 2 | Set `SIM_WEIGHT=0` for the demo and keep the Simulation trader betting | Removes "half your futures come from a model that loses to persistence", and turns Q4.2 into a clean live A/B: "the sim trades against data-only prices, and the leaderboard scores it" | 5 min (env var plus restart) | yes |
| 3 | Add the usual-Saturday slope to the City Pulse and hub drift (`fit_log_walk` drift + typical(t_end)/typical(now)) | Closes the biggest leak (+48% out of sample on City Pulse). Done when Tube Crowd's City Pulse ROI ≤ about +5% in the replay and Brier doesn't get worse | 45–60 min | yes, if the pricing owner is free; otherwise rehearse Q1.3 |
| 4 | Persist AI trader records (write `Exchange.record` to the ledger DB or a JSON file on each settle; load on start) | "Track record" is the TOPS claim, and today a restart wipes it | 30–45 min | yes |
| 5 | Settle crowding on TfL's `timeUtc` > close, not our poll time; poll crowding every 60 s | Turns Q3.1 and Q5.3 from weak to strong | 45 min (recorder + `crowd_values` + tests) | maybe. At minimum fix the rule text (5 min) |
| 6 | Clean sheet: close betting 15 min after open, or add a 30 s bet delay | Courtsiding and insider exposure (a `patch_courtsider.py` analysis exists in the shared scratchpad) | 30 min | maybe |
| 7 | Get london_vix listing for the demo (engine warm for > 60 min on `MULTIVERSE_URL`), or stop leading with the VIX | It's in the tagline and has listed 0 rounds live | 10 min ops | yes |
| 8 | Raise the pruning cap or resample (weighted filter) so that late readings still move the model | 80% of markets stop learning | 1–2 h | roadmap |
| 9 | Weight trader picks into the price by track record (the real TOPS loop) | The strongest MW answer if it exists | 2–3 h | roadmap; pitch it as next |
| 10 | Drop low-typical stations, or use unrounded baselines; fix the `X-Forwarded-For` spoof | Manipulation and Sybil | 1 h each | roadmap |

## 5. 3-minute demo script (pre-empts questions 1, 2 and 3)

**0:00 Hook.** "This is a live stock exchange on London's next five minutes. Every five minutes we list markets on
the city, priced by 50 futures, settled by TfL. You're trading against an automated market maker and seven AI
traders, each reading one live feed."

**0:20 Pick hub_surge.** The hubs light up, and the strip shows each hub against its usual Saturday. A judge bets.
"Who's on the other side? *(pre-empts Q1)* The house: an LMSR market maker over the model's probabilities. Its
worst-case loss across every open bet is capped on a double-entry ledger, and you're capped at a quarter of your
points per market."

**0:50 The traders.** Show the reasons on the card and the leaderboard. "Look who's winning: Tube Crowd. It reads
one thing, TfL's usual-Saturday curve, and it beats our own market maker. *(pre-empts Q3)* That's the point. Marshall
Wace built TOPS to find out which broker to trust. Our leaderboard tells us which data the market maker should
listen to next. Today that's the usual curve: +48% on City Pulse in a replay it hadn't seen."

**1:30 Settle.** A round settles. Show the frozen reading and the wallet update. "Source and rule on every market,
betting closes before the reading, no reading means void and refund."

**2:00 Technical, with the honest number.** "Feeds from a 16-source recorder, 50 futures per market, pruned by each
reading, LMSR, then settlement. Is it accurate? *(pre-empts Q2)* On real Oxford Circus cameras, our simulation ties
'nothing changes': 1.68 against 1.67. Our agent simulation of central London loses to it today. So we don't hide the
model in the price. It trades as the Simulation trader, and the market scores it against the data-only traders,
live."

**2:40 Close.** "Marshall Wace built TOPS to find out which brokers to trust. We built it to find out which data
reads a city best, and it has already told us something our own model missed."

Rehearsal notes:
- The 0:50 line only works if Tube Crowd is visibly ahead on the leaderboard. Check `/leaderboard` before going on.
  If fix 4 isn't in, avoid restarting the server after about 08:00.
- The "+48% out of sample" line is n=23. If asked, say "n of 23, one evening, directional".
- If fix 2 isn't in, change the 2:00 line to: "half the futures come from the simulation, and it's losing to
  persistence today; the Simulation trader is how we find out live."

## Appendix: reproduce every number

```sh
RAW=/path/to/data/raw
python3 analysis/judge/replay_score.py $RAW 2026-11-07T12:40:00Z 2026-11-07T20:10:00Z 30   # full-day replay
python3 analysis/judge/replay_score.py $RAW 2026-11-07T18:30:00Z 2026-11-07T20:35:00Z 30   # out of sample
python3 analysis/judge/crowd_latency.py $RAW                                               # settlement timing
python3 analysis/judge/profile_precision.py $RAW                                           # typical-day rounding
(cd services/multiverse && python3 eval_real.py --raw $RAW --windows 12)                   # Oxford Circus vs persistence
(cd services/multiverse && python3 eval_real.py --raw $RAW --central --windows 7 --minutes 60 --events-ab)
python3 services/multiverse/eval_events.py $RAW 2026-11-07                                 # event layer skill
python3 -m unittest discover -s services/city    # 129
python3 -m unittest discover -s services/multiverse    # 127
python3 -m unittest discover -s recorder    # 16
```

The replay runs the real `Exchange` in-process with an in-memory ledger: no network and no writes to the recorder
data. AI trader P&L there uses 100-point stakes at the real quotes, so it shows how a copycat player would have done
against the house.
