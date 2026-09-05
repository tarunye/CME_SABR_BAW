# SPY Options VaR — replicating the SR-FICC-2013-02 margining methodology

A research codebase that reproduces the options margining methodology described in the
2013 FICC/NYPC rule filing with the SEC (File No. **SR-FICC-2013-02**), applied to SPY
options instead of the filing's interest rate futures options.

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

**The raw data is not in this repository.** It is roughly 600 MB across fourteen files,
which is too large for GitHub, and it is redistributed under terms we do not hold.

The project uses **SPY end-of-day option chains covering 2010–2023**, originally published
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

## Running it

```bash
pip install -r requirements.txt      # (added in Phase 8)
python3 main.py
```

That runs the whole pipeline and writes every figure and table. First run parses the
parquet files and caches derived pulls to `data/*.csv`; later runs read the caches. Delete
those CSVs to force a rebuild.

Several modules also run on their own:

```bash
python3 sabr.py            # SABR calibration and fit plot
python3 baw.py             # the full BAW validation suite (~30s)
python3 historical_sim.py  # scenario generation and return statistics
```

## What each file does

| File | Responsibility |
|---|---|
| `data_loader.py` | Loading and validation: price history, option chains, the NYSE trading calendar, the put-call-parity forward, and the out-of-the-money smile |
| `sabr.py` | Hagan et al. (2002) implied volatility expansion and least-squares calibration |
| `baw.py` | Barone-Adesi & Whaley American pricer, European baseline, critical-price solver, a binomial reference pricer, and the validation suite |
| `historical_sim.py` | Daily returns, scenario generation, and the return-sample diagnostics |
| `plots.py` | One function per figure, all saving to `outputs/figures/` |
| `main.py` | The `CONFIG` block holding every assumption, and the phase-by-phase pipeline |
| `PROJECT_LOG.md` | Running record of what each phase built, every decision and why, and the consolidated list of assumptions and deviations |

## Reading the results

Start with **`PROJECT_LOG.md`**. It is the honest account of the project: what was built in
each phase, the key finding from each, every judgement call with its reasoning, the bugs
found along the way, and a consolidated table of every approximation and deviation from
the filing.

Outputs land in `outputs/figures/` and `outputs/tables/`. These *are* committed, so the
repository is reviewable without the underlying dataset.

## Status

Phases 0–5 complete: data sourcing and validation, SABR calibration, the BAW pricing
engine, wiring the two together against market prices, historical simulation, and the full
revaluation loop. Phase 6 (VaR) and Phase 8 (visualisation suite, `requirements.txt`, and
the consolidated write-up) are still to come, at which point this README will be expanded.
