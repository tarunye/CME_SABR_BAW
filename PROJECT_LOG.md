# PROJECT LOG — SPY Options VaR (SR-FICC-2013-02 replication)

Running record of what has been built, decided, and deferred. Append-only: each phase adds
a section, nothing earlier gets rewritten. If you are picking this project up cold, read
this file top to bottom and you will know exactly where things stand.

**Current status: Phase 1 complete. Awaiting approval for Phase 2.**

---

## The project in one paragraph

We are replicating the options margining methodology from the 2013 FICC/NYPC SEC filing
SR-FICC-2013-02, applied to SPY options instead of the filing's interest rate futures
options. Three pillars: 99% historical-simulation VaR with linear interpolation between
order statistics, Barone-Adesi & Whaley for American option pricing, and SABR for the
implied volatility curve. The binding constraint is that **every historical scenario gets
a fresh SABR vol and a fresh BAW reprice — full revaluation, never a delta/gamma
approximation.**

Built in nine phases (0-8), one at a time, stopping for written approval after each.

---

## File map

| File | Responsibility | Added in |
|---|---|---|
| `data_loader.py` | Load and validate SPY prices and option chains; NYSE calendar; forward from put-call parity; OTM smile construction | Phase 0 |
| `sabr.py` | Hagan et al. (2002) implied vol expansion and least-squares calibration | Phase 1 |
| `baw.py` | Barone-Adesi & Whaley American pricer, European baseline, critical-price solver, binomial reference, and the validation suite | Phase 2 |
| `historical_sim.py` | Daily returns, scenario generation, return-sample statistics, tail and lookback-window diagnostics | Phase 4 |
| `plots.py` | One function per figure, all saving to `outputs/figures/` | Phase 0 |
| `main.py` | `CONFIG` block (every assumption, with sources) and the phase runners | Phase 0 |
| `data/spy_eod_YYYY.parquet` | Raw vendor data, 2010-2023 (supplied, not generated) | — |
| `data/*.csv` | Cached derived pulls, regenerated if deleted | Phase 0 |
| `outputs/figures/` | All `.png` output | Phase 0 |
| `outputs/tables/` | All `.csv` output | Phase 0 |

**Run everything with `python3 main.py`.** Individual modules with a standalone block can
also be run alone: `python3 sabr.py`, `python3 baw.py` (the latter takes ~30s — it runs
thousands of binomial lattices — and is not part of the pipeline).

---

## Reference configuration

All of this lives in the `CONFIG` dict at the top of `main.py`.

| Quantity | Value | Source |
|---|---|---|
| Reference date | 2023-12-29 | Last quote date in the dataset |
| Reference expiry | 2024-02-16 | Standard monthly, 49 DTE, 156 strikes, deeply liquid |
| SPY spot | $475.31 | `UNDERLYING_LAST`, 16:00 stamp |
| Time to expiry | 0.13425 yr | 49 calendar days, ACT/365 |
| Risk-free rate | 5.4000% | FRED DGS3MO (3m Treasury CMT), 2023-12-29 — **assumed** |
| Implied forward | $478.892 | **Derived** from put-call parity, 22 strikes, std $0.203 |
| Cost of carry `b` | 5.5926% | **Derived**, from `F = S·exp(b·T)` |
| Implied dividend yield | −0.1926% | **Derived**, `q = r − b`. Near zero, correctly — see below |
| Lookback | 250 trading days | Filing specifies one year |
| VaR window | 2022-12-30 to 2023-12-29 | The 250 days ending on the reference date |
| SABR beta | 1.0 (fixed) | Equity index convention — see Phase 1 |
| Calibration range | \|moneyness\| ≤ 15% | 103 strikes — see Phase 1 |
| Volatility source | Re-implied from mid prices | **Phase 3** — vendor IVs superseded |
| SABR alpha | 0.109797 | Phase 3 calibration |
| SABR rho | −0.524225 | Phase 3 calibration |
| SABR nu | 2.132810 | Phase 3 calibration |

---

## Data source

