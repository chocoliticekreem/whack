---
title: Judge prep — a sceptical Marshall Wace panel vs OverWatch
date: 2026-11-07
status: prep for Sunday judging · numbers re-run against main at e59773b (#81) and the live API, Sat 23:30–23:50
---

# Judge prep: what Marshall Wace will ask, and the true answers

The panel: two quant traders, a market-structure specialist and a quant researcher. They get 3 minutes of demo plus
Q&A, and they will press on whatever sounds like a claim. This doc gives the hardest questions they will ask and the
best answer that is also true. The first version (#57, Sat 20:40) listed weak spots; most were fixed overnight
(#61, #72, #77, #81) and are in "Fixed since" (section 4).

**Ground rule for the Q&A:** say "we measured X on n=Y, here's the caveat" before they ask. MW people respect a
team that knows where its edge isn't. They'll shred a team that oversells.

Confidence ratings: **strong** = we can defend it with code and a number. **ok** = the answer is true but the
evidence is thin. **weak** = the honest answer hurts. Say it first.

## 0. The numbers (re-run tonight; commands in the appendix)

All replay numbers are one Saturday (7 Nov 2026) of recorded data, run through the real `Exchange` on main.
hub_surge's reversion rate and softening were fitted on 12:00–17:00, so **17:00–21:30 is the out-of-sample window**.

| What | Number | Source |
|---|---|---|
| LMSR liquidity | b = 1200, fee 5% of stake, odds 1.05x–20x, model prob clamped to [3%, 97%] | `services/city/pricing.py` |
| Price impact | 100 points on a 50% option moves it about +3.8 pts; the max stake (250 = 25% of the 1000 grant) about +9 pts | LMSR formula, `Book.quote` |
| House limits | bankroll 100,000; worst-case open-bet loss capped at min(100,000, bankroll + P&L), and since #81 AI open stakes count toward it; position cap 25% of equity per market (10% on clean sheet and local markets) | `ledger.py`, `integrity.py` |
| Full-day replay, 12:40–20:10 | 91 rounds, 273 markets, **0 voids** | `analysis/judge/replay_score.py` |
| Open price vs uniform (Brier gain), full day | hub_surge +0.025 ± 0.018 (t = 1.4, n=86), city_pulse −0.002 ± 0.018 (t = −0.1, n=87), heathrow_stack +0.023 ± 0.027 (t = 0.9, n=76). **None significant** | same |
| Out of sample, 17:00–21:30 (55 rounds) | hub_surge Brier 0.755 vs uniform 0.800, gain +0.045 ± 0.027 (t = 1.7, n=51); city_pulse +0.029 ± 0.029 (t = 1.0, n=51); heathrow_stack −0.003 (t = −0.1, n=53) | same |
| Copying Tube Crowd vs the house, out of sample 17:00–21:30 | City Pulse 32/51, **+12.6%** per point staked; hub_surge 19/51, −17.8%. On 18:30–20:35 alone (n=22): +18% and +20%. Before #72/#77 the same rule made +32% (City Pulse) and +44% (hub_surge) full day | same (trader stakes min(100, 10% of points)) |
| #77's own copycat test | 10 points on the furthest-below hub at the open quote, 54 races after 17:00: **+13%, 95% CI −27% to +55%** (was +32%). Not proven gone | `services/city/README.md`, #77 |
| Plane Spotter (fade the Heathrow stack) | out of sample 22/34, **+66%**; full day 29/52, +39%. Still a leak | `replay_score.py` |
| Backing the open favourite | loses 9–19% per series, full day (fee plus spread) | same |
| City Pulse line | the half point (x.0 or x.5) nearest the futures' median at settle. Favourite opens in 30–70% in 82% of all replay markets; live since the last restart, City Pulse 9/9 | `markets.py`, `GET /stats` |
| Pruning cap | 214 of 273 markets hit the 60% ruled-out cap. Since #72 weights keep moving the price after it: of 102 capped markets with later readings, 42 stop learning (96 of 96 before) | `replay_score.py` |
| Favourite-longshot, full day | options priced under 10% won 10.0% (priced 7.7%, n=60); over 70% won 64% (priced 74%, n=11). No stable bias | same |
| Settlement timing | station markets settle on the first reading **TfL stamped after betting closed** (#72), so a round takes about 14 min to settle; cards say "result about HH:MM, when TfL publishes" (#74). A value measured at or before close voids (#81). On the old poll-time rule, 2,779 of 2,779 station-rounds would have settled on a number stamped before the close | `markets.py`, `analysis/judge/crowd_latency.py` |
| Courtsiding | bet delay per series (clean sheet and local markets 45 s, hubs and pulse 5 s, VIX and Heathrow 2 s), suspend on a feed change, caps. A clean-sheet courtsider with up to 44 s head start: edge 0 (was +305) | `integrity.py`, `CourtsidingTest` (#61) |
| Oxford Circus sim vs persistence | 30 min to 23:38 UTC, 48 camera counts: MAE market 1.37, persistence 1.35, equal-weight worlds 1.37; 79% inside the 80% range. Bus delay: market 0.99 vs persistence 0.65 (persistence wins) | `services/multiverse/eval_real.py --windows 6` |
| Central-London sim vs persistence | station MAE 3.94 vs persistence 3.61 (sim loses by 9%); events on vs off 3.94 vs 3.94 after #60's fix (was 4.14 vs 3.94) | #60 (not re-run tonight) |
| Event layer | line_disruption vs control 4/15 (27%, base 47%), crowding_surge 8/26 (31%, base 53%), line_displacement 3/4. **No skill** | `services/multiverse/eval_events.py` (re-run, 12:06–23:42) |
| Live trading test (#79, code before #72/#77/#81) | 75 min, 327 bot bets: 0 voids, 0 fills after close, 120/120 wallet reconciliations exact, 46/46 bets on halted markets refused | `analysis/live_trading/README.md` |
| Tests | city 205, multiverse 132, recorder 16, all passing | `python3 -m unittest discover -s services/<x>` |
| Live, 23:45 | london_vix listed in 9 of 9 rounds since the last restart (heathrow_stack skipped: "the London VIX is live"); 0 voids; `fromSim: 0` on every market (`SIM_WEIGHT=0`). AI records survived the restart: Tube Crowd 24–18, 2,450 points, top of the board; Simulation 8–30, 78 points; The Contrarian −181 points | `GET /round`, `/stats`, `/markets`, `/traders` |

## 1. The 10 most likely, most dangerous questions

Memorise the one-liners. The full answers are in section 2.

| # | Question | One-line answer | Conf. |
|---|---|---|---|
| 1 | "Who's on the other side, and what's your worst case?" | "An automated market maker, LMSR with b = 1200 over the model's probabilities. The ledger caps the house's worst-case open loss at 100,000 points, AI stakes included, and each player at 25% of equity per market." | ok |
| 2 | "Your forecast ties 'nothing changes'. Why should anyone trust these prices?" | "As a forecast, not yet. Out of sample the hub race beats a uniform guess (Brier 0.755 vs 0.800, t = 1.7), the rest don't. What we built is a market structure that measures that honestly." | ok |
| 3 | "Your own Tube Crowd trader beat your market maker. Is the house still a piggy bank?" | "It was: +32% to +48% a bet. We priced the usual-Saturday curve into the futures. Out of sample, copying it now makes +13% on City Pulse and loses 18% on hubs, with a confidence interval that still includes a leak. Not proven gone, and Plane Spotter's fade still beats us." | ok |
| 4 | "Someone on a train sees a line stop before TfL does. Isn't clean sheet an insider market?" | "It's the one single-event market, so it gets the strictest rules: bets fill 45 s after you place them, any line change pauses the market and cancels pending bets, a 10% cap, and betting closes with its first 5-minute round. A head start under 44 s gains nothing." | ok |
| 5 | "You settle on the 'first reading after bets close'. Is that number already public?" | "No longer. We settle on the first reading TfL itself stamped after the close, and a value measured at or before the close voids. The cost is a 14-minute wait, which the card shows: 'result about 22:00'." | strong |
| 6 | "What does the agent simulation actually add? Evidence, not adjectives." | "Nothing measurable yet. Oxford Circus ties persistence, central London loses by 9%, the event layer has no skill. So it's out of the price (`SIM_WEIGHT=0`) and trades as the Simulation trader, which is losing live. Its real use today is scenario mode." | weak |
| 7 | "How is this TOPS? Where's the track-record weighting?" | "Half of it. Every trader has a public, settled record that now survives restarts, and a losing trader's stake shrinks to 10% of its points. Weighting their picks into the price by that record isn't built." | weak |
| 8 | "Why LMSR rather than an order book? Is this an exchange or a bookmaker?" | "Today it's a bookmaker with a published model: a thin market of strangers needs guaranteed liquidity. The target is a Kalshi-style order book with AI market makers and no house; the design is written (exchange-design.md), not built." | ok |
| 9 | "What is the London VIX?" | "The spread of predicted crowds across 50 simulated worlds of Oxford Circus. It covers one area and needs an hour to burn in, so call it an uncertainty gauge, not a fear index. Camera Eye fades its moves, and that's 12–12 live." | ok |
| 10 | "Real money: which regulator, and who on earth hedges footfall?" | "Consumers: the UK Gambling Commission, or the CFTC route Kalshi took. The first paying customer we'd test is a data buyer, not a hedger. Footfall hedging is a hypothesis with zero customer conversations." | ok |

## 2. The full Q&A bank

### 2.1 Is it a real market or a game?

**Q1.1 Who's on the other side of my bet?** *(ok)*
The house, through an automated market maker: prob_i ∝ model_i · e^(q_i/b), with b = 1200 (`pricing.py` `Book`). The
seven AI traders bet through the same book. Their records now persist in the ledger DB (`ai_records`, #72) and their
open stakes count toward the house exposure cap (#81), but they still aren't double-entry accounts. Say it plainly:
it's a house-banked market today.

**Q1.2 What's the house's worst case? Give me b·ln(n).** *(ok)*
With a fixed prior, LMSR loses at most b·ln(n): 832 points on a binary market and 1,931 on a 5-way one. With the prior
clamped at a 3% floor, b·ln(1/0.03) ≈ 4,200 per market. The textbook bound doesn't strictly hold, because the prior
moves as data arrives. So the hard limit is the ledger: the worst-case loss over all open bets, AI stakes included,
must stay under min(100,000, bankroll + P&L), or the bet is refused (`ledger.py` `_worst_case`). A randomised 1000-bet
invariant test checks the books balance.

**Q1.3 What's the market maker's P&L expectation?** *(ok)*
If the model were calibrated, the house keeps about the 5% fee. Backing the open favourite loses 9–19% per series. The
known informed flows, out of sample (17:00–21:30): copying Tube Crowd makes +12.6% on City Pulse (32/51) and −17.8%
on hubs (19/51); fading the Heathrow stack makes +66% (22/34). So the house still loses to one public rule on
Heathrow, and possibly on City Pulse. n is small; say "directional".

**Q1.4 Why LMSR and not an order book?** *(ok)*
A demo room of 20 strangers can't sustain a two-sided book every 5 minutes. LMSR always quotes, has a known loss bound
for a fixed prior, and lets the model set the opening price. The cost: a player moves a price about 9 points at most,
so there's little price discovery. The target is in `docs/exchange-design.md` (#67): binary contracts on an order book,
Kalshi's taker fee, designated AI market makers quoting both sides, hedge-fund and retail agents, no house. Not built.

**Q1.5 Fees?** *(strong)*
5% of every stake comes off before shares are bought (`Book.quote`). Locked odds round down and never exceed the shares
bought, so no payout exceeds the market maker's shares. Bets under 1.05x are refused, and since #81 a pending bet that
would fill below that floor is refused when you place it, not later.

### 2.2 Pricing

**Q2.1 Where do the probabilities come from?** *(ok)*
Each market starts 50 futures from the last real reading, at TfL's own stamp:
- **City Pulse:** a log random walk with sigma and drift fitted on today's changes, plus the usual-Saturday slope over
  the horizon (#72). The line is the half point nearest the futures' median, so it opens near 50/50 (#77).
- **hub_surge:** `RelativeRaceSim` (#77). Each hub follows its usual-day slope, reverts toward the five-hub average
  (κ = 0.0161/min, fitted 12:00–17:00), takes correlated shocks, and the shares are softened p ∝ share^0.5 (a Platt
  fit before 17:00).
- **VIX and Heathrow:** a bootstrap of today's own h-minute changes. **Clean sheet:** a per-line break hazard.
- **Local markets (#71):** the same machinery on the one sensor clicked.

The central sim can supply a share of the futures (`SIM_WEIGHT`), but it's 0 live because the sim loses to
persistence (`fromSim: 0` on every market). Don't call these "simulated futures of the city".

**Q2.2 The particle filter: what's the likelihood, and what happens when everything's wrong?** *(ok)*
Each reading weights every surviving future by exp(−(e/tol)²/2), tempered so the effective sample size stays at half
the survivors; futures off by more than tol are ruled out, never more than 60% in total. Since #72, the weights keep
moving the price after that cap. In the full-day replay 214 of 273 markets hit the cap; of the 102 that got later
readings, 42 still stopped learning (96 of 96 before). Better, not solved: resampling is the next step.

**Q2.3 Calibration evidence: numbers and sample sizes.** *(ok)*
Full day (12:40–20:10): open-price Brier vs uniform: hub_surge 0.775 vs 0.800 (n=86), City Pulse 0.502 vs 0.500
(n=87), Heathrow 0.477 vs 0.500 (n=76); none significant. Out of sample (17:00–21:30): hub_surge 0.755 vs 0.800,
t = 1.7 (n=51), the best we have. One dry Saturday. Say "directional".

**Q2.4 Is it overconfident?** *(ok)*
Full day: options priced under 10% won 10.0% (priced 7.7%, n=60), over 70% won 64% (priced 74%, n=11). Out of sample
the extremes are thin (n=33 and n=11). No stable bias on this sample. Structural limits: 50 futures (2% granularity)
and the 3% clamp.

**Q2.5 How are strikes chosen?** *(ok)*
- City Pulse: the half point nearest the futures' median at settle (#77).
- Heathrow: the count at open + 0.5. The VIX: its value at open.
- Brackets are placed on the futures, none under 5%, and a bracket market whose favourite is over 85% isn't listed.
- **hub_hour:** sigma widened until the favourite opens at 70% or less. **Clean sheet:** P(break) held to 30–70% at
  open.

The last two choose the price, not the strike. Say it: "on hub_hour the 5-min fit priced 89% against a real 56%, so
we widened it. That's a patch, not a model."

**Q2.6 Is the favourite-longshot bias present?** *(ok)*
In the model, no stable bias (above). Among players we can't tell: there are a handful of humans and they barely move
the price.

### 2.3 Edge and information

**Q3.1 Courtsiding: can I beat your feed?** *(strong for crowding, ok for clean sheet)*
- **Crowding:** settles on the first reading TfL stamped after the close (#72), and a value measured at or before the
  close voids (#81). There's nothing to see early. The hub and pulse markets also pause if a hub moves more than 10%
  (or the pulse 3%) in one publish.
- **Clean sheet:** 45 s bet delay, a pause and cancel on any line change, 10% cap, and it closes with its first
  5-minute round (#61, #72). Measured: a head start up to 44 s gains nothing (was +305); longer leads are bounded by
  the cap and the short window.
- **Heathrow stacks:** a home ADS-B receiver beats adsb.lol by seconds; a 2 s delay and a 60 s close make it minor.
- **Local markets:** the strictest setting (45 s delay, 10% cap) and a pause on a big move of their own sensor.

**Q3.2 Insider trading: TfL staff?** *(ok)*
Rule: list aggregates of many people's behaviour, never discrete operational events. Clean sheet is the exception and
gets the defences above. No one person knows a station's live busyness ahead of TfL's measurement; TfL staff seeing it
before publication is a data-agreement issue for real money.

**Q3.3 Manipulation: can one person move a hub?** *(weak)*
Untested. Bank is the softest target: its Saturday-evening typical is 5–6% of baseline, quoted to 2 decimals, so its
vs-usual jumps about 20% at band edges on rounding alone. The limits: the position cap, the 10% suspend, and
settlement on TfL's number. The fix is to drop low-typical stations or use unrounded baselines (roadmap).

**Q3.4 Does an AI trader front-run players?** *(strong)*
No. Traders are deterministic rules on public feeds, decide once per market, and wait out the same bet delay as
players. The Contrarian waits until halfway and fades where the points are, at the price players left. There are no
unfilled orders to see. It does move the price a player gets (100 points ≈ 4 pts), which the card shows.

**Q3.5 Is there a guaranteed-profit strategy?** *(ok)*
No riskless one: buying every outcome loses the fee, odds round down, a market closes when its outcome is decided, and
settlement is exactly-once with idempotent bets (an 8-thread hammer test). The confirm-bypass and pending-order holes
the live test found are fixed (#81). Positive-expectation ones, out of sample: fade the Heathrow stack (+66%, n=34)
and possibly copy Tube Crowd on City Pulse (+13%, n=51). In a points game, `X-Forwarded-For` can still be spoofed past
the new-player limit (30 a minute, 500 a day per network) to farm grants (a `debt:` note in `server.py`).

### 2.4 Accuracy

**Q4.1 The forecast ties persistence. Why trust the prices?** *(ok, if said first)*
Re-run tonight: 30 minutes of Oxford Circus to 23:38 UTC (48 counts), MAE 1.37 for the market and 1.35 for
persistence, with 79% inside the 80% range. It ties because every world is anchored to the last real count. On bus
delay persistence wins (0.65 vs 0.99). The prices are honest about uncertainty, not clever about direction.

**Q4.2 What does the simulation add? Evidence.** *(weak)*
- **Central London (#54):** station MAE 3.94 against persistence 3.61, so the sim loses by 9%. It beats the typical
  day.
- **Events:** after #60's fix (no more closing busy stations), on vs off is 3.94 vs 3.94: no gain, no harm.
- **Live:** the Simulation trader is 8–30 and down to 78 points.

So it's out of the price (`SIM_WEIGHT=0`). Its job today is scenario mode: "what if Regent Street closes?" runs on
copies of the central sim and shows the odds it would imply (#66). That's a what-if, not a forecast claim.

**Q4.3 The event layer: any skill?** *(weak, so say so)*
No. Re-run on 12:06–23:42: line disruptions called busyness direction 4/15 against a no-event control (27%, base 47%);
crowding surges 8/26 (31%, base 53%). Fixtures, road closures and rain have no station ground truth. What would prove
it: about 200 episodes per rule family (a week of recording), a significant hit rate against control, and
sim-with-events beating sim-without on held-out days.

**Q4.4 Which signals actually work?** *(ok)*
- The usual-Saturday curve calls station direction 66/72/79% at 15/30/60 min (n=482/697/741, `docs/questions.md`).
  It's now in the price, so as a trader it makes +13% on City Pulse out of sample, down from +48%.
- Fade the Heathrow stack: 22/34 out of sample, +66%. Not yet in the price.
- Momentum: worthless.

### 2.5 Data and oracle

**Q5.1 Single source of truth? Revisions?** *(ok)*
Each market names one source and one rule, and freezes the settled reading. Since #81 the snapshot holds `{value,
source, measuredAt, fetchedAt}`, so TfL's own time is on the record. No reading in the window means void and refund:
0 voids in 273 replay markets and 0 in the live test. There's no backup source; real money would need a data
agreement and a second source.

**Q5.2 TfL outages?** *(ok)*
A stale feed halts the market (3 min, or 6 min for the 5-minutely crowding feed). A missing settle reading voids it
after the 20-minute window. The recorder runs under a supervisor that restarts it after 3 min of stall. Its host is
one laptop (see 2.8).

**Q5.3 Why does a station market take 14 minutes to settle?** *(strong)*
Because TfL publishes its crowding numbers late: our poll sees a row a median 7 min after TfL's stamp. Settling on the
first row TfL stamped after the close is fair but slow. The owner chose fair, and the card says so: "Betting closed ·
result about 21:45, when TfL publishes" (#74). Fast series (VIX, Heathrow) settle within a minute.

**Q5.4 How accurate are the YOLO counts, and are they biased?** *(ok)*
No 5-minute city market settles on YOLO. Counts feed the Oxford Circus sim, the VIX, and local camera markets, which
settle on the first TfL image published after close. We have no ground truth: low-res 10 s JamCam clips and an unknown
bias with occlusion and night. Say "counts are an input; settlement on a camera is the soft spot".

**Q5.5 Privacy?** *(strong)*
Public TfL JamCam clips. The YOLO server writes each clip to a temp file and discards it. Only counts are stored, with
no face or identity processing.

### 2.6 The TOPS analogy

**Q6.1 Is the TOPS analogy real?** *(weak)*
Half of it. TOPS collects broker ideas, scores them on outcomes and weights trust by track record. We have the first
two: each trader reads one feed, states a reason and settles publicly, and the record now survives restarts (#72,
checked live tonight). The weighting loop isn't built: traders' picks don't feed the price. Don't say "TOPS-style"
without saying which half.

**Q6.2 How are traders scored? Decay?** *(weak)*
Points at locked odds. Each stake is min(100, 10% of points), and a trader sits out below 10 points. That's crude
decay by bankroll: live, the Simulation trader is down to 78 points and the Contrarian shows −181 (its record predates
the stake rule, or it's a bug; we haven't traced it). No Sharpe, no per-series weighting.

**Q6.3 Is "which data has alpha" actually measured? With what sample?** *(ok)*
On one day, against the house, out of sample after 17:00 (n ≈ 51 per series). Plane Spotter and Tube Crowd are the
only traders with an edge there. Line Watcher barely trades and Rain Radar never traded on a dry day. That's a
measurement design, not a result.

### 2.7 Scale and business

**Q7.1 Real money: which regulator?** *(ok)*
- **UK consumer betting:** the Gambling Commission. Betfair runs a licensed exchange; a house-banked LMSR is
  bookmaking.
- **US:** the CFTC designated-contract-market route Kalshi took.
- **Business hedging:** possibly FCA-regulated derivatives, or an insurance partner (parametric cover).

Not legally verified. The ledger is real-money grade; the order book, payments, ID checks and licensing are the work
left.

**Q7.2 Who pays?** *(weak)*
In order of believability: data buyers (live probabilities and the uncertainty index), sponsors for free-entry
prizes, then hedgers. No customer conversations.

**Q7.3 Why would anyone hedge footfall?** *(weak)*
A West End retailer or event operator has revenue that's sensitive to footfall, and weather derivatives and parametric
insurance show that index cover can sell. But 5-minute contracts are useless for hedging. A hedger needs daily or
weekly notional on a trusted index. The 5-minute game is the engagement and calibration engine.

**Q7.4 Liquidity bootstrapping and cold start?** *(ok)*
That's why we chose LMSR: the house always quotes, and the AI traders make the board look alive (and expose
mispricings) with zero users. The cost is house risk, capped by the exposure limit.

### 2.8 Engineering

**Q8.1 What runs where?** *(ok)*
Everything runs on one laptop: recorder, YOLO, both sims and the exchange. The front end is on Vercel, reaching the
laptop through a Cloudflare quick tunnel whose URL changes on restart. If the laptop sleeps, the site falls back to
demo data.

**Q8.2 Persistence and failure modes?** *(strong)*
- **Player money:** an append-only double-entry SQLite ledger. After a restart, bets on recorded markets settle from
  data and unresolvable ones are refunded.
- **Since #72 and #81:** AI records persist; open hourly markets are rebuilt from their records; the position cap
  reads stakes from the ledger; the VIX is fetched at startup so it lists in round 1. Checked live: hourly market
  re-listed, caps held, VIX listed in round 1, AI records kept.
- **Still lost:** pending (unfilled) bets, which are cancelled with no points moved. Current 5-minute markets rebuild
  from fresh data, so City Pulse may get a new line under the same id.

**Q8.3 Test coverage?** *(strong)*
205 city, 132 multiverse and 16 recorder tests, all passing tonight: a randomised 1000-bet ledger invariant, an
8-thread hammer, idempotency, exactly-once settlement, halts and voids, courtsiding, and 17 regression tests from the
live trading run (`test_live_fixes.py`). The edge-case suite (`edge.py`) passed 28/28 against a scratch server.

**Q8.4 How did you test it under real trading?** *(ok)*
Bots (random, favourite, longshot, signal, copycat, Kelly, a last-second reader) traded 75 minutes of live rounds on a
scratch ledger (#79): 0 voids, 0 fills after close, 120/120 wallet reconciliations exact. It found restart,
pending-bet, confirm-bypass and settlement-record bugs, all fixed in #81. The run predates #72 and #77, so its pricing
findings (hub_surge reader +143%, City Pulse opening at 97%) need a re-run on main.

**Q8.5 What breaks under load?** *(ok)*
Every HTTP request and the 1 s tick share one global lock (`server.py` `LOCK`). Fine for a judging room, not for
launch. SQLite with one writer is the next limit.

**Q8.6 Security review results?** *(ok)*
The red team found that anyone could claim any player id, and that a price near certainty could pay more than the
market maker's shares. Both fixed: server-issued hashed tokens and an odds floor. The server binds 127.0.0.1, rejects
bodies over 16 KB and confirms stakes over 25%. Open: the `X-Forwarded-For` spoof and the quick-tunnel URL.

### 2.9 Ethics

**Q9.1 Betting on crowds? Surveillance?** *(ok)*
Points only. Counts only, no faces. The city markets settle on public TfL feeds.

**Q9.2 Harm markets?** *(ok)*
None: no accidents, crime, injuries or protests. Clean sheet is the edge case: "No" pays when a line fails, which can
mean a casualty. Consider excluding "customer incident" reasons, or drop it for real money.

**Q9.3 Responsible play?** *(ok)*
Built: a daily gross-loss limit (500 default; raising it takes 24 h; gross by design, so a winning streak can't fund
unlimited risk), confirmation over 25% of the balance counting pending stakes, and 1 h / 24 h breaks. Not built:
reality checks and self-exclusion. A 5-minute cadence is a known harm vector; real money needs slower contracts or a
cooling-off design.

## 3. Claims to stop making

| Claim | Why a quant shreds it | Say instead |
|---|---|---|
| "Every price is 50 simulated futures of the city" | They're statistical walks; the sim's share is 0 live | "Every price comes from 50 futures fitted to today's data and TfL's usual day" |
| "The sim makes the prices better" | It loses to persistence by 9% and is out of the price | "The sim powers what-ifs and trades as one trader; the market scores it" |
| "The Tube Crowd leak is fixed" | +13% with a CI of −27% to +55% | "Down from +32–48% to about +13%, not proven gone" |
| "No public rule beats the house" | Fading the Heathrow stack made +66% out of sample | "Two public rules still beat it on one day; that's what the leaderboard finds" |
| "TOPS-style" (unqualified) | No weighting by track record | "Each trader builds a public track record, the first half of TOPS" |
| "The London VIX is the city's fear index" | One area, 1 h burn-in | "An uncertainty gauge for Oxford Circus: the spread of 50 simulated worlds" |
| "Points to pounds is a licence, not a rewrite" | A house-banked LMSR is bookmaking | "The ledger is real-money grade; the order book and licensing are the work left" |
| "City-scale aggregates one person can't move" | Untested; Bank sits at 5–6% of baseline | "Five busy hubs against their own baselines; low-traffic stations are the soft spot" |
| "Kalshi-style integrity" (as a whole) | Kalshi runs surveillance | "Kalshi-style rules: source and rule on every market, close before settle, TfL's own timestamp, void and refund, bet delays" |
| "Pruning tracks the data all round" | 42 of 102 capped markets still stop learning | "Each reading reweights the futures; a few markets still stall late in the round" |

## 4. Fixed since the first draft, and what's still open

**Fixed since (Sat 20:40 → 23:00):**

| Weak spot (#57) | Fix | Evidence |
|---|---|---|
| Copying Tube Crowd beat the house by +32–48% | Usual-Saturday slope in the walks (#72); `RelativeRaceSim` for hubs and median City Pulse lines (#77) | Out of sample: City Pulse +12.6%, hubs −17.8% (replay); #77's test +13%, CI −27% to +55% |
| City Pulse opened lopsided (favourite ~94% in the evening; 97% in the live test) | Line = half point nearest the futures' median (#77) | Live: City Pulse favourite in 30–70% in 9/9 rounds since the restart |
| Pruning froze 80% of markets | Weighted updating after the cap (#72) | Capped markets that stop learning: 96 → 42 |
| AI records reset on restart | `ai_records` in the ledger DB (#72) | Live: Tube Crowd 24–18 kept across tonight's restart |
| Settlement on our poll time; TfL stamped every settling row before the close | Settle on the first reading TfL stamped after close (#72); `measuredAt` on the record, pre-close values void (#81); "result about HH:MM" on cards (#74) | `test_judge_fixes.py`, `test_live_fixes.py` |
| Clean sheet open for 59 min with no bet delay | 45 s delay, pause on line change, 10% cap (#61); closes with its first round (#72) | Courtsider edge 0 for leads up to 44 s |
| london_vix never listed | Freshness check fixed (#72); VIX fetched at startup (#81) | Live: listed in 9/9 rounds since the restart |
| Half the futures came from a sim that loses | `SIM_WEIGHT` default 0 (#72) | Live `fromSim: 0` |
| Event layer closed busy stations | Line set from the whole day, busy-station guard (#60) | Central MAE events on vs off 3.94 vs 3.94 (was 4.14) |
| Restart bypassed the position cap and dropped the hourly market; pending bets could bypass confirm | #81 | 17 new tests; `edge.py` 28/28 |

**Still open:**
1. Plane Spotter's fade beats the house on Heathrow (+66% out of sample, n=34).
2. The Tube Crowd copycat on City Pulse may still be a small leak (+13%, wide CI).
3. The central sim and event layer have no measured skill.
4. Track-record weighting into the price (the second half of TOPS).
5. Re-run the live trading test on main (its pricing findings predate #72 and #77).
6. The Contrarian's live −181 points (an old record, or a bug in the sit-out rule).
7. Low-typical stations (Bank) and the `X-Forwarded-For` spoof.

## 5. 3-minute demo script

**0:00 Hook, on a fast market.** "This is a live stock exchange on London's next five minutes." Open on the London VIX
card ("Will London get more unpredictable or calmer by 23:45?") or click a camera spot on the map to open a local
market. "You're trading against an automated market maker and seven computer traders, each reading one live feed."
Bet 100: "Bet 100 · win 160". It fills after the bet delay.

**0:30 Who's on the other side. (Pre-empts Q1.)** "The house: an LMSR market maker over 50 futures fitted to today's
data. Its worst case is capped on a double-entry ledger, and you're capped at a quarter of your points per market."

**0:50 A station market's fair wait. (Pre-empts Q5.)** Point at a closed hub card: "Betting closed · result about
23:50, when TfL publishes." "We settle only on numbers TfL stamped after betting closed. That's 14 minutes of
waiting, and it means nobody polling faster than us can see the answer."

**1:20 The traders. (Pre-empts Q3.)** Open the traders dropdown. "Tube Crowd reads TfL's usual-Saturday curve. It
used to beat our market maker by 30–48% a bet. We priced that curve in; out of sample it's down to about 13%, not
proven gone. That's TOPS pointed at a city: the leaderboard tells the market maker which data to listen to next."

**1:50 What if.** Click "What if…", click Regent Street, "Close this street". "This runs our central-London agent
simulation on copies, live state untouched. It doesn't beat 'nothing changes' as a forecast, so it isn't in the
price. It answers what data can't: what happens if."

**2:20 Settle.** The VIX market settles. Earnings dropdown: "+60"; on a win, confetti with the profit. "Source and
rule on every market, the frozen reading, void and refund if there's no data."

**2:40 Close.** "Marshall Wace built TOPS to find out which brokers to trust. We built it to find out which data
reads a city best, and it has already told us something our own model missed."

Rehearsal notes:
- Check `GET /round` before going on: london_vix should be listed. If it isn't, open on Heathrow or a camera spot.
- Don't restart the server in the 10 min before the slot: 5-minute markets rebuild from fresh data.
- The "+13%" line is n=54 with a CI of −27% to +55%. If asked, say it.

## Appendix: reproduce every number

```sh
RAW=/path/to/data/raw
python3 analysis/judge/replay_score.py $RAW 2026-11-07T12:40:00Z 2026-11-07T20:10:00Z 30   # full day
python3 analysis/judge/replay_score.py $RAW 2026-11-07T17:00:00Z 2026-11-07T21:30:00Z 30   # out of sample
python3 analysis/judge/replay_score.py $RAW 2026-11-07T18:30:00Z 2026-11-07T20:35:00Z 30   # late evening slice
python3 analysis/judge/crowd_latency.py $RAW                                               # TfL publish lag
python3 services/multiverse/eval_real.py --raw $RAW --windows 6                           # Oxford Circus vs persistence
python3 services/multiverse/eval_events.py $RAW 2026-11-07                                 # event layer skill
python3 -m unittest discover -s services/city          # 205
python3 -m unittest discover -s services/multiverse    # 132
python3 -m unittest discover -s recorder               # 16
```

The replay runs the real `Exchange` in-process with an in-memory ledger: no network and no writes to the recorder
data. AI trader P&L there uses each trader's real stake rule (min(100, 10% of points)) at the real quotes, so it shows
how a copycat would have done against the house.
