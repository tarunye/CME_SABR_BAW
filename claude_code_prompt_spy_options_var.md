# PROJECT: Replicating the FICC/NYPC (SR-FICC-2013-02) Options Margining Methodology on SPY Options

You are helping a quantitative finance team build a research codebase from scratch. Read this entire brief before writing a single line of code.

---

## 0. HOW WE ARE GOING TO WORK TOGETHER — READ THIS FIRST

This project is built in **nine sequential phases (Phase 0 through Phase 8)**.

**Hard rules on process — these override any instinct you have to be efficient:**

1. You build **exactly one phase at a time**. You do not read ahead and start scaffolding future phases "while you're in there."
2. At the end of every phase you **STOP COMPLETELY** and wait for my explicit written approval before touching anything else. The literal words I will use to approve are **"approved, continue to Phase N+1"**. Anything else I say — questions, comments, "looks good," "interesting" — is *not* approval. If you are unsure whether you have been approved, assume you have not been and ask.
3. At every stop point, you must post a **PHASE COMPLETE** block in this exact format:

```
=== PHASE N COMPLETE ===
FILES CREATED/MODIFIED:  <list every file, with a one-line description of each>
HOW TO RUN IT:           <the exact command(s) I type to reproduce your results>
WHAT IT OUTPUTS:         <files written, figures saved, numbers printed>
VALIDATION PERFORMED:    <what you checked, and what the result was>
KEY NUMBERS:             <the actual output values, printed inline so I can eyeball them>
ASSUMPTIONS & DEVIATIONS: <every approximation or simplification vs. the filing, listed>
OPEN QUESTIONS FOR YOU:  <anything you need me to decide>
=== AWAITING APPROVAL TO PROCEED TO PHASE N+1 ===
```

4. **Every phase must leave the repository in a runnable, reviewable state.** After Phase 2, I should be able to run the BAW pricer on its own and get sensible numbers. After Phase 4, I should be able to generate scenarios on their own. No phase may leave broken imports, stub functions that raise `NotImplementedError`, or half-written files.
5. **Do not refactor or rewrite code from a previously approved phase without asking me first.** If you think an earlier design decision needs revisiting, say so in the OPEN QUESTIONS section and wait.
6. If a phase turns out to be bigger than expected, **do not silently split or merge phases**. Tell me and let me decide.

---

## 1. PROJECT CONTEXT — WHAT WE ARE REPLICATING AND WHY

In 2013, FICC/NYPC filed a proposed rule change with the SEC (File No. **SR-FICC-2013-02**) describing the risk methodology used to margin options positions. The methodology rests on three pillars:

- **(i) Value-at-Risk via historical simulation** — 99th percentile confidence level, one-year lookback window, with **linear interpolation** to the 99% threshold between the relevant order statistics.
- **(ii) Barone-Adesi & Whaley (BAW)** approximation for pricing **American-style** options.
- **(iii) SABR (Stochastic Alpha, Beta, Rho)** for modelling the implied volatility curve.

The filing is explicit that the VaR model must account for the **non-linear risk posed by options by performing full revaluation of such options using BAW and SABR**. This is the single most important design constraint in this project:

> **Every historical scenario requires a fresh SABR-implied volatility and a fresh BAW reprice. We do NOT use a delta/gamma Taylor approximation anywhere. Full revaluation, every scenario, no shortcuts.**

If you ever find yourself tempted to approximate a scenario price with `price_today + delta * dS + 0.5 * gamma * dS**2`, stop — that is precisely the shortcut the filing forbids.

**Our adaptation:** the original filing concerned interest rate futures options. We are applying the same methodology to **SPY (SPDR S&P 500 ETF) options**. SPY options are **American-style**, which is exactly why BAW is the appropriate pricing approximation rather than plain Black-Scholes. Wherever our SPY adaptation forces a departure from what the filing describes (e.g. cost-of-carry treatment for an equity ETF versus a futures contract), you must flag it explicitly in a code comment.

### The workflow, end to end

```
SABR  →  supplies the implied volatility for a given strike/expiry
BAW   →  converts that volatility into an American option price
HISTORICAL SIMULATION  →  generates ~250 alternative "tomorrow" market states
FULL REVALUATION       →  reprices the position under each state (fresh SABR vol + fresh BAW price)
P&L                    →  scenario price minus today's price, per scenario
VaR                    →  99th percentile of the loss distribution, linearly interpolated
```