**OptionsDX SPY end-of-day option chains, 2010-2023**, 14 parquet files supplied in
`data/`. Confirmed with the user in preference to yfinance (no history) or paid vendors
(CBOE DataShop, ORATS, OptionMetrics, Polygon). One row per (quote date, expiry, strike)
carrying both the call and put leg. Raw column names are wrapped in square brackets
(`[QUOTE_DATE]`) — a vendor quirk stripped on load.

Two consequences worth remembering:

- The SPY price series comes from `UNDERLYING_LAST` in these same files, not a separate
  equity feed. This keeps spot synchronous with the option quotes it is paired against.
- We have **14 years of daily volatility surfaces**, which means the Phase 5 vol-shock
  extension (jointly shocking spot and SABR parameters from the same historical day) is
  buildable. The brief assumed we would not have this. Not built — see open questions.

---

## PHASE 0 — Data sourcing and validation ✅ approved

### What was built

`data_loader.py`, `plots.py` (one function), `main.py` (CONFIG + Phase 0 runner).

Loads the SPY price series and the option chain snapshot, caches both to CSV in `data/`,
validates them, derives the forward from put-call parity, builds the out-of-the-money
implied volatility smile, and plots it.

### Key results

- Price history: 506 trading days, 2022-01-03 to 2023-12-29, $356.58 to $477.77.
- Chain snapshot: 4,143 rows, 34 expiries. Reference expiry has 156 strikes, $210-$575.
- Smile after OTM and quality filters: **151 strikes** (112 puts, 39 calls), IV 9.94% to
  64.20%.
- Forward implied from 22 liquid strikes with a standard deviation of $0.203 (~4bp of
  spot). That tightness is the evidence the method worked.
- `outputs/figures/vol_smile_raw.png` shows a textbook equity index skew: monotone decline
  from 64% on the far left wing to a 9.9% minimum near the 500-505 strikes, then turning
  up through the call wing. Put and call legs join seamlessly at the forward (11.2% /
  11.7%), confirming the OTM stitching is correct.

### Vendor data defect found (important)

**The 2022 file's date stamping is broken.**

- 4 real NYSE trading days are absent: 2022-05-11, 06-17, 06-24, 12-16.
- 9 rows are stamped on days the NYSE was **closed**: 2022-01-17, 02-21, 04-15, 05-30,
  06-20, 07-04, 09-05, 11-24, 12-26.

These damage the return sample in opposite directions. A missing day compresses two days
of movement into one return, manufacturing an outlier that never happened — 2022-12-15 to
2022-12-19 appears as a single −2.49% move when it was really −1.65% then −0.85%. A
phantom row on a closed market carries a stale price forward and injects a spurious
near-zero return, diluting the tail instead.

**Zero defects fall inside the 250-day VaR window.** The 2023 file is clean and the window
starts 2022-12-30. `validate_price_history` now checks this on every run against a derived
NYSE calendar rather than assuming it.

### Decisions taken

1. **Data source**: OptionsDX parquet for both the option chain and the price series.
2. **Reference date/expiry**: 2023-12-29 / 2024-02-16.
3. **Rate and carry**: pin `r` externally from the Treasury yield, then **imply the
   forward from put-call parity** and derive `b` and `q` from it. The forward is what SABR
   needs, and this makes it consistent with the quotes we are fitting.
4. **Smile construction**: out-of-the-money quotes only, split at the **forward** rather
   than spot. The in-the-money leg is illiquid and its vendor IV is unreliable — at the
   405 strike the deep-ITM call shows 27.2% against 23.2% for the liquid OTM put at the
   same strike.

### Why the dividend yield is ~zero and why that is right

SPY's December 2023 ex-dividend date was the 15th; the next is 2024-03-15. **No dividend
falls between 2023-12-29 and 2024-02-16.** Plugging in SPY's ~1.4% trailing yield would
have mispriced the forward by about $0.90 — roughly four strike increments — and tilted
the entire smile. The near-zero implied yield is independent corroboration that the 5.40%
rate assumption is sound.

