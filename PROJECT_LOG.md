# PROJECT LOG — SPY Options VaR (SR-FICC-2013-02 replication)

Running record of what has been built, decided, and deferred. Append-only: each phase adds
a section, nothing earlier gets rewritten. If you are picking this project up cold, read
this file top to bottom and you will know exactly where things stand.

**Current status: COMPLETE. Phases 0-6 and 8 built; Phase 7 skipped by choice.
Phase 9 adds an independent cross-vendor validation of the reference date against
Databento — no pipeline result changed.**

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
| `var.py` | 99% VaR by explicit interpolation between order statistics, expected shortfall, and the rank-convention comparison | Phase 6 |
| `plots.py` | One function per figure, all saving to `outputs/figures/` | Phase 0 |
| `requirements.txt` | Pinned dependency versions | Phase 8 |
| `README.md` | Project overview, data sourcing, figure guide, consolidated assumptions, limitations | Phase 8 |
| `main.py` | `CONFIG` block (every assumption, with sources) and the phase runners | Phase 0 |
| `data/spy_eod_YYYY.parquet` | Raw vendor data, 2010-2023 (supplied, not generated) | — |
| `data/*.csv` | Cached derived pulls, regenerated if deleted | Phase 0 |
| `outputs/figures/` | All `.png` output | Phase 0 |
| `outputs/tables/` | All `.csv` output | Phase 0 |

**Run everything with `python3 main.py`.** Individual modules with a standalone block can
also be run alone: `python3 sabr.py`, `python3 historical_sim.py`, `python3 var.py`, and
`python3 baw.py` (the last takes ~30s — it runs thousands of binomial lattices — and is
not part of the pipeline).

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

## PHASE 2 — BAW pricing engine ✅ complete, awaiting approval

### What was built

`baw.py`. No SABR involvement — volatility is just an input argument in this phase.

- `black_scholes_price` — generalised Black-Scholes-Merton with cost of carry `b`, so it
  doubles as Black-76 when `b = 0`.
- `baw_price` — the quadratic approximation: European price plus early-exercise premium.
- `solve_critical_price` — Newton-Raphson from the BAW seed for the exercise boundary,
  with an iteration cap and a hard error on non-convergence.
- `binomial_american_price` — a Cox-Ross-Rubinstein lattice. **Validation reference only**,
  never called by the pricing pipeline.
- Nine validation checks, run by `python3 baw.py`.

`main.py` is unchanged: the pricer is not wired into the pipeline until Phase 3.

### Validation results — all nine pass

| Test | Result |
|---|---|
| 1. European put-call parity | Worst error **2.8e-14** across 6 cases |
| 2. American call = European when `b = r` | Worst spurious premium **exactly 0.0** across 5 cases |
| 3. American ≥ European and ≥ intrinsic | **0 violations in 1,350** grid points |
| 4. BAW vs binomial, stress grid | Worst abs error **$0.0599** (tol $0.10); worst rel 2.01% |
| 4b. BAW vs binomial, SPY regime | Worst abs error **$0.1439** (tol $0.20) |
| 4c. P&L error propagation | Informational — see below |
| 5. Monotonicity | 10 sequences, all correct |
| 6. Critical exercise prices | Correct side of strike, move away from it as vol rises |
| 7. SPY reference case | Informational |

Test 2 is the sharpest test of a BAW implementation and it passes to *exactly* zero, not
merely to tolerance.

### BAW's approximation error, measured honestly

This is the phase's most important finding and it constrains everything downstream.

**Calls are exact in our regime.** With `b = 5.5926% > r = 5.4000%`, early exercise on a
call is never optimal, `baw_price` returns the European price, and the lattice agrees to
0.02%. There is no premium to get wrong.

**Puts carry real error, with a sign that flips with moneyness.** At our reference strike:

| Strike | True premium | BAW premium | BAW price | Lattice | Abs err | Rel err |
|---|---|---|---|---|---|---|
| 450 (OTM) | $0.0167 | $0.0362 | 0.635978 | 0.616456 | 0.0195 | **3.17%** |
| 475 (ATM) | $0.3069 | $0.2914 | 6.467076 | 6.482558 | 0.0155 | 0.24% |
| 500 (ITM) | — | — | 24.690000 | 24.702464 | 0.0125 | 0.05% |

BAW **understates** the ATM put premium (recovering ~95%) and **overstates** it out of the
money (by more than 2×). The absolute errors are small; the OTM relative error is large
only because the denominator is small.

**The error does not cancel in the P&L — it amplifies.** I assumed it would largely cancel
in the scenario-minus-base subtraction, measured it, and was wrong:

| Shock | BAW P&L | True P&L | P&L error | % of P&L |
|---|---|---|---|---|
| −3% | 8.558949 | 8.644133 | −0.085185 | 0.985% |
| −2% | 5.176196 | 5.236558 | −0.060362 | 1.153% |
| −1% | 2.330060 | 2.358205 | −0.028145 | **1.193%** |
| +1% | −1.849299 | −1.869590 | +0.020290 | 1.085% |
| +3% | −4.319769 | −4.358010 | +0.038241 | 0.877% |

Worst P&L error $0.0852 against a price-level error of $0.0155 — **5.5× larger in the P&L
than in the price**, because the error varies with spot and changes sign with moneyness.

**Practical consequence: expect roughly 1% error in the scenario P&Ls, and therefore in the
Phase 6 VaR, purely from using BAW rather than a lattice.** That is acceptable for a
methodology replication — and the filing specifies BAW, so it is the intended behaviour —
but it is a real precision floor and should be stated in the Phase 8 README.

### A note on how the tolerances were set

