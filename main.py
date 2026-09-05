"""
Top-level pipeline for the SPY options VaR replication (SR-FICC-2013-02 methodology).

Run this file to reproduce every result in the project:

    python main.py

All configuration lives in the CONFIG block immediately below. There are no magic numbers
scattered through the other modules -- anything you might want to change is here.
"""

import os

import numpy as np
import pandas as pd

import baw
import data_loader
import historical_sim
import plots
import sabr


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

    # Alongside it, one row recording the market state everything else is derived from.
    # This exists so that `python3 sabr.py` can reconstruct the reference market without
    # importing main.py, which would make the import graph circular.
    state_table_path = os.path.join(config["tables_dir"], "reference_market_state.csv")
    pd.DataFrame([{
        "reference_date": reference_date,
        "expiry_date": expiry_date,
        "spot": spot,
        "forward": forward,
        "time_to_expiry_years": time_to_expiry_years,
        "risk_free_rate": config["risk_free_rate"],
        "cost_of_carry": forward_result["cost_of_carry"],
        "implied_dividend_yield": forward_result["implied_dividend_yield"],
        "sabr_beta": config["sabr_beta"],
        "calibration_moneyness_band": config["calibration_moneyness_band"],
    }]).to_csv(state_table_path, index=False)
    print(f"  Saved table:  {state_table_path}")

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