---

## PHASE 1 — SABR calibration ✅ complete, awaiting approval

### What was built

`sabr.py`, containing the Hagan et al. (2002) lognormal implied volatility expansion and a
least-squares calibration of `alpha`, `rho`, `nu` to the observed smile. Plus
`plot_sabr_fit` in `plots.py` and a Phase 1 runner in `main.py`.

Runnable standalone: `python3 sabr.py`.

### Key results

Calibrated to 103 strikes ($408-$550, |moneyness| ≤ 15%):

| Parameter | Value | Reading |
|---|---|---|
| `alpha` | 0.109922 | Vol level. At beta=1 this is close to the ATM vol, so ~11.0% |
| `beta` | 1.0 | **Fixed**, not fitted |
| `rho` | −0.510842 | Negative as an equity index should be — vol rises when SPY falls |
| `nu` | 2.103836 | Vol-of-vol; sets smile curvature |

RMSE **0.2946 vol points**, max abs error 0.6378. ATM skew −0.5461 vol points per 1% move
in strike (negative = downward skew, correct for an equity index).

### The residuals are a data problem, not a model problem

This is the main finding of Phase 1 and it matters for how much to trust Phase 3 onward.

**The vendor's put and call implied vols are mutually inconsistent.** Put-call parity says
a put and a call at the same strike must carry the same IV. Ours do not: the put wing ends
at K=$476 heading for 10.91%, and the call wing opens at K=$479 at 11.67% — a **+0.76 vol
point step** at the crossover. No smooth curve can fit both wings, so the optimiser splits
the difference. That is exactly the residual pattern in `sabr_fit.png`: puts sitting at a
consistent +0.25 vol points, then a sharp drop to −0.6 right at the forward.

Fitting each wing on its own confirms it:

| Fit | Strikes | RMSE | Data noise floor | `rho` |
|---|---|---|---|---|
| Both wings | 103 | 0.2946 | 0.1140 | −0.5108 |
| Puts only | 69 | **0.0751** | 0.0501 | −0.2578 |
| Calls only | 34 | **0.2036** | 0.1851 | −0.6008 |

Each wing individually fits to **its own noise floor**. The combined RMSE is worse than
either because the two wings disagree with each other. The "noise floor" column is the RMS
strike-to-strike jitter in the quoted vols themselves (`data_loader.smile_local_roughness`)
— a true smile is smooth, so that jitter is quote noise, and a model fitting to it has done
as well as anything possibly could.

Note the call wing is 3.7× noisier than the put wing (0.185 vs 0.050 vol points). SPY put
quotes are simply better maintained than call quotes at end of day.

### Sensitivity, and the identification problem made visible

Calibration range (beta fixed at 1.0):

| Band | Strikes | RMSE | Max err | `alpha` | `rho` | `nu` |
|---|---|---|---|---|---|---|
| 5% | 46 | 0.2040 | 0.4303 | 0.1126 | −0.5097 | 1.7349 |
| 10% | 74 | 0.2349 | 0.4926 | 0.1117 | −0.5198 | 1.9425 |
| **15%** | **103** | **0.2946** | **0.6378** | **0.1099** | **−0.5108** | **2.1038** |
| 20% | 118 | 0.3602 | 0.9800 | 0.1086 | −0.5033 | 2.1887 |
| 30% | 128 | 0.5532 | 1.6850 | 0.1057 | −0.4985 | 2.3326 |
| 100% | 151 | 0.8116 | 2.5150 | 0.0991 | −0.4784 | 2.5841 |

RMSE degrades smoothly as the wings are included — 2.8× worse at the full smile — while
`rho` stays remarkably stable at −0.48 to −0.52. The skew is well determined; it is the
wings the model cannot reach.

Beta (band fixed at 15%) — **this is the reason beta is not fitted**:

| `beta` | RMSE | `alpha` | `rho` | `nu` |
|---|---|---|---|---|
| 0.00 | 0.2548 | 52.3342 | −0.4488 | 2.0076 |
| 0.30 | 0.2653 | 8.2298 | −0.4681 | 2.0352 |
| 0.50 | 0.2730 | 2.3979 | −0.4807 | 2.0542 |
| 0.70 | 0.2813 | 0.6987 | −0.4929 | 2.0737 |
| **1.00** | **0.2946** | **0.1099** | **−0.5108** | **2.1038** |

RMSE moves by 0.04 vol points across the entire range of beta — nothing — while `alpha`
swings by a factor of 476 and `rho` slides to compensate. This is the flat valley described
in `calibrate_sabr`: **the data cannot tell beta and rho apart.** Fitting them jointly
would produce parameters that jump around day to day while the fit quality never improves.

### Two bugs found and fixed during the phase

1. **`alpha` upper bound did not scale with beta.** At beta=1 alpha is a proportional vol
   (~0.11); at beta=0 the model is normal and alpha is an *absolute* vol in dollars (~52).
   A fixed bound of 5.0 made the beta=0 fit throw `Initial guess is outside of provided
   bounds`. The bound is now `5.0 * forward**(1-beta)` and seeds are clipped inside it.
2. **The ATM continuity check was measuring the real smile slope**, not a discontinuity,
   because it probed strikes 0.1% away from the forward where the skew genuinely moves vol
   by 0.055 vol points. It now probes within a few multiples of the 1e-7 tolerance and
   subtracts the slope contribution computed from the model's own skew. Result: observed
   gap 5.515e-04 vol points, explained by skew 5.514e-04, **unexplained excess 4.5e-08** —
   floating point noise. The branch is clean.

### Decisions taken

1. **`beta` fixed at 1.0, not fitted.** `beta` and `rho` are close to jointly
   unidentifiable — both control skew and trade off against each other, so a joint fit
   wanders along a flat valley. 1.0 is the equity index convention: it makes the model
   lognormal, matching how equity vols are quoted, and leaves `rho` as the clean skew
   driver. Configurable via `CONFIG["sabr_beta"]`.
2. **Calibration range `|moneyness| <= 15%`.** Two reasons the full 151-strike smile is
   the wrong fit target: Hagan's formula is an *asymptotic* expansion that degrades far
   from the money, and 35 of the 151 strikes have a mid below $0.10 with a median relative
   spread of 40% — quote noise that would carry equal weight in an unweighted fit and drag
   the at-the-money region off. Configurable via `CONFIG["calibration_moneyness_band"]`.
3. **ATM singularity handled by an explicit branch** on `|log(F/K)|` below a tolerance,
   using the closed-form ATM limit. Flagged `# NOTE (approximation)` in the code and
   verified continuous across the branch boundary.
4. **Scalar function plus an explicit loop wrapper**, not a vectorised implementation.
   The brief prioritises readability, and the `z/x(z)` branch is far clearer as a plain
   `if`. Performance is irrelevant at this scale.

---

## Decisions made (previously open)

All resolved by the user after Phase 1:

1. **Re-imply IVs ourselves in Phase 3**, using our parity forward. ✅ **Done in Phase 3.**
   RMSE fell 0.2946 → 0.2346 and the crossover gap changed from the wrong sign to the
   right one. It did not reach the 0.075 the put wing achieves alone, because a residual
   put/call inconsistency remains — see Phase 3 above for the diagnosis.
2. **Keep `|moneyness| <= 15%` and `beta = 1.0`.** ✅ Retained in CONFIG.
3. **No vol-shock extension.** ✅ Phase 5 will hold the calibrated SABR parameters fixed
   and re-evaluate at the new forward (sticky-moneyness).
4. **Phase 5 reference option is a PUT.** ✅ Confirmed by Phase 2: the call has exactly
   zero early-exercise value in this regime (`b > r`), so a call would exercise none of
   the American machinery. The put has a real premium of $0.29 on a $6.47 price.
5. **Leave the 2022 data loaded and flagged.** ✅ `price_history_start` stays 2022-01-03.
   The validator reports the defects on every run and confirms zero fall inside the VaR
   window.

