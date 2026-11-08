# Pricing engine

How OverWatch prices a market: a particle filter over simulated futures gives the model probability `m`, and an LMSR
market maker quotes on top of it. Code: `services/city/pricing.py` (`Futures`, `Book`), trade flow in
`services/city/exchange.py`, tests in `services/city/test_pricing_props.py`. All numbers below are from the recorded
day (7 Nov 2026, 13:00 to 01:00 UTC, `data/raw`) unless stated. Every script named here is read-only and reruns them.

## 1. The particle filter (`Futures`)

**State.** N = 50 futures per market, each a full simulated path from the open reading to the settle time, built
once at the open from a seed fixed per market id (`crc32(id)`), so a restart rebuilds the same futures.

| Series | Simulator | Transition |
|---|---|---|
| city_pulse | `ScalarSim` | log City Pulse random walk, x_{t+1} = x_t + μ + σ·clip(ε, ±2.5), ε ~ N(0,1) |
| hub_hour | `RaceSim` | the same walk per hub, hubs independent; highest vs usual at settle wins |
| hub_surge | `RelativeRaceSim` | walk + pull to the five hubs' mean at κ = 0.0161/min, shocks correlated across hubs |
| heathrow_stack | `CountSim` | final count ~ Poisson(λ), λ = open count pulled 47% per 5 min to its 30-min mean |
| clean_sheet | `BreakSim` | first break time ~ Exp(λ), λ = −ln(1 − p_break)/T |
| london_vix | `DeltaSim` | bootstrap of today's own h-minute changes |

μ and σ per minute are fitted on the last 3 h of changes around TfL's usual-day curve (`fit_log_walk`). One capped
step has sd `CAPPED_SD` = 0.9887 (variance of N(0,1) clipped at ±2.5).

**Likelihood.** Each reading multiplies future i's weight by exp(−½(e_i/τ)²), where e_i = |log y − x_{i,k}| / σ and
τ = 1.5. So the likelihood is Gaussian with sd 1.5σ per-minute walk sigma. Futures with e > τ are also removed, up to
60% of them (30 of 50). For hub races, e_i is the largest error over the hubs (an L∞ error, not a product of per-hub
likelihoods).

**Measured noise.** For a random walk read with white noise, the first differences have lag-1 autocovariance −σ_n².
On 78 pairs of TfL readings 4 to 6 min apart (`analysis/judge/pf_audit.py`):

| | σ_n | walk σ/min | likelihood sd (1.5σ) |
|---|---|---|---|
| City Pulse | 0.000 | 0.0062 | 0.0094 |
| Oxford Circus | 0.021 | 0.0162 | 0.0244 |
| King's Cross / Piccadilly | 0.000 | 0.012 / 0.016 | 0.018 / 0.024 |
| Waterloo / Bank | 0.004 / 0.006 | 0.014 / 0.022 | 0.020 / 0.032 |

TfL readings carry almost no noise. The 1.5σ likelihood is a deliberate widening, not a fitted measurement model. If
σ_n ≈ 0, the posterior given a reading is the walk restarted from that reading. That is what `RelativeRaceSim.restart`
does, and what the exact probabilities below do.

**Resampling.** There is none. Paths are simulated to settle at the open, so the weighted estimator Σ wᵢ·1[outcomeᵢ]
is the Rao-Blackwellisation of any resampled one. Systematic or residual resampling of fixed paths only duplicates
them and adds variance; it would need a move step (re-simulating from the reading) to help. Instead the filter
tempers the likelihood, w → w^α with the largest α ≤ 1 that keeps ESS ≥ N_alive/2. Degeneracy in the replay
(`pf_audit.py`, per reweight):

| Series | Updates | Survivors | ESS/N of the raw likelihood | Tempered |
|---|---|---|---|---|
| hub_hour | 86 | 22.4 | 0.08 | 100% |
| city_pulse | 116 | 45.0 | 0.58 | 29% |
| clean_sheet | 343 | 34.4 | 0.96 | 0% |