I initially gated tests 4 and 4b on *relative* error and they failed. Rather than simply
loosening the threshold until they passed, I verified against a 40,000-step lattice that
the errors were BAW's documented behaviour and not a bug: the error is smallest at the
money, peaks just below the exercise boundary, and vanishes past it. The error profile for
a call at σ=40%, T=0.25:

| S | S/S* | Abs err | Rel err |
|---|---|---|---|
| 100 | 0.732 | 0.0035 | 0.047% |
| 120 | 0.878 | 0.0598 | 0.281% |
| 125 | 0.915 | 0.0700 | 0.273% |
| 140 | 1.024 | 0.0000 | 0.000% |

The tests were then restructured to gate on **absolute** dollar error, on the grounds that
the deliverable is a dollar VaR, with relative error reported but not gated. Correctness of
the implementation is established by tests 1, 2, 3, 5 and 6 — all exact structural
properties — not by tests 4/4b, which measure the accuracy of the *method*.

### Decisions taken

1. **`cost_of_carry` is a required argument with no default**, contrary to the brief's
   suggestion of defaulting it for SPY. Any hardcoded default would conflict with the `b`
   derived from the market in Phase 0 (5.5926%), and a silently wrong carry produces
   prices that look entirely plausible.
2. **No literature benchmark table was transcribed.** The brief asked for values from the
   BAW 1987 paper or Haug's book. I could not verify those from this environment, and a
   mis-remembered reference number would make the test worse than useless — it would
   "validate" the pricer against fiction. The substitute is a convergent binomial lattice,
   which is the same methodology BAW used to assess their own approximation, and covers a
   whole grid rather than a handful of points. Easy to add if you have the tables.
3. **`T <= 0` and `sigma <= 0` raise rather than returning intrinsic value.** Both have
   well-defined limits, but silently returning them would hide a caller feeding an expired
   option into a pricing loop.

---

## PHASE 3 — Wire SABR into BAW, and re-imply the volatilities ✅ complete, awaiting approval

### What was built

- `baw.implied_volatility` — inverts `baw_price` by Brent's method to recover the vol that
  reproduces an observed price. Returns NaN, not a number, for quotes no vol can match.
- `baw.baw_vega` — finite-difference vega. **Diagnostic only, never used to price** (the
  filing forbids Greeks-based approximation of scenario prices).
- `main.price_option_with_sabr` — the junction: SABR supplies the vol, BAW turns it into a
  price. Placed in `main.py` deliberately, so `baw.py` keeps no SABR dependency and stays
  testable in isolation with volatility as a plain input argument.
- `main.reimply_smile_volatilities` — replaces the vendor's IVs with our own.
- `plots.plot_reimplied_vs_vendor_vols`, `plots.plot_baw_vs_market_prices`.
- `baw.py` test 8: implied-volatility round trip.

### Re-implying the volatilities worked

All 151 OTM strikes inverted, none failed. Our vols minus the vendor's:

| | Mean difference (vol points) |
|---|---|
| OTM puts (112) | **+0.1231** |
| OTM calls (39) | **−0.0911** |

The difference is a systematic offset that jumps sign at the forward, not random noise —
visible directly in the lower panel of `reimplied_vs_vendor_vols.png`. That is the
signature of a structural inconsistency in the vendor's numbers, exactly as diagnosed.

**The decisive evidence is the sign of the crossover gap.** The smile slopes *down* through
this strike region, so a self-consistent smile must show a *negative* gap going from the
last put to the first call:

| | Last put K=476 | First call K=479 | Adjacent gap |
|---|---|---|---|
| Vendor IV | 11.379% | 11.670% | **+0.291** ← wrong sign |
| Our IV | 11.567% | 11.358% | **−0.209** ← correct sign |

The vendor's smile had a kink pointing the wrong way, which no smooth curve can fit. Ours
is monotone through the crossover.

Fit quality, before and after:

| | Vendor IV | Our IV |
|---|---|---|
| SABR RMSE (vol points) | 0.2946 | **0.2346** |
| Max error (vol points) | 0.6378 | **0.5075** |
| Local roughness, put wing | 0.0501 | **0.0320** |
| Local roughness, call wing | 0.1851 | 0.1813 |
| `alpha` | 0.109922 | 0.109797 |
| `rho` | −0.510842 | −0.524225 |
| `nu` | 2.103836 | 2.132810 |

### A measurement error of mine, corrected

Phase 1 reported a "+0.76 vol point put/call step" from a linear extrapolation of the put
wing across the crossover. That number is **not trustworthy**, and Phase 3 found out why:
the two strikes nearest the forward, 477 and 478, both have **crossed put quotes** (bid
7.36 / ask 7.33 and bid 7.78 / ask 7.77), so the quality filters drop them and leave a $3
hole exactly at the crossover, in the most curved part of the smile. Extrapolating across
it gives anything from +0.21 to +0.41 vol points depending on the fit used.

`data_loader.put_call_crossover_step` now returns the robust `adjacent_gap` (no fitting at
all) alongside the fragile `extrapolated_step`, and the phase output says which to trust.
The Phase 1 conclusion — that the wings were inconsistent — was correct; the precise
magnitude was not.

### Why the residual gap is not the forward

I checked whether the residual could be closed by moving the forward. It is very sensitive
to it: the step crosses zero near F ≈ $479.6, about $0.70 above our parity forward of
$478.892 — 3.5 standard deviations of the parity estimate, so not noise.

I then tested the mechanism I had flagged in Phase 0 note #3: European parity applied to
American options biases the implied forward down by the put's early-exercise premium.
De-Americanising the parity calculation (subtracting each leg's early-exercise premium
before applying parity; the call premium is exactly zero here since `b > r`) moves the
forward from $478.892 to **$479.036, only +$0.144** — about 20% of the gap. So the
early-exercise bias is real and in the right direction, but it is *not* the main cause.
The remainder is most likely that the parity forward is computed from both legs, one of
which is always the illiquid in-the-money one. Left as-is and documented.