## Standing open questions

1. **Is `plots.py` in the right place?** Created in Phase 0 rather than Phase 8, because
   Phase 0 requires a figure and the brief names `plots.py` as the home for figures. It
   holds only real functions, no stubs.
2. **Does the ~1% BAW-induced P&L error need addressing?** Quantified in Phase 2 (test 4c).
   The filing specifies BAW, so this is the intended methodology and I would leave it. But
   if you want a tighter VaR, `binomial_american_price` already exists and could be swapped
   in — at roughly 1000× the runtime, which would make the 250-scenario loop take minutes
   rather than a second. Flagging it as a known, measured limitation for the README.

---

## Consolidated assumptions and deviations

Every `# NOTE` comment in the codebase, collected. This list is the seed for the README in
Phase 8.

| # | Type | Where | Substance |
|---|---|---|---|
| 1 | approximation | `data_loader.load_spy_price_history` | `UNDERLYING_LAST` is the **unadjusted** SPY price, so SPY's four annual ex-dividend dates appear as spurious ~0.4% down-moves in the return sample. Accepted: the option contracts are written on the unadjusted price, so shocking it is internally consistent, and synchronicity with the option quotes is worth more than removing four small dips. |
| 2 | approximation | `data_loader.year_fraction_to_expiry` | ACT/365 day count. ACT/252 and ACT/365.25 are the alternatives; at 49 days the difference is well under a vol point and is absorbed by the calibration. What matters is using the same `T` everywhere. |
| 3 | approximation | `data_loader.imply_forward_from_parity` | Put-call parity holds exactly only for **European** options and SPY options are American, so the American put's early-exercise premium biases the implied forward slightly low. At 49 days near the money that premium is a cent or two, inside the bid-ask noise. |
| 4 | deviation | `main.CONFIG` | Dividend yield is **derived from the quotes**, not assumed from SPY's trailing yield. Deliberate — no ex-dividend date falls inside this window, so the trailing yield would be simply wrong here. |
| 5 | approximation | `data_loader.build_otm_smile` | Smile built from **out-of-the-money quotes only**, split at the forward rather than spot. The ITM leg is illiquid and its vendor IV is unreliable. |
| 6 | approximation | `data_loader.nyse_trading_days` | The derived NYSE calendar ignores one-off closures (state funerals, Hurricane Sandy). Those surface as "missing" days for a human to judge, which is the right outcome. |
| 7 | approximation | `sabr.sabr_implied_vol` | The `z/x(z)` factor is `0/0` at the money. Handled by an explicit branch to the closed-form ATM limit below a tolerance on `\|log(F/K)\|`. |
| 8 | deviation | `sabr.calibrate_sabr` | `beta` is **fixed at 1.0, not fitted**, because it is close to jointly unidentifiable with `rho`. The filing does not specify a value. |
| 9 | approximation | `sabr.calibrate_sabr` | Calibration restricted to `\|moneyness\| <= 15%`; the far wings are penny quotes and lie outside the range where Hagan's asymptotic expansion is reliable. |
| 10 | deviation | `baw` module docstring | The filing prices options on interest rate **futures**, where `b = 0` (Black-76). SPY is a dividend-paying equity ETF, so `b = r - q`. We implement the general `b` form and derive `b` from the market rather than assuming a dividend yield. |
| 11 | deviation | `baw.baw_price` | `cost_of_carry` is a **required** argument with no SPY default, contrary to the brief's suggestion. A hardcoded default would conflict with the derived `b = 5.5926%`, and a silently wrong carry produces plausible-looking wrong prices. |
| 12 | deviation | `baw.check_against_binomial` | No literature benchmark table transcribed. Reference is a convergent CRR binomial lattice instead — the same methodology BAW used to assess their own approximation — because unverifiable reference numbers would validate the pricer against fiction. |
| 13 | approximation | `baw.check_spy_regime_accuracy` | BAW's put error changes sign with moneyness: it understates the ATM premium (~95% recovered) and overstates it OTM (>2×). Consequently it does **not** cancel in scenario P&Ls but amplifies ~5.5×, contributing **roughly 1% error to the Phase 6 VaR**. |
| 14 | deviation | `main.reimply_smile_volatilities` | Implied vols are **re-implied by us** from OTM mid prices by inverting BAW against the parity forward, replacing the vendor's. Their put and call IVs are mutually inconsistent — the vendor smile is non-monotone at the crossover. |
| 15 | approximation | `main.run_phase_3` | A residual put/call inconsistency remains after re-implying (adjacent gap −0.209 vs −0.058 predicted by the local slope). De-Americanising the parity forward explains only ~20% of it; the rest is most likely that the parity forward is computed from both legs, one of which is always the illiquid ITM one. |
| 16 | deviation | `baw.baw_vega` | Vega is computed and used **for error attribution only**, never to price a scenario. The filing forbids Greeks-based approximation of scenario prices and Phase 5 will fully reprice. |
| 17 | approximation | `data_loader.put_call_crossover_step` | The extrapolated crossover step is unreliable on this date because crossed quotes at strikes 477 and 478 leave a $3 hole at the forward. The robust `adjacent_gap` is reported alongside it and is what conclusions are drawn from. |
| 18 | approximation | `historical_sim.compute_daily_returns` | **Simple** returns, not log returns, because they are exactly what `S * (1 + r)` needs. Differ by second order; 2bp on this sample's worst day. |
| 19 | deviation | `historical_sim` module docstring | The simulation is **unweighted**, matching the filing. Every day counts equally despite volatility clustering. No exponential weighting, no filtered historical simulation. |
| 20 | approximation | `main.run_phase_4` | The lookback-window comparison years are context only and are **not** put through the Phase 0 calendar validation; vendor data quality varies by year. Our 2023 window is clean. |
| 21 | limitation | Phase 4 result | **Our 250-day window contains no crisis.** Excess kurtosis is −0.18 (thinner tails than normal), no day exceeded 3σ, and the 1st percentile is −1.64% against −6.76% for a 2020-ending window — **4.1× smaller**. The VaR will be benign as a direct consequence of the window, not of the position. |
| 22 | deviation | `main.revalue_across_scenarios` | **Time to expiry held FIXED** at 49 days rather than decremented. Excludes theta, which would shift every scenario down by roughly the same amount and inflate the VaR regardless of market direction. Isolates pure market risk. |
| 23 | deviation | `main.revalue_across_scenarios` | **Sticky-moneyness**: SABR parameters held fixed and re-evaluated at the new forward. Our strike's vol **rises when the market rallies** (K/F falls, sliding it into the steep left wing). Dampens P&L both ways and is the **less conservative** convention for a long put. |
| 24 | deviation | `main.revalue_across_scenarios` | **No vol-surface shock.** Scenario P&Ls contain spot risk and the smile's response to spot, but no independent volatility risk. Declined by the user; the historical surfaces to build it do exist. |
| 25 | deviation | `main.taylor_approximation_counterfactual` | Deliberately computes the delta/gamma shortcut the filing forbids, **for demonstration only**, never for a risk number. |
| 26 | approximation | `main.run_phase_5` | The stress-window run keeps **today's** volatility surface and changes only the return distribution. Since a long put is long vega and loses on rallies, this **overstates** its loss tail relative to a true joint replay of 2020. |

---

## Process rules (from the brief)

- One phase at a time. Full stop after each, awaiting **"approved, continue to Phase N+1"**.
- Every phase leaves the repo runnable — no broken imports, no `NotImplementedError` stubs.
- No refactoring of an approved phase without asking first.
- Flat structure, no phase-named folders, no deep nesting, no version numbers in filenames.
- Plain functions on plain data. No classes without agreement, no metaclasses, no ABCs.
- Every function gets a docstring with units; every non-trivial block gets a comment
  explaining the *finance*, not the syntax. Flag approximations with `# NOTE`.
- **Full revaluation always.** Never `price + delta*dS + 0.5*gamma*dS²`.