On hub_hour the raw weights collapse onto about 4 futures, and tempering throws away most of each reading. That is
why hub_hour and clean_sheet now price from the reading itself (below).

**Exact probabilities.** Where the model has a closed form, `Futures.probs` uses it instead of the futures' share. The
result goes through the same `smooth` (half-future prior, [3%, 97%] clamp) as N_FUTURES futures' worth.
- clean_sheet: the break time is memoryless, so P(no) = 1 − exp(−λ·minutes left), or 1 once a break is seen.
- heathrow_stack: P(over) = 1 − F_Poisson(⌊line⌋; λ), with λ updated by each reading (`CountSim.posterior`). The
  line is the half point whose exact P(over) is nearest 50% (`poisson_line`); before, it was placed on the 50 draws.
- hub_hour: from the hubs' state x at the open, or at the newest reading that has all five, each hub's log vs usual
  at settle is N(a_j, s_j²), with a_j = x_j + μ_j·r − ln typ_j and s_j = σ_j·0.9887·√r for r minutes left. Then
  P(j wins) = ∫ φ(z) Π_{l≠j} Φ((a_j + s_j z − a_l)/s_l) dz, by the trapezoid rule on a 0.25 grid over ±6. This is
  within 7e-10 of a 0.01 grid, and within 1.04 SE of 20,000 simulated futures. A reading missing a hub falls back to
  the weighted futures, each counted with this integral from its own path (Rao-Blackwellised).
- Markets with any futures sampled from the city simulation (`SIM_WEIGHT` > 0) keep the futures' share.

**Monte Carlo error at the open.** Each market was rebuilt 20 times from the same readings with salted seeds at 16
opens (`analysis/judge/mc_error.py <raw> 20 [N]`). The table gives the SD of the opening price across seeds, in
points, for the option that moves most:

| Series | N=50 before | N=200 | N=500 | N=1000 | N=50 now |
|---|---|---|---|---|---|
| city_pulse | 8.4 (max 19.3) | 6.0 | 3.4 | 2.8 | 8.4 (5.7 with the line held fixed) |
| hub_surge | 3.4 (max 3.8) | 2.1 | 1.1 | 0.8 | 3.4 |
| heathrow_stack | 5.9 (max 9.4) | 4.4 | 2.7 | 2.0 | **0.0** |
| clean_sheet | 5.4 | 3.0 | 2.4 | 1.4 | **0.0** |
| hub_hour | 6.9 | 3.4 | 2.2 | 2.0 | **0.0** |

Before, heathrow_stack's error came entirely from the line: with the line held fixed its share had 0 error, but the
line moved with the draws (SD 0.39 aircraft). city_pulse's line also moves with the draws (SD 0.15 points).

**Why not more futures.** One build costs 1 to 76 ms at N=1000, so CPU in production is fine. But N=500 took the test
suite from 28 s to 72 s, broke 9 tests that assume 50, and grows `/markets` by N×12 points per market in the fan
chart. The particle count also feeds hub_surge's tempering power (0.5, Platt-fitted on 50 futures). Closed forms
remove the error where they exist; more futures cut it only as 1/√N.

**Effect on scores** (`analysis/judge/replay_score.py`, same window, Brier / log loss):

| Series | n | Open, before → now | Close Brier, before → now |
|---|---|---|---|
| hub_hour | 10 | 0.989 / 2.012 → 0.974 / 1.821 | 0.893 → **0.536** |
| clean_sheet | 11 | 0.572 / 0.766 → 0.564 / 0.759 | 0.538 → 0.532 |
| heathrow_stack, same lines | 136 | 0.476 / 0.671 → 0.475 / 0.667 | 0.426 → 0.423 |
| heathrow_stack, exact line | 136 | 0.493 / 0.686 (different questions: each opens nearer 50/50) | 0.435 |

city_pulse, hub_surge and london_vix are unchanged.

## 2. The LMSR (`Book`)