### The SABR→BAW junction is provably correct

103 strikes priced through SABR into BAW against the market:

- Mean absolute error **$0.0718**, max **$0.3373**
- Mean **signed** error **−$0.0007** — no systematic bias, only scatter

Only 6 of 103 (5.8%) land inside the bid-ask spread, which looks alarming until you look
at the spreads: **SPY option spreads here are a single penny** (median $0.010, 0.94% of
mid). "Inside the spread" demands matching the market to half a cent, which no smile model
achieves. Absolute error is the meaningful measure.

The important test is error attribution. If the wiring were wrong — spot passed where the
forward belongs, a mismatched day count, the wrong carry — the error would be structural
and would not track the volatility fit error. Multiplying each strike's vol error by its
vega:

| | Mean absolute |
|---|---|
| Observed price error | $0.07177 |
| Predicted from vol error × vega | $0.07182 |
| **Unexplained residual** | **$0.000567** (max $0.003516) |

**99.21% of the pricing error is the SABR fit residual seen through vega**, correlation
0.999974. The forward/spot handling is correct; what remains is the smile fit, and that is
limited by the data rather than by the plumbing.

### Two bugs found and fixed in `baw.py`

Both were surfaced by the inverter probing regions the pricer had never been asked about:

1. **Put critical-price seed overflowed at low volatility.** As σ → 0 the perpetual put
   boundary collapses onto the strike, the denominator `(K − S_perpetual)` goes to zero,
   `h1` blows up to +∞ and `exp(h1)` overflows to NaN. Fixed by clamping `h1` at zero,
   which is exactly the correct limit: with no volatility there is no reason to wait, so
   the exercise boundary *is* the strike.
2. **Newton-Raphson diverged to a negative critical price** for deep in-the-money puts at
   high volatility, after which the `log` in `d1` poisoned every subsequent iterate. Fixed
   by clamping each iterate into the region where the boundary can economically lie — above
   K for a call, in (0, K) for a put.

Also added: `solve_critical_price` now raises a clear error when called for a call with
`b >= r`, where no finite boundary exists, instead of exhausting its iteration cap and
reporting a misleading convergence failure.

### Two things `implied_volatility` must refuse to answer

Both return NaN rather than a plausible-looking number, and test 8 asserts that no
out-of-the-money strike ever hits either:

- An option **past its exercise boundary** is worth exactly its intrinsic value at any
  volatility. Every vol is a solution, so none is.
- A **deep in-the-money** option can have `N(d1)` and `N(d2)` both equal to 1.0 in double
  precision. Vega underflows, the price becomes a deterministic forward — bit-identical at
  5% and 9% vol — and the root finder would return wherever it happened to land.

### Housekeeping

`sabr.py` standalone now prefers `reimplied_smile.csv` when it exists, so `python3 sabr.py`
reports the same parameters as `python3 main.py` rather than the superseded vendor-IV ones.
Phase 1's tables were renamed to `*_vendor_iv.csv` so they cannot be confused with Phase 3's.

---

## PHASE 4 — Historical simulation scenario generation ✅ complete, awaiting approval

### What was built

`historical_sim.py` — `compute_daily_returns`, `generate_scenarios`, `summarise_returns`,
`largest_moves`, `compare_tails_against_normal`, `rolling_window_statistics`. Plus
`plot_spy_price_history` and `plot_daily_returns_histogram`, and `run_phase_4` in `main.py`.

Runnable standalone: `python3 historical_sim.py`. No option pricing anywhere in this phase.

### The scenarios

250 scenarios from the window **2023-01-03 to 2023-12-29** — exactly calendar 2023, since
250 trading days back from the reference date lands on the first trading day of the year.
Scenario prices run **$465.80 to $486.16** around today's $475.31. Zero calendar defects in
the window (checked explicitly against the derived NYSE calendar, since a missing trading
day would compress two sessions into one return and manufacture a tail event).

| Statistic | Value |
|---|---|
| Mean daily return | +0.0904% |
| Daily volatility | 0.8277% |
| **Annualised volatility** | **13.14%** |
| Skewness | −0.0278 |
| **Excess kurtosis** | **−0.1784** |
| Worst day | **−2.0012%** on 2023-02-21 |
| Best day | +2.2827% on 2023-01-06 |

Five largest down moves: 2023-02-21 (−2.00%), 2023-03-09 (−1.81%), 2023-03-22 (−1.66%),
2023-09-21 (−1.62%), 2023-04-25 (−1.58%). These are recognisable 2023 episodes — the
February inflation repricing, the March regional-banking stress, and the September FOMC —
rather than quiet days, which is the sanity check that no missing session has fabricated a
move.

### The headline finding: this window has THIN tails, and I had assumed otherwise

I wrote comments asserting that daily equity returns "essentially always" show positive
excess kurtosis, which is the standard argument against parametric normal VaR. **The data
contradicted that for this window and I corrected the comments.**

| Threshold | Observed | Normal predicts | Ratio |
|---|---|---|---|
| 2σ | 13 | 11.38 | 1.14× |
| 3σ | **0** | 0.67 | 0.00× |
| 4σ | **0** | 0.02 | 0.00× |

Excess kurtosis of **−0.18** means the tails are *thinner* than a normal, not fatter. Not
a single day in 2023 moved more than 3 standard deviations. The mechanism is
straightforward once seen: a normal fitted to a calm year is stretched wide by the handful
of moderately large days, ending up with more tail weight than the data itself.

The long-run textbook result is real; it just does not hold over this particular
250 trading days. The code now says so and points at the evidence rather than asserting the
generality.

