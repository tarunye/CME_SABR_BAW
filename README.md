# SPY Options VaR — replicating the SR-FICC-2013-02 margining methodology

A research codebase that reproduces the options margining methodology described in the
2013 FICC/NYPC rule filing with the SEC (File No. **SR-FICC-2013-02**), applied to SPY
options instead of the filing's interest rate futures options.

**Headline result** — the same methodology run on two independent reference cases, from
two independent data sources:

| | 2023-12-29 (OptionsDX) | 2026-06-01 (Databento) |
|---|---|---|
| Position | +1 × 2024-02-16 $475 put | +1 × 2026-07-17 $759 put |
| Position value | $6.6391 | $14.4221 |
| **99% one-day VaR** | **$2.4488** (36.89%) | **$4.1216** (28.58%) |
| Expected shortfall | $2.5805 (3 breaches) | $5.3674 (3 breaches) |
| Worst scenario in sample | $2.7719, from 2023-01-06 | $6.0986, from 2026-03-31 |
| Stress-window VaR | $4.9321 (2020, 2.0×) | $5.2226 (2026-01-02, 1.3×) |

The dollar figures are not directly comparable — different strikes on a different
underlying level. **VaR as a percentage of position value is the like-for-like number.**
The two cases differ because the market differed: rates fell 5.40% → 3.66%, the carry
regime flipped from `b > r` to `b < r` (a dividend falls inside the second window and not
the first), and the lookback window's excess kurtosis went from −0.18 to +1.57. The data
source contributes almost nothing to that gap — see *Two reference cases* below.

## The methodology in one paragraph

The filing's approach rests on three pillars. **SABR** (Hagan et al., 2002) models the
implied volatility smile, supplying a volatility for any strike. **Barone-Adesi & Whaley**
(1987) converts that volatility into an American option price — SPY options are
American-style, which is why a European Black-Scholes price will not do. **Historical
simulation** generates roughly 250 alternative states for tomorrow by replaying the last
year of daily SPY moves. The binding constraint, stated explicitly in the filing, is that
options must be **fully revalued** under each scenario: a fresh SABR volatility and a
fresh BAW reprice every time. No delta/gamma Taylor approximation is used anywhere. The
99% Value-at-Risk is then read off the resulting P&L distribution by linear interpolation
between the adjacent order statistics.

```
SABR                   ->  implied volatility for a given strike and expiry
BAW                    ->  converts that volatility into an American option price
HISTORICAL SIMULATION  ->  ~250 alternative "tomorrow" market states
FULL REVALUATION       ->  reprice under each state (fresh SABR vol + fresh BAW price)
P&L                    ->  scenario price minus today's price, per scenario
VaR                    ->  99th percentile of the loss distribution, interpolated
```

## Getting the data

**The raw data is not in this repository.** There are two datasets, one per reference
case; neither is ours to redistribute.

### The original dataset (2010–2023, OptionsDX)