**Cost function.** C(q) = b · ln Σᵢ mᵢ · e^{qᵢ/b}, with b = 1200 and q the shares sold per option. The price is
pᵢ = mᵢ e^{qᵢ/b} / Σ mⱼ e^{qⱼ/b}. This is Hanson's LMSR seeded at the prior m, and C(0) = 0. A stake s buys δ
shares with C(q + δeᵢ) − C(q) = s(1 − f), with f = 5%:

δ = b · ln(1 + (e^{s(1−f)/b} − 1)/pᵢ)

It is computed in log space (log-sum-exp, expm1/log1p). The old `exp(q/b)` overflowed past q ≈ 850,000 shares.

**Payout.** Locked odds = min(20, ⌊δ/s⌋ to 0.01), and the bet is refused under 1.05. The ledger pays ⌊s · odds⌋. So a
payout is never more than δ, the shares bought.

**Worst-case loss.** With m fixed, the house's loss if j wins is at most

L_j ≤ b · ln(1/m_j) − f · Σ stakes

That is 832 for a 50/50 binary (b ln 2), 1,931 for a uniform 5-way race (b ln 5), and 4,208 at the 3% floor. The
bound is tight on shares: buying j without limit takes the loss to b ln(1/m_j) within 1e-6. With the 20× odds cap
and rounding, the worst of 3,000 random sequences on uniform priors reached 0.88 · b ln n.

**When data moves m under a fixed q** (the futures reweight mid-round, see section 3), the bound becomes

L_j ≤ b · ln(1/m_end,j) + Σ_updates b · ln E_{p}[m′/m] − f · Σ stakes

Here p is the book's price at the update. The term is 0 when the book is flat (p = m, E_m[m′/m] = 1). It is positive
when news agrees with the crowd's side. The derivation telescopes C over the updates. Tested over 1,000 random
sequences, and the fixed-prior bound alone was breached in some of them.

**Book resets.** `integrity.suspend` replaces the book (q = 0) after a big feed move, so each reset grants a fresh
b · ln(1/m_j). In the test, four books on one 50/50 market lost 2,360, which is 2.8 × the single-book 832. What bounds this is the per-player
position cap (25%, or 10% on local series) and the ledger's exposure cap, which counts locked payouts exactly.

**Why fixed b.** Over 451 markets in the replay, the AI traders staked 81 points per market on average (max 200). The
largest |q|/b at settle averaged 0.09 (max 0.72), and the price ended within 1.2 points of the model on average. A
liquidity-sensitive LMSR (Othman et al. 2013) sets b = α·Σq. At these volumes that makes the opening book nearly
illiquid, or else α is so large that b is fixed in practice. Fixed b gives 3.8 points of impact per 100 staked on a
50/50, and a known worst case per market. Revisit it when per-market volume reaches multiples of b.

## 3. How they connect, and what can be gamed

Price = LMSR(m, q), and m = `tops.tilted(Futures.probs())`. A reading moves m and leaves q, so the price moves with no
trade. A quote is never stored: `Exchange.book` re-quotes inside the ledger transaction at fill time, and a pending
bet (delay 2 to 45 s by series, `integrity.py`) fills at the price then.

Adversarial tests against a local Exchange (`AdversaryTest`, hub_surge: 5 s delay, 10% suspend):
- A stale quote can't be filled. A bet placed before a +4% reading fills at the post-reading odds, which are shorter.
- In a sandwich, a bet placed just before a +15% move is cancelled by the suspension, and no points move.
- **Known limit.** A reading the exchange ingests after the fill leaves the fill at the old price. The delay only
  covers readings ingested within it, and TfL stamps a reading 228 to 423 s (p10 to p90) before the recorder polls it
  (`crowd_latency.py`). A bot that reads TfL or adsb.lol directly and backs the option the next reading favours makes
  (`analysis/judge/stale_edge.py`, 10-point bets at the stale quote vs the same pick after ingest, bootstrap 95% CI):