### How much does the window matter? A lot.

The same position, same model, same method, evaluated as if standing at the end of each
year instead:

| As of | Ann. vol | Skew | Excess kurt | Worst day | 1st percentile |
|---|---|---|---|---|---|
| 2011-12-30 | 23.08% | −0.433 | +2.581 | −6.51% | −4.34% |
| 2015-12-31 | 15.65% | −0.231 | +2.117 | −4.13% | −2.78% |
| 2018-12-31 | 17.18% | −0.409 | +3.148 | −4.21% | −3.21% |
| 2020-12-31 | 34.14% | −0.598 | +7.631 | −11.51% | −6.76% |
| 2022-12-30 | 24.09% | +0.026 | +0.362 | −4.34% | −3.74% |
| **2023-12-29 (ours)** | **13.14%** | **−0.028** | **−0.178** | **−2.00%** | **−1.64%** |

Ours is the calmest of the six by a wide margin, and the **only one with negative excess
kurtosis** — every other window shows the textbook fat tails. The 1st percentile, which is
essentially what the 99% VaR reads, is **−1.64% for us against −6.76% for a 2020 window:
4.1× larger**.

That factor is not a modelling choice or a market view. It is entirely a consequence of
which twelve months precede the reference date. This is the central weakness of unweighted
historical simulation: a crisis counts fully until the day it ages out of the window, then
not at all. **The Phase 6 VaR will be correspondingly benign, and that is the number's
biggest single caveat.** The method is working exactly as the filing specifies.

`spy_price_history.png` shows this directly: the entire 2022 bear market, which took SPY
from $477 to $357, sits outside the shaded window and contributes nothing.

### A latent bug fixed

`load_spy_price_history` returned a cached CSV **without checking it covered the requested
date range**. Harmless while there was one cache; Phase 4 introduced a second with a
different range, at which point changing a date in CONFIG would silently return the
previous range and every downstream number would be computed from data the config no
longer describes — the worst kind of bug, since nothing fails and the output looks
reasonable. The cache is now validated against the requested range and rebuilt if it falls
short, with a printed notice.

### Decisions taken

1. **Simple returns, not log returns.** They are exactly what `S * (1 + r)` needs. The two
   differ by second order; at this sample's worst day (−2.00%) the log return is −2.02%, a
   2bp difference on the scenario price. Log returns' advantage is additive aggregation
   across time, which matters for multi-day horizons and ours is one day.
2. **Unweighted window**, matching the filing. No exponential weighting, no filtered
   historical simulation rescaling by current-to-historical volatility. Noted in the module
   docstring as a deliberate deviation from common practice, made in order to match the
   filing rather than to improve the estimate.
3. **The window comparison years are shown for context only** and are not put through the
   Phase 0 calendar validation. Data quality varies across the vendor's history — 2022
   alone carries 4 missing trading days and 9 phantom rows. Our own 2023 window is clean,
   which is what matters for the result.

---

## PHASE 5 — Full revaluation loop ✅ complete, awaiting approval

### The position

**Long 1 × SPY 2024-02-16 $475 put.** Spot $475.31, forward $478.892, T = 49 days held
fixed. SABR vol today 11.7554%, theoretical price **$6.639087** against a market mid of
$6.5250 (+$0.1141).

### What was built

Two functions in `main.py`, next to `price_option_with_sabr`:

- `revalue_across_scenarios` — the loop. For each scenario: move spot, recompute the
  forward, get a **fresh SABR vol**, run a **fresh BAW reprice**. A plain loop, one
  scenario per iteration, no vectorisation.
- `taylor_approximation_counterfactual` — deliberately computes the shortcut the filing
  **forbids**, purely to show in dollars what the prohibition buys. Never used for a risk
  number.

Placed in `main.py` rather than a new module because they are the orchestration that joins
SABR, BAW and the scenarios — the same reasoning that put `price_option_with_sabr` there.

### Sanity checks — all three pass

1. **Zero-return scenario reprices to today's price.** 2023-04-19 had a +0.0000% return;
   scenario price $6.639087 against base $6.639087, P&L **exactly $0.000000**.
2. **P&L monotone in the underlying move.** A long put must have P&L decreasing in spot —
   holds across all 250 scenarios.
3. **The P&L is convex**, which is the entire reason for full revaluation:

| Move | P&L down | P&L up | Sum |
|---|---|---|---|
| 0.5% | +0.894788 | −0.772707 | +0.122080 |
| 1.0% | +1.930181 | −1.440300 | +0.489880 |
| 2.0% | +4.500072 | −2.518551 | +1.981521 |
| 3.0% | +7.851610 | −3.332826 | +4.518784 |

A linear position sums to exactly zero on every row. The positive sums are gamma.

### What the forbidden shortcut would have cost

Delta −0.350823, gamma +0.021615, both taken from the **full** chain including SABR's
response — deliberately the most favourable version of the shortcut.

| | Mean error | Max error |
|---|---|---|
| Delta only | $0.166302 | **$1.166583** |
| Delta + gamma | $0.017813 | **$0.237816** |

On the single worst scenario the delta+gamma shortcut gives −$2.534 against the true
−$2.772: it **understates the worst-case loss by $0.238, or 8.6%**. On a $6.64 position the
delta-only error peaks at 17.6% of the whole position value. That is what the filing's
prohibition is protecting against.

### Two sign errors of mine, both caught by checking against numbers

**1. The direction our strike's volatility moves.** I wrote in the docstring that as spot
falls the put "picks up vol from the steep left wing". That is backwards. The equity smile
slopes down in strike and sticky-moneyness pins it to the forward, so:

- market **rallies** → F rises → K/F **falls** → our strike slides deeper into the steep
  left wing → **vol rises**
