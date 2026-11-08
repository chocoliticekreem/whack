---
title: Steering log: the human decisions that shaped OverWatch
date: 2026-11-07
status: running log · source for Devpost "Challenges", "What we learned" and the pitch Q&A
---

# Steering log

We built OverWatch with a team plus many parallel AI coding agents. The agents wrote a lot of
the code fast; **the direction came from us.** This log records each point where a human looked
at what had been built, said what was wrong, and changed course. It also records what changed
as a result. Times are Sat 7 Nov 2026, London.

Devpost fields this feeds: Inspiration, What it does, How we built it, Challenges, Accomplishments,
What we learned, What's next for OverWatch.

## Decisions

| # | When | What we saw | What we said (paraphrased) | What changed |
|---|---|---|---|---|
| 1 | ~15:00 | A 2D exchange board full of jargon | "I have no idea what anything means. The judge won't know where to look." | Pivot to a 3D-first game with a tiny overlay: pick a future, countdown, reveal |
| 2 | ~15:10 | The overlay was still dense | "It has to feel like a game, easy to understand." | One question, three answers, you vs the AI, reality decides |
| 3 | ~15:50 | Desktop-only | "I want to trade from my phone." | Phone-first layout; judges play on their own phones |
| 4 | ~16:55 | Questions like "How late are the buses?" | "Too vague, not enticing. It has to be bigger." | City-wide markets across 5 London hubs with multiplier odds |
| 5 | ~16:58 | Anonymous "AI forecasters" | "The roles of the AI players aren't clear." | 6 named AI traders, each reading ONE live feed, with reasons citing real numbers (the TOPS idea) |
| 6 | ~17:00 | Markets with no rules or settlement guarantees | "Look at how real trading platforms do it. The live data has to be robust." | Research into Kalshi, Polymarket and Betfair, producing 12 integrity rules (source and rule on every market, close before settle, void and refund, halts, LMSR). Data supervisor and health checks |
| 7 | ~17:00 | The forecast lost to "nothing changes" on real data | (We measured it before pitching it) | Recalibrated until it ties, and we don't claim we beat the baseline |
| 8 | ~18:05 | UI with uppercase mono pills, emojis, glow | "This font needs to go. The point is to look less vibe-coded." | Restyle: Kalshi's market cards × PostHog's product chrome, no emojis |
| 9 | ~18:05 | "Trade London" | Renamed | **OverWatch** (capital O and W) |
| 10 | ~18:10 | Random markets that close at random times | "Too random. I can't bet at scale if it's this random, and it might be too quick." | Kalshi-style recurring series in fixed 5-minute rounds, bracket ladders |
| 11 | ~18:15 | Local questions (one camera, one bus stop) | "I'd only feel the urge to bet if I was there in person." | Data-tested question design: city-scale questions nobody on the street can answer, with a visible clue that actually predicts the outcome |
| 12 | ~18:20 | Ethics of betting on a city | "People are scared of this. We need incentives, and to think about real money." | docs/business.md: the path from game to prizes to exchange to hedging and data. Fairness and harm rules |
| 13 | ~18:25 | We had worried about insiders | "Nobody can know the true outcome, not even us. That's the beauty of it." | The emergent-only rule: list outcomes that emerge from many people, never decisions one person makes |
| 14 | ~18:30 | Points in the browser | "I want to monitor winnings and spending, designed as if it were real money." | A ledger that's never edited and always balances, each bet settled exactly once, responsible-play limits |
| 15 | ~18:40 | — | "Pressure-test edge cases, e.g. people exploiting it." | Red team plus adversarial review. Found an identity hole, an odds floor that paid above the price, and a net loss limit. All fixed with tests |
| 16 | ~18:45 | three.js city too heavy | "Check my RAISE project, it rendered nicely." | MapLibre with vector tiles, 60 fps |
| 17 | ~19:30 | 50 futures invisible | "The 50 worlds thing feels out of the picture." | A futures fan on every card: "N of 50 futures ruled out" |
| 18 | ~19:40 | Markets ran on data alone | "What does the twin and the simulation add besides looking cool?" | The simulation forecasts and the data settles. Simulation trader, scenario mode |
| 19 | ~19:45 | One-junction sim, hand-picked cases | "I don't want to build around specific cases. Can we at least do central London?" | General event layer (no hardcoded venues or lines, lint-tested). Central-London simulation, 1,017 streets |
| 20 | ~20:00 | The market's economics weren't written down | "Market makers, the balance of the economy: not pinned down." | Researched market-structure spec plus an economy simulation |
| 21 | ~20:35 | — | "If someone on the train sees it stop before the API does, can they trade on that?" | Courtsiding defences: bet delay, pause on feed change, tighter caps. Measured edge 0 for head starts up to 44 s |
| 22 | ~20:40 | — | "The judges are Marshall Wace quants; they'll ask these." | Judge-prep pressure test: the hardest questions, honest answers, a fix list |
| 23 | ~20:40 | A what-if settings form | "This makes no sense." | Redesigned as direct manipulation: click a street, pick an action, see the odds move |
| 24 | ~20:55 | Market cards stacked with clue lines, a futures fan, counts, source and rules | "I don't understand even a bit of it. Any person should be able to understand how to trade." | Plain-language cards: a question, big answer buttons with chance and payout, details behind a tap, 3-step onboarding |
| 25 | ~21:40 | Settlement either fast (cheatable) or fair (about 14 min, because TfL publishes late) | "Fair, and yes" to showing the wait | Station markets settle only on readings TfL stamped after betting closed; cards say "result about HH:MM, when TfL publishes" |
| 26 | ~21:45 | Hint lines under every card, a static clue strip | "Remove these useless literal subwordings, they bloat the screen"; "make this rotate like a stock exchange" | Hints cut; the strip became a scrolling exchange ticker |
| 27 | ~21:50 | Light editorial chrome around a colourful dusk map | "Make the UI nicer, it's pretty bland" | Dark glass theme matching the map, series colours, tactile buttons, motion that signals live prices |
| 28 | ~21:00 | A flat-looking 3D map | "I want the 3D part to be colourful… now it feels like a plane until I zoom in." Stay on MapLibre for performance | Dusk palette, pitch 70, deck.gl hub columns and arcs, still 60 fps (#68) |
| 29 | ~21:05 | Markets only on the 5 fixed hubs | "If I click around Oxford Circus I bet on that area; if I click elsewhere the pin drops there and I trade there." | Click-anywhere local markets on the nearest real sensor within 400 m, settled only on that sensor; forecast only where there is none (#71) |
| 30 | ~22:15 | Traders and earnings buried in panels, "81% of usual", payouts as totals | Traders as a dropdown, live earnings on the right, a subtle confetti win, "Bank −19%" instead of "81% of usual", "Bet 100 · win 400", fewer words, keep the ticker. The dark theme from row 27 was tried and rejected | Traders and earnings dropdowns, confetti on a win, change-vs-normal everywhere; the light look stays (#78). Win toasts then show profit so they add up with the earnings total (#80) |
| 31 | ~00:40 | Recovery by link and long code | "Short code should just be a 4-digit PIN, make it easier, no link" | Nickname + 4-digit PIN (hashed, lockout after 5 tries); W play-money currency with W1,000 start |
| 32 | ~00:55 | AI trader records were public but fed back into nothing | "Marshall Wace really likes loop engineering" | The TOPS loop: decayed, shrunk track records set each trader's trust weight, which moves opening prices only when skill is proven |

## Challenges (for Devpost)
- **Markets that were obvious.** At first 286 of 309 markets opened with a favourite above 90%.
  We set lines from the futures distribution (strike selection, as Kalshi does) and retired
  questions the data showed were decided or random.
- **Frozen camera counts.** YOLO was asked to count 83 cameras on 2 workers, so counts were hours old.
  We limited it to the cameras the markets read, and every count became fresh within minutes.
- **The model lost to persistence.** We said so, then anchored the forecasts and recalibrated until
  they tied, with 77–84% of readings inside the 80% range.
- **The 3D twin was too heavy** for a city-wide view. We switched to MapLibre vector tiles.
- **Security.** Our own red team found that anyone could act as any player, and that sure bets paid
  above the price. Both were fixed with tests that fail on the old code.
- **The event rules hurt the forecast** until we found they treated busy stations as closed.
- **Running many agents at once.** It needed strict file ownership, rebases, and a human reviewing
  every merge.

## What we learned (draft notes)
- Measure before you claim: the honest number (a tie with persistence) is more defensible to quants
  than a flattering one.
- Information is good; riskless information isn't. Design markets so one observer's edge is small.
- Agents give speed, but taste and direction stayed human; each turn in this log was one.

## Accomplishments (draft notes)
- A live exchange on central London: real TfL data, 5-minute rounds, 7 AI traders including one
  that bets from the simulation.
- Every price traceable to 50 simulated futures, with how many real data has ruled out.
- An exchange architecture built for real money: ledger, integrity rules, responsible play,
  pressure-tested.

## To append as the night goes on
Add each new human decision as a numbered row, plus any new challenge or lesson.
