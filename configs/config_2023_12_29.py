"""
Configuration for the ORIGINAL reference case: 2023-12-29, OptionsDX-sourced.

This is the run recorded in PROJECT_LOG.md and README.md -- 99% one-day VaR of
$2.4488 on a long 1 x SPY 2024-02-16 $475 put. The values below were lifted out
of main.py unchanged when configuration was moved into its own files; not one
of them was altered, because this run is already complete and approved.

Run it with:

    python3 main.py config_2023_12_29        (or just: python3 main.py)
"""

import os



# ======================================================================================
# CONFIGURATION
# ======================================================================================
#
# Every assumption in the project is stated here, with its source. Nothing below this
# block invents a constant of its own.

CONFIG = {
    # ---- Paths -----------------------------------------------------------------------
    "data_dir": "data",
    "figures_dir": os.path.join("outputs", "figures"),
    "tables_dir": os.path.join("outputs", "tables"),

    # ---- Reference date and expiry ---------------------------------------------------
    # 2023-12-29 is the last quote date in the OptionsDX files we hold. Working from the
    # end of the dataset means the 250-day historical lookback lands almost exactly on
    # calendar year 2023, which makes the VaR window easy to describe and to check.
    "reference_date": "2023-12-29",

    # 2024-02-16 is a standard third-Friday monthly expiry, 49 calendar days out. It was
    # chosen for liquidity: 156 quoted strikes from $210 to $575, tight two-sided markets
    # through the at-the-money region, and no SPY ex-dividend date inside the window.
    "reference_expiry": "2024-02-16",

    # ---- Historical simulation window ------------------------------------------------
    # The filing specifies a one-year lookback for the historical simulation. 250 trading
    # days is the conventional count for one year. We load extra history so that the
    # return series has a full lookback available with room to spare.
    "lookback_days": 250,
    "price_history_start": "2022-01-03",

    # Used only for the Phase 4 diagnostic showing how much the risk estimate depends on
    # which twelve months happen to precede the reference date. Not part of the VaR.
    "window_comparison_start": "2010-01-04",
    "window_comparison_dates": [
        "2011-12-30",  # post-crisis, European sovereign debt stress
        "2015-12-31",  # calm, before the August 2015 flash crash aged out
        "2018-12-31",  # the February volatility spike and the Q4 selloff
        "2020-12-31",  # the COVID crash sits squarely inside this window
        "2022-12-30",  # the 2022 bear market
        "2023-12-29",  # ours
    ],

    # ---- Rates and carry -------------------------------------------------------------
    # 3-month Treasury constant maturity yield on 2023-12-29 (FRED series DGS3MO).
    # We pin the rate externally because a Treasury yield is directly observable and
    # uncontroversial, whereas the dividend yield is not -- see below.
    "risk_free_rate": 0.0540,

    # The dividend yield and the cost of carry are NOT set here. They are IMPLIED from
    # put-call parity on the option quotes themselves at run time, so that the forward we
    # hand to SABR is the forward the option market is actually trading. Assuming SPY's
    # ~1.4% trailing dividend yield would be wrong for this particular expiry: SPY's
    # December 2023 ex-dividend date was the 15th and the next is 2024-03-15, so NO
    # dividend falls between our reference date and our expiry. The implied yield should
    # therefore come out near zero, and it does. See `imply_forward_from_parity`.

    # ---- Quote quality filters -------------------------------------------------------
    # Used when implying the forward: only strikes within this band of spot, with both
    # legs quoted no wider than this, are trusted.
    "parity_moneyness_band": 0.05,
    "parity_max_spread": 0.30,

    # ---- The position we are measuring risk on ---------------------------------------
    # ONE option, as the filing's methodology is demonstrated on a single position first.
    # A put rather than a call, deliberately: with b = 5.5926% > r = 5.4000% (no SPY
    # dividend falls inside this window) early exercise on a CALL is never optimal, so BAW
    # would collapse to the European price and the American machinery would do nothing
    # visible. The put carries a genuine early-exercise premium of about $0.29 on a $6.47
    # price. The 475 strike is the closest listed strike to spot, and its quotes are among
    # the tightest on the board.
    "position": {
        "strike": 475.0,
        "option_type": "put",
        "quantity": 1.0,          # signed: positive is long, negative is short
    },

    # A second, harsher lookback window run alongside the baseline for context. The 2023
    # window contains no crisis (see Phase 4), so the headline VaR is benign; this shows
    # what the same position and the same method produce when the window does contain one.
    "stress_window_end": "2020-12-31",

    # The filing specifies a 99th percentile confidence level.
    "confidence_level": 0.99,

    # ---- SABR ------------------------------------------------------------------------
    # beta is FIXED, not fitted, because it is close to jointly unidentifiable with rho --
    # both control skew and trade off against each other. 1.0 is the equity index
    # convention: it makes the forward process lognormal and leaves rho as the sole,
    # interpretable driver of skew. See the long note in `sabr.calibrate_sabr`.
    "sabr_beta": 1.0,

    # Only strikes within this fraction of the forward are calibrated to. The full smile
    # runs from -53% to +20% moneyness, and both extremes are bad fit targets: Hagan's
    # formula is an ASYMPTOTIC expansion that degrades far from the money, and 35 of the
    # 151 quoted strikes have a mid below $0.10 with a median relative spread of 40% --
    # quote noise that would carry equal weight in an unweighted least-squares fit and
    # drag the at-the-money region off. See the Phase 1 sensitivity table.
    "calibration_moneyness_band": 0.15,
}