- market **sells off** → F falls → K/F **rises** → our strike moves toward the smile
  minimum → **vol falls**

Our 475 put's volatility goes **up when the market goes up**. That reads backwards against
"vol spikes in a selloff", but that intuition is about the whole surface level moving,
which this model deliberately freezes.

| Move | K/F | Our vol | Sticky-moneyness | Sticky-strike | Difference |
|---|---|---|---|---|---|
| −3% | 1.0225 | 10.26% | $14.49 | $15.15 | −$0.66 |
| −1% | 1.0019 | 11.16% | $8.57 | $8.97 | −$0.40 |
| +1% | 0.9821 | 12.41% | $5.20 | $4.78 | +$0.42 |
| +3% | 0.9630 | 13.77% | $3.31 | $2.27 | +$1.04 |

Sticky-moneyness **dampens** the P&L in both directions. A long put loses when the market
rallies, so this is the **less conservative** convention for this position's VaR — the
opposite of what I first wrote. Now reported as a table in the phase output.

**2. The direction of the frozen-surface bias in the stress run.** I wrote that holding
today's surface *understates* the stress. Also backwards. A long put is **long vega**, and
its losses come from **rallies**. The worst stress scenario is 2020-03-13, a **+9.29%** day:

| | Put price | Position P&L |
|---|---|---|
| Frozen surface (vol 17.98%) | $1.05 | **−$5.59** |
| Vol +10 points | $4.74 | −$1.90 |
| Vol +20 points | $9.96 | **+$3.32** |

Through 2020 the surface sat far above today's, so freezing it **overstates** the loss tail
for a long put. The stress figures are harsher than a true joint simulation would give.

### Stress window comparison (2020, as requested)

| | 2023 baseline | 2020 stress |
|---|---|---|
| Worst daily move | −2.00% | −11.51% |
| Worst scenario P&L | −2.7719 | **−5.5885** |
| Best scenario P&L | +4.5035 | +47.7675 |
| P&L standard deviation | 1.3980 | **5.5767** |

Same position, same surface, same method — only the return distribution differs. P&L
volatility is 4× higher and the worst case twice as deep.

### Decisions taken

1. **Time to expiry HELD FIXED** at 49 days, per instruction. Decrementing would include
   theta, a near-constant negative contribution to every scenario that would shift the
   whole distribution down and inflate the VaR regardless of market direction. Holding T
   fixed isolates pure market risk. One-line change if revisited.
2. **Sticky-moneyness**, implemented by holding (alpha, beta, rho, nu) fixed and
   re-evaluating SABR at the new forward. Quantified against sticky-strike above.
3. **No vol-surface shock** — declined earlier. The scenario P&Ls therefore contain spot
   risk and the smile's response to spot, but **no independent volatility risk**.

---

## PHASE 6 — VaR calculation ✅ complete, awaiting approval

### What was built

`var.py` — `compute_var`, `cross_check_against_numpy`, `compare_rank_conventions`,
`describe_var`. Runnable standalone with `python3 var.py`.

### The headline number

**99% one-day VaR: $2.4488. Expected shortfall: $2.5805.** On a position worth $6.6391,
that is **36.89%** and 38.87% respectively.

The interpolation, exactly as printed by the pipeline:

```
tail probability alpha  : 0.0100
fractional rank         : (250 - 1) x 0.0100 = 2.4900
lower order statistic   : index 2 (the 3rd worst) = -2.462080
                          from 2023-11-14, a +1.940% move
upper order statistic   : index 3 (the 4th worst) = -2.435070
                          from 2023-11-02, a +1.912% move
interpolation weight    : 2.4900 - 2 = 0.4900
blended quantile        : -2.462080 + 0.4900 x (-2.435070 - -2.462080) = -2.448845
```

Worst single scenario: a **$2.7719** loss on 2023-01-06 (+2.283%). Expected shortfall is
the mean of the 3 breaching scenarios.

**Sign convention, stated once and used everywhere: VaR and expected shortfall are
reported as POSITIVE loss magnitudes.** The underlying quantile keeps its natural sign
(negative for a loss) and is negated once at the point of reporting. If a position never
lost money even at its 1st percentile the reported VaR comes out negative; `compute_var`
flags that rather than clipping it to zero.

### Verification

- **NumPy cross-check agrees to 0.000e+00.** `numpy.percentile(..., method="linear")`
  gives −2.448845174615 against our −2.448845174615. They match because NumPy's default
  uses the same `(N-1)*alpha` rank definition we implemented — which is exactly why it is
  a good check and a bad answer. Calling it would have meant adopting whichever convention
  NumPy happens to default to rather than implementing the filing's.
- **Hand-checked** against the four worst P&Ls independently of the module.
- **Edge cases**: an all-profitable sample returns a negative VaR unclipped; N=101 lands
  exactly on an observation (weight 0.00); NaN input and single-scenario input are both
  rejected with explanatory errors.

### The rank convention matters more than the wording admits

"Linear interpolation to the 99% threshold" does not fully specify a calculation — it
leaves open where in index space the 99% point sits.

| Convention | Rank | VaR | vs ours |
|---|---|---|---|
| `(N-1)*alpha` — R type 7, NumPy default, **ours** | 2.4900 | 2.4488 | — |
| `N*alpha` | 2.5000 | 2.4486 | −0.0003 |
| `(N+1)*alpha - 1` — R type 6, Weibull | 1.5100 | 2.4844 | +0.0355 |

Spread **$0.0358 on a $2.4488 VaR, 1.46%**. Small at 250 scenarios, but it bounds how
precisely the filing's wording can be reproduced at all, and it grows as the scenario
count falls.

### Stress window comparison