def run_phase_1(config, phase_0_results):
    """
    Phase 1: calibrate the SABR model to the observed volatility smile.

    Inputs:
        config (dict):           the CONFIG block above.
        phase_0_results (dict):  output of `run_phase_0`.

    Returns:
        dict carrying:
            'calibration' (dict)      output of `sabr.calibrate_sabr`
            'fit_smile'   (DataFrame) the strikes actually calibrated to
    """
    smile = phase_0_results["smile"]
    forward = phase_0_results["forward"]
    time_to_expiry_years = phase_0_results["time_to_expiry_years"]
    beta = config["sabr_beta"]
    band = config["calibration_moneyness_band"]

    # ---- Choose the calibration strikes ----------------------------------------------
    print_section("PHASE 1.1  CALIBRATION RANGE")

    moneyness = smile["strike"] / forward - 1.0
    fit_smile = smile.loc[moneyness.abs() <= band].reset_index(drop=True)

    print(f"  Full smile             : {len(smile)} strikes, moneyness "
          f"{moneyness.min():+.1%} to {moneyness.max():+.1%}")
    print(f"  Calibration band       : |moneyness| <= {band:.0%}")
    print(f"  Strikes calibrated to  : {len(fit_smile)} "
          f"(${fit_smile['strike'].min():.0f} to ${fit_smile['strike'].max():.0f})")
    print(f"  Cheapest quote in band : ${fit_smile['mid'].min():.2f} mid")

    # ---- Fit --------------------------------------------------------------------------
    print_section("PHASE 1.2  SABR FIT")

    calibration = sabr.calibrate_sabr(
        strikes=fit_smile["strike"].to_numpy(),
        market_vols=fit_smile["market_iv"].to_numpy(),
        forward=forward,
        time_to_expiry=time_to_expiry_years,
        beta=beta,
    )

    sabr.describe_calibration(calibration)

    # ---- Per-strike residuals ---------------------------------------------------------
    print_section("PHASE 1.3  PER-STRIKE RESIDUALS")

    residual_table = pd.DataFrame({
        "strike": fit_smile["strike"],
        "option_type": fit_smile["option_type"],
        "moneyness": fit_smile["strike"] / forward - 1.0,
        "market_iv": fit_smile["market_iv"],
        "sabr_iv": calibration["fitted_vols"],
        "residual_vol_points": calibration["residuals"] * 100,
        "mid": fit_smile["mid"],
    })

    # Print every fifth strike so the table fits on a screen while still spanning the
    # whole range, then the five largest misses in full.
    print("  Every 5th strike:")
    print(residual_table.iloc[::5].to_string(index=False, float_format=lambda x: f"{x:.5f}"))

    worst = residual_table.reindex(
        residual_table["residual_vol_points"].abs().sort_values(ascending=False).index
    ).head(5)
    print()
    print("  Five largest absolute residuals:")
    print(worst.to_string(index=False, float_format=lambda x: f"{x:.5f}"))

    # These filenames say "vendor_iv" because Phase 3 supersedes this calibration with one
    # fitted to volatilities we invert ourselves. Both are kept on disk so the before and
    # after can be compared, but only Phase 3's writes the unqualified filename that the
    # rest of the project treats as the answer.
    residual_path = os.path.join(config["tables_dir"],
                                 "sabr_fit_diagnostics_vendor_iv.csv")
    residual_table.to_csv(residual_path, index=False)

    parameter_path = os.path.join(config["tables_dir"], "sabr_calibration_vendor_iv.csv")
    pd.DataFrame([{
        "reference_date": config["reference_date"],
        "expiry_date": config["reference_expiry"],
        "forward": forward,
        "time_to_expiry_years": time_to_expiry_years,
        "alpha": calibration["alpha"],
        "beta": calibration["beta"],
        "rho": calibration["rho"],
        "nu": calibration["nu"],
        "rmse_vol_points": calibration["rmse"] * 100,
        "max_abs_error_vol_points": calibration["max_abs_error"] * 100,
        "n_strikes": calibration["n_strikes"],
        "calibration_moneyness_band": band,
    }]).to_csv(parameter_path, index=False)

    # ---- Numerical checks -------------------------------------------------------------
    print_section("PHASE 1.4  NUMERICAL CHECKS")

    # The at-the-money branch is the one place the formula has an explicit `if`, so it is
    # the one place a silent step could hide. Confirm the two sides agree, after allowing
    # for the fact that the smile genuinely slopes.
    continuity = sabr.check_atm_branch_continuity(
        forward=forward,
        time_to_expiry=time_to_expiry_years,
        alpha=calibration["alpha"],
        beta=calibration["beta"],
        rho=calibration["rho"],
        nu=calibration["nu"],
    )
    print(f"  ATM branch continuity  : observed gap {continuity['observed_gap'] * 100:.3e} "
          f"vol points")
    print(f"                           explained by genuine skew "
          f"{continuity['expected_gap'] * 100:.3e}")
    print(f"                           UNEXPLAINED EXCESS "
          f"{continuity['excess'] * 100:.3e} vol points  <- must be ~0")

    # A calibrated equity index smile must be downward sloping at the money: that is the
    # skew, and it is the whole reason we are not using a single Black-Scholes vol.
    slope_step = forward * 0.01
    vol_below = sabr.sabr_implied_vol(
        forward, forward - slope_step, time_to_expiry_years,
        calibration["alpha"], calibration["beta"], calibration["rho"], calibration["nu"],
    )
    vol_above = sabr.sabr_implied_vol(
        forward, forward + slope_step, time_to_expiry_years,
        calibration["alpha"], calibration["beta"], calibration["rho"], calibration["nu"],
    )
    skew_per_percent = (vol_above - vol_below) / 2.0
    print(f"  ATM skew               : {skew_per_percent * 100:+.4f} vol points per 1% "
          f"move in strike")
    print(f"                           (negative = downward skew, as an equity index "
          f"should be)")

    # ---- Is the RMSE actually good? --------------------------------------------------
    # An RMSE means nothing without knowing how clean the data was. Compare it against
    # the market smile's own strike-to-strike jitter.
    print()
    roughness = data_loader.smile_local_roughness(fit_smile)
    print(f"  DATA NOISE FLOOR (RMS strike-to-strike jitter in the quoted vols):")
    print(f"    overall              : {roughness['overall'] * 100:.4f} vol points")
    print(f"    put wing             : {roughness['put'] * 100:.4f} vol points")
    print(f"    call wing            : {roughness['call'] * 100:.4f} vol points")
    print(f"    SABR fit RMSE        : {calibration['rmse'] * 100:.4f} vol points")

    # ---- Do the two wings agree with each other? -------------------------------------
    crossover = data_loader.put_call_crossover_step(fit_smile)

    if crossover is not None:
        print()
        print(f"  PUT/CALL CROSSOVER CONSISTENCY:")
        print(f"    put wing ends at K=${crossover['last_put_strike']:.0f}, "
              f"call wing starts at K=${crossover['first_call_strike']:.0f}")
        print(f"    strike gap across crossover: ${crossover['strike_gap']:.0f}")
        print(f"    ADJACENT GAP (robust)      : "
              f"{crossover['adjacent_gap'] * 100:+.4f} vol points")
        print(f"    local slope predicts       : "
              f"{crossover['slope_implied_gap'] * 100:+.4f} vol points")
        print(f"    extrapolated step (fragile): "
              f"{crossover['extrapolated_step'] * 100:+.4f} vol points")
        print(f"    The smile slopes DOWN here, so a consistent smile needs a NEGATIVE")
        print(f"    adjacent gap. A positive one reverses the local slope -- a kink no")
        print(f"    smooth curve can fit. See the per-side fits below.")

    # ---- Fit each wing on its own ----------------------------------------------------
    # If each wing individually fits to its own noise floor while the combined fit does
    # not, the residual is the two wings disagreeing, not the model failing.
    print()
    print(f"  PER-SIDE FITS (same band, same beta):")
    print(f"    {'side':>16}  {'strikes':>7}  {'RMSE':>9}  {'rho':>9}")

    for label, side_smile in [
        ("both wings", fit_smile),
        ("puts only", fit_smile.loc[fit_smile["option_type"] == "put"]),
        ("calls only", fit_smile.loc[fit_smile["option_type"] == "call"]),
    ]:
        side_fit = sabr.calibrate_sabr(
            strikes=side_smile["strike"].to_numpy(),
            market_vols=side_smile["market_iv"].to_numpy(),
            forward=forward,
            time_to_expiry=time_to_expiry_years,
            beta=beta,
        )
        print(f"    {label:>16}  {side_fit['n_strikes']:>7d}  "
              f"{side_fit['rmse'] * 100:>8.4f}  {side_fit['rho']:>+9.4f}")

    # ---- Sensitivity to the two judgement calls --------------------------------------
    print_section("PHASE 1.5  SENSITIVITY TO THE CALIBRATION CHOICES")

    print("  Fit quality across calibration ranges (beta fixed at "
          f"{beta:.2f}):")
    print(f"    {'band':>6}  {'strikes':>7}  {'RMSE':>9}  {'max err':>9}  "
          f"{'alpha':>8}  {'rho':>9}  {'nu':>7}")

    for trial_band in [0.05, 0.10, 0.15, 0.20, 0.30, 1.00]:
        trial_smile = smile.loc[moneyness.abs() <= trial_band]
        trial = sabr.calibrate_sabr(
            strikes=trial_smile["strike"].to_numpy(),
            market_vols=trial_smile["market_iv"].to_numpy(),
            forward=forward,
            time_to_expiry=time_to_expiry_years,
            beta=beta,
        )
        marker = "  <- chosen" if abs(trial_band - band) < 1e-9 else ""
        print(f"    {trial_band:>5.0%}  {trial['n_strikes']:>7d}  "
              f"{trial['rmse'] * 100:>8.4f}  {trial['max_abs_error'] * 100:>8.4f}  "
              f"{trial['alpha']:>8.4f}  {trial['rho']:>+9.4f}  {trial['nu']:>7.4f}"
              f"{marker}")

    print()
    print(f"  Fit quality across beta (band fixed at {band:.0%}):")
    print(f"    {'beta':>6}  {'RMSE':>9}  {'max err':>9}  {'alpha':>8}  {'rho':>9}  "
          f"{'nu':>7}")

    for trial_beta in [0.0, 0.3, 0.5, 0.7, 1.0]:
        trial = sabr.calibrate_sabr(
            strikes=fit_smile["strike"].to_numpy(),
            market_vols=fit_smile["market_iv"].to_numpy(),
            forward=forward,
            time_to_expiry=time_to_expiry_years,
            beta=trial_beta,
        )
        marker = "  <- chosen" if abs(trial_beta - beta) < 1e-9 else ""
        print(f"    {trial_beta:>6.2f}  {trial['rmse'] * 100:>8.4f}  "
              f"{trial['max_abs_error'] * 100:>8.4f}  {trial['alpha']:>8.4f}  "
              f"{trial['rho']:>+9.4f}  {trial['nu']:>7.4f}{marker}")

    print()
    print("  Reading: near-identical RMSE across beta is exactly the identification")
    print("  problem described in `calibrate_sabr` -- rho slides to compensate for beta,")
    print("  so the data cannot tell them apart. That is why beta is fixed by convention.")

    # ---- Plot -------------------------------------------------------------------------
    print_section("PHASE 1.6  FIT PLOT")

    plots.plot_sabr_fit(
        fit_smile=fit_smile,
        full_smile=smile,
        calibration=calibration,
        forward=forward,
        time_to_expiry=time_to_expiry_years,
        spot=phase_0_results["spot"],
        reference_date=config["reference_date"],
        expiry_date=config["reference_expiry"],
        output_path=os.path.join(config["figures_dir"], "sabr_fit.png"),
    )
    print(f"  Saved table:  {residual_path}")
    print(f"  Saved table:  {parameter_path}")

    return {
        "calibration": calibration,
        "fit_smile": fit_smile,
    }


