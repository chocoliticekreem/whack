---
title: Judge prep — a sceptical Marshall Wace panel vs OverWatch
date: 2026-11-07
status: prep for Sunday judging · replay numbers re-run against main at bbfc214 (#87), Sun 00:55; TOPS and sizing numbers from #102 (docs/traders-and-tops.md); tests re-run on 10c3486 (#102 + #98), Sun ~03:00; live trading re-run (#84, #85) on code at #79, Sat 22:36–Sun 00:31; demo script rewritten for #93–#102
---

# Judge prep: what Marshall Wace will ask, and the true answers

The panel: two quant traders, a market-structure specialist and a quant researcher. They get 3 minutes of demo plus
Q&A, and they will press on whatever sounds like a claim. This doc gives the hardest questions they will ask and the
best answer that is also true. The first version (#57, Sat 20:40) listed weak spots; most were fixed overnight
(#61, #72, #77, #81, #86, #87) and are in "Fixed since" (section 4).

**Ground rule for the Q&A:** say "we measured X on n=Y, here's the caveat" before they ask. MW people respect a
team that knows where its edge isn't. They'll shred a team that oversells.

Confidence ratings: **strong** = we can defend it with code and a number. **ok** = the answer is true but the
evidence is thin. **weak** = the honest answer hurts. Say it first.

## 0. The numbers (re-run tonight; commands in the appendix)

All replay numbers are one Saturday (7 Nov 2026, to 00:05 Sun) of recorded data, run through the real `Exchange` on
main. hub_surge's reversion rate and softening, and Heathrow's reversion (#86), were fitted before 17:00, so **after
17:00 is out of sample**. Two windows: 17:00–21:30 (as in earlier drafts) and 17:00–00:05 (#86's window, which adds
the falling late evening).

| What | Number | Source |
|---|---|---|
| LMSR liquidity | b = 1200, fee 5% of stake, odds 1.05x–20x, model prob clamped to [3%, 97%] | `services/city/pricing.py` |
| Price impact | 100 points on a 50% option moves it about +3.8 pts; the max stake (250 = 25% of the 1000 grant) about +9 pts | LMSR formula, `Book.quote` |
| House limits | bankroll 100,000; worst-case open-bet loss capped at min(100,000, bankroll + P&L), and since #81 AI open stakes count toward it; position cap 25% of equity per market (10% on clean sheet and local markets) | `ledger.py`, `integrity.py` |
| Full-day replay, 12:40–20:10 | 91 rounds, 275 markets, **0 voids** | `analysis/judge/replay_score.py` |
| Open price vs uniform (Brier gain), full day | hub_surge +0.025 ± 0.018 (t = 1.4, n=86), city_pulse −0.002 ± 0.018 (t = −0.1, n=87), heathrow_stack +0.017 ± 0.011 (t = 1.6, n=78). **None significant** | same |
| Out of sample, 17:00–21:30 (55 rounds) | hub_surge Brier 0.755 vs uniform 0.800, gain +0.045 ± 0.027 (t = 1.7, n=51); city_pulse +0.029 ± 0.029 (t = 1.0, n=51); heathrow_stack +0.033 ± 0.014 (t = 2.3, n=53) | same |
| Out of sample, 17:00–00:05 (86 rounds) | hub_surge 0.753 vs 0.800, gain +0.047 ± 0.018 (t = 2.7, n=83); city_pulse +0.012 ± 0.025 (t = 0.5, n=82); heathrow_stack +0.024 ± 0.018 (t = 1.3, n=84) | same |
| Copying Tube Sigma vs the house, out of sample | 17:00–21:30: City Pulse 32/51, **+17%** per point staked (95% CI −11% to +46%); hub_surge 19/51, +5% (CI −35% to +49%). 17:00–00:05: City Pulse 60/82, **+42% (CI +23% to +61%)**; hub_surge −3%. Before #72/#77 the same rule made +32% (City Pulse) and +44% (hub_surge) full day | same (bootstrap CI, 2,000 resamples) |
| #77's own copycat test | 10 points on the furthest-below hub at the open quote, 54 races after 17:00: **+13%, 95% CI −27% to +55%** (was +32%). Not proven gone | `services/city/README.md`, #77 |
| Runway72 (fade the Heathrow stack) | after #86: 17:00–00:05 38/64, **−7.6% (CI −28% to +14%)**; 17:00–21:30 24/43, −12%; full day 32/64, −18%. Before #86: +66% (22/34, 17:00–21:30) and +47% (CI −1% to +122%, #86's baseline after 17:00) | `replay_score.py`, #86 |
| Hub "top riser since open" at the first in-round TfL publish | after #86, 17:00–00:05: 36/78 at a mean price 0.46, **+6% (CI −25% to +42%)**, model Brier 0.723 → 0.649 (#86). Before: +76% (CI +31% to +129%). Full day 12:40–20:10, which includes the fit window: still 44/78, **+48% (CI +8% to +97%)** | `replay_score.py`, `analysis/judge/fit_crowd.py`, #86 |
| Backing the open favourite | full day: hub_surge −9%, City Pulse −19%, Heathrow +5% (fee plus spread) | same |
| City Pulse line | the futures' median at settle, on the coarsest of .5/.25/.1/.05/.01 no wider than the futures' spread (IQR/1.349). Replay 12:00–02:35, 175 rounds × 5 seeds: favourite ≤ 70% in 98% (was 87% on the fixed .0/.5), 0 of 875 above 0.85 (was 39), max 0.77 (was 0.97); daytime lines stay on .0/.5 (91–100%, 12:00–19:00). Live 03:20 on 8 Nov: line 2.5, 0 of 50 futures over, 3%/97%; rebuilt: line 2.3, 21 of 50, 42% | `markets.py`, `RERUN.md` |
| Pruning cap | full day, 73 of 275 markets hit the 60% ruled-out cap; of the 24 with later readings, 0 stop learning (96 of 96 before #72, 42 of 102 before #86) | `replay_score.py` |
| Favourite-longshot, full day | options priced under 10% won 10.0% (priced 7.7%, n=60); over 70%: n=2 only. No stable bias | same |
| Settlement timing | station markets settle on the first reading **TfL stamped after betting closed** (#72), so a round takes about 14 min to settle; cards say "result about HH:MM, when TfL publishes" (#74). A value measured at or before close voids (#81). On the old poll-time rule, 2,779 of 2,779 station-rounds would have settled on a number stamped before the close | `markets.py`, `analysis/judge/crowd_latency.py` |
| Courtsiding | bet delay per series (clean sheet and local markets 45 s, hubs and pulse 5 s, VIX and Heathrow 2 s), suspend on a feed change, caps. A clean-sheet courtsider with up to 44 s head start: edge 0 (was +305) | `integrity.py`, `CourtsidingTest` (#61) |
| Oxford Circus sim vs persistence | 30 min to 23:38 UTC, 48 camera counts: MAE market 1.37, persistence 1.35, equal-weight worlds 1.37; 79% inside the 80% range. Bus delay: market 0.99 vs persistence 0.65 (persistence wins) | `services/multiverse/eval_real.py --windows 6` |
| Central-London sim vs persistence | station MAE 3.94 vs persistence 3.61 (sim loses by 9%); events on vs off 3.94 vs 3.94 after #60's fix (was 4.14 vs 3.94) | #60 (not re-run tonight) |
| Event layer | line_disruption vs control 4/15 (27%, base 47%), crowding_surge 8/26 (31%, base 53%), line_displacement 3/4. **No skill** | `services/multiverse/eval_events.py` (re-run, 12:06–23:42) |
| Live trading test (#79, code before #72/#77/#81) | 75 min, 327 bot bets: 0 voids, 0 fills after close, 120/120 wallet reconciliations exact, 46/46 bets on halted markets refused | `analysis/live_trading/README.md` |
| Live trading re-run (#84, #85; code at #79, before #81, #86, #87) | 65 min, 15 rounds, 280 bot + 34 reader bets. **30/30** crowding markets settled on TfL readings stamped after close (none seen by the recorder before close); City Pulse opened 0.50–0.66; **0** frozen prices; **0 of 229** fills after close; 26/26 bets on halted markets refused; ledger audit clean; house **−177** vs players (was −1,408). Open: hub_surge reader 6/10, +202% ± 108% (before #86's mid-round re-price); City Pulse Under won 14/15 in a falling evening; vs-usual markets void after the midnight profile refresh and one 54 s server stall (both fixed in #87) | `analysis/live_trading/RERUN.md` |
| Tests | city 306, multiverse 132, recorder 16, all passing (re-run on 10c3486, after #102 and #98) | `python3 -m unittest discover -s services/<x>` |
| Live, 23:45 | london_vix listed in 9 of 9 rounds since the last restart (heathrow_stack skipped: "the London VIX is live"); 0 voids; `fromSim: 0` on every market (`SIM_WEIGHT=0`). AI records survived the restart: Tube Sigma 24–18, 2,450 points, top of the board; Simulation 8–30, 78 points; Fadewater −181 points (cause found and fixed in #86: see 6.2) | `GET /round`, `/stats`, `/markets`, `/traders` |

## 1. The 10 most likely, most dangerous questions

Memorise the one-liners. The full answers are in section 2.

| # | Question | One-line answer | Conf. |
|---|---|---|---|
| 1 | "Who's on the other side, and what's your worst case?" | "An automated market maker, LMSR with b = 1200 over the model's probabilities. The ledger caps the house's worst-case open loss at 100,000 points, AI stakes included, and each player at 25% of equity per market." | ok |
| 2 | "Your forecast ties 'nothing changes'. Why should anyone trust these prices?" | "As a forecast, not yet. Out of sample the hub race beats a uniform guess (Brier 0.755 vs 0.800, t = 1.7), the rest don't. What we built is a market structure that measures that honestly." | ok |
| 3 | "Your own Tube Sigma trader beat your market maker. Is the house still a piggy bank?" | "On hubs, no longer: copying it is flat out of sample. On City Pulse, yes, in one regime: over the falling late evening it made +42% (CI +23% to +61%, n=82), because our futures don't carry the evening decline. That's our known open leak. The Heathrow fade that used to beat us now loses 7.6%." | weak |
| 4 | "Someone on a train sees a line stop before TfL does. Isn't clean sheet an insider market?" | "It's the one single-event market, so it gets the strictest rules: bets fill 45 s after you place them, any line change pauses the market and cancels pending bets, a 10% cap, and betting closes with its first 5-minute round. A head start under 44 s gains nothing." | ok |
| 5 | "You settle on the 'first reading after bets close'. Is that number already public?" | "No longer. We settle on the first reading TfL itself stamped after the close, and a value measured at or before the close voids. The cost is a 14-minute wait, which the card shows: 'result about 22:00'." | strong |
| 6 | "What does the agent simulation actually add? Evidence, not adjectives." | "Nothing measurable yet. Oxford Circus ties persistence, central London loses by 9%, the event layer has no skill. So it's out of the price (`SIM_WEIGHT=0`) and trades as Simaissance, which is losing live. Its real use today is scenario mode." | weak |
| 7 | "How is this TOPS? Where's the track-record weighting?" | "It's built. Every view a trader states at open is scored at settle against our own futures. Skill per trader per series is a Bayesian posterior (half-life 20 markets, empirical-Bayes prior). Trust is proportional to P(skill > 0), capped at 40% each, and the opening price blends the pool in by λ ≤ 0.3, learned from how much it improved past log loss: no proven skill, zero lean. Walk-forward over 509 markets it beats the futures by 0.0010 ± 0.0009 log loss, not significant. A permutation test says about 0.5 of 10 trader-series look skilled by chance; 2 do, none survives Bonferroni or BH. The City Pulse leak is shaved from +19.3% to +16.8%, not closed." | ok |
| 8 | "Why LMSR rather than an order book? Is this an exchange or a bookmaker?" | "The live site is a bookmaker with a published model: a thin market of strangers needs guaranteed liquidity. The exchange is built as a prototype beside it: an order book per contract, AI market makers quoting both sides, the same integrity rules, no house. It's live at /multiverse/exchange. On synthetic markets with a known truth, its prices end 52% closer to the truth than the forecast. We haven't measured whether it prices London better than the LMSR." | ok |
| 8b | "Seven traders on the game, thousands on the Exchange. Why two markets, and why not one?" | "The game's prices come from the house, so there's nothing for thousands of agents to trade on there; its seven traders bet against the house. The Exchange is the same questions on an order book with no house: 12 market makers, 184 funds running the seven traders' rules with Kelly sizing, and 2,000 retail. We haven't switched the game onto it yet because that moves the live money path, and we want a longer real-data replay first: on synthetic markets with a known truth it prices about twice as close to the truth as the forecast, on 25 recorded minutes it tied the forecast (n = 16). Switching is one reviewed PR once real data agrees." | ok |
| 9 | "What is the London VIX?" | "The spread of predicted crowds across 50 simulated worlds of Oxford Circus. It covers one area and needs an hour to burn in, so call it an uncertainty gauge, not a fear index. Camadel fades its moves, and that's 12–12 live." | ok |
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
known informed flows, out of sample after 17:00 (17:00–00:05): fading the Heathrow stack now loses 7.6% (CI −28% to
+14%, was +47%); the hub "top riser" rule makes +6% (CI −25% to +42%, was +76%); copying Tube Sigma on City Pulse
makes +42% (CI +23% to +61%, n=82), almost all from the falling late evening. So the house still loses to one public
rule: Under on City Pulse when the city empties. One day of data; say "directional".

**Q1.4 Why LMSR and not an order book?** *(ok)*
A demo room of 20 strangers can't sustain a two-sided book every 5 minutes. LMSR always quotes, has a known loss bound
for a fixed prior, and lets the model set the opening price. The cost: a player moves a price about 9 points at most,
so there's little price discovery. The live site is house-banked: a bookmaker with a published model.

The exchange in `docs/exchange-design.md` (#67) runs beside it (#90, #94,
`services/city/venue_server.py` on :8062): one order book per binary contract, no house, 12 AI market makers quoting
both sides (designated ones held to a 6-point spread and 90% uptime for a share of fees), 184 hedge funds, 2,000
retail, and the same speed bumps, position limits, halts and settlement sources as the live exchange. Since #98 its
fees are off by default (a points game has no exchange to fund); `--fees kalshi` turns on Kalshi's taker fee
ceil(0.07·C·P·(1−P)) with makers free. Since #112 it is live at `/multiverse/exchange` (Vercel `VENUE_URL` → :8062),
one click from the main screen's top bar (#120); #121/#123 gave it a trading-terminal page (markets, price chart vs
model, book, trades, ticket with order status and positions) and an account on arrival.

What we can say: on 25 minutes of Saturday's recorded data (`venue_demo.py --replay-from 2026-11-07T15:00:00Z`) every
structural check passed (no crossed books, no trade after close, ledger audit clean), the median quoted spread was
4 points and designated market makers quoted 95–96% of the time. The closing price was no better calibrated than the
forecast it quotes around (Brier 0.1256 vs 0.1255, n=16).

Since #98, a known-truth test (`analysis/exchange/simulate.py`: synthetic contracts whose true probability is known,
6 scenarios × 3 seeds, 216 contracts each, the venue's own agents and rules). Baseline (20 informed funds): the price
ends 33% closer to the truth than the forecast (4.1 → 2.7 points), on 89% of contracts. With no informed money or only
noise traders it stays as accurate as the forecast. It found market makers losing to informed flow (−1.24 points per
contract in the baseline); widening quotes on one-sided flow, plus a public fast-market flag that lets designated makers
quote up to 12 points, took that to −0.26. #111 starts widening only at 70% one-sided flow (spreads 7–13% tighter,
accuracy unchanged). #113 has market makers take 30% of fair value from the last minute's traded price and drops the
forecast-only funds, which pulled prices back to the forecast: the error cut goes 33% → 52% (baseline), 34% → 51%
(unpriced edge), and more than half of a known mispricing is traded away (was about a third). With no information in
the market it is 0.1 points worse. A TOPS-style capital allocator was tried and dropped: 5 minutes of P&L is mostly luck.

What we can't say yet: whether it prices London better than the LMSR, or whether the City Pulse leak survives on it.
That comparison isn't measured.

**Q1.5 Fees?** *(strong)*
5% of every stake comes off before shares are bought (`Book.quote`). Locked odds round down and never exceed the shares
bought, so no payout exceeds the market maker's shares. Bets under 1.05x are refused, and since #81 a pending bet that
would fill below that floor is refused when you place it, not later.

### 2.2 Pricing

**Q2.1 Where do the probabilities come from?** *(ok)*
Each market starts 50 futures from the last real reading, at TfL's own stamp:
- **City Pulse:** a log random walk with sigma and drift fitted on today's changes, plus the usual-Saturday slope over
  the horizon (#72). The line is the futures' median on a step that fits their spread (.0/.5 by day, as fine as
  0.01 on a quiet night), so it opens near 50/50 (#77).
- **hub_surge:** `RelativeRaceSim` (#77). Each hub follows its usual-day slope, reverts toward the five-hub average
  (κ = 0.0161/min, fitted 12:00–17:00), takes correlated shocks, and the shares are softened p ∝ share^0.5 (a Platt
  fit before 17:00). Since #86, the first TfL publish stamped after the open restarts every future from that reading,
  untempered.
- **Heathrow (#86):** a Poisson count around a level that reverts 47% per 5 min toward its trailing 30-min mean
  (`pricing.CountSim`, chosen out of sample over bootstrap, AR(1) and √-scaled variants in `fit_heathrow.py`).
- **VIX:** a bootstrap of today's own h-minute changes. **Clean sheet:** a per-line break hazard.
- **Local markets (#71):** the same machinery on the one sensor clicked.

The central sim can supply a share of the futures (`SIM_WEIGHT`), but it's 0 live because the sim loses to
persistence (`fromSim: 0` on every market). Don't call these "simulated futures of the city".

**Q2.2 The particle filter: what's the likelihood, and what happens when everything's wrong?** *(ok)*
Each reading weights every surviving future by exp(−(e/tol)²/2), tempered so the effective sample size stays at half
the survivors; futures off by more than tol are ruled out, never more than 60% in total. Since #72, the weights keep
moving the price after that cap. In the full-day replay on main, 73 of 275 markets hit the cap and 0 of the 24 with
later readings stopped learning (96 of 96 before #72, 42 of 102 before #86). Resampling is still the cleaner fix.

**Q2.3 Calibration evidence: numbers and sample sizes.** *(ok)*
Full day (12:40–20:10): open-price Brier vs uniform: hub_surge 0.775 vs 0.800 (n=86), City Pulse 0.502 vs 0.500
(n=87), Heathrow 0.483 vs 0.500 (n=78); none significant. Out of sample: hub_surge 0.753 vs 0.800, t = 2.7 (n=83,
17:00–00:05); Heathrow 0.467 vs 0.500, t = 2.3 (n=53, 17:00–21:30); City Pulse no better than a coin. One dry
Saturday. Say "directional".

**Q2.4 Is it overconfident?** *(ok)*
Full day: options priced under 10% won 10.0% (priced 7.7%, n=60); over 70% has n=2. Out of sample the extremes are
thin (n=33 and n=7, 17:00–21:30). No stable bias on this sample. Structural limits: 50 futures (2% granularity)
and the 3% clamp.

**Q2.5 How are strikes chosen?** *(ok)*
- City Pulse: the futures' median at settle (#77), on the coarsest step in .5/.25/.1/.05/.01 no wider than their
  spread, so a quiet night (futures spanning ~0.2) does not open at 3%/97%.
- Heathrow: the half point next to the futures' median whose Over share is nearest 50% (#86). The VIX: its value at
  open.
- Brackets are placed on the futures, none under 5%, and a bracket market whose favourite is over 85% isn't listed.
- **hub_hour:** sigma widened until the favourite opens at 70% or less. **Clean sheet:** P(break) held to 30–70% at
  open.

The last two choose the price, not the strike. Say it: "on hub_hour the 5-min fit priced 89% against a real 56%, so
we widened it. That's a patch, not a model."

**Q2.6 Is the favourite-longshot bias present?** *(ok)*
In the model, no stable bias (above). Among players we can't tell: there are a handful of humans and they barely move
the price.

**Q2.7 In the evening, "bet Under on City Pulse" beats you. Why haven't you fixed it?** *(weak, so say it first)*
It's our one known open leak, and we found it ourselves. In the live re-run the pulse fell from 22.7 to 16.6 over 70
minutes and Under won 14 of 15; the Over favourite at open lost 8 of 8. In replay after 17:00, copying Tube Sigma on
City Pulse made +42% (CI +23% to +61%, n=82). The cause: the line sits on the futures' median, the settle reading is
stamped 12–20 minutes after the newest reading the market sees, and the futures don't carry the evening decline over
that gap. #86 confirmed the horizon is right (a new test pins it) and tested forecasts on TfL's own stamp grid
(`analysis/judge/fit_crowd.py`), but none gave a stable fix in the fit window, so we didn't ship a patch tuned on one
evening. The answer to give: "We measured it, we know the mechanism, and the fix is fitting the evening decline across
more than one day. Until then, the leaderboard shows Tube Sigma taking it, which is the product working as designed."

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
players. Fadewater waits until halfway and fades where the points are, at the price players left. There are no
unfilled orders to see. It does move the price a player gets (100 points ≈ 4 pts), which the card shows.

**Q3.5 Is there a guaranteed-profit strategy?** *(ok)*
No riskless one: buying every outcome loses the fee, odds round down, a market closes when its outcome is decided, and
settlement is exactly-once with idempotent bets (an 8-thread hammer test). The confirm-bypass and pending-order holes
the live test found are fixed (#81). One positive-expectation rule is left out of sample: copy Tube Sigma
(Under) on City Pulse when the city is emptying (+42%, n=82, 17:00–00:05). The Heathrow fade (−7.6%) and hub top
riser (+6%, CI −25% to +42%) were priced in by #86. In a points game, `X-Forwarded-For` can still be spoofed past
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
- **Live:** Simaissance is 8–30 and down to 78 points.

So it's out of the price (`SIM_WEIGHT=0`). Scenario mode ("what if Regent Street closes?", #66) is built but off the
trading screen: a public what-if either repeats what the price already knows or hands every player a free edge against
the house. Its use is planning (TfL, retailers, event operators), not betting.

**Q4.3 The event layer: any skill?** *(weak, so say so)*
No. Re-run on 12:06–23:42: line disruptions called busyness direction 4/15 against a no-event control (27%, base 47%);
crowding surges 8/26 (31%, base 53%). Fixtures, road closures and rain have no station ground truth. What would prove
it: about 200 episodes per rule family (a week of recording), a significant hit rate against control, and
sim-with-events beating sim-without on held-out days.

**Q4.4 Which signals actually work?** *(ok)*
- The usual-Saturday curve calls station direction 66/72/79% at 15/30/60 min (n=482/697/741, `docs/questions.md`).
  It's in the price, yet as a trader it still makes +42% on City Pulse after 17:00 (n=82): it catches the evening
  decline our futures miss.
- Fade the Heathrow stack: was +66% out of sample; priced in by #86's reversion model, now −7.6%.
- Momentum: worthless.

### 2.5 Data and oracle

**Q5.1 Single source of truth? Revisions?** *(ok)*
Each market names one source and one rule, and freezes the settled reading. Since #81 the snapshot holds `{value,
source, measuredAt, fetchedAt}`, so TfL's own time is on the record. No reading in the window means void and refund:
0 voids in 275 replay markets and 0 in the first live test. The re-run voided one market on a VIX feed gap and three
vs-usual markets after the midnight profile refresh (a bug, fixed in #87), all refunded. There's no backup source; real
money would need a data agreement and a second source.

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

**Q6.1 Is the TOPS analogy real?** *(ok)*
Yes, all three parts. TOPS collects broker ideas, scores them on outcomes and weights trust by track record. Each
trader reads one feed and states a view at every open; each view is scored at settle against the futures prior (log
score, decayed with a half-life of 20 settled markets). Skill per trader per series is a Bayesian posterior with an
empirical-Bayes prior (strength ≈ 23 markets: the traders barely differ beyond noise). Trust is proportional to
P(skill > 0), with a 5% floor and a 40% cap. The opening price is (1 − λ)·futures + λ·(pooled views), where λ is
learned from how much the pool improved log loss on past markets, shrunk by its own significance (λ̂·(1 − 1/t²),
positive-part James–Stein) and capped at 0.3
(`services/city/tops.py`, `GET /tops`, `docs/traders-and-tops.md`). Walk-forward, paired, from an empty record
(`analysis/judge/tops_replay.py`), log loss vs the futures prior:

| Window | n | Old (softmax, #93) | New (Bayesian) | New − old |
|---|---|---|---|---|
| All | 509 | −0.0006 ± 0.0004 | −0.0010 ± 0.0009 | −0.0004 ± 0.0006 |
| Opened ≥ 17:00 | 354 | −0.0009 ± 0.0005 | −0.0018 ± 0.0012 | −0.0009 ± 0.0008 |
| Opened ≥ 00:55 (unseen) | 54 | −0.0006 ± 0.0009 | +0.0003 ± 0.0024 | +0.0008 ± 0.0018 |

Say it plainly: the loop is real and principled, but nobody has proven skill yet, so it moves prices very little.
Permutation test (`analysis/judge/tops_permutation.py`, 200 reruns): of 10 trader-series about 0.5 would look
skilled at p < 0.05 by chance; 2 do (Tube Sigma on City Pulse, Runway72 on Heathrow), and none survives
Bonferroni or Benjamini–Hochberg. Known limit: λ is now shrunk by its evidence (t), not by a count of markets, so
noise gives λ = 0 (hub_surge and hub_hour went 0.3 → 0 on the live ledger), but λ̂ = Σr/Σr² grows as the pool's
moves shrink, so weak evidence can still reach the cap: City Pulse at 03:25 on 8 Nov had λ̂ = 1.93, t = 1.38 → 0.3.
Tiny price effect there (median |pool − futures| 0.024, about 0.7 points at λ = 0.3); documented, not tuned.

**Q6.2 How are traders scored and sized?** *(ok)*
Points at locked odds. Each stake is quarter-Kelly on the LMSR: the stake that maximises expected log bankroll given
the trader's probability, the price, LMSR slippage, the 5% fee and the odds cap, times 0.25, capped at 100 and
floored at 10, on free points (points − open stakes). Kelly never spends past the point where the next share costs
q/(1 − f), which has the closed form x = b/(1 − f)·ln((1 − p)/(1 − q(1 − f))), tested against `pricing.Book`. No
edge (or no record) means no bet. A stake is at most a quarter of free points, so a trader can't go negative: a
property test over 300 random bet/settle sequences fails on the pre-#86 sizing. Fadewater's old −181 was that
bug.

**Q6.3 Is "which data has alpha" actually measured? With what sample?** *(ok)*
On one day, against the house, out of sample after 17:00 (n = 43–84 per series). After #86, Tube Sigma on City Pulse
is the only trader with an edge there (Runway72's was priced in). Line-ennium barely trades and Rain Street never
traded on a dry day. That's a measurement design, not a result.

### 2.7 Scale and business

**Q7.1 Real money: which regulator?** *(ok)*
- **UK consumer betting:** the Gambling Commission. Betfair runs a licensed exchange; a house-banked LMSR is
  bookmaking.
- **US:** the CFTC designated-contract-market route Kalshi took.
- **Business hedging:** possibly FCA-regulated derivatives, or an insurance partner (parametric cover).

Not legally verified. The ledger is real-money grade; the order book is live in points; real money, payments, ID
checks and licensing are the work left.

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
laptop through Cloudflare quick tunnels (city :8051, sim :8449, exchange :8062) whose URLs change on restart; the
ngrok static domain hit its monthly bandwidth cap on Sun 07:30. If the laptop sleeps, the site falls back to demo
data. The order-book exchange is a separate process (:8062) with its books and ledger in memory; it reads
the same recorder files and touches nothing the site uses.

**Q8.2 Persistence and failure modes?** *(strong)*
- **Player money:** an append-only double-entry SQLite ledger. After a restart, bets on recorded markets settle from
  data and unresolvable ones are refunded.
- **Since #72 and #81:** AI records persist; open hourly markets are rebuilt from their records; the position cap
  reads stakes from the ledger; the VIX is fetched at startup so it lists in round 1. Checked live: hourly market
  re-listed, caps held, VIX listed in round 1, AI records kept.
- **Still lost:** pending (unfilled) bets, which are cancelled with no points moved. Current 5-minute markets rebuild
  from fresh data, so City Pulse may get a new line under the same id.

**Q8.3 Test coverage?** *(strong)*
306 city, 132 multiverse and 16 recorder tests, all passing (re-run on 10c3486): a randomised 1000-bet ledger
invariant, an 8-thread hammer, idempotency, exactly-once settlement, halts and voids, courtsiding, and 40 regression
tests from the two live trading runs (26 in `test_live_fixes.py`, 14 in `test_rerun_fixes.py`), the TOPS no-look-ahead
test and the 300-sequence Kelly property test (#102). The edge-case suite
(`edge.py`) passed 28/28 against a scratch server.

**Q8.4 How did you test it under real trading?** *(ok)*
Twice. Bots (random, favourite, longshot, signal, copycat, Kelly, a last-second reader) traded 75 minutes of live
rounds on a scratch ledger (#79). It found restart, pending-bet, confirm-bypass and settlement-record bugs, all fixed
in #81. We re-ran it for 65 minutes on code with #72 and #77 (#84, #85):
- **Fixed:** 30 of 30 crowding markets settled on TfL readings stamped after close, and the recorder saw none of them
  before close. City Pulse opened at 0.50–0.66 (was up to 0.97). 0 frozen prices (was 20 of 28). 0 of 229 fills
  after close. House P&L vs players −177 (was −1,408).
- **Still beatable, then fixed:** the hub "top riser" reader won 6 of 10 (+202% ± 108%). #86 re-prices hubs at the
  first in-round publish; in replay after 17:00 that rule fell from +76% to +6%. Not yet re-tested live.
- **Also fixed in #86:** Fadewater's −181 points on the live server, an AI stake-sizing bug (Q6.2).
- **Fixed in #87:** vs-usual markets voiding after the midnight profile refresh, and one server stall (Q8.5).
- **Still open:** City Pulse in a falling evening (Q2.7).

**Q8.5 What breaks under load?** *(ok)*
Every HTTP request and the 1 s tick share one global lock (`server.py` `LOCK`). Fine for a judging room, not for
launch. SQLite with one writer is the next limit. The live re-run saw one stall: for 54 s POST /bet took 2.3–10 s, and
one bet the client timed out on was filled on the server, so a client that doesn't retry with its idempotency key
thinks it failed. The cause was inferred, not measured. #87 builds each round's markets outside the lock (round-open
lock hold 100–124 ms → about 1 ms on scratch servers), caches the time-band lookup, and logs any lock hold over
500 ms. Retrying with the same key returns the original bet; the UI still makes a new key on retry (fix on a
branch, not merged).

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
| "The Tube Sigma leak is fixed" | On City Pulse it made +42% after 17:00 (CI +23% to +61%) | "Fixed on hubs; on City Pulse it still wins in a falling evening, and the leaderboard shows it" |
| "No public rule beats the house" | Under on City Pulse in a falling evening made +42% | "One public rule still beats it on one day. The Heathrow fade and the hub riser were measured and priced in" |
| "TOPS fixes the leak" | The blend shaves Tube Sigma's City Pulse pick from +19.3% to +16.8% after 17:00 (n=112); λ is capped at 0.3 | "The market leans on proven traders, a little and only where they're proven" |
| "Tube Sigma is a skilled trader" | p = 0.015 is one of 10 tests; none survives Bonferroni or BH, and its posterior ends at P(skill > 0) = 0.37 | "Two of ten look skilled where chance gives about one half; none survives correction" |
| "The London VIX is the city's fear index" | One area, 1 h burn-in | "An uncertainty gauge for Oxford Circus: the spread of 50 simulated worlds" |
| "Points to pounds is a licence, not a rewrite" | A house-banked LMSR is bookmaking | "The ledger is real-money grade; the order book is live beside the house in points; real money plus licensing are the work left" |
| "City-scale aggregates one person can't move" | Untested; Bank sits at 5–6% of baseline | "Five busy hubs against their own baselines; low-traffic stations are the soft spot" |
| "Kalshi-style integrity" (as a whole) | Kalshi runs surveillance | "Kalshi-style rules: source and rule on every market, close before settle, TfL's own timestamp, void and refund, bet delays" |
| "Pruning tracks the data all round" | 73 of 275 markets still hit the 60% ruled-out cap; it's a heuristic, not resampling | "Each reading reweights the futures; pruning is capped at 60% rather than resampled" |

## 4. Fixed since the first draft, and what's still open

**Fixed since (Sat 20:40 → Sun 00:50):**

| Weak spot (#57) | Fix | Evidence |
|---|---|---|
| Copying Tube Sigma beat the house by +32–48% | Usual-Saturday slope in the walks (#72); `RelativeRaceSim` for hubs and median City Pulse lines (#77) | Hubs fixed: −3% after 17:00. City Pulse is not: +42% after 17:00 (still open, below) |
| City Pulse opened lopsided (favourite ~94% in the evening; 97% in the live test; 97% again at 03:20 on 8 Nov, a quiet night) | Line = the futures' median (#77), on a step that fits their spread | Live re-run: opens 0.50–0.66, median 0.54, 0 of 13 above 0.85. Replay to 02:35: 0 of 875 above 0.85, max 0.77 |
| Pruning froze 80% of markets | Weighted updating after the cap (#72) | Capped markets that stop learning, full-day replay: 96 → 42 (#72) → 0 of 24 (main after #86); live re-run: 0 frozen of 30 |
| AI records reset on restart | `ai_records` in the ledger DB (#72) | Live: Tube Sigma 24–18 kept across tonight's restart |
| Settlement on our poll time; TfL stamped every settling row before the close | Settle on the first reading TfL stamped after close (#72); `measuredAt` on the record, pre-close values void (#81); "result about HH:MM" on cards (#74) | Live re-run: 30/30 settled on readings stamped after close, none seen before close; 0 of 229 fills after close |
| Clean sheet open for 59 min with no bet delay | 45 s delay, pause on line change, 10% cap (#61); closes with its first round (#72) | Courtsider edge 0 for leads up to 44 s |
| london_vix never listed | Freshness check fixed (#72); VIX fetched at startup (#81) | Live: 9/9 rounds since the restart; re-run 14/15 (missed round 1); round 1 after a restart since #87 |
| Half the futures came from a sim that loses | `SIM_WEIGHT` default 0 (#72) | Live `fromSim: 0` |
| Event layer closed busy stations | Line set from the whole day, busy-station guard (#60) | Central MAE events on vs off 3.94 vs 3.94 (was 4.14) |
| Restart bypassed the position cap and dropped the hourly market; pending bets could bypass confirm | #81 | 17 new tests; `edge.py` 28/28 |
| Fading the Heathrow stack beat the house (+66% / +47%) | Poisson count around a mean-reverting level, line on the futures' median, in-round readings re-run the model (#86) | After 17:00: −7.6% (CI −28% to +14%) |
| Hub "top riser since open" beat the house (+76% in replay; 6/10, +202% in the live re-run) | Every future re-simulated from the first in-round TfL publish, untempered (#86) | After 17:00: +6% (CI −25% to +42%). Full day still +48%, so narrowed, not proven closed; not re-run live |
| Fadewater showed −181 points | Stakes sized on points − open stakes (#86), now quarter-Kelly on free points; a negative record loads as 0 | Can't go below 0; new tests in `test_live_fixes.py` |
| House lost 1,408 to bots in the first live test | Pricing fixes #72, #77 | Live re-run: house −177 vs players, ledger audit clean |
| Every vs-usual market voided after the midnight profile refresh, until a restart | Profile re-read on mtime or size change, merged per station and weekday, a cut or corrupt gzip retried next tick (#87) | New `test_rerun_fixes.py` fails or errors on the old code |
| POST /bet stalled 2–10 s at round opens | Round markets built off the lock, time-band lookup cached, lock holds over 500 ms logged (#87) | Round-open lock hold 100–124 ms → about 1 ms (scratch servers) |
| london_vix skipped in the first round after a restart | Boot order: recover, then fetch sim and VIX, then the first tick (#87) | Listed in round 1 on 2 restarts (scratch servers) |
| An order book isn't built; the house banks every bet | Built and live at `/multiverse/exchange` (#112), beside the house: a parallel order-book venue with AI market makers, the Kalshi fee and the same integrity rules (#90, #94). The live site still runs the LMSR | Demo on 25 recorded minutes: all 9 health checks pass, median spread 4, designated market makers up 95–96%. Pricing vs the LMSR not measured |

**Still open:**
1. **City Pulse in a falling evening** (known limitation, Q2.7): Under won 14/15 live; copying Tube Sigma made +42%
   after 17:00 (CI +23% to +61%). No stable fix in the fit window.
2. The hub top-riser fix is out of sample on one evening (+6%, CI −25% to +42%) and not re-tested live. None of #86 or
   #87 has been through a live bot run yet.
3. The UI makes a new idempotency key when it retries after a client timeout, so a slow fill can look rejected (fix
   on a branch, not merged).
4. The central sim and event layer have no measured skill.
5. The TOPS blend is small by design (λ ≤ 0.3, 40% per trader), not significantly better than the old loop, and no
   trader-series survives multiple-testing correction. λ counts markets, not information, so it can sit on its cap
   where the pool barely differs from the futures (`docs/traders-and-tops.md` §7).
6. Low-typical stations (Bank) and the `X-Forwarded-For` spoof. Minor: the daily loss limit counts gross losses, and
   AI traders are off the ledger.

## 5. 3-minute demo script

Seven timed beats. **Say** is the exact line. **Show** is what's on screen. **If slow** is the fallback when live data
lags; never wait on the network in silence, say the line and move on.

**Before the slot (T−20 min):**
- Place W100 on a City Pulse or hub card. Station markets settle about 14 minutes after betting closes, so this bet
  settles on a real TfL reading during or just before the demo (beat 3).
- Check `GET /round`: london_vix listed (if not, Heathrow stands in). Check `GET /tops`: note which series have
  `lambdaNow` > 0. A fresh ledger needs about an hour of rounds before any lean (#93).
- Check the live server runs #102 or later (trust bars, quarter-Kelly reasons) and #87 or later (no vs-usual voids
  after the 00:03 profile refresh).
- Don't restart the server in the 10 minutes before the slot: 5-minute markets rebuild from fresh data.
- Check `https://overwatchcapital.vercel.app/api/venue/health` returns `"ok": true`, then open `/multiverse/exchange`
  in a background tab (beat 6). Its tunnel is a Cloudflare quick tunnel: if it restarted, re-set `VENUE_URL` and redeploy.
- Open, in background tabs: `docs/traders-and-tops.md` and the Devpost section "How it works under the hood" (fallbacks
  for beats 4 and 5).

**0:00 Hook: parallel Londons (20 s).**
- **Show:** the 3D map with the ticker running. Tap a City Pulse card, open Details, scroll to "Simulated futures":
  "50 simulated futures · N ruled out by live data".
- **Say:** "Right now there are 50 parallel Londons five minutes from now. Every five minutes, reality picks one. This
  is OverWatch: a live exchange on the London nobody can see yet. Each future starts from TfL's latest reading; every
  new reading reweights them and rules out the ones that are wrong. Those weights are the prices."
- **If slow:** if the futures fan hasn't loaded, point at the answer buttons ("12 of 50 futures") in Details, or at the
  odds on the card: "these odds are the share of 50 futures on each answer."

**0:20 Place a live bet (30 s).**
- **Show:** the London VIX card ("Will the model become more uncertain by HH:MM?"), or Heathrow if the VIX is down.
  Tap an answer, W100, confirm. The pop-up "W100 on … · Bet placed · fills in 2s", then "Your bets" on the right:
  "Filling in 2s", then "Result in …".
- **Say:** "I'll put W100 on it; the button shows what I'd win. It fills after a short delay so nobody can trade on a feed faster
  than ours. On the other side: an LMSR market maker over those 50 futures, and seven AI traders, each reading one live
  feed: TfL crowding, Tube status, street cameras counted by YOLO, planes stacking over Heathrow."
- **If slow:** if the fill hangs, the bet still shows as filling in "Your bets": "the delay is the point; it fills or it's
  cancelled, never half." If the VIX and Heathrow are both missing, click anywhere on the map ("Detected area: …") and bet on the local
  market it opens.

**0:50 A market settling on real TfL data, and why it's fair (40 s).**
- **Show:** "Your bets" on the right, the T−20 bet: "Result in … · about HH:MM", or "Won +W…" / "Lost −W…" for
  10 s once it settles (then it's in the earnings dropdown's Orders tab). Then the portfolio page, bet
  table, column "Settlement reading and source": the frozen TfL reading the market settled on. (Settled cards leave the
  feed since #100, so the reading lives in the portfolio.)
- **Say:** "This one settled on real TfL data. TfL stamps every crowding reading, then publishes it a couple of
  minutes later. We settle only on the first reading TfL stamped after betting closed, so nobody polling faster than us
  can know the answer; a reading from before the close voids the market and refunds you. That's why the card waits
  about 14 minutes. Fair beats fast."
- **If slow:** if it still says "Waiting for TfL…", say "that's the fair wait in action: TfL hasn't published the
  reading stamped after our close yet" and open the rule in Details. If there's no settled bet, open an open station
  card's Details: "How it is decided" gives the rule and the source.

**1:30 The TOPS loop: the traders' trust (40 s).**
- **Show:** the traders dropdown: "The market leans on traders who've been right", trust bars, sorted by trust. Then a
  card's Details: "Computer traders moved … by +N points" (shown only when the move rounds to at least 1 point).
- **Say:** "This is your TOPS, rebuilt on a city. Every AI trader states a view on every market. Reality scores it
  against our own futures. A Bayesian model estimates each trader's skill, trust follows the chance that skill is real,
  and the opening price leans on the proven ones, at most 30%. They size bets by quarter-Kelly, and with no edge they
  sit out. We also tested it against luck: of ten trader-series, about one half should look skilled by chance. Two do,
  and neither survives a multiple-testing correction, so the market leans very little. That's the loop working."
- **If slow:** if every trust bar is 0 (fresh ledger), say "this ledger restarted, so nobody has a record yet and the
  lean is exactly zero: no record, no trust" and show the permutation table in `docs/traders-and-tops.md` §6.

**2:10 Under the hood (20 s).**
- **Show:** the menu, "Under the hood".
- **Say:** "Under the hood it's six steps. 50 futures from TfL's own timestamp. Each reading reweights them. An LMSR
  turns them into prices with a capped worst case. Settlement only on readings stamped after the close. AI traders
  sized by Kelly on the market maker's real slippage. And a Bayesian trust loop that only moves the price as far as a
  trader's record earns."
- **If slow:** if the panel isn't on the live build, open the Devpost section "How it works under the hood" or
  `docs/traders-and-tops.md` and say the same six steps.

**2:30 The exchange: no house (15 s).**
- **Show:** `/multiverse/exchange` (open in a background tab before the slot). The order book for one hub contract,
  recent trades scrolling.
- **Say:** "What you just saw is a bookmaker. This is where it goes: the same markets on a real order book, no house.
  12 AI market makers, 184 hedge funds, 2,000 retail. Where we know the true answer, its prices end 52% closer to the
  truth than our own forecast."
- **If slow:** if the book is empty or the page errors, skip it and say the line over the map. If asked: the 52% is a
  known-truth simulation, not London.

**2:45 Honest limits, and close (15 s).**
- **Show:** back to the map.
- **Say:** "What it doesn't do yet: our crowd forecast ties 'nothing changes', it's one day of data, and bet Under on
  City Pulse when the city empties still beats us. Marshall Wace built TOPS to find out which brokers to trust. We
  built it to find out which data reads a city best."
- **If slow:** none needed. If over time, cut to the last two sentences.

Rehearsal notes:
- If the fast bet from 0:20 settles before 2:45, "Your bets" shows "Won +W…" for 10 s and a win shows confetti with the
  profit; call it out in one line. Start the slot in the last 3 minutes of a round to make that likely.
- The "+42%" line is n=82 on one evening (replay 17:00–00:05), CI +23% to +61%. If asked, say it.
- The permutation numbers: 10 trader-series, 200 re-walks, 0.5 expected at p < 0.05, 2 observed under the shuffle
  null (P(≥ 2 by chance) = 0.095), none survives Bonferroni (p < 0.005) or Benjamini–Hochberg.
- Don't say "50 simulated futures of the city" as a technical claim (section 3). If pressed on "parallel Londons":
  "statistical paths fitted to today's data and TfL's usual Saturday; the agent simulation is out of the price."

## Appendix: reproduce every number

```sh
RAW=/path/to/data/raw
python3 analysis/judge/replay_score.py $RAW 2026-11-07T12:40:00Z 2026-11-07T20:10:00Z 30   # full day
python3 analysis/judge/replay_score.py $RAW 2026-11-07T17:00:00Z 2026-11-07T21:30:00Z 30   # out of sample
python3 analysis/judge/replay_score.py $RAW 2026-11-07T17:00:00Z 2026-11-08T00:05:00Z 30   # out of sample, #86's window
python3 analysis/judge/tops_replay.py $RAW 2026-11-07T12:40:00Z 2026-11-08T02:35:00Z 30 2026-11-07T17:00:00Z 2026-11-08T00:55:00Z   # TOPS, walk-forward
python3 analysis/judge/tops_permutation.py $RAW 2026-11-07T12:40:00Z 2026-11-08T02:35:00Z 200   # skill vs chance
python3 analysis/exchange/simulate.py                                                      # venue on known-truth markets, ~12 min
python3 analysis/judge/crowd_latency.py $RAW                                               # TfL publish lag
python3 services/multiverse/eval_real.py --raw $RAW --windows 6                           # Oxford Circus vs persistence
python3 services/multiverse/eval_events.py $RAW 2026-11-07                                 # event layer skill
CITY_RAW=$RAW python3 services/city/venue_demo.py --replay-from 2026-11-07T15:00:00Z    # venue prototype, 25 min
python3 -m unittest discover -s services/city          # 306 (venue and TOPS included)
python3 -m unittest discover -s services/multiverse    # 132
python3 -m unittest discover -s recorder               # 16
```

The replay runs the real `Exchange` in-process with an in-memory ledger: no network and no writes to the recorder
data. AI trader P&L there uses each trader's real stake rule (quarter-Kelly on unstaked points) at the real quotes,
so it shows how a copycat would have done against the house. The live re-run is reproduced in
`analysis/live_trading/RERUN.md`.