Roughly 600 MB across fourteen files, too large for GitHub. **SPY end-of-day option chains
covering 2010–2023**, originally published
by [OptionsDX](https://www.optionsdx.com/) as free sample data and widely mirrored on
Kaggle — search Kaggle for *"SPY options EOD"* or *"SPY option chain 2010 2023"*. Any
mirror of the OptionsDX SPY EOD set will work.

Place the files in `data/`, named exactly:

```
data/spy_eod_2010.parquet
data/spy_eod_2011.parquet
...
data/spy_eod_2023.parquet
```

If your download is CSV rather than parquet, convert it — the loader reads parquet so that
column selection can be pushed down into the file and a two-column pull out of an 85 MB
file stays fast.

**Verifying you have the right data.** Each row is one (quote date, expiry, strike) and
carries *both* the call and put side. Column names in the raw files are wrapped in square
brackets — a vendor quirk the loader strips. The columns the project needs are:

```
[QUOTE_DATE]  [EXPIRE_DATE]  [DTE]  [UNDERLYING_LAST]  [STRIKE]
[C_BID] [C_ASK] [C_LAST] [C_IV] [C_VOLUME]
[P_BID] [P_ASK] [P_LAST] [P_IV] [P_VOLUME]
```

The 2023 file should contain 972,162 rows across 250 quote dates, and SPY should close at
**475.31** on 2023-12-29. The pipeline prints a data summary and runs validation checks on
every run, so a mismatched dataset will announce itself rather than fail silently.

Only the files for the years you actually use are needed. The default configuration reads
2022 and 2023 for the main pipeline, plus 2010–2023 for one diagnostic that compares
lookback windows across market regimes.

### The second dataset (2025–2026, Databento)

The 2026-06-01 case uses **SPY option quotes from Databento's OPRA feed**, pulled and
reshaped by the scripts in `databento_pullers/`. That data is not in this repository
either — 363 MB raw, and likewise not ours to redistribute — but unlike the OptionsDX set
it is fully reproducible from this repo with a Databento API key:

```bash
export DATABENTO_API_KEY=...
python3 databento_pullers/databento_pull_2025_2026.py all   # ~$8, 353 sessions
python3 databento_pullers/databento_reshape.py              # -> data/databento_2025_2026/
```

The pull takes a three-minute pre-close window of `cbbo-1m` quotes per session, joins them
to per-day instrument definitions, and collapses each contract to one end-of-day row.
`UNDERLYING_LAST` is SPY's observed consolidated close (`EQUS.SUMMARY` daily bars).
The reshaped output uses the same bracketed column layout as the OptionsDX files, so
`data_loader.py` reads it unchanged — only `data_dir` moves.

Two calendar facts the pull surfaced, both now handled explicitly in
`databento_pull_2025_2026.py`, and both invisible to a naive calendar:

- **Early closes.** The NYSE shuts at 1pm ET on about three days a year. Those are still
  trading days, so a 4pm window looks ordinary while sitting three hours after the bell.
- **One-off closures.** 2025-01-09 was a national day of mourning. The derived calendar
  lists it as a trading day; no SPY option was active at all.

## Two reference cases

The project has been run end to end twice, on two independently sourced datasets. The
methodology is identical — `sabr.py`, `baw.py`, `historical_sim.py` and `var.py` are byte
for byte the same in both runs. Only the config and the dataset differ.

The comparison matters because the two runs disagree substantially, and the question is
whether that disagreement is the market or the plumbing. It is the market:

| | 2023-12-29 | 2026-06-01 |
|---|---|---|
| Implied dividend yield | −0.19% (no ex-div in window) | +1.90% (**$1.82** on $758.54) |
| Carry regime | `b > r` | `b < r` |
| Risk-free rate | 5.40% | 3.66% |
| SABR `rho` (skew) | −0.524 | −0.634 |
| Excess kurtosis of window | −0.18 (thin tails) | +1.57 (fat tails) |
| Fit RMSE | 0.2346 vol pts | 0.2080 vol pts |

The data source's own contribution was measured directly, by pulling Databento data for
**2023-12-29 itself** and running the project's unchanged code against it: median mid
price difference **$0.0000** across 151 common strikes, median implied-vol difference
**0.0000 vol points**, parity forward apart by **0.89 bp**. Against differences of 8.3
percentage points in VaR-to-position, that cannot be what separates the two answers.

That cross-check also settled an open question from Phase 3. The OptionsDX vendor's
implied vols stepped the *wrong way* across the put/call crossover — an impossible shape
that no smooth smile can fit, and the reason the project re-implies its own volatilities.
Databento does not reproduce it, and neither does the second case. The kink was the
vendor's. The residual inconsistency underneath it, roughly +0.15 vol points, is real and
appears in both sources.

`PROJECT_LOG.md` carries the full side-by-side under *Stage D*, including what the second
case does **not** establish.

## Running it

```bash
pip install -r requirements.txt
python3 main.py                      # the 2023-12-29 case (OptionsDX)
python3 main.py config_2026_06_01    # the 2026-06-01 case (Databento)
```

That runs the whole pipeline and writes every figure and table, in about nine seconds from
a cold start. Each case writes to its own output directory, so runs never overwrite one
another. The first run parses the parquet files and caches derived pulls to
`data/*.csv`; later runs read the caches. Delete those CSVs to force a rebuild.

Every module with a self-contained result also runs on its own:

```bash
python3 sabr.py            # SABR calibration and fit plot
python3 baw.py             # the full BAW validation suite (~30s, not part of the pipeline)
python3 historical_sim.py  # scenario generation and return statistics
python3 var.py             # the VaR, from the saved scenario revaluation
```

All configuration lives in `configs/`, one file per reference case, with every assumption
stated next to its source. There are no magic numbers elsewhere, and `main.py` is a thin
runner that loads whichever config it is given — adding a third reference case means
adding a file to `configs/`, never editing `main.py`.

## What each file does

| File | Responsibility |
|---|---|
| `data_loader.py` | Loading and validation: price history, option chains, the NYSE trading calendar, the put-call-parity forward, and the out-of-the-money smile |
| `sabr.py` | Hagan et al. (2002) implied volatility expansion and least-squares calibration |
| `baw.py` | Barone-Adesi & Whaley American pricer, European baseline, critical-price solver, implied-volatility inverter, a binomial reference pricer, and a ten-check validation suite |
| `historical_sim.py` | Daily returns, scenario generation, and the return-sample diagnostics |
| `var.py` | 99% VaR by explicit interpolation between order statistics, expected shortfall, and the rank-convention comparison |
| `plots.py` | One function per figure, all saving to `outputs/figures/` |
| `main.py` | The phase-by-phase pipeline, and the runner that loads a config |
| `configs/` | One config per reference case: `config_2023_12_29.py`, `config_2026_06_01.py` |
| `databento_pullers/` | Cost checks, the Databento pull, the reshape into this project's dataset format, and the validation and sanity-check scripts |
| `PROJECT_LOG.md` | Running record of what each phase built, every decision and why, the bugs found, and the consolidated assumptions table |

## What each figure shows

| Figure | What it shows |
|---|---|
| `vol_smile_raw.png` | The raw market implied volatility smile for the reference expiry, out-of-the-money quotes only. The Phase 0 sanity check: it should look like a skew, and it does |
| `reimplied_vs_vendor_vols.png` | Our own BAW-inverted volatilities against the vendor's, and their difference. The lower panel shows the vendor's put/call inconsistency as a systematic offset that flips sign at the forward |
| `sabr_fit.png` | Market vols as scatter, the calibrated SABR curve as a line, with per-strike residuals underneath |
| `baw_vs_market_prices.png` | Theoretical SABR→BAW prices against market prices across strikes, with the bid-ask band shaded, and the pricing error in units of the half-spread below |
| `spy_price_history.png` | The SPY price path with the 250-day lookback window shaded — everything outside it contributes nothing to the VaR |
| `daily_returns_histogram.png` | The distribution of daily returns in the window against a fitted normal, with skewness and excess kurtosis in the title |
| `pnl_distribution.png` | The ~250 scenario P&Ls, with the 99% VaR threshold as a labelled line, breaching scenarios in red, and the worst scenario annotated |
| `pnl_timeseries.png` | Each scenario's P&L against the historical date that produced it, so the market episodes driving the tail can be read off directly |
| `summary_table.png` | Every headline number as a rendered image: position, market state, calibrated SABR, pricing, simulation, result, and how to read it |

## Assumptions and deviations from the filing

Every `# NOTE` comment in the codebase, collected. `PROJECT_LOG.md` carries the same list
with the evidence behind each. Grep the source for `# NOTE` to find them in place.

### Data

1. **Unadjusted prices.** `UNDERLYING_LAST` is the unadjusted SPY price, so SPY's four
   annual ex-dividend dates appear as spurious ~0.4% down moves. Accepted because the
   option contracts are written on the unadjusted price, and because synchronicity with
   the option quotes is worth more than removing four small dips.
2. **ACT/365 day count** for time to expiry. ACT/252 and ACT/365.25 are the alternatives;
   at 49 days the difference is well under a vol point and is absorbed by the calibration.
3. **Put-call parity applied to American options.** Parity holds exactly only for European
   options, so the American put's early-exercise premium biases the implied forward
   slightly low — a cent or two at 49 days, inside the bid-ask noise.
4. **Dividend yield derived, not assumed.** No ex-dividend date falls inside our window, so
   SPY's ~1.4% trailing yield would be simply wrong here. The forward is implied from
   parity and `b` and `q` derived from it.
5. **Out-of-the-money quotes only**, split at the forward rather than spot. The
   in-the-money leg is illiquid and its vendor IV is unreliable.
6. **The derived NYSE calendar ignores one-off closures** (state funerals, Hurricane
   Sandy). Those surface as "missing" days for a human to judge, which is the right
   outcome.
7. **Implied volatilities are re-implied by us** from OTM mid prices by inverting BAW
   against the parity forward, replacing the vendor's. Theirs are mutually inconsistent —
   the vendor smile is non-monotone at the crossover.
8. **A residual put/call inconsistency remains** after re-implying. De-Americanising the
   parity forward explains only ~20% of it; the rest is most likely that the parity forward
   is computed from both legs, one of which is always the illiquid in-the-money one.
9. **The extrapolated crossover-step metric is unreliable** on this date, because crossed
   quotes at strikes 477 and 478 leave a $3 hole at the forward. The robust `adjacent_gap`
   is what conclusions are drawn from.

### Model

10. **SABR ATM singularity.** The `z/x(z)` factor is `0/0` at the money; handled by an
    explicit branch to the closed-form limit below a tolerance on `|log(F/K)|`, verified
    continuous to 4.5e-08 vol points.
11. **`beta` fixed at 1.0, not fitted.** It is close to jointly unidentifiable with `rho` —
    across `beta` from 0 to 1 the RMSE moves 0.04 vol points while `alpha` swings by a
    factor of 476. The filing specifies SABR but states no `beta`.
12. **Calibration restricted to `|moneyness| ≤ 15%`.** The far wings are penny quotes
    outside the range where Hagan's asymptotic expansion is reliable.
13. **Cost of carry.** The filing prices futures options where `b = 0` (Black-76). SPY is a
    dividend-paying ETF, so `b = r − q`; we implement the general `b` form and derive it
    from the market.
14. **`cost_of_carry` is a required argument** with no SPY default, contrary to the brief's
    suggestion. A hardcoded default would conflict with the derived `b = 5.5926%`, and a
    silently wrong carry produces plausible-looking wrong prices.
15. **No literature benchmark table transcribed.** Reference values from the BAW 1987 paper
    could not be verified from the build environment, and an unverifiable reference number
    would validate the pricer against fiction. A convergent binomial lattice is used
    instead — the same methodology BAW used to assess their own approximation.
16. **BAW's approximation error contributes ~1% to the scenario P&Ls**, and therefore to
    the VaR. Its sign changes with moneyness, so it does *not* cancel in the P&L difference
    but amplifies about 5.5×. Measured, not estimated.
17. **Vega is computed for error attribution only**, never to price a scenario.

### Simulation and VaR

18. **Simple returns**, not log returns — exactly what `S × (1 + r)` needs. They differ by
    2bp on this sample's worst day.
19. **The simulation is unweighted**, matching the filing. Every day counts equally despite
    volatility clustering being real. No exponential weighting, no filtered historical
    simulation.
20. **Time to expiry held fixed** at 49 days rather than decremented, so theta is excluded.
    Decrementing would shift every scenario down by roughly the same amount and inflate the
    VaR regardless of market direction; holding `T` fixed isolates pure market risk.
21. **Sticky-moneyness**, implemented by holding the SABR parameters fixed and
    re-evaluating at the new forward. Our strike's volatility *rises when the market
    rallies* (K/F falls, sliding it into the steep left wing). It dampens P&L in both
    directions and is the **less conservative** convention for a long put.
22. **No volatility-surface shock.** Scenario P&Ls contain spot risk and the smile's
    response to spot, but no independent volatility risk. The historical surfaces to build
    it do exist in the dataset.
23. **The stress-window run keeps today's surface** and changes only the return
    distribution. Since a long put is long vega and loses on rallies, this **overstates**
    its loss tail relative to a true joint replay of 2020.
24. **The rank convention** `(N-1)·alpha` is one of several in common use and the filing
    does not pin one down. Alternatives shift the VaR by up to $0.0358 (1.46%) at this
    sample size.
25. **Expected shortfall averages only 3 scenarios**, so it is noisy — a limitation of 250
    scenarios, not of the estimator. Simple mean of breaching scenarios, not an integrated
    tail expectation.
26. **`taylor_approximation_counterfactual` deliberately computes the forbidden
    delta/gamma shortcut**, for demonstration only, never for a risk number. It shows the
    shortcut understating the worst-case loss by 8.6%.

## Limitations

Beyond the itemised deviations above, six things about these results deserve emphasis.

**The lookback window's severity dominates the answer, in both cases.** Calendar 2023 was
calm: realised volatility 13.14%, excess kurtosis −0.18 (tails *thinner* than a normal),
and not one day beyond 3σ. Its 1st percentile return is −1.64% against −6.76% for a window
ending in 2020 — **4.1× smaller**. The VaR is benign as a consequence of the window, not of
the position. This is the central weakness of unweighted historical simulation: a crisis
counts fully until the day it ages out of the window, then not at all. The
`spy_price_history.png` figure shows the entire 2022 bear market sitting outside the shaded
region, contributing nothing.

The second case demonstrates the same effect *inside one continuous dataset*, which is
more direct evidence than the original could offer. The April 2025 tariff selloff (−5.85%,
then +10.50% five sessions later) sits at sessions 63–68 of the Databento pull. It is
inside the lookback window for reference dates up to roughly 2026-03, and has aged out by
2026-06-01 — the 1st percentile return halving from −3.56% to −1.75% as it falls off the
back of the window. Nothing about the market changed at that moment; only the window did.

**There is no volatility risk in this number.** The surface is frozen at today's
calibration and only the anchor point moves with spot. A genuine full revaluation of a
historical day would shock the surface too, drawn from that same day. For a long option
this matters a great deal: in March 2020 the same spot move with the surface where it
actually was turns a $5.59 loss into a $3.32 gain.

**One-day horizon, one underlying, one position.** No multi-day scaling, no correlation
across underlyings, no portfolio effects. Phase 7 (portfolio VaR with per-expiry
calibration and a diversification analysis) was scoped and deliberately skipped.

**No liquidity or bid-ask adjustment.** Positions are marked at theoretical mid. A real
margin model would consider the cost of actually exiting, which for a large position in
the far wings is not the mid.

**End-of-day only — though no longer single-source.** One snapshot per day in both cases.
The original limitation was that a single vendor supplied everything, and that vendor's own
implied vols turned out to be internally inconsistent enough to require recomputing. That
check has since been run: Databento data for the same reference date agrees to a median
$0.0000 in mid price and 0.0000 vol points in implied volatility, and does *not* reproduce
the vendor's crossover kink. What remains is the daily-snapshot limitation itself — an
end-of-day mark says nothing about the intraday path a real margin model would care about.

**The second case has no external price benchmark of its own.** The 2023-12-29 case can be
checked against two independent sources. The 2026-06-01 case rests on one, cross-validated
only in the sense that the source was verified on a *different* date.

## Reading the results

Start with **`PROJECT_LOG.md`**. It is the honest account of the project: what was built in
each phase, the key finding from each, every judgement call with its reasoning, the bugs
found along the way — including several sign errors I made and corrected after checking
against numbers — and the consolidated assumptions table.

Outputs land in `outputs/figures/` and `outputs/tables/`. These *are* committed, so the
repository is reviewable without the underlying dataset.

## Provenance

Built in nine phases (0–8), one at a time, with a review gate after each. Phase 7
(portfolio-level VaR) was scoped and skipped by choice. The Phase 0–5 commits were
reconstructed retroactively, since the project predates the repository; each was verified
to run end to end from a clean checkout. See the *Version control* section of
`PROJECT_LOG.md` for the details and caveats.

A second reference case was added later, in four stages: a cross-vendor validation of the
original reference date (Phase 9), a Databento pull and reshape (Stages A–B), a full
independent run (Stage C), and the comparison above (Stage D). Configuration moved out of
`main.py` into `configs/` at that point, and the original run was re-verified afterwards to
reproduce all 17 of its output tables byte for byte.