| | 2023 baseline | 2020 stress |
|---|---|---|
| 99% VaR | 2.4488 | **4.9321** |
| Expected shortfall | 2.5805 | 5.3881 |
| Worst scenario | 2.7719 | 5.5885 |

The stress VaR is **2.0×** the baseline — same position, same models, same method, only
the twelve months behind the reference date differ. Its bracketing scenarios are
2020-04-06 (+6.792%) and 2020-03-17 (+5.985%).

**Read the stress figure as an upper bound.** Phase 5 measured the direction of the
frozen-surface bias: a long put is long vega and loses on rallies, so holding today's calm
surface instead of 2020's elevated one makes the losing tail *deeper* than a true joint
replay would. On the worst stress scenario, lifting vol 10 points cut the loss from $5.59
to $1.90. So $4.93 overstates what a full joint simulation of 2020 would give.

### Reading the number

Every scenario in the tail is an **up** day. A long put loses when the market rallies, so
the dates driving this VaR are the best days of 2023, not the worst — which inverts the
usual "worst market day" intuition.

Two caveats carry forward, both quantified earlier rather than asserted here:

- **The 2023 window contains no crisis** (Phase 4). Its 1st percentile return is −1.64%
  against −6.76% for a 2020 window. This VaR is benign as a consequence of the window, not
  of the position.
- **BAW's approximation error contributes roughly 1%** to the scenario P&Ls (Phase 2), and
  therefore to this number.

---

## PHASE 7 — Portfolio VaR ⏭️ skipped by choice

The brief made this phase conditional and required asking first. Offered at the end of
Phase 6 and declined: the single-option result was sufficient. Had it been built it would
have extended to several strikes across at least two expiries with signed positions, given
**each expiry its own SABR calibration** (reusing one calibration across tenors is a common
and expensive mistake, since smile shape differs by tenor), aggregated P&L per scenario
before computing VaR, and reported the diversification benefit against the sum of the
standalone VaRs.

---

## PHASE 8 — Visualisation suite and results compilation ✅ complete

### What was built

Three new figures in `plots.py` — `plot_pnl_distribution`, `plot_pnl_timeseries`,
`plot_summary_table` — plus `build_summary_rows` and `run_phase_8` in `main.py`,
`requirements.txt` with pinned versions, and a fully expanded `README.md`.

The other three figures the brief requires were already produced by the phases that
generated their data: `spy_price_history.png` (Phase 4), `sabr_fit.png` (Phases 1 and 3),
`baw_vs_market_prices.png` (Phase 3).

### Final output inventory

**9 figures**, all at 150 DPI with titles, axis labels carrying units, and legends:

| Figure | Phase |
|---|---|
| `vol_smile_raw.png` | 0 |
| `sabr_fit.png` | 1, regenerated in 3 |
| `reimplied_vs_vendor_vols.png` | 3 |
| `baw_vs_market_prices.png` | 3 |
| `spy_price_history.png` | 4 |
| `daily_returns_histogram.png` | 4 |
| `pnl_distribution.png` | **8** |
| `pnl_timeseries.png` | **8** |
| `summary_table.png` | **8** |

**16 tables** in `outputs/tables/`, including `summary.csv` as the machine-readable twin
of the summary image.

### Two rendering bugs found and fixed

1. **Matplotlib was parsing currency as LaTeX maths.** Text between a pair of `$` is
   treated as a maths expression, so a title reading `+1 x $475 put, 250 scenarios,
   position worth $6.6391 today` silently lost both dollar signs and italicised everything
   between them. Invisible until a single string happens to carry two dollar amounts.
   Fixed with a `plots.escape_dollars` helper, applied to composed money strings.
2. **A mislabelled row in the summary table.** "Window contains no crisis" was quoting
   `statistics["min"]` — the *worst daily move*, −2.00% — under the label "1st pctile
   return", which is −1.64%. Now pulled from the Phase 4 window comparison so the baseline
   and stress figures come from the same calculation.

### Verification

Cold run from deleted caches completes in ~9 seconds; all five entry points exit 0
(`main.py`, `sabr.py`, `baw.py`, `historical_sim.py`, `var.py`), and `baw.py`'s ten-check
suite passes.

---

## PHASE 9 — Independent data validation against Databento ✅ complete

A cross-vendor correctness test of the reference date, run before deciding whether to
extend the project to a second reference period sourced from Databento. Not a data
extension: one day, $0.0390 of metadata-priced pulls, no change to any pipeline result.

This addresses README limitation #5 — *"Single-source volatility data, end-of-day only...
A second source would be the obvious next check."* That check has now been run.

### What was built

| File | Responsibility |
|---|---|
| `databento_cost_check.py` | Single-day `metadata.get_cost` probe, DST-aware close window |
| `databento_cost_check_full.py` | Full-range quote plus quarterly sampled per-day costs |
| `databento_cost_check_2025.py` | Same for a proposed 2025-06-25 → 2026-06-25 period; reuses `data_loader.nyse_trading_days` |
| `databento_validation_20231229.py` | The cross-check itself: joins definition × cbbo-1m and compares against Phase 0–3 outputs |
| `outputs/tables/databento_iv_comparison.csv` | Per-strike IV comparison, 151 common strikes |

`metadata.get_cost` is free, so every cost figure below was established before spending
anything. **Total spent: $0.0390** — definition $0.014256, cbbo-1m $0.024694.

### The data

`OPRA.PILLAR`, parent symbology `SPY.OPT`. Two pulls for 2023-12-29:

- **definition**, full 24h UTC day (the docs state definition cost is only accurate in
  24-hour multiples, so it is not narrowed). 8,504 instruments.