| Series | Bets | ROI at the stale quote | ROI after ingest |
|---|---|---|---|
| hub_surge | 118 | +129% [+81%, +176%] | +10% [−13%, +33%] |
| city_pulse | 100 | +30% [+11%, +50%] | −2% [−16%, +13%] |
| heathrow_stack | 133 | +33% [+12%, +57%] | −5% [−18%, +9%] |
| hub_hour | 10 | +343% [+34%, +737%] | +148% [−9%, +322%] |

This assumes TfL serves a reading from its own stamp, which is the worst case. The book can't fix this. The fix is to
ingest within the delay: poll TfL Crowding about every 30 s, and set each crowding series' delay to at least the poll
interval. The alternative is to hold a fill until the reading due before the bet was placed has been ingested.

## 4. Invariants and their tests (`test_pricing_props.py`, fixed seeds, 2,000 cases each)

| Invariant | Test |
|---|---|
| Prices sum to 1 and lie in [0, 1], for q up to ±1e9 | `test_prices_sum_to_one_and_each_is_a_probability` |
| A bet moves C by exactly s(1 − f) | `test_a_bet_costs_exactly_its_stake_net_of_the_fee_on_the_cost_function` |
| Path independence: any order, or all at once, costs C(q_end) − C(0) | `test_cost_is_path_independent` |
| L_j ≤ b ln(1/m_j) − f·Σs, and ≤ b ln n on uniform priors | `test_house_loss_never_exceeds_b_ln_one_over_the_winners_prior_less_the_fees` |
| The bound is tight on shares | `test_the_bound_is_tight_on_shares_without_the_odds_cap` |
| No bundle of bets over all options locks a profit | `test_no_bundle_of_bets_locks_a_profit` |
| Payout ≤ shares and ≤ 20 × stake; refused under 1.05 | `test_a_locked_payout_never_exceeds_the_shares_bought` |
| Finite and monotone at extreme q and stakes up to 1e12 | `test_extreme_positions_and_stakes_stay_finite_and_monotone` |
| Moving-prior bound, and that its extra term is needed | `test_a_moving_model_adds_b_ln_of_the_expected_likelihood_ratio_per_update` |
| Each book reset grants a fresh bound | `test_each_book_reset_grants_a_fresh_loss_bound` |
| Exact hub_hour / Poisson / exponential probabilities match simulation | `ExactProbabilityTest` |
| Same market id gives the same futures; heathrow's open is independent of the seed | `SeedTest` |
| Stale quote, sandwich, ingest-lag limit | `AdversaryTest` |

There is no sell, so there is no buy-then-sell round trip. Its equivalent is the bundle test. Mutating the fee to 0,
or overpaying shares by 2%, fails 3 of the LMSR tests.

## 5. Known limits

- **city_pulse** still has 8.4 points of Monte Carlo error at the open. 5.7 of that comes from sampling and the rest
  from the line moving with the draws. The fix is this document's recipe applied to `ScalarSim`: an analytic
  P(over) = Φ((x + μ·r − ln line)/(0.9887·σ√r)), conditioned on the newest reading, with the line at the analytic
  median. It is left to the owner of the city_pulse sim.
- **hub_surge** (3.4 points) is tempered at p ∝ share^0.5, so its error is already halved. Its restart is a fresh
  50-path draw, not a closed form.
- The generic filter's likelihood (1.5σ, an L∞ error across hubs, a 60% removal cap) is a heuristic. It is tempered
  on 29% of city_pulse updates.
- The jump halt (`markets.jump_of`) compares each reading only with the previous in-round reading. The first reading
  after the open is never checked, and in a 5-minute round that is usually the only one. The integrity scan's 10%
  suspension still covers it in steady state. On a cold start, the scan's first baseline is the newest reading.
- AI bets settle at `round(stake × odds)`, which can exceed the shares by up to 0.5 point. They are outside the house
  account and the exposure cap.
- Latency is the main open risk (section 3).
