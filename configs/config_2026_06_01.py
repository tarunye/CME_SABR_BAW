"""
Configuration for the SECOND reference case: 2026-06-01, Databento-sourced.

An independent replication of the same methodology on a different data source, a
different market regime and a different position. Every value that differs from
configs/config_2023_12_29.py is marked and explained; everything unmarked is
deliberately identical, because the point of this case is to change the DATA and
the DATE, not the method.

Run it with:

    python3 main.py config_2026_06_01
"""

import os

CONFIG = {
    # ---- Paths -----------------------------------------------------------------------
    # DIFFERS: the Databento-sourced dataset, built by databento_pullers/. It carries the
    # same bracketed column layout as the OptionsDX files, so data_loader reads it with
    # no code change; only this path moves.
    "data_dir": os.path.join("data", "databento_2025_2026"),

    # DIFFERS: this case writes to its own output directories. The 2023-12-29 figures and
    # tables are committed results referenced throughout PROJECT_LOG.md and README.md,
    # and a second run must not overwrite them.
    "figures_dir": os.path.join("outputs", "2026-06-01", "figures"),
    "tables_dir": os.path.join("outputs", "2026-06-01", "tables"),

    # ---- Reference date and expiry ---------------------------------------------------
    # DIFFERS. 2026-06-01 is the last session in the Databento pull. As with the original,
    # working from the end of the dataset puts a full 250-day lookback behind the
    # reference date with nothing wasted.
    "reference_date": "2026-06-01",

    # DIFFERS. 2026-07-17 is a standard third-Friday monthly expiry, 46 calendar days out
    # -- the closest analogue available to the original's 49-day 2024-02-16. Chosen on the
    # same grounds: 209 quoted strikes, a forward pinned to a $0.27 standard deviation
    # across 45 strikes, and a 197-strike out-of-the-money smile.
    "reference_expiry": "2026-07-17",

    # ---- Historical simulation window ------------------------------------------------
    # Identical to the original: the filing specifies a one-year lookback and 250 trading
    # days is the conventional count.
    "lookback_days": 250,

    # DIFFERS: the Databento dataset begins here. 353 sessions to 2026-06-01, so the
    # 250-day window fits with 103 sessions to spare.
    "price_history_start": "2025-01-02",

    # DIFFERS, and this is the one place where the second case is genuinely weaker than
    # the first. The original compared six lookback windows spanning 2011 to 2023, because
    # fourteen years of OptionsDX history sat behind it. This dataset spans seventeen
    # months, so only sessions from 2025-12-31 onward have a full 250-day window behind
    # them at all.
    #
    # What that narrow range does still show is the single most important property of
    # unweighted historical simulation, and it shows it more sharply than the original
    # could: the April 2025 tariff selloff (-5.85% on the 4th, +10.50% on the 9th) sits at
    # sessions 63-68, so it is INSIDE the window for the earlier dates below and has AGED
    # OUT of it by the reference date. The 1st-percentile return roughly halves, from
    # -3.58% to -1.75%, purely because the crisis fell off the back of the window. That is
    # the same effect the original demonstrated by comparing 2020 against 2023, observed
    # here within a single continuous dataset.
    "window_comparison_start": "2025-01-02",
    "window_comparison_dates": [
        "2025-12-31",  # earliest date with a full window; April 2025 fully inside
        "2026-01-30",  # April 2025 still inside
        "2026-03-16",  # April 2025 still inside, near the back edge
        "2026-04-28",  # April 2025 has just aged out
        "2026-06-01",  # ours -- calm window, no crisis
    ],

    # ---- Rates and carry -------------------------------------------------------------
    # DIFFERS. 3-month Treasury constant maturity yield on 2026-06-01, the same FRED
    # DGS3MO series and the same external-pin methodology as the original's 5.40%. Rates
    # fell over the intervening period; this is not an assumption about the option market.
    "risk_free_rate": 0.0366,

    # As in the original, the dividend yield and cost of carry are NOT set here. They are
    # IMPLIED from put-call parity on the quotes at run time. Note that unlike the
    # original -- where no SPY ex-dividend date fell between reference and expiry, so the
    # implied yield came out near zero -- SPY's June 2026 ex-dividend date DOES fall
    # inside 2026-06-01 to 2026-07-17. The implied yield should therefore come out
    # materially positive here, and if it does not, something upstream is wrong.

    # ---- Quote quality filters -------------------------------------------------------
    # Identical to the original. Databento's quotes are tighter than the vendor's (the
    # Phase 9 cross-check found zero crossed quotes against the vendor's two on the
    # reference date), so a filter calibrated on the noisier source is not binding here.
    "parity_moneyness_band": 0.05,
    "parity_max_spread": 0.30,

    # ---- The position we are measuring risk on ---------------------------------------
    # DIFFERS in strike only, and for exactly the reason the original chose 475: it is the
    # closest listed strike to spot. Spot is $758.54 and 759 is $0.46 away, quoted
    # 14.34/14.38 -- a four-cent spread, among the tightest on the board.
    #
    # A put again, and again deliberately. Whether the American machinery does anything
    # visible depends on the sign of b - r, which is derived at run time rather than
    # assumed; with a June ex-dividend date inside this window b should come out BELOW r,
    # which is the opposite of the original's regime. That makes the put's early-exercise
    # premium larger here, not smaller, so the choice holds.
    "position": {
        "strike": 759.0,
        "option_type": "put",
        "quantity": 1.0,          # signed: positive is long, negative is short
    },

    # DIFFERS. The original's stress window was 2020, which this dataset cannot reach. The
    # harshest window available is the earliest one -- 2025-01-02 to 2025-12-31 -- which
    # contains the April 2025 selloff in full: 1st percentile -3.58% against the baseline
    # window's -1.75%, worst day -5.85% against -2.70%.
    "stress_window_end": "2025-12-31",

    # Identical: the filing specifies a 99th percentile confidence level.
    "confidence_level": 0.99,

    # ---- SABR ------------------------------------------------------------------------
    # Identical to the original. beta is fixed at the equity-index convention rather than
    # fitted, and the calibration band is unchanged, because changing the model between
    # the two cases would confound a data comparison with a method comparison.
    "sabr_beta": 1.0,
    "calibration_moneyness_band": 0.15,
}