- **cbbo-1m**, 20:57–21:00 UTC. 2023-12-29 is EST, so the 16:00 ET close is 21:00 UTC;
  the conversion is done with `zoneinfo`, never a hardcoded offset. The closing-minute
  bar (ts_recv 20:59, covering 15:59–16:00 ET) is the snapshot used.

Definition supplies strike, expiry and put/call for each `instrument_id`; without it the
cbbo rows are numeric IDs with no strike attached. **312 definitions** for expiry
2024-02-16 joined to the closing bar gave **156 strikes with both legs — exactly the 156
the project has.** The join was verified independently: the OSI symbol carried on each
quote matches the symbol on its definition for all 312 instruments, **0 mismatches**.

### Result: the existing numbers are corroborated

The project's own `imply_forward_from_parity` and `baw.implied_volatility` were run
**unchanged** on Databento quotes, so every difference below is attributable to the data
rather than to the method.

| Quantity | Project (OptionsDX) | Databento (OPRA) | Difference |
|---|---|---|---|
| Chain strikes, 2024-02-16 | 156 | 156 | 0 |
| Parity forward | $478.8920 | $478.8495 | **−$0.0425** (−0.89 bp) |
| Forward dispersion | $0.203 (22 strikes) | $0.182 (32 strikes) | tighter |
| Implied dividend yield | −0.1926% | −0.1265% | +0.066 pp |
| Implied spot | $475.31 | $475.2678 | −$0.0422 |
| $475 put bid/ask | 6.51 / 6.54 | 6.58 / 6.61 | mid **+$0.0700** |
| BAW theoretical − market mid | +$0.1141 | +$0.0441 | Databento closer to theory |
| Re-implied IV, median difference | — | — | **0.0000 vol pts** |
| Re-implied IV, mean difference | — | — | −0.0168 vol pts |
| Re-implied IV, 95th pctile \|diff\| | — | — | 0.2286 vol pts |
| Strikes differing > 1 vol point | — | — | **1 of 151** |
| Crossed OTM quotes | **2** (K=477, 478) | **0** | — |

Every difference is inside normal cross-vendor noise. Median mid difference across the 151
common strikes is **$0.0000** and median IV difference is **0.0000 vol points**.

Two entries deserve their own note:

- **The $0.07 on the $475 put** is the largest *positive* mid difference in the sample
  (which ranges −$0.225 to +$0.070), and it lands on the one contract the headline VaR
  rests on. In IV terms it is **+0.104 vol points**. Both vendors quote a 3-cent spread;
  the difference is snapshot timing — OptionsDX stamps 16:00, Databento's bar covers
  15:59–16:00.
- **The single >1 vol point outlier** is K=260, a deep-wing penny put: Databento 0.01/0.02
  against the project's mid $0.025. A **one-cent** disagreement amplified into 2.36 vol
  points by near-zero vega. Not a data defect.

The −$0.0422 "implied spot" difference is not an independent measurement. OPRA carries no
underlying equity price, so spot cannot be read from this data at all; the figure is the
forward difference propagated back through the carry, and is the same number.

### The crossover: Phase 3's diagnosis confirmed, its metric found fragile

This is the substantive finding, and it splits into three parts.

**1. The vendor's wrong-sign kink was an artefact.** Phase 1 and Phase 3 found the
OptionsDX vendor IVs stepping **+0.291 vol points upward** across a downward-sloping
smile — the impossible shape that motivated re-implying the vols in the first place.
Databento does not reproduce it. Relatedly, the **two crossed put quotes at K=477 and
K=478** (bid 7.36/ask 7.33 and 7.78/7.77) that forced those strikes out of the smile are
**clean and two-sided in Databento** (7.37/7.40 and 7.80/7.83). Zero crossed quotes in the
whole OTM chain. Both were OptionsDX defects.

**2. The Phase 3 fix was right.** Like-for-like on the same 476→479 pair, the re-implied
adjacent gap is **−0.2088** for the project and **−0.2164** for Databento. Independent
data reproduces the corrected number to 0.008 vol points.

**3. The residual inconsistency is real — and the number in assumption #15 is not.**
Because Databento has clean quotes at 477 and 478, the crossover can be measured across a
**$1 gap** rather than extrapolated across the $3 hole that assumption #17 flags as
unreliable. Measuring the residual as *adjacent gap minus what the put wing's slope
predicts*, using a **fitted slope over strikes 470 to the last put**:

| | Adjacent gap | Fitted wing slope | Predicted gap | **Residual** |
|---|---|---|---|---|
| Project, 476→479 ($3) | −0.2088 | −0.1264 /$1 | −0.3792 | **+0.1704** |
| Databento, 476→479 ($3) | −0.2164 | −0.1287 /$1 | −0.3860 | **+0.1697** |
| Databento, 478→479 ($1) | +0.0208 | −0.1254 /$1 | −0.1254 | **+0.1462** |

All in vol points. The two vendors agree on identical strikes to **0.0007 vol points**,
and closing the $3 hole moves the answer by only 0.024. **The residual put/call
inconsistency is a real market feature of roughly +0.15 vol points, not an OptionsDX
artefact.** Phase 3's conclusion stands, and the ~20%-explained-by-early-exercise
diagnosis with it.

**But `slope_implied_gap` should not be trusted.** `put_call_crossover_step` predicts the
gap from a **two-point** local slope — the last two put quotes only — deliberately, to
keep the measure local. On this date that choice backfires:

| | Two-point slope | Predicted gap | Residual |
|---|---|---|---|
| Project, 476→479 | −0.0193 /$1 | −0.0578 | **−0.1510** |
| Databento, 476→479 | −0.1084 /$1 | −0.3253 | **+0.1089** |
| Databento, 478→479 | −0.1126 /$1 | −0.1126 | **+0.1334** |