def price_option_with_sabr(strike, underlying_price, forward, time_to_expiry,
                           risk_free_rate, cost_of_carry, calibration, option_type):
    """
    The junction of the two models: SABR supplies a volatility, BAW turns it into a price.

    This is the single function the whole project has been building towards. Everything
    before it produces one of its inputs; Phase 5 calls it once per historical scenario.

    THE FORWARD-VERSUS-SPOT DISTINCTION -- READ THIS BEFORE CHANGING ANYTHING
    ------------------------------------------------------------------------
    The two models are parameterised on DIFFERENT underlying quantities, and mixing them up
    is the most common bug at this junction. It is also a quiet one: the code runs, the
    prices look plausible, and the smile is simply shifted sideways.

        SABR takes the FORWARD.  F = S * exp(b * T) = $478.89 for our reference expiry.
        BAW takes the SPOT.      S = $475.31.

    They differ by $3.58 here -- more than three strike increments -- because rates were
    at 5.4% and no dividend falls inside the window. Passing spot to SABR would evaluate
    the smile at the wrong moneyness for every strike; passing the forward to BAW would
    have the pricer discount an underlying it does not own.

    Both are handled below by taking them as separate arguments, so that no part of this
    function has to infer one from the other.

    Inputs:
        strike (float):            K, in dollars.
        underlying_price (float):  spot S, in dollars. Goes to BAW.
        forward (float):           F, in dollars. Goes to SABR.
        time_to_expiry (float):    T, in years.
        risk_free_rate (float):    r, decimal.
        cost_of_carry (float):     b, decimal, consistent with F = S * exp(b * T).
        calibration (dict):        output of `sabr.calibrate_sabr`.
        option_type (str):         'call' or 'put'.

    Returns:
        tuple (volatility, price): the SABR implied volatility as a decimal, and the BAW
        American option price in dollars.
    """
    # Step 1: SABR reads the smile at this strike, given where the FORWARD is.
    volatility = sabr.sabr_implied_vol(
        forward=forward,
        strike=strike,
        time_to_expiry=time_to_expiry,
        alpha=calibration["alpha"],
        beta=calibration["beta"],
        rho=calibration["rho"],
        nu=calibration["nu"],
    )

    # Step 2: BAW converts that volatility into an American price, from SPOT.
    price = baw.baw_price(
        underlying_price=underlying_price,
        strike=strike,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
        volatility=volatility,
        option_type=option_type,
    )

    return volatility, price