---

## 2. NON-NEGOTIABLE STRUCTURE AND SIMPLICITY REQUIREMENTS

These apply to every phase. They are not stylistic preferences; treat them as hard requirements.

### Folder structure — flat and minimal

The target structure for the finished project is roughly this, and nothing more:

```
data/                     # cached raw data pulls (CSV)
outputs/
    figures/              # all .png plots, flat, no subfolders
    tables/               # all .csv result tables
data_loader.py
sabr.py
baw.py
historical_sim.py
var.py
plots.py
main.py
requirements.txt
README.md
```

- **Do not create folders named after phases.** No `phase1/`, no `phase_2_sabr/`. The phases are a *build and review process*, not a directory layout.
- **Do not create deep nesting.** No `src/models/volatility/sabr/hagan.py`. If you think you need a subfolder, you almost certainly don't.
- Add a file only when a phase genuinely needs it. Don't pre-create empty modules.

### Naming

- Simple, obvious, lowercase, `snake_case`.
- No cryptic abbreviations (`vol_srf_clb.py` is banned; `sabr.py` is right).
- No version numbers or dates in filenames (`baw_v2.py`, `var_final.py` are banned).
- **Test:** someone opening this folder for the first time should be able to guess what each file does from its name alone, without opening it. If a filename fails that test, rename it.

### Code style

- **Every `.py` file must be readable top to bottom by someone with basic Python fluency.** Plain functions operating on plain data. No clever one-liners. No metaclasses, decorators, or abstract base classes. No premature class hierarchies for things that are just functions. A class is justified only if it holds genuinely persistent state and I've agreed to it.
- **Strict PEP 8 spacing.** Blank lines between logical blocks, two blank lines between top-level functions, spaces around binary operators, one statement per line, consistent 4-space indentation, lines under ~100 characters. Run the code through your own read-through for formatting before you present it.
- **Prefer the simpler construction every time.** If there is a version that is marginally slower but much easier to follow, write that one. This is a research and review codebase, not a production risk engine. Readability beats speed at every single step. Do not vectorise something into inscrutability to save 200ms.
- No premature optimisation, no caching layers, no parallelism, unless I ask.

### Documentation — this codebase teaches

Assume I have **basic Python fluency but am learning the financial mathematics as I read**. Write the code as a teaching artifact.

- **Every function gets a docstring** stating what it does, each input (with units and expected type), and what it returns. Where the function implements a formula from a paper, name the paper and equation.
- **Every non-trivial block gets an inline comment explaining WHAT it is doing and WHY** — the financial or mathematical reasoning, not a restatement of the syntax. `# increment i` is worthless. `# We solve for the critical price S* above which early exercise is optimal; below S*, the American call is worth the European price plus the early-exercise premium` is what I want.
- **Flag every approximation, edge case, and simplification** with a comment marked `# NOTE (deviation from filing):` or `# NOTE (approximation):` so I can grep for them. Examples of things that must be flagged: cost-of-carry treatment, whether time-to-expiry is decremented in scenarios, sticky-strike versus sticky-moneyness vol assumptions, handling of the ATM singularity in SABR, use of simple versus log returns.
- Variable names are descriptive and spelled out (`underlying_price`, `strike`, `time_to_expiry_years`, `risk_free_rate`). Single letters are permitted **only** inside tight mathematical formulas where convention demands it (`d1`, `d2`, `z`, `x_z`, `q2`) — and when you use them, define them in a comment immediately above.

---

## 3. THE PHASES

### PHASE 0 — Data sourcing and validation

**Goal:** get real data in hand and prove it is sane before anything is built on top of it.

We need two distinct datasets:

1. **SPY daily price history** — adjusted close, at least 2 years back (we need ~250 trading days for the lookback plus buffer). This is easy to source.
2. **SPY options chain snapshot** — for at least one reference date: strikes, expiries, and **market-observed implied volatilities**, plus bid/ask/last so we can validate prices later. This is the harder one.

**⚠️ MANDATORY STOP BEFORE ANY CODE — CONFIRM THE OPTIONS DATA SOURCE WITH ME.**

Before you write a single line of data-loading code, present me with a short comparison of the realistic options for sourcing the SPY options chain, covering at minimum:

- What **free** options exist (e.g. `yfinance`'s `option_chain()`), what they actually give us, and their known limitations — snapshot-only with no history, vendor-computed implied vols of uncertain provenance, stale quotes outside market hours, thin data on illiquid strikes.
- What **paid** sources exist (e.g. CBOE DataShop, ORATS, OptionMetrics/IvyDB, Polygon.io) and roughly what each costs and provides.
- Whether the free route is good enough for a methodology replication, and where it will bite us.

Then **ask me explicitly which source to use and wait for my answer.** If your recommendation requires a paid subscription, say so plainly and do not proceed until I confirm. Do not assume, do not pick a default, do not write "I'll start with yfinance and we can swap later."

**Once I have confirmed the source, build:**

- `data_loader.py` with clearly separated functions for (a) loading SPY price history and (b) loading the options chain snapshot, each caching its raw pull to `data/` as CSV so we are not hammering an API on every run and so results are reproducible.
- Validation checks with loud, human-readable failures: no missing dates in the price series beyond expected market holidays, no zero or negative prices, no absurd single-day returns (flag anything beyond ±20% for manual inspection), implied vols within a plausible range (say 2% to 200%), strikes sorted and positive, expiries in the future relative to the reference date.
- Print a compact data summary: date range, number of rows, spot price on the reference date, number of strikes and expiries retrieved, the expiries available.
- **One plot:** `outputs/figures/vol_smile_raw.png` — the raw market-observed implied volatility smile for the chosen reference date and a single chosen expiry, implied vol on the y-axis against strike on the x-axis, with the spot price marked. This is the sanity check: it should look like a smile or skew. If it looks like noise, we have a data problem and we stop here.

**Also in Phase 0:** pick and document (a) the reference expiry we will use throughout Phases 1–6, and (b) the risk-free rate and SPY dividend yield we will assume, with sources. Keep these in one obvious place — a small block of named constants at the top of `main.py` or a plain `config` dict — not scattered as magic numbers.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 1 — SABR calibration

**Goal:** fit the SABR model to the observed SPY volatility smile from Phase 0.

Create `sabr.py` containing:

- **`sabr_implied_vol(forward, strike, time_to_expiry, alpha, beta, rho, nu)`** — the Hagan et al. (2002) lognormal implied-volatility asymptotic expansion. Implement it explicitly, term by term, with comments explaining what each factor is doing:
  - the leading `alpha / ((F*K)**((1-beta)/2) * (1 + ...))` denominator expansion,
  - the `z / x(z)` factor where `z = (nu/alpha) * (F*K)**((1-beta)/2) * log(F/K)` and `x(z) = log((sqrt(1 - 2*rho*z + z**2) + z - rho) / (1 - rho))`,
  - the `(1 + [...] * T)` correction term.
  - **The at-the-money singularity is a real trap:** when `F` and `K` are close, `z → 0` and `z/x(z)` is `0/0`. Handle this with an explicit branch using the ATM limit formula, with a clearly commented tolerance. Flag it as `# NOTE (approximation):`.
- **`calibrate_sabr(strikes, market_vols, forward, time_to_expiry, beta)`** — least-squares fit of `alpha`, `rho`, `nu` to the observed smile using `scipy.optimize.least_squares`. Comment on why `beta` is conventionally **fixed rather than fitted** (it is nearly unidentifiable jointly with `rho` — the two trade off against each other) and default it to a sensible value for equity index options, stating the choice and the reasoning in a comment. Enforce parameter bounds (`alpha > 0`, `-1 < rho < 1`, `nu > 0`) and explain in comments why each bound exists economically, not just numerically. Try multiple starting points to avoid a local minimum, and report which one won.
- Report fit quality: RMSE in vol points, maximum absolute error, and the per-strike residuals. Print the calibrated parameters with a one-line plain-English interpretation of each (`rho` is the spot/vol correlation and controls skew; `nu` is vol-of-vol and controls smile curvature; `alpha` sets the overall level).

**Plot:** `outputs/figures/sabr_fit.png` — market implied vols as scatter points, SABR fitted curve as a smooth line over a dense strike grid, forward marked, RMSE in the title.

Include a short standalone runnable block so I can run `python sabr.py` and see the calibration and plot on their own.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 2 — BAW pricing engine

**Goal:** a correct, standalone, tested American option pricer. **No SABR involvement in this phase** — volatility is just an input argument.

Create `baw.py` containing:

- **`black_scholes_price(...)`** (with cost-of-carry `b`, so it doubles as Black-76 when `b = 0`) — the European baseline. Comment on `d1`, `d2` and what they represent.
- **`baw_price(underlying_price, strike, time_to_expiry, risk_free_rate, cost_of_carry, volatility, option_type)`** — the Barone-Adesi & Whaley (1987) quadratic approximation: the European price **plus an early-exercise premium**. Implement it with the intermediate quantities named and explained:
  - `M = 2*r / sigma**2`, `N = 2*b / sigma**2`, `K_baw = 1 - exp(-r*T)`,
  - the roots `q1`, `q2` of the quadratic,
  - the coefficients `A1`, `A2`,
  - the piecewise result: for a call, `european + A2 * (S/S_crit)**q2` when `S < S_crit`, else the intrinsic `S - K` (the option is optimally exercised immediately).
- **`solve_critical_price(...)`** — the iterative root-find for the critical exercise price `S*`. Use the standard Newton-Raphson scheme with the Barone-Adesi–Whaley seed value. Comment on **what `S*` means economically**: the underlying level at which immediate exercise becomes optimal. Cap the iterations, check convergence, and raise a clear error (not a silent wrong number) if it fails to converge.

**Cost of carry for SPY — flag this explicitly.** The original filing prices futures options, where `b = 0` (Black-76). SPY is a dividend-paying equity ETF, so `b = r - q`. Implement the general `b` form, default it correctly for SPY, and mark the departure with a `# NOTE (deviation from filing):` comment.

**Validation is the deliverable of this phase, not the code.** Test against known reference values and show me the results in a printed table:

- **Put-call parity** on the European baseline (should hold to machine precision).
- **American ≥ European** for every case tested, and **American ≥ intrinsic value** always.
- **American call on a non-dividend-paying underlying (`b = r`) should equal the European call** — early exercise is never optimal. This is the single sharpest test of a BAW implementation; if it fails, the implementation is wrong.
- At least one **published benchmark**: reproduce a handful of values from the original Barone-Adesi & Whaley (1987) paper's tables or from Haug's *Complete Guide to Option Pricing Formulas*, and show the comparison with absolute errors.
- Monotonicity sanity checks: price rising in volatility, call price rising in spot, put price falling in spot.

Include a standalone runnable block so `python baw.py` prints the full validation table.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 3 — Wire SABR into BAW

**Goal:** connect the two components and prove the connection is correct against real market prices.

- Write a single small, obvious function — put it in `main.py` or as a thin function in `baw.py`, your call, but justify where you put it — that takes a strike and expiry, calls `sabr_implied_vol` to get the volatility, and feeds that volatility into `baw_price` to produce a theoretical price.
- Be explicit and careful about the **forward versus spot** distinction: SABR is parameterised on the forward `F = S * exp(b * T)`, while BAW takes spot `S`. Getting this wrong is the most common bug at this junction. Comment on it clearly.
- **Sanity check against the market:** for a handful of real strikes on the reference expiry, produce a table of `strike | market IV | SABR IV | market price (mid) | BAW price | absolute diff | % diff`. Print it and save it to `outputs/tables/`.
- Discuss the discrepancies honestly in your PHASE COMPLETE block. Small differences are expected — bid-ask spread, SABR fit error, our assumed rate and dividend yield, snapshot timing mismatch between the underlying quote and the option quotes. **Large or systematic differences mean a bug, not "close enough."** If deep out-of-the-money strikes are badly off but ATM strikes match well, say so and diagnose it rather than moving on.

**Plot:** `outputs/figures/baw_vs_market_prices.png` — BAW theoretical price against market price across strikes, both series on one chart.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 4 — Historical simulation scenario generation

**Goal:** build the set of "possible tomorrow" market states. No option pricing in this phase at all.

Create `historical_sim.py`:

- **`compute_daily_returns(price_series)`** — daily percentage returns from the SPY adjusted close history. Use **simple returns** (`P_t / P_{t-1} - 1`) as the default, since we apply them multiplicatively to today's price. Comment on the alternative (log returns) and why the choice barely matters at daily horizons but should still be stated. Flag as `# NOTE (approximation):`.
- **`generate_scenarios(current_price, historical_returns, lookback_days=250)`** — take the most recent ~250 trading days (one year, matching the filing) and apply each historical day's move to **today's** SPY price: `scenario_price[i] = current_price * (1 + return[i])`. Return the scenario prices **alongside the date each return came from** — we need that mapping for the Phase 8 time-series plot, so carry it through as a small DataFrame rather than a bare array.
- Comment on the core assumption of historical simulation: we are asserting that the empirical distribution of the last year's daily moves is a reasonable sample of tomorrow's possible moves. No distributional assumption, no normality — that is the method's main appeal and its main weakness. Note explicitly that it is **unweighted** (every day in the window counts equally, so a move from 250 days ago has the same weight as yesterday's) and that the filing's approach is what we are matching here.
- Print summary statistics of the return sample: mean, standard deviation, skewness, kurtosis, min, max, and the dates of the largest up and down moves. Sanity-check that the extremes correspond to real market events.