The residual **changes sign between vendors on the same strikes**. The cause is a single
quote: the 475→476 put IV step is **−0.0193** vol points in OptionsDX against **−0.1084**
in Databento, because the OptionsDX 475 put mid is 7 cents low. A two-point slope has no
averaging, so a one-cent quote error propagates undamped into the prediction — here it
flattened the slope 5.6× and flipped the residual's sign.

The **fitted-slope** version is stable across vendors (+0.1704 vs +0.1697) and across
strike pairs (+0.1697 vs +0.1462). It is the measure to use.

### Supersedes

Per this file's append-only convention, the consolidated assumptions table below is left
as written. Two of its rows should now be read with this section alongside them:

- **Assumption #15** states the residual gap as "−0.209 vs −0.058 predicted by the local
  slope". The −0.209 is confirmed. **The −0.058 is unreliable** — it comes from a
  two-point slope distorted by one 7-cent quote, and a second vendor puts the same
  quantity at −0.3253. The residual is better stated as **+0.15 vol points** from a fitted
  wing slope, which both vendors agree on.
- **Assumption #17** states the extrapolated crossover step is unreliable because crossed
  quotes at 477 and 478 leave a $3 hole. Still true of the OptionsDX data. **Databento has
  clean quotes at both strikes**, so on a Databento-sourced rebuild the hole does not
  exist and this assumption can be retired.

Nothing in Phases 0–8 is changed by this. No pipeline number moves; `main.py` was not
touched.

### What this does and does not establish

**Does:** the OptionsDX-derived reference state is independently corroborated; the
instrument_id → strike/expiry/type mapping works; the project's parity and BAW-inversion
code runs unchanged against Databento data and produces consistent results; the Phase 3
re-implying decision was correct; the residual crossover inconsistency is a genuine market
feature.

**Does not:** validate any date other than 2023-12-29. It is one snapshot. A Databento
rebuild of a 2025–2026 period would be resting on a mapping confirmed on a single day —
about 3 cents buys a second date, and that is worth doing before trusting a period with no
ground truth behind it.

Also unestablished: Databento's cbbo-1m is a *consolidated BBO at a minute boundary*,
which is not the same object as an OptionsDX end-of-day snapshot. They agree closely here,
but a rebuild would need a documented rule for reducing minute bars to an EOD-equivalent
quote, and that rule is a modelling decision, not a data pull.

### Cost of a Databento-sourced period, for reference

Established from `metadata.get_cost` only, no data pulled. Per-day cbbo-1m cost rises with
the SPY chain — **+42.1%** across 2024–2026 and **+55.2%** across 2025-06-25 → 2026-06-25 —
so a single sampled day materially understates a year. Sampling quarterly:

| Period | Trading days | Per-day mean | cbbo-1m total | + definition (4–12 pulls) |
|---|---|---|---|---|
| 2025-06-25 → 2026-06-25 | 252 (from `nyse_trading_days`) | $0.033284 | $8.39 | **$8.45 – $8.57** |
| 2024-01-01 → 2026-09-05 | ~670 | $0.030560 | $20.48 | **$20.63 – $20.97** |

One contiguous pull of *every hour* rather than the pre-close window costs **$168.08** and
**$423.61** for those two ranges — about 20× more for data the pipeline discards, since it
consumes one snapshot per day.

Definition cost is roughly flat (~$0.015/day) because its records are one-per-instrument
rather than volume-driven, and instrument definitions are largely static, so it does not
need a daily pull.

**Cost is not the constraint on this extension.** The loader work is: reducing minute bars
to an EOD-equivalent snapshot and mapping OPRA definitions onto the
`[QUOTE_DATE]/[EXPIRE_DATE]/[STRIKE]` schema. That work is identical at $8 or $424.

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
| 27 | approximation | `var.compute_var` | The `(N-1)*alpha` rank definition is one of several in common use and the filing does not pin one down. Alternatives shift the VaR by up to $0.0358 (1.46%) at this sample size; `compare_rank_conventions` quantifies it. |
| 28 | approximation | `var.compute_var` | Expected shortfall averages only the **3** scenarios beyond the 99% threshold, so it is correspondingly noisy — a limitation of 250 scenarios, not of the estimator. Simple mean of breaching scenarios, not an integrated tail expectation. |

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

---

## Version control

**One commit per phase.** At the end of every phase, before asking for approval to
proceed, commit the phase's work with a message summarising what it built and its key
finding. This file is the source of truth for those messages.

The commit goes *after* the phase's work is finished and verified, and *before* the
PHASE COMPLETE block is posted. Nothing is pushed to a remote yet — that will be set up
separately.

`.gitignore` excludes the raw `data/*.parquet` vendor files (~600 MB, and not ours to
redistribute), the derived `data/*.csv` caches, and `__pycache__`. The `outputs/`
directory **is** committed, so the repository stays reviewable by someone who does not
have the dataset. `README.md` explains where to obtain it.

### A caveat on the Phase 0–5 commits

Those six commits were created retroactively, after Phase 5 was finished, because the
project was built before the repository existed. They are a reconstruction: each contains
the code introduced up to and including that phase, with anything from later phases
removed. Every one was verified to run `python3 main.py` end to end from a clean checkout,
and the standalone modules were verified at the commit that introduced them.

Two known artifacts of that reconstruction, neither of which affects correctness:

- Bug fixes made in a later phase to an earlier file appear in the earlier commit. For
  example the two `baw.py` fixes found in Phase 3 are present in the Phase 2 commit, since
  un-fixing them would mean committing code known to be broken.
- The consolidated assumptions table at the end of this file is not truncated per phase,
  so early commits carry a few forward references in that table.

From Phase 6 onward the commits are genuine, made at the time.