def reimply_smile_volatilities(smile, underlying_price, time_to_expiry, risk_free_rate,
                               cost_of_carry):
    """
    Replace the vendor's implied volatilities with our own, inverted from mid prices.

    WHY WE DO THIS
    --------------
    Phase 1 found that the vendor's put and call implied vols are mutually inconsistent:
    the put wing ends heading for 10.91% while the call wing opens at 11.67%, a +0.76 vol
    point step at the crossover. Put-call parity says a put and a call at the same strike
    must carry the same volatility, so that step is an artefact of how the vendor computed
    the numbers -- almost certainly a different forward or dividend assumption on the two
    sides, and possibly a European model applied to American puts.

    No smooth curve can fit a discontinuous smile, so the step was the dominant term in the
    Phase 1 SABR fit error and it would propagate into every scenario price in Phase 5.

    The fix is to stop using their number and compute our own from the one thing that is
    genuinely observed: the mid price. We invert the same BAW pricer we will later use to
    price, against the same put-call-parity forward we derived in Phase 0. That makes the
    volatility surface internally consistent with the rest of the project by construction.

    Inputs:
        smile (DataFrame):        output of `data_loader.build_otm_smile`, with 'strike',
            'option_type' and 'mid'.
        underlying_price (float): spot S, in dollars.
        time_to_expiry (float):   T, in years.
        risk_free_rate (float):   r, decimal.
        cost_of_carry (float):    b, decimal.

    Returns:
        tuple (reimplied_smile, n_failed):
            reimplied_smile (DataFrame): a copy of the input with 'market_iv' replaced by
                our own value, the vendor's kept as 'vendor_iv', and any strike we could
                not invert dropped.
            n_failed (int): how many strikes could not be inverted.
    """
    reimplied = smile.copy()
    reimplied["vendor_iv"] = reimplied["market_iv"]

    # A plain loop over a few hundred strikes. Each iteration is an independent
    # root-find, so there is nothing to gain from vectorising and a great deal of clarity
    # to lose -- see the same argument in `sabr.sabr_vol_curve`.
    reimplied_vols = []

    for _, row in reimplied.iterrows():
        vol = baw.implied_volatility(
            market_price=row["mid"],
            underlying_price=underlying_price,
            strike=row["strike"],
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            option_type=row["option_type"],
        )
        reimplied_vols.append(vol)

    reimplied["reimplied_iv"] = reimplied_vols

    # A strike that cannot be inverted is one whose quote no volatility reproduces --
    # typically a mid sitting below intrinsic value because one side of the quote went
    # stale. We drop those and report the count rather than patching them.
    n_failed = int(reimplied["reimplied_iv"].isna().sum())
    reimplied = reimplied.loc[reimplied["reimplied_iv"].notna()].reset_index(drop=True)

    # From here on 'market_iv' means OUR implied vol, so that everything downstream --
    # the SABR calibration, the diagnostics, the plots -- works unchanged.
    reimplied["market_iv"] = reimplied["reimplied_iv"]

    return reimplied, n_failed


