# Economy simulation

An agent-based simulation of the OverWatch points exchange as it is coded in `services/city`. It is stdlib only. The
findings are written up in `docs/market-structure.md` (section 9 and the recommendations).

```bash
python3 analysis/economy/simulate.py           # every scenario, 16 seeds x 7 days, ~4 min
python3 analysis/economy/simulate.py --quick   # 3 seeds x 2 days, smoke test
```

Outputs go to `out/`, which is gitignored:
- `results.json` holds every scenario's metrics, the LMSR bounds, the price impact and the courtsider table.
- `summary.md` holds the tables the doc quotes.

## What is mirrored from the code

Each item cites its source lines in the script.

| Rule | Source in `services/city` |
|---|---|
| Smoothing and the [3%, 97%] clamp | `pricing.py:39-45` |
| Futures pruning under the 60% cap | `pricing.py:303-318` |
| LMSR over the model prior, with the 5% fee taken off the stake | `pricing.py:321-347` |
| Locked odds, floored to 0.01, capped at 20, refused under 1.05 | `pricing.py:28-32` |
| Payout floor | `ledger.py:174-176` |
| Minimum stake of 10 | `ledger.py:492-493` |
| 25% position cap on equity | `exchange.py:208-212` |
| Cash check and daily gross-loss limit (counting escrow) | `ledger.py:416-424` |
| Worst-case exposure cap against min(100,000, bankroll + P&L) | `ledger.py:432-452`, `ledger.py:506-510` |
| Settlement legs | `ledger.py:543-552` |
| AI traders: fixed 100 stake, shares committed to the book, points kept off the ledger | `exchange.py:234-257`, `exchange.py:271-279` |
| The Contrarian waits until half the window has passed | `exchange.py:240-241` |
| The Contrarian's rule | `traders.py:186-196` |

## What is assumed

The full list is `ASSUMPTIONS` in `simulate.py`.

**Rounds.** Each round lists hub_surge (5-way), city_pulse, and either london_vix or heathrow_stack. hub_hour and
clean_sheet are added once an hour. A day is 144 rounds.

**Market truths.**
- Binary favourites are uniform on 50–80%.
- 5-way probabilities are Dirichlet(4), or Dirichlet(1.5) for hub_hour.
- The 50 futures are drawn from the truth, or from truth^1.6 in the overconfident scenario.

**Information arrival.** Each minute, futures with the wrong outcome are ruled out with p = 0.06 and futures with the
right outcome with p = 0.02.

**AI hit rates.** These come from the evidence in the `traders.py` docstrings and `docs/questions.md`:

| Trader, series | Hit rate |
|---|---|
| Tube, hub_surge | 0.36 |
| Tube, hub_hour | 0.56 |
| Camera Eye | 0.65 |
| Plane Spotter | 0.60 |
| Others | 0.50 |

The hit rate does not depend on the price, so the specialists' P&L depends heavily on these assumptions.

**Players.** There are 40 at the start, 10 of each type, and 8 join each day. Each plays a round with p = 0.3 and a
market within it with p = 0.7. Players resize a refused stake to fit their room, as they would after a 409.

| Type | Behaviour |
|---|---|
| Random | random option, stake 10–100 |
| Informed | noisy truth plus a private signal; bets edges above 5% at quarter Kelly |
| Herding | backs the most-staked option |
| Contrarian | follows the AI Contrarian's rule |
| Oracle | stress test only; knows the outcome |

**Courtsider.**
- The clean_sheet window is ~59 min.
- TfL publish lag is U(0, 180) s, plus a 60 s poll phase and a 10 s reaction.
- One account stakes 250.
- Variants: bet delays, a 5-min window, and a suspension triggered by a faster related feed.

## Not modelled

- Breaks and confirm tokens; players always confirm.
- Restarts.
- Halts on stale feeds.
- The spread of bets through time within a step.
- Sybil sign-ups.