**Plots:** `outputs/figures/spy_price_history.png` (the price path over the lookback period used, with the lookback window shaded or bounded) and a histogram of the historical daily returns.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 5 — Full revaluation loop

**Goal:** the heart of the project. **Start with exactly ONE option position — a single strike, single expiry.** Do not generalise to a portfolio yet; that is Phase 7 and I may not even want it.

For each of the ~250 scenarios:

1. Shift the underlying to the scenario price.
2. Recompute the forward `F' = S' * exp(b * T)`.
3. Get a **fresh SABR volatility** for our strike at the new forward.
4. **Reprice with BAW** using that fresh volatility — full revaluation, no Greeks-based approximation.
5. Compute scenario P&L as `scenario_price - base_price` for the position.

**The critical modelling decision in this phase — you must handle it explicitly and flag it:** when the underlying moves, what happens to the volatility surface? Implement the **baseline** as holding the calibrated SABR parameters (`alpha`, `beta`, `rho`, `nu`) fixed and re-evaluating the SABR formula at the new forward. This means our strike's implied vol changes because its *moneyness* changed — the smile is anchored to the forward and moves with it. Comment on this at length:

- Name the assumption (this is essentially **sticky-moneyness / sticky-delta**, as opposed to **sticky-strike** where each strike's vol is held constant).
- Explain that a fuller implementation would also **shock the SABR parameters themselves** using the historical evolution of the vol surface — jointly simulating spot moves and vol-surface moves from the same historical day. Note that this requires historical options data we may not have from our Phase 0 source, and mark it as `# NOTE (deviation from filing):` since the filing's full revaluation would in principle capture vol risk too.
- **Ask me in your OPEN QUESTIONS whether I want the vol-shock extension.** Do not build it unprompted.

Also flag: whether **time to expiry is decremented by one day** in the scenarios. The theoretically clean one-day-horizon answer decrements it and thereby includes theta in the P&L; holding `T` fixed isolates pure market risk. Pick one, state it loudly in a comment, and tell me which you chose and why.

Structural requirement: write this as a **plain, obvious loop over scenarios**. Clarity beats speed here. 250 BAW calls is nothing. Do not vectorise it into something unreadable.

Output a per-scenario table to `outputs/tables/` with: date, historical return, scenario underlying price, scenario SABR vol, scenario option price, scenario P&L. Print the head and tail, plus the five worst scenarios.

**Sanity checks to perform and report:** the zero-return scenario (or the nearest to it) should reprice to approximately today's price; P&L should be monotone in the underlying move for a single vanilla option; the P&L distribution should show visible **convexity** — that asymmetry is the entire reason we are doing full revaluation instead of a linear approximation, so point it out explicitly.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 6 — VaR calculation

**Goal:** the filing's exact 99% VaR, not a library shortcut.

Create `var.py`:

- **`compute_var(scenario_pnls, confidence_level=0.99)`** — sort the scenario P&Ls, locate the 99% point in the order statistics, and **linearly interpolate between the two adjacent order statistics** when the percentile does not land exactly on an observation. With 250 scenarios it generally will not, which is exactly why the filing specifies interpolation.
- **Implement the interpolation explicitly and arithmetically.** Do not call `numpy.percentile` or `numpy.quantile` to produce the answer. Show the index arithmetic in the code with comments: the fractional rank, the floor and ceiling indices, and the weighted blend. This is the whole point of matching the filing's stated methodology rather than a naive percentile call.
- **You may and should call `numpy.percentile` as a separate cross-check** and print both numbers side by side, with a comment explaining that NumPy's default interpolation mode happens to correspond to the same convention (or noting where it differs). Show me the comparison; do not use it as the result.
- Be explicit and consistent about the **sign convention**: state in a docstring whether VaR is reported as a positive loss magnitude or a negative P&L number, and stick to it everywhere in the codebase.
- Report alongside the VaR: the number of scenarios, the two order statistics bracketing the threshold with their dates, the interpolation weight, the worst single scenario in the sample and its date, and the expected shortfall (mean of the losses beyond the VaR) as a useful supplementary figure.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 7 — Generalise to a small portfolio *(conditional — ask me first)*

**Before building anything in this phase, ask me whether I actually want portfolio-level VaR.** I may be satisfied with the single-option result.

If I say yes:

- Extend to a small portfolio of a few SPY options across **several strikes and at least two expiries**, with signed positions (long and short) and position sizes.
- Represent the portfolio as a **plain list of dicts or a simple DataFrame** — no `Position` class, no `Portfolio` class, unless I explicitly agree. This is the exact point where codebases sprout unnecessary object hierarchies. Resist it.
- Handle multiple expiries correctly: **each expiry needs its own SABR calibration**, since the smile shape differs by tenor. Make this obvious in the code and comments — it is a common and expensive mistake to reuse one calibration across tenors.
- Aggregate P&L per scenario across all positions **before** computing VaR — the whole point is that diversification and offsetting positions are captured naturally by revaluing everything under the same scenario.
- Report and comment on the **diversification benefit**: portfolio VaR versus the sum of the standalone VaRs, and why the difference arises.

**STOP. Post the PHASE COMPLETE block. Wait for approval.**

---

### PHASE 8 — Visualisation suite and results compilation

**Goal:** everything presentable, reproducible with one command.

Create `plots.py` with **one clearly-named function per figure**, each saving to `outputs/figures/` as a `.png` at a readable DPI (150 or above). One plot per concept — do not combine unrelated things into subplot grids. Every figure needs a title, axis labels with units, a legend where there is more than one series, and no unexplained abbreviations.

**Required figures:**

1. `spy_price_history.png` — SPY price over the lookback period used, with the lookback window clearly indicated.
2. `sabr_fit.png` — market-observed vol smile (scatter) versus SABR-fitted curve (line), with RMSE shown.
3. `baw_vs_market_prices.png` — BAW theoretical prices versus actual market option prices across strikes.
4. `pnl_distribution.png` — histogram of the ~250 scenario P&Ls, with the **99% VaR threshold marked as a labelled vertical line**, and the worst scenario annotated.
5. `pnl_timeseries.png` — scenario P&L against the historical date that produced it, so I can see which market episodes drive the tail.
6. `summary_table.png` — a **rendered image** of the summary table (matplotlib table or equivalent — it must be an image file, not just a CSV) showing: option details (underlying, strike, expiry, type, position), today's underlying price, calibrated SABR parameters, today's theoretical price and market price, the 99% VaR estimate, the worst-case scenario in the sample with its date, and expected shortfall.

Also in this phase:

- **`main.py`** — a single top-level script that runs the entire pipeline end to end, in order, with clear printed section headers so I can follow along in the terminal. Constants and configuration in one obvious block at the top. This is the one command I should need to reproduce everything.
- **`requirements.txt`** — pinned versions.
- **`README.md`** — what the project does, the three-pillar methodology in a short paragraph, how to run it, what each `.py` file is responsible for, what each output figure shows, and a consolidated list of **every assumption and deviation from the filing** (collect all your `# NOTE` comments here in one place). Also include a short honest section on the limitations of the approach: unweighted historical simulation, single-source vol data, no vol-surface shock unless we built it, one-day horizon, no liquidity or bid-ask adjustment, no correlation across underlyings.

**STOP. Post the final PHASE COMPLETE block.**

---

## 4. FINAL REMINDERS

- **One phase at a time. Stop after each. Wait for "approved, continue to Phase N+1".**
- **Full revaluation always** — fresh SABR vol and fresh BAW price for every scenario. Never a delta/gamma approximation.
- **Flag every approximation and deviation** with a `# NOTE` comment and collect them in the README.
- **Teach through the code.** Comments explain the finance and the mathematics, not the syntax.
- **Flat structure, obvious names, plain functions, strict PEP 8 spacing, readability over cleverness — every time.**
- If anything in this brief is ambiguous, **ask me before building**. A clarifying question costs a minute; a wrong phase costs an afternoon.

**Begin with Phase 0. Your first output should be the options data source comparison and your question to me about which source to use — no code yet.**