def run_phase_3(config, phase_0_results, phase_1_results):
    """
    Phase 3: re-imply the volatilities, recalibrate SABR, and wire it into the BAW pricer.

    Three things happen here, in order:
      1. We replace the vendor's implied vols with our own, inverted from mid prices.
      2. We recalibrate SABR to the corrected smile.
      3. We price real strikes through SABR into BAW and compare against the market.

    Inputs:
        config (dict):          the CONFIG block above.
        phase_0_results (dict): output of `run_phase_0`.
        phase_1_results (dict): output of `run_phase_1`, used only for the before/after
            comparison of fit quality.

    Returns:
        dict carrying:
            'calibration'  (dict)      the recalibrated SABR parameters
            'smile'        (DataFrame) the re-implied smile
            'fit_smile'    (DataFrame) the strikes calibrated to
            'comparison'   (DataFrame) the market-vs-theoretical price table
    """
    smile = phase_0_results["smile"]
    spot = phase_0_results["spot"]
    forward = phase_0_results["forward"]
    time_to_expiry = phase_0_results["time_to_expiry_years"]
    cost_of_carry = phase_0_results["cost_of_carry"]
    risk_free_rate = config["risk_free_rate"]
    beta = config["sabr_beta"]
    band = config["calibration_moneyness_band"]

    # ---- 1. Re-imply -----------------------------------------------------------------
    print_section("PHASE 3.1  RE-IMPLYING VOLATILITIES FROM MID PRICES")

    print(f"  Inverting BAW on {len(smile)} out-of-the-money mid prices...")
    print(f"  Using spot ${spot:.2f}, forward ${forward:.3f}, b = {cost_of_carry:.4%}, "
          f"r = {risk_free_rate:.4%}")

    reimplied_smile, n_failed = reimply_smile_volatilities(
        smile=smile,
        underlying_price=spot,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
    )

    print(f"  Inverted successfully : {len(reimplied_smile)} strikes")
    print(f"  Could not invert      : {n_failed} "
          f"(mid below intrinsic, or no vega -- dropped)")

    difference = reimplied_smile["reimplied_iv"] - reimplied_smile["vendor_iv"]
    is_put = reimplied_smile["option_type"] == "put"

    print()
    print(f"  Our vol minus the vendor's, in vol points:")
    print(f"    all strikes  : mean {difference.mean() * 100:+.4f}, "
          f"min {difference.min() * 100:+.4f}, max {difference.max() * 100:+.4f}")
    print(f"    OTM puts     : mean {difference[is_put].mean() * 100:+.4f} "
          f"({is_put.sum()} strikes)")
    print(f"    OTM calls    : mean {difference[~is_put].mean() * 100:+.4f} "
          f"({(~is_put).sum()} strikes)")

    # ---- 2. Did it remove the crossover step? ----------------------------------------
    print_section("PHASE 3.2  DID RE-IMPLYING FIX THE PUT/CALL INCONSISTENCY?")

    in_band = (reimplied_smile["strike"] / forward - 1.0).abs() <= band
    reimplied_fit_smile = reimplied_smile.loc[in_band].reset_index(drop=True)

    # Measure the crossover step and the noise floor both ways, on the same strikes.
    vendor_view = reimplied_fit_smile.copy()
    vendor_view["market_iv"] = vendor_view["vendor_iv"]

    vendor_step = data_loader.put_call_crossover_step(vendor_view)
    reimplied_step = data_loader.put_call_crossover_step(reimplied_fit_smile)

    vendor_roughness = data_loader.smile_local_roughness(vendor_view)
    reimplied_roughness = data_loader.smile_local_roughness(reimplied_fit_smile)

    print(f"  The two strikes nearest the forward (477, 478) both have CROSSED put quotes")
    print(f"  and were filtered out, leaving a ${reimplied_step['strike_gap']:.0f} hole at "
          f"the crossover. So we judge by the")
    print(f"  SIGN of the adjacent gap, not by a value extrapolated across that hole.")
    print(f"  The smile slopes down here, so a consistent smile needs a NEGATIVE gap.")
    print()
    print(f"  {'':<28} {'vendor IV':>12} {'our IV':>12}")
    print(f"  {'adjacent gap (robust)':<28} "
          f"{vendor_step['adjacent_gap'] * 100:>+11.4f}  "
          f"{reimplied_step['adjacent_gap'] * 100:>+11.4f}   vol points")
    print(f"  {'  local slope predicts':<28} "
          f"{vendor_step['slope_implied_gap'] * 100:>+11.4f}  "
          f"{reimplied_step['slope_implied_gap'] * 100:>+11.4f}   vol points")
    print(f"  {'extrapolated step (fragile)':<28} "
          f"{vendor_step['extrapolated_step'] * 100:>+11.4f}  "
          f"{reimplied_step['extrapolated_step'] * 100:>+11.4f}   vol points")
    print(f"  {'local roughness, overall':<28} "
          f"{vendor_roughness['overall'] * 100:>12.4f} "
          f"{reimplied_roughness['overall'] * 100:>12.4f}")
    print(f"  {'local roughness, put wing':<28} "
          f"{vendor_roughness['put'] * 100:>12.4f} "
          f"{reimplied_roughness['put'] * 100:>12.4f}")
    print(f"  {'local roughness, call wing':<28} "
          f"{vendor_roughness['call'] * 100:>12.4f} "
          f"{reimplied_roughness['call'] * 100:>12.4f}")

    # ---- 3. Recalibrate ---------------------------------------------------------------
    print_section("PHASE 3.3  RECALIBRATING SABR ON THE CORRECTED SMILE")

    calibration = sabr.calibrate_sabr(
        strikes=reimplied_fit_smile["strike"].to_numpy(),
        market_vols=reimplied_fit_smile["market_iv"].to_numpy(),
        forward=forward,
        time_to_expiry=time_to_expiry,
        beta=beta,
    )

    sabr.describe_calibration(calibration)

    old_calibration = phase_1_results["calibration"]

    print()
    print(f"  BEFORE AND AFTER (both at |moneyness| <= {band:.0%}, beta = {beta:.1f}):")
    print(f"    {'':<22} {'vendor IV':>12} {'our IV':>12}")
    print(f"    {'strikes':<22} {old_calibration['n_strikes']:>12d} "
          f"{calibration['n_strikes']:>12d}")
    print(f"    {'RMSE (vol points)':<22} {old_calibration['rmse'] * 100:>12.4f} "
          f"{calibration['rmse'] * 100:>12.4f}")
    print(f"    {'max error (vol points)':<22} "
          f"{old_calibration['max_abs_error'] * 100:>12.4f} "
          f"{calibration['max_abs_error'] * 100:>12.4f}")
    print(f"    {'alpha':<22} {old_calibration['alpha']:>12.6f} "
          f"{calibration['alpha']:>12.6f}")
    print(f"    {'rho':<22} {old_calibration['rho']:>+12.6f} "
          f"{calibration['rho']:>+12.6f}")
    print(f"    {'nu':<22} {old_calibration['nu']:>12.6f} "
          f"{calibration['nu']:>12.6f}")

    # ---- 4. Price against the market --------------------------------------------------
    print_section("PHASE 3.4  THEORETICAL PRICE VS MARKET PRICE")

    comparison_rows = []

    for _, row in reimplied_fit_smile.iterrows():
        sabr_vol, theoretical_price = price_option_with_sabr(
            strike=row["strike"],
            underlying_price=spot,
            forward=forward,
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            calibration=calibration,
            option_type=row["option_type"],
        )

        half_spread = (row["ask"] - row["bid"]) / 2.0

        comparison_rows.append({
            "strike": row["strike"],
            "option_type": row["option_type"],
            "moneyness": row["strike"] / forward - 1.0,
            "market_iv": row["vendor_iv"],
            "reimplied_iv": row["reimplied_iv"],
            "sabr_iv": sabr_vol,
            "market_bid": row["bid"],
            "market_ask": row["ask"],
            "market_mid": row["mid"],
            "baw_price": theoretical_price,
            "absolute_diff": theoretical_price - row["mid"],
            "percent_diff": (theoretical_price - row["mid"]) / row["mid"],
            "half_spreads": (theoretical_price - row["mid"]) / half_spread,
        })

    comparison = pd.DataFrame(comparison_rows)

    display_columns = ["strike", "option_type", "market_iv", "sabr_iv", "market_mid",
                       "baw_price", "absolute_diff", "percent_diff", "half_spreads"]

    print("  Every 6th strike ('market_iv' is the vendor's, for reference):")
    print(comparison[display_columns].iloc[::6].to_string(
        index=False, float_format=lambda x: f"{x:.5f}"))

    inside_spread = comparison["half_spreads"].abs() <= 1.0
    spread_width = comparison["market_ask"] - comparison["market_bid"]

    print()
    print(f"  Strikes priced             : {len(comparison)}")
    print(f"  Mean absolute error        : ${comparison['absolute_diff'].abs().mean():.4f}")
    print(f"  Max absolute error         : ${comparison['absolute_diff'].abs().max():.4f}")
    print(f"  Mean SIGNED error          : "
          f"${comparison['absolute_diff'].mean():+.4f}  "
          f"<- near zero means no systematic bias, only scatter")
    print()
    print(f"  Inside the bid-ask spread  : {inside_spread.sum()} of {len(comparison)} "
          f"({inside_spread.mean():.1%})")
    print(f"  ... but SPY spreads are a PENNY wide: median ${spread_width.median():.3f}, "
          f"which is {(spread_width / comparison['market_mid']).median():.2%} of mid.")
    print(f"  'Inside the spread' therefore demands matching the market to half a cent,")
    print(f"  which no smile model achieves. Judge by absolute error instead.")

    # ---- Where does the pricing error come from? --------------------------------------
    # If the SABR-to-BAW wiring were wrong -- spot passed where the forward belongs, a
    # mismatched day count, the wrong carry -- the error would be structural and would NOT
    # track the volatility fit error. So we attribute: multiply each strike's vol error by
    # its vega and see how much of the observed price error that accounts for.
    vegas = []

    for _, row in comparison.iterrows():
        vegas.append(baw.baw_vega(
            underlying_price=spot,
            strike=row["strike"],
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            volatility=row["sabr_iv"],
            option_type=row["option_type"],
        ))

    comparison["vega"] = vegas
    comparison["vol_error_vol_points"] = (
        comparison["sabr_iv"] - comparison["reimplied_iv"]
    ) * 100
    comparison["predicted_price_error"] = (
        comparison["vol_error_vol_points"] * comparison["vega"]
    )

    unexplained = comparison["absolute_diff"] - comparison["predicted_price_error"]
    explained_fraction = 1.0 - (
        unexplained.abs().mean() / comparison["absolute_diff"].abs().mean()
    )

    print()
    print(f"  ERROR ATTRIBUTION -- is this the vol fit, or a broken pricing chain?")
    print(f"    observed price error       : mean abs "
          f"${comparison['absolute_diff'].abs().mean():.5f}")
    print(f"    predicted from vol x vega  : mean abs "
          f"${comparison['predicted_price_error'].abs().mean():.5f}")
    print(f"    UNEXPLAINED residual       : mean abs ${unexplained.abs().mean():.6f}, "
          f"max ${unexplained.abs().max():.6f}")
    print(f"    explained by the vol fit   : {explained_fraction:.2%}")
    print(f"    Essentially all of the pricing error is the SABR fit residual seen")
    print(f"    through vega. The forward/spot wiring is correct; what is left is the")
    print(f"    smile fit, and that is limited by the data, not by the plumbing.")

    worst = comparison.reindex(
        comparison["half_spreads"].abs().sort_values(ascending=False).index
    ).head(5)
    print()
    print("  Five worst strikes, measured in half-spreads:")
    print(worst[display_columns].to_string(
        index=False, float_format=lambda x: f"{x:.5f}"))

    # ---- 5. Save and plot -------------------------------------------------------------
    print_section("PHASE 3.5  TABLES AND FIGURES")

    comparison_path = os.path.join(config["tables_dir"], "baw_vs_market_prices.csv")
    comparison.to_csv(comparison_path, index=False)

    smile_path = os.path.join(config["tables_dir"], "reimplied_smile.csv")
    reimplied_smile.to_csv(smile_path, index=False)

    diagnostics_path = os.path.join(config["tables_dir"], "sabr_fit_diagnostics.csv")
    pd.DataFrame({
        "strike": reimplied_fit_smile["strike"],
        "option_type": reimplied_fit_smile["option_type"],
        "moneyness": reimplied_fit_smile["strike"] / forward - 1.0,
        "vendor_iv": reimplied_fit_smile["vendor_iv"],
        "reimplied_iv": reimplied_fit_smile["reimplied_iv"],
        "sabr_iv": calibration["fitted_vols"],
        "residual_vol_points": calibration["residuals"] * 100,
        "mid": reimplied_fit_smile["mid"],
    }).to_csv(diagnostics_path, index=False)

    calibration_path = os.path.join(config["tables_dir"], "sabr_calibration.csv")
    pd.DataFrame([{
        "reference_date": config["reference_date"],
        "expiry_date": config["reference_expiry"],
        "forward": forward,
        "time_to_expiry_years": time_to_expiry,
        "alpha": calibration["alpha"],
        "beta": calibration["beta"],
        "rho": calibration["rho"],
        "nu": calibration["nu"],
        "rmse_vol_points": calibration["rmse"] * 100,
        "max_abs_error_vol_points": calibration["max_abs_error"] * 100,
        "n_strikes": calibration["n_strikes"],
        "calibration_moneyness_band": band,
        "volatility_source": "reimplied_from_mid_prices",
    }]).to_csv(calibration_path, index=False)

    plots.plot_reimplied_vs_vendor_vols(
        comparison=reimplied_smile,
        forward=forward,
        spot=spot,
        reference_date=config["reference_date"],
        expiry_date=config["reference_expiry"],
        output_path=os.path.join(config["figures_dir"], "reimplied_vs_vendor_vols.png"),
    )

    plots.plot_baw_vs_market_prices(
        comparison=comparison,
        forward=forward,
        spot=spot,
        reference_date=config["reference_date"],
        expiry_date=config["reference_expiry"],
        output_path=os.path.join(config["figures_dir"], "baw_vs_market_prices.png"),
    )

    plots.plot_sabr_fit(
        fit_smile=reimplied_fit_smile,
        full_smile=reimplied_smile,
        calibration=calibration,
        forward=forward,
        time_to_expiry=time_to_expiry,
        spot=spot,
        reference_date=config["reference_date"],
        expiry_date=config["reference_expiry"],
        output_path=os.path.join(config["figures_dir"], "sabr_fit.png"),
    )

    print(f"  Saved table:  {comparison_path}")
    print(f"  Saved table:  {smile_path}")
    print(f"  Saved table:  {diagnostics_path}")
    print(f"  Saved table:  {calibration_path}")

    return {
        "calibration": calibration,
        "smile": reimplied_smile,
        "fit_smile": reimplied_fit_smile,
        "comparison": comparison,
    }


