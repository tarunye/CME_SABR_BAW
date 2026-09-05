"""
Top-level pipeline for the SPY options VaR replication (SR-FICC-2013-02 methodology).

Run this file to reproduce every result in the project:

    python main.py

All configuration lives in the CONFIG block immediately below. There are no magic numbers
scattered through the other modules -- anything you might want to change is here.
"""

import os

import pandas as pd

import data_loader
import plots


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

}


def print_section(title):
    """
    Print a labelled section header so the terminal output is navigable.

    Inputs:
        title (str): the section name.

    Returns:
        None.
    """
    print()
    print("=" * 86)
    print(title)
    print("=" * 86)


def run_phase_0(config):
    """
    Phase 0: load the raw data, validate it, and confirm the volatility smile is real.

    Everything later in the project stands on this data being sound, so this phase does
    no modelling at all -- it loads, checks, summarises, and draws one diagnostic plot.

    Inputs:
        config (dict): the CONFIG block above.

    Returns:
        dict carrying the loaded objects the later phases will need:
            'prices'        (DataFrame) the daily SPY close series
            'chain'         (DataFrame) the full option chain snapshot, all expiries
            'expiry_chain'  (DataFrame) the reference expiry only
            'smile'         (DataFrame) the filtered out-of-the-money smile
            'spot'          (float)     SPY spot on the reference date
            'forward'       (float)     implied forward at the reference expiry
            'time_to_expiry_years' (float)
            'cost_of_carry' (float)     b, where F = S * exp(b * T)
    """
    reference_date = config["reference_date"]
    expiry_date = config["reference_expiry"]

    os.makedirs(config["data_dir"], exist_ok=True)
    os.makedirs(config["figures_dir"], exist_ok=True)
    os.makedirs(config["tables_dir"], exist_ok=True)

    # ---- Load ------------------------------------------------------------------------
    print_section("PHASE 0.1  LOADING DATA")

    price_cache = os.path.join(config["data_dir"], "spy_price_history.csv")
    prices = data_loader.load_spy_price_history(
        data_dir=config["data_dir"],
        start_date=config["price_history_start"],
        end_date=reference_date,
        cache_path=price_cache,
    )
    print(f"  Loaded SPY price history ({len(prices)} rows) -> cached at {price_cache}")

    chain_cache = os.path.join(config["data_dir"], f"options_chain_{reference_date}.csv")
    chain = data_loader.load_options_chain(
        data_dir=config["data_dir"],
        reference_date=reference_date,
        cache_path=chain_cache,
    )
    print(f"  Loaded option chain snapshot ({len(chain)} rows) -> cached at {chain_cache}")

    expiry_chain = data_loader.select_expiry(chain, expiry_date)

    # ---- Validate --------------------------------------------------------------------
    print_section("PHASE 0.2  VALIDATION")

    data_loader.validate_price_history(prices, lookback_days=config["lookback_days"])
    data_loader.validate_options_chain(expiry_chain, reference_date, expiry_date)

    # ---- Derive the forward ----------------------------------------------------------
    print_section("PHASE 0.3  RATES, CARRY AND THE FORWARD")

    spot = float(expiry_chain["underlying_price"].iloc[0])
    time_to_expiry_years = data_loader.year_fraction_to_expiry(reference_date, expiry_date)

    forward_result = data_loader.imply_forward_from_parity(
        single_expiry_chain=expiry_chain,
        underlying_price=spot,
        risk_free_rate=config["risk_free_rate"],
        time_to_expiry_years=time_to_expiry_years,
        moneyness_band=config["parity_moneyness_band"],
        max_spread=config["parity_max_spread"],
    )

    forward = forward_result["forward"]

    print(f"  Risk-free rate (assumed, FRED DGS3MO)   : {config['risk_free_rate']:.4%}")
    print(f"  Time to expiry (ACT/365)                : {time_to_expiry_years:.5f} years "
          f"({round(time_to_expiry_years * 365)} calendar days)")
    print(f"  SPY spot                                : ${spot:.2f}")
    print(f"  Implied forward (put-call parity)       : ${forward:.3f}")
    print(f"    strikes used                          : {forward_result['n_strikes_used']}")
    print(f"    dispersion across strikes (std)       : ${forward_result['forward_std']:.3f}")
    print(f"  Implied dividend yield (derived)        : "
          f"{forward_result['implied_dividend_yield']:+.4%}")
    print(f"  Cost of carry b, F = S*exp(b*T)         : "
          f"{forward_result['cost_of_carry']:.4%}")
    print()
    print("  Reading: the implied dividend yield is near zero because no SPY ex-dividend")
    print("  date falls between 2023-12-29 and 2024-02-16 (Dec ex-div was the 15th, the")
    print("  next is 2024-03-15). Assuming SPY's ~1.4% trailing yield here would have")
    print("  mispriced the forward by roughly $0.90 and tilted the whole smile.")

    # ---- Build the smile -------------------------------------------------------------
    print_section("PHASE 0.4  DATA SUMMARY")

    smile = data_loader.build_otm_smile(expiry_chain, forward=forward)

    data_loader.summarise_price_history(prices)
    print()
    data_loader.summarise_options_chain(chain, expiry_chain, smile, reference_date,
                                        expiry_date)

    # ---- Plot ------------------------------------------------------------------------
    print_section("PHASE 0.5  DIAGNOSTIC PLOT")

    plots.plot_raw_vol_smile(
        smile=smile,
        spot=spot,
        forward=forward,
        reference_date=reference_date,
        expiry_date=expiry_date,
        output_path=os.path.join(config["figures_dir"], "vol_smile_raw.png"),
    )

    # Save the filtered smile as a table too -- it is the direct input to the Phase 1
    # SABR calibration, and having it on disk makes that phase reviewable on its own.
    smile_table_path = os.path.join(config["tables_dir"], "market_smile.csv")
    smile.to_csv(smile_table_path, index=False)
    print(f"  Saved table:  {smile_table_path}")

    return {
        "prices": prices,
        "chain": chain,
        "expiry_chain": expiry_chain,
        "smile": smile,
        "spot": spot,
        "forward": forward,
        "time_to_expiry_years": time_to_expiry_years,
        "cost_of_carry": forward_result["cost_of_carry"],
    }


def main():
    """
    Run the full pipeline end to end.

    Returns:
        None. Prints progress and writes figures and tables under outputs/.
    """
    print_section("SPY OPTIONS VaR  --  SR-FICC-2013-02 METHODOLOGY REPLICATION")
    print(f"  Reference date : {CONFIG['reference_date']}")
    print(f"  Reference expiry: {CONFIG['reference_expiry']}")

    run_phase_0(CONFIG)

    print_section("PHASE 0 COMPLETE")


if __name__ == "__main__":
    main()