def run_phase_4(config, phase_0_results):
    """
    Phase 4: build the historical simulation scenarios. No option pricing happens here.

    This phase is purely about the underlying: it converts the SPY price history into a set
    of alternative prices for tomorrow. Phase 5 is what reprices the option under each one.

    Inputs:
        config (dict):          the CONFIG block above.
        phase_0_results (dict): output of `run_phase_0`, for the price history and spot.

    Returns:
        dict carrying:
            'returns'    (DataFrame) all daily returns available
            'scenarios'  (DataFrame) the lookback-window scenarios, one row each
            'statistics' (dict)      summary statistics of the return sample
    """
    prices = phase_0_results["prices"]
    spot = phase_0_results["spot"]
    lookback_days = config["lookback_days"]

    # ---- Returns and scenarios --------------------------------------------------------
    print_section("PHASE 4.1  SCENARIO GENERATION")

    returns = historical_sim.compute_daily_returns(prices)
    scenarios = historical_sim.generate_scenarios(
        current_price=spot,
        historical_returns=returns,
        lookback_days=lookback_days,
    )

    print(f"  Price history available   : {len(prices)} days "
          f"({prices['date'].iloc[0]:%Y-%m-%d} to {prices['date'].iloc[-1]:%Y-%m-%d})")
    print(f"  Daily returns computed    : {len(returns)}")
    print(f"  Lookback window used      : {lookback_days} days "
          f"({scenarios['historical_date'].iloc[0]:%Y-%m-%d} to "
          f"{scenarios['historical_date'].iloc[-1]:%Y-%m-%d})")
    print(f"  Today's SPY price         : ${spot:.2f}")
    print(f"  Scenario price range      : "
          f"${scenarios['scenario_price'].min():.2f} to "
          f"${scenarios['scenario_price'].max():.2f}")

    # Confirm the window is free of the vendor date-stamping defects that Phase 0 found in
    # the 2022 file. This matters more here than anywhere else: a missing trading day would
    # compress two sessions into one return and manufacture a tail event that never
    # happened, and the tail is precisely what the VaR reads.
    window_days = data_loader.nyse_trading_days(
        scenarios["historical_date"].iloc[0], scenarios["historical_date"].iloc[-1]
    )
    dates_in_window = pd.DatetimeIndex(scenarios["historical_date"])
    missing_in_window = window_days.difference(dates_in_window)
    phantom_in_window = dates_in_window.difference(window_days)

    print(f"  Calendar defects in window: {len(missing_in_window)} missing, "
          f"{len(phantom_in_window)} phantom  <- must both be 0")

    # ---- Summary statistics -----------------------------------------------------------
    print_section("PHASE 4.2  RETURN SAMPLE STATISTICS")

    statistics = historical_sim.summarise_returns(scenarios)

    # ---- Extremes ---------------------------------------------------------------------
    print_section("PHASE 4.3  LARGEST MOVES IN THE SAMPLE")

    largest_down, largest_up = historical_sim.largest_moves(scenarios, n=5)

    def format_moves(moves):
        """Render a small table of extreme moves."""
        lines = []
        for _, row in moves.iterrows():
            lines.append(
                f"    {row['historical_date']:%Y-%m-%d}  "
                f"{row['historical_return']:+8.4%}  ->  "
                f"scenario price ${row['scenario_price']:.2f}"
            )
        return "\n".join(lines)

    print("  Five largest DOWN moves (these drive the VaR for a long position):")
    print(format_moves(largest_down))
    print()
    print("  Five largest UP moves:")
    print(format_moves(largest_up))
    print()
    print("  Sanity check: these should be recognisable market dates, not quiet Tuesdays.")
    print("  A big move on a day nothing happened would point to a missing trading day")
    print("  compressing two sessions into one return.")

    # ---- Fat tails --------------------------------------------------------------------
    print_section("PHASE 4.4  ARE THE TAILS FATTER THAN A NORMAL?")

    tail_comparison = historical_sim.compare_tails_against_normal(scenarios)

    print("  Days exceeding each threshold, observed against what a fitted normal predicts:")
    print(f"    {'threshold':>10} {'observed':>9} {'normal says':>12} {'ratio':>8}")

    for _, row in tail_comparison.iterrows():
        ratio_text = f"{row['ratio']:.2f}x" if np.isfinite(row["ratio"]) else "  --"
        print(f"    {row['threshold_sigma']:>9.0f}s {int(row['observed']):>9d} "
              f"{row['normal_expected']:>12.2f} {ratio_text:>8}")

    print()
    print("  Read this carefully rather than assuming the textbook answer. Long samples of")
    print("  equity returns are reliably fat-tailed, but a single 250-day window need not")
    print("  be -- and this one is not. See the window comparison below.")

    # ---- How much does the answer depend on WHICH year we happen to be standing in? ----
    print_section("PHASE 4.5  HOW MUCH DOES THE LOOKBACK WINDOW MATTER?")

    long_history_path = os.path.join(config["data_dir"], "spy_price_history_full.csv")
    long_prices = data_loader.load_spy_price_history(
        data_dir=config["data_dir"],
        start_date=config["window_comparison_start"],
        end_date=config["reference_date"],
        cache_path=long_history_path,
    )
    long_returns = historical_sim.compute_daily_returns(long_prices)

    window_comparison = historical_sim.rolling_window_statistics(
        returns=long_returns,
        reference_dates=config["window_comparison_dates"],
        lookback_days=lookback_days,
    )

    print(f"  The SAME position, the SAME model, the SAME {lookback_days}-day method --")
    print(f"  evaluated as if we were standing at the end of each of these years instead:")
    print()
    print(f"    {'as of':>12} {'ann. vol':>9} {'skew':>8} {'exc.kurt':>9} "
          f"{'worst day':>10} {'1st pctile':>11}")

    for _, row in window_comparison.iterrows():
        marker = "  <- ours" if row["as_of"] == pd.to_datetime(
            config["reference_date"]) else ""
        print(f"    {row['as_of']:%Y-%m-%d} {row['annualised_vol']:>8.2%} "
              f"{row['skewness']:>+8.3f} {row['excess_kurtosis']:>+9.3f} "
              f"{row['worst_day']:>+10.2%} {row['first_percentile']:>+11.2%}{marker}")

    ours = window_comparison.loc[
        window_comparison["as_of"] == pd.to_datetime(config["reference_date"])
    ]

    if not ours.empty:
        worst_window = window_comparison.loc[window_comparison["first_percentile"].idxmin()]
        our_percentile = float(ours["first_percentile"].iloc[0])
        ratio = worst_window["first_percentile"] / our_percentile

        print()
        print(f"  Our window's 1st percentile is {our_percentile:+.2%}. The harshest window")
        print(f"  in this comparison ({worst_window['as_of']:%Y-%m-%d}) has "
              f"{worst_window['first_percentile']:+.2%} -- {ratio:.1f}x larger.")
        print(f"  That factor is not a modelling choice or a market view. It is entirely a")
        print(f"  consequence of WHICH twelve months happen to sit behind the reference")
        print(f"  date, and it is the central weakness of unweighted historical simulation:")
        print(f"  a crisis counts fully until the day it ages out of the window, then not")
        print(f"  at all. Our 2023 window contains no crisis, so the Phase 6 VaR will be")
        print(f"  correspondingly benign. That is the method working as specified, not a")
        print(f"  bug -- but it is the number's biggest caveat.")

    # NOTE (approximation): the comparison years are shown for context only and are not
    # subjected to the Phase 0 calendar validation. Data quality varies across the vendor's
    # history -- 2022 alone carries 4 missing trading days and 9 phantom rows stamped on
    # market holidays. Our own 2023 window is clean, which is what matters for the result.

    # ---- Tables and figures ------------------------------------------------------------
    print_section("PHASE 4.6  TABLES AND FIGURES")

    scenario_path = os.path.join(config["tables_dir"], "scenarios.csv")
    scenarios.to_csv(scenario_path, index=False)

    statistics_path = os.path.join(config["tables_dir"], "return_statistics.csv")
    pd.DataFrame([statistics]).to_csv(statistics_path, index=False)

    window_path = os.path.join(config["tables_dir"], "lookback_window_comparison.csv")
    window_comparison.to_csv(window_path, index=False)

    plots.plot_spy_price_history(
        prices=prices,
        scenarios=scenarios,
        reference_date=config["reference_date"],
        output_path=os.path.join(config["figures_dir"], "spy_price_history.png"),
    )

    plots.plot_daily_returns_histogram(
        scenarios=scenarios,
        statistics=statistics,
        output_path=os.path.join(config["figures_dir"],
                                 "daily_returns_histogram.png"),
    )

    print(f"  Saved table:  {scenario_path}")
    print(f"  Saved table:  {statistics_path}")
    print(f"  Saved table:  {window_path}")

    return {
        "returns": returns,
        "scenarios": scenarios,
        "statistics": statistics,
        "window_comparison": window_comparison,
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

    phase_0_results = run_phase_0(CONFIG)
    phase_1_results = run_phase_1(CONFIG, phase_0_results)
    run_phase_3(CONFIG, phase_0_results, phase_1_results)
    run_phase_4(CONFIG, phase_0_results)

    print_section("PHASE 4 COMPLETE")


if __name__ == "__main__":
    main()
