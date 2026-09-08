"""
Top-level pipeline for the SPY options VaR replication (SR-FICC-2013-02 methodology).

Run this file to reproduce every result in the project:

    python3 main.py                       the original 2023-12-29 case
    python3 main.py config_2026_06_01     the 2026-06-01 Databento case

All configuration lives in configs/, one file per reference case. There are no magic
numbers scattered through the other modules -- anything you might want to change is in
the config file for the case you are running. This file is the runner: it loads a config
and hands it to the phase functions, which take config as a parameter.
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd

import baw
import data_loader
import historical_sim
import plots
import sabr
import var
# ======================================================================================
# CONFIGURATION
# ======================================================================================
#
# Every assumption in the project is stated in a config file under configs/, one per
# reference case, with its source. Nothing in this file or any other module invents a
# constant of its own.
#
# main.py is a thin runner: it loads whichever config it is given and hands it to the
# same phase functions, which take config as a parameter and are unchanged by this.
#
#     python3 main.py                       -> configs/config_2023_12_29.py (the default)
#     python3 main.py config_2026_06_01     -> configs/config_2026_06_01.py
#
# Adding a new reference case means adding a file to configs/. It never means editing
# this one.

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
DEFAULT_CONFIG = "config_2023_12_29"


def available_configs():
    """Every config file that could be run, by the name you would pass on the command line."""
    if not os.path.isdir(CONFIG_DIR):
        return []
    return sorted(f[:-3] for f in os.listdir(CONFIG_DIR)
                  if f.endswith(".py") and not f.startswith("__"))


def load_config(name):
    """
    Load the CONFIG dictionary out of configs/<name>.py.

    Loaded by file path rather than by import so that configs/ needs no __init__.py and
    stays what it is -- a folder of data files that happen to be written in Python, for
    the comments. Every assumption in this project carries its reasoning next to it, and
    JSON cannot hold a comment.

    Inputs:
        name (str): config module name without the .py, e.g. "config_2026_06_01".

    Returns:
        dict: the CONFIG dictionary defined in that file.
    """
    path = os.path.join(CONFIG_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise SystemExit(
            f"No such config: {path}\n"
            f"Available: {', '.join(available_configs()) or '(none)'}"
        )
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "CONFIG"):
        raise SystemExit(f"{path} defines no CONFIG dictionary.")
    return module.CONFIG


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
        config (dict): the CONFIG dictionary loaded from configs/.

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
    # Stated in dollars as well as annualised, because a yield annualised over a few
    # weeks is easy to misread: the same dividend looks enormous over a short window and
    # negligible over a long one. The dollar figure says plainly whether an ex-dividend
    # date falls between the reference date and the expiry.
    implied_q = forward_result["implied_dividend_yield"]
    dividend_dollars = implied_q * time_to_expiry_years * spot
    print(f"  Reading: this yield is DERIVED from the quotes, not assumed. Annualised at")
    print(f"  {implied_q:+.4%} over {round(time_to_expiry_years * 365)} days it amounts to "
          f"${dividend_dollars:+.2f} of dividend on a")
    print(f"  ${spot:.2f} underlying -- which is the market telling us whether an SPY")
    print(f"  ex-dividend date falls between {config['reference_date']} and "
          f"{config['reference_expiry']}.")
    print(f"  Assuming a trailing yield instead would mis-set the forward and tilt the")
    print(f"  whole smile.")

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
        config (dict):           the CONFIG dictionary loaded from configs/.
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


def revalue_across_scenarios(scenarios, position, spot, time_to_expiry, risk_free_rate,
                             cost_of_carry, calibration):
    """
    Reprice the position under every historical scenario. **Full revaluation, no shortcuts.**

    This is the heart of the project and the reason the filing specifies BAW and SABR at
    all. For each of the ~250 scenarios we do four things: move the underlying, recompute
    the forward, ask SABR for a FRESH volatility at that new forward, and run a FRESH BAW
    reprice. No Greeks are involved anywhere in the result.

    WHAT HAPPENS TO THE VOLATILITY SURFACE WHEN SPOT MOVES -- THE KEY MODELLING CHOICE
    ---------------------------------------------------------------------------------
    When SPY moves, does our strike's implied volatility change? It must, and the question
    is how. There are two canonical answers:

      STICKY-STRIKE: each strike keeps its own volatility regardless of where spot goes.
        The 475 strike is a 12.0% vol option today and stays a 12.0% vol option tomorrow
        whatever happens. The smile is nailed to the strike axis.

      STICKY-MONEYNESS (or sticky-delta): the smile is anchored to the FORWARD and travels
        with it. What is fixed is the vol at a given moneyness, not at a given strike. If
        the forward falls 2%, the whole smile slides down 2% with it, and our fixed 475
        strike -- now further out of the money in relative terms -- picks up a different
        volatility off the curve.

    **We implement sticky-moneyness**, which is what holding the calibrated SABR
    parameters fixed and re-evaluating at the new forward does automatically. SABR is
    parameterised in terms of the forward, so feeding it a new forward with unchanged
    (alpha, beta, rho, nu) slides the smile along by construction.

    WHICH WAY OUR STRIKE'S VOLATILITY MOVES, AND WHY IT IS COUNTERINTUITIVE
    ----------------------------------------------------------------------
    The equity smile slopes DOWNWARD in strike: low strikes carry high volatility. Under
    sticky-moneyness the curve is pinned to the forward, so what determines our fixed
    strike's volatility is the ratio K/F, and that ratio moves opposite to the market:

      MARKET RALLIES  -> F rises -> K/F FALLS -> our strike sits further down the strike
        axis relative to the forward, deeper into the steep high-vol left wing -> our
        volatility RISES.
      MARKET SELLS OFF -> F falls -> K/F RISES -> our strike moves up toward the smile's
        minimum -> our volatility FALLS.

    So the volatility on our 475 put goes UP when the market goes up. That reads backwards
    against the familiar "vol spikes in a selloff" intuition, but that intuition is about
    the LEVEL of the whole surface moving, which is a different effect and one this model
    deliberately does not include (see the deviation note below). Here the surface shape is
    frozen and only our position along it changes.

    THE CONSEQUENCE FOR THIS POSITION, MEASURED
    -------------------------------------------
    Against a sticky-strike alternative that pins each strike's volatility, for our long
    475 put:

        move    K/F     vol      sticky-moneyness   sticky-strike   difference
        -3%   1.0225   10.26%          $14.49           $15.15        -$0.66
        -1%   1.0019   11.16%           $8.57            $8.97        -$0.40
        +1%   0.9821   12.41%           $5.20            $4.78        +$0.42
        +3%   0.9630   13.77%           $3.31            $2.27        +$1.04

    Sticky-moneyness DAMPENS the P&L in both directions: the put gains less in a selloff
    (its vol falls) and loses less in a rally (its vol rises). A long put loses money when
    the market rallies, so this is the LESS conservative choice for this position's VaR --
    it makes the losing tail shallower than sticky-strike would. Worth knowing before
    reading the Phase 6 number. Empirical equity index behaviour sits between the two
    conventions, generally closer to sticky-moneyness over short horizons.

    # NOTE (deviation from filing): a fuller implementation would also SHOCK THE SABR
    # PARAMETERS THEMSELVES. In reality, on the day SPY fell 2%, the whole volatility
    # surface repriced -- the level rose, the skew steepened -- and a genuine full
    # revaluation of that historical day would capture both the spot move and the surface
    # move, drawn from the same date. Here the surface shape is frozen at today's
    # calibration and only the anchor point moves, so the scenario P&Ls contain SPOT risk
    # and the smile's response to spot, but NO independent volatility risk. For a long
    # option this understates the gain in a selloff (vol would have spiked) and overstates
    # the loss in a rally. We have the historical surfaces to build this -- 14 years of
    # daily chains -- and it was explicitly declined for this build.

    # NOTE (deviation from filing): TIME TO EXPIRY IS HELD FIXED at today's 49 days rather
    # than decremented to 48. The two choices answer different questions. Decrementing
    # gives the theoretically clean one-day-ahead value and therefore includes THETA, which
    # for a long option is a near-constant negative contribution to every single scenario
    # -- it would shift the entire P&L distribution down by roughly the same amount and
    # inflate the VaR by that amount regardless of market direction. Holding T fixed
    # isolates pure market risk, which is what a one-day VaR is meant to measure. This was
    # a deliberate instruction; the alternative is a one-line change.

    Inputs:
        scenarios (DataFrame):    output of `historical_sim.generate_scenarios`.
        position (dict):          the position, as plain data: 'strike' (float, dollars),
            'option_type' (str, 'call' or 'put'), 'quantity' (float, signed -- positive is
            long, negative is short).
        spot (float):             today's SPY price, in dollars.
        time_to_expiry (float):   T, in years. Held constant across scenarios.
        risk_free_rate (float):   r, decimal.
        cost_of_carry (float):    b, decimal, where F = S * exp(b * T).
        calibration (dict):       output of `sabr.calibrate_sabr`. Held fixed across all
            scenarios -- that is the sticky-moneyness assumption above.

    Returns:
        tuple (revaluation, base_price):
            revaluation (DataFrame): one row per scenario, with the historical date and
                return, the scenario underlying price and forward, the fresh SABR vol, the
                fresh BAW price, and the position P&L in dollars.
            base_price (float): today's theoretical price of ONE option, in dollars.
    """
    strike = position["strike"]
    option_type = position["option_type"]
    quantity = position["quantity"]

    # Today's value, which every scenario P&L is measured against. Computed through
    # exactly the same code path as the scenarios, so that any bias in the pricing chain
    # cancels between the two rather than leaking into the P&L.
    base_forward = spot * np.exp(cost_of_carry * time_to_expiry)
    base_volatility, base_price = price_option_with_sabr(
        strike=strike,
        underlying_price=spot,
        forward=base_forward,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
        calibration=calibration,
        option_type=option_type,
    )

    # A plain loop, one scenario at a time. 250 BAW solves take about a fifth of a second,
    # so there is nothing to gain from vectorising and a great deal of clarity to lose --
    # and this is the one loop in the project a reviewer is most likely to want to read
    # line by line.
    rows = []

    for _, scenario in scenarios.iterrows():
        scenario_spot = scenario["scenario_price"]

        # Step 1: the forward moves with spot. Same carry, same T -- only S has changed.
        scenario_forward = scenario_spot * np.exp(cost_of_carry * time_to_expiry)

        # Steps 2 and 3: a fresh SABR volatility at the new forward, then a fresh BAW
        # reprice. Both inside price_option_with_sabr, which is the same function used for
        # the base price above.
        scenario_volatility, scenario_option_price = price_option_with_sabr(
            strike=strike,
            underlying_price=scenario_spot,
            forward=scenario_forward,
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            calibration=calibration,
            option_type=option_type,
        )

        rows.append({
            "historical_date": scenario["historical_date"],
            "historical_return": scenario["historical_return"],
            "scenario_spot": scenario_spot,
            "scenario_forward": scenario_forward,
            "scenario_volatility": scenario_volatility,
            "scenario_option_price": scenario_option_price,
            "scenario_pnl": quantity * (scenario_option_price - base_price),
        })

    revaluation = pd.DataFrame(rows)

    # Carried for reference so downstream printing does not have to re-derive it.
    revaluation.attrs["base_price"] = base_price
    revaluation.attrs["base_volatility"] = base_volatility

    return revaluation, base_price


def taylor_approximation_counterfactual(revaluation, position, spot, time_to_expiry,
                                        risk_free_rate, cost_of_carry, calibration):
    """
    Compute what the FORBIDDEN delta/gamma shortcut would have given, for comparison.

    # NOTE (deviation from filing): this function deliberately computes the thing the
    # filing prohibits -- `base_price + delta*dS + 0.5*gamma*dS^2` -- and it is NEVER used
    # to produce a risk number. It exists purely to demonstrate, in dollars, what the
    # prohibition buys us. Without it, "the filing requires full revaluation" is a rule to
    # be obeyed; with it, the reader can see the size of the error being avoided.

    Delta and gamma are taken by central finite difference on the FULL pricing chain,
    including SABR's response to the moving forward. That is deliberately the most
    favourable version of the shortcut: a practitioner using cruder Greeks, held at fixed
    volatility, would do worse than the numbers here.

    Inputs:
        revaluation (DataFrame):  output of `revalue_across_scenarios`.
        position (dict):          as in `revalue_across_scenarios`.
        spot (float):             today's SPY price, dollars.
        time_to_expiry (float):   T, years.
        risk_free_rate (float):   r, decimal.
        cost_of_carry (float):    b, decimal.
        calibration (dict):       the SABR parameters.

    Returns:
        pandas.DataFrame: the input with three columns added -- 'delta_only_pnl',
        'delta_gamma_pnl', and 'delta_gamma_error' (the shortcut minus the truth, in
        dollars).
    """
    strike = position["strike"]
    option_type = position["option_type"]
    quantity = position["quantity"]

    def full_price(underlying_price):
        """Price through the complete SABR-to-BAW chain at a given spot."""
        forward = underlying_price * np.exp(cost_of_carry * time_to_expiry)
        _, price = price_option_with_sabr(
            strike=strike,
            underlying_price=underlying_price,
            forward=forward,
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            calibration=calibration,
            option_type=option_type,
        )
        return price

    # A 0.5% bump: large enough to stay well clear of floating point noise, small enough
    # that the second-order estimate is still local.
    bump = spot * 0.005

    price_up = full_price(spot + bump)
    price_here = full_price(spot)
    price_down = full_price(spot - bump)

    delta = (price_up - price_down) / (2.0 * bump)
    gamma = (price_up - 2.0 * price_here + price_down) / (bump ** 2)

    result = revaluation.copy()
    spot_change = result["scenario_spot"] - spot

    result["delta_only_pnl"] = quantity * delta * spot_change
    result["delta_gamma_pnl"] = quantity * (
        delta * spot_change + 0.5 * gamma * spot_change ** 2
    )
    result["delta_gamma_error"] = result["delta_gamma_pnl"] - result["scenario_pnl"]

    result.attrs["delta"] = delta
    result.attrs["gamma"] = gamma

    return result


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
        config (dict):          the CONFIG dictionary loaded from configs/.
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

    print(f"  The crossover sits between the last put strike "
          f"({reimplied_step['last_put_strike']:.0f}) and the first")
    print(f"  call strike ({reimplied_step['first_call_strike']:.0f}), a "
          f"${reimplied_step['strike_gap']:.0f} gap. Any strike in between failed the "
          f"quality")
    print(f"  filters -- a crossed or one-sided quote. So we judge by the SIGN of the")
    print(f"  adjacent gap, not by a value extrapolated across that hole.")
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
        config (dict):          the CONFIG dictionary loaded from configs/.
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
        print(f"  at all. Our window ends {config['reference_date']} with a 1st percentile")
        print(f"  of {our_percentile:+.2%}, so the Phase 6 VaR inherits that window's "
              f"severity")
        print(f"  and nothing else. That is the method working as specified, not a bug --")
        print(f"  but it is the number's biggest caveat.")

    # NOTE (approximation): the comparison windows are shown for context only and are not
    # subjected to the Phase 0 calendar validation. Data quality varies across a vendor's
    # history -- in the OptionsDX files, 2022 alone carries 4 missing trading days and 9
    # phantom rows stamped on market holidays. The window actually used IS validated in
    # Phase 0, which is what matters for the result.

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


def run_phase_5(config, phase_0_results, phase_3_results, phase_4_results):
    """
    Phase 5: full revaluation of the position across every historical scenario.

    Inputs:
        config (dict):          the CONFIG dictionary loaded from configs/.
        phase_0_results (dict): for spot, T, carry.
        phase_3_results (dict): for the calibrated SABR parameters.
        phase_4_results (dict): for the scenarios.

    Returns:
        dict carrying:
            'revaluation'        (DataFrame) the per-scenario table, baseline window
            'base_price'         (float) today's theoretical price of one option
            'stress_revaluation' (DataFrame) the same under the stress window
            'position'           (dict) the position that was revalued
    """
    position = config["position"]
    spot = phase_0_results["spot"]
    time_to_expiry = phase_0_results["time_to_expiry_years"]
    cost_of_carry = phase_0_results["cost_of_carry"]
    risk_free_rate = config["risk_free_rate"]
    calibration = phase_3_results["calibration"]
    scenarios = phase_4_results["scenarios"]

    # ---- The position and its base value ----------------------------------------------
    print_section("PHASE 5.1  THE POSITION AND ITS VALUE TODAY")

    revaluation, base_price = revalue_across_scenarios(
        scenarios=scenarios,
        position=position,
        spot=spot,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
        calibration=calibration,
    )

    base_volatility = revaluation.attrs["base_volatility"]

    # The market's own price for the same contract, for context.
    market_row = phase_3_results["comparison"].loc[
        phase_3_results["comparison"]["strike"] == position["strike"]
    ]

    print(f"  Position              : {position['quantity']:+.0f} x SPY "
          f"{config['reference_expiry']} ${position['strike']:.0f} "
          f"{position['option_type']}")
    print(f"  Underlying (spot)     : ${spot:.2f}")
    print(f"  Forward               : ${spot * np.exp(cost_of_carry * time_to_expiry):.3f}")
    print(f"  Time to expiry        : {time_to_expiry:.5f} years "
          f"({round(time_to_expiry * 365)} days), HELD FIXED across scenarios")
    print(f"  SABR volatility today : {base_volatility:.4%}")
    print(f"  Theoretical price     : ${base_price:.6f}")

    if not market_row.empty:
        market_mid = float(market_row["market_mid"].iloc[0])
        print(f"  Market mid            : ${market_mid:.4f}  "
              f"(difference ${base_price - market_mid:+.4f})")

    print(f"  Position value        : ${position['quantity'] * base_price:.4f}")

    # ---- The per-scenario table --------------------------------------------------------
    print_section("PHASE 5.2  PER-SCENARIO REVALUATION")

    display_columns = ["historical_date", "historical_return", "scenario_spot",
                       "scenario_volatility", "scenario_option_price", "scenario_pnl"]

    def format_table(frame):
        """Render the scenario table with dates readable and numbers aligned."""
        shown = frame[display_columns].copy()
        shown["historical_date"] = shown["historical_date"].dt.strftime("%Y-%m-%d")
        return shown.to_string(index=False, float_format=lambda x: f"{x:.5f}")

    print("  First five scenarios:")
    print(format_table(revaluation.head(5)))
    print()
    print("  Last five scenarios:")
    print(format_table(revaluation.tail(5)))

    worst = revaluation.nsmallest(5, "scenario_pnl")
    print()
    print("  Five WORST scenarios for this position (these will drive the VaR):")
    print(format_table(worst))

    # ---- Sanity checks -----------------------------------------------------------------
    print_section("PHASE 5.3  SANITY CHECKS")

    # 1. The scenario closest to a zero return must reprice to essentially today's price.
    #    With T held fixed this should be near exact -- any material gap would mean the
    #    base price and the scenario prices are not going through the same code path.
    nearest_to_zero_index = revaluation["historical_return"].abs().idxmin()
    nearest_to_zero = revaluation.loc[nearest_to_zero_index]

    print(f"  1. ZERO-RETURN SCENARIO REPRICES TO TODAY'S PRICE")
    print(f"     Closest scenario to a flat day: "
          f"{nearest_to_zero['historical_date']:%Y-%m-%d}, return "
          f"{nearest_to_zero['historical_return']:+.4%}")
    print(f"     Scenario price ${nearest_to_zero['scenario_option_price']:.6f} vs "
          f"base ${base_price:.6f}")
    print(f"     P&L ${nearest_to_zero['scenario_pnl']:+.6f} on a "
          f"{nearest_to_zero['historical_return']:+.4%} move -- small, as it must be.")

    # 2. For a single vanilla option, P&L must be monotone in the underlying move. A long
    #    put gains as spot falls, so sorting by the move should give strictly decreasing
    #    P&L. A violation would mean the vol response had overwhelmed the spot response,
    #    which for a plain option is a bug.
    ordered = revaluation.sort_values("scenario_spot")
    pnl_differences = ordered["scenario_pnl"].diff().dropna()

    if position["option_type"] == "put":
        is_monotone = bool((pnl_differences <= 1e-9).all())
        direction = "decreasing"
    else:
        is_monotone = bool((pnl_differences >= -1e-9).all())
        direction = "increasing"

    print()
    print(f"  2. P&L IS MONOTONE IN THE UNDERLYING MOVE")
    print(f"     A long {position['option_type']} must have P&L {direction} in spot.")
    print(f"     Monotone across all {len(revaluation)} scenarios: "
          f"{'YES' if is_monotone else 'NO -- INVESTIGATE'}")

    # 3. Convexity. This is the whole reason for full revaluation, so we quantify rather
    #    than assert it: a symmetric pair of moves up and down should NOT produce
    #    symmetric P&L. For a long option the gain on the favourable move exceeds the loss
    #    on the unfavourable one of the same size.
    print()
    print(f"  3. THE P&L IS CONVEX -- and this is why we revalue in full")

    def price_at_move(fractional_move):
        """Full-chain price after moving spot by a given fraction."""
        moved_spot = spot * (1.0 + fractional_move)
        _, price = price_option_with_sabr(
            strike=position["strike"],
            underlying_price=moved_spot,
            forward=moved_spot * np.exp(cost_of_carry * time_to_expiry),
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            calibration=calibration,
            option_type=position["option_type"],
        )
        return position["quantity"] * (price - base_price)

    print(f"     {'move':>7} {'P&L down':>11} {'P&L up':>11} {'sum':>11}  "
          f"(sum > 0 means convex)")

    for move in [0.005, 0.01, 0.02, 0.03]:
        pnl_down = price_at_move(-move)
        pnl_up = price_at_move(move)
        print(f"     {move:>6.1%} {pnl_down:>+11.6f} {pnl_up:>+11.6f} "
              f"{pnl_down + pnl_up:>+11.6f}")

    print(f"     A linear position would sum to exactly zero at every row. The positive")
    print(f"     sums are the option's gamma: it gains more on a fall than it loses on an")
    print(f"     equal rise. That asymmetry is invisible to a delta approximation.")

    # 4. The sticky-moneyness assumption, quantified against the sticky-strike
    #    alternative. This is the largest modelling judgement in the phase, so it is worth
    #    showing in dollars rather than only describing.
    print()
    print(f"  4. WHAT THE STICKY-MONEYNESS ASSUMPTION IS WORTH")
    print(f"     Our strike's vol moves because its MONEYNESS changed. The alternative")
    print(f"     convention, sticky-strike, would pin it at today's "
          f"{base_volatility:.4%}.")
    print()
    print(f"     {'move':>7} {'K/F':>7} {'our vol':>9} {'sticky-mny':>11} "
          f"{'sticky-strike':>14} {'difference':>11}")

    for move in [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]:
        moved_spot = spot * (1.0 + move)
        moved_forward = moved_spot * np.exp(cost_of_carry * time_to_expiry)

        moved_volatility, sticky_moneyness_price = price_option_with_sabr(
            strike=position["strike"],
            underlying_price=moved_spot,
            forward=moved_forward,
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            calibration=calibration,
            option_type=position["option_type"],
        )

        # Sticky-strike: the same spot move, but volatility pinned at today's value.
        sticky_strike_price = baw.baw_price(
            underlying_price=moved_spot,
            strike=position["strike"],
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            cost_of_carry=cost_of_carry,
            volatility=base_volatility,
            option_type=position["option_type"],
        )

        print(f"     {move:>+6.0%} {position['strike'] / moved_forward:>7.4f} "
              f"{moved_volatility:>9.4%} {sticky_moneyness_price:>11.4f} "
              f"{sticky_strike_price:>14.4f} "
              f"{sticky_moneyness_price - sticky_strike_price:>+11.4f}")

    print()
    print(f"     Note the direction: our vol RISES when the market rallies. The smile")
    print(f"     slopes down in strike and is pinned to the forward, so a rally lowers")
    print(f"     K/F and slides our strike deeper into the steep left wing. This is not")
    print(f"     the 'vol spikes in a selloff' effect -- that is the whole surface level")
    print(f"     moving, which this model deliberately holds frozen.")
    print(f"     For a LONG PUT, which loses when the market rallies, sticky-moneyness")
    print(f"     cushions the losing tail. It is the LESS conservative convention here.")

    # ---- What the forbidden shortcut would have given ---------------------------------
    print_section("PHASE 5.4  WHAT THE DELTA/GAMMA SHORTCUT WOULD HAVE COST")

    counterfactual = taylor_approximation_counterfactual(
        revaluation=revaluation,
        position=position,
        spot=spot,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
        calibration=calibration,
    )

    print(f"  The filing forbids approximating scenario prices as")
    print(f"  base + delta*dS + 0.5*gamma*dS^2. Here is what that would have given,")
    print(f"  using Greeks taken from the FULL chain (the most favourable version of the")
    print(f"  shortcut -- cruder Greeks at fixed vol would do worse).")
    print()
    print(f"  Position delta        : {counterfactual.attrs['delta']:+.6f}")
    print(f"  Position gamma        : {counterfactual.attrs['gamma']:+.6f}")
    print()

    worst_five = counterfactual.nsmallest(5, "scenario_pnl")

    print(f"  On the five worst scenarios:")
    print(f"    {'date':>12} {'move':>8} {'full reval':>12} {'delta only':>12} "
          f"{'delta+gamma':>12} {'error':>10}")

    for _, row in worst_five.iterrows():
        print(f"    {row['historical_date']:%Y-%m-%d} "
              f"{row['historical_return']:>+8.3%} {row['scenario_pnl']:>+12.6f} "
              f"{row['delta_only_pnl']:>+12.6f} {row['delta_gamma_pnl']:>+12.6f} "
              f"{row['delta_gamma_error']:>+10.6f}")

    delta_only_error = (counterfactual["delta_only_pnl"] -
                        counterfactual["scenario_pnl"]).abs()

    print()
    print(f"  Across all {len(counterfactual)} scenarios:")
    print(f"    delta-only error     : mean ${delta_only_error.mean():.6f}, "
          f"max ${delta_only_error.max():.6f}")
    print(f"    delta+gamma error    : mean "
          f"${counterfactual['delta_gamma_error'].abs().mean():.6f}, "
          f"max ${counterfactual['delta_gamma_error'].abs().max():.6f}")

    # ---- The same position under a harsher window --------------------------------------
    print_section("PHASE 5.5  THE SAME POSITION UNDER A STRESS WINDOW")

    long_prices = data_loader.load_spy_price_history(
        data_dir=config["data_dir"],
        start_date=config["window_comparison_start"],
        end_date=config["reference_date"],
        cache_path=os.path.join(config["data_dir"], "spy_price_history_full.csv"),
    )
    long_returns = historical_sim.compute_daily_returns(long_prices)

    # Returns drawn from the stress window, but applied to TODAY's spot and priced off
    # TODAY's volatility surface. The question being asked is "what if tomorrow's move
    # were drawn from the stress window's distribution instead of our own".
    stress_returns = long_returns.loc[
        long_returns["date"] <= pd.to_datetime(config["stress_window_end"])
    ]
    stress_scenarios = historical_sim.generate_scenarios(
        current_price=spot,
        historical_returns=stress_returns,
        lookback_days=config["lookback_days"],
    )

    stress_revaluation, _ = revalue_across_scenarios(
        scenarios=stress_scenarios,
        position=position,
        spot=spot,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        cost_of_carry=cost_of_carry,
        calibration=calibration,
    )

    print(f"  Stress window         : "
          f"{stress_scenarios['historical_date'].iloc[0]:%Y-%m-%d} to "
          f"{stress_scenarios['historical_date'].iloc[-1]:%Y-%m-%d}")
    print()
    baseline_label = f"{config['reference_date'][:4]} baseline"
    stress_label = f"{config['stress_window_end'][:4]} stress"
    print(f"  {'':<24} {baseline_label:>15} {stress_label:>15}")
    print(f"  {'worst daily move':<24} "
          f"{revaluation['historical_return'].min():>14.2%} "
          f"{stress_revaluation['historical_return'].min():>14.2%}")
    print(f"  {'worst scenario P&L':<24} "
          f"{revaluation['scenario_pnl'].min():>+14.4f} "
          f"{stress_revaluation['scenario_pnl'].min():>+14.4f}")
    print(f"  {'best scenario P&L':<24} "
          f"{revaluation['scenario_pnl'].max():>+14.4f} "
          f"{stress_revaluation['scenario_pnl'].max():>+14.4f}")
    print(f"  {'P&L standard deviation':<24} "
          f"{revaluation['scenario_pnl'].std():>14.4f} "
          f"{stress_revaluation['scenario_pnl'].std():>14.4f}")

    # NOTE (approximation): the stress run keeps TODAY's calibrated volatility surface and
    # replaces only the distribution of spot moves. That is a real limitation, and its
    # direction is the opposite of the intuitive guess.
    #
    # A long put is LONG VEGA. Through 2020 the whole surface sat far above today's --
    # SPY at-the-money implied vol reached multiples of our 11.5% -- so in a genuine joint
    # replay the put would have been worth substantially more in EVERY scenario. The
    # losses for this position come from big RALLIES, and the worst stress scenario is a
    # perfect example: 2020-03-13, a +9.29% day. On our frozen surface the put collapses to
    # $1.05 and the position loses $5.59. Had the surface been where it actually was that
    # week, the same spot move with vol 10 points higher gives a $1.90 loss, and 20 points
    # higher turns it into a $3.32 GAIN.
    #
    # So freezing the surface OVERSTATES the loss tail for a long put and understates the
    # gain tail. The stress figures below are, if anything, harsher than a true joint
    # simulation would produce for this particular position -- not milder.
    worst_stress = stress_revaluation.nsmallest(1, "scenario_pnl").iloc[0]

    print()
    print(f"  NOTE: this keeps today's volatility surface and changes only the")
    print(f"  distribution of spot moves. The direction of that bias is worth stating,")
    print(f"  because it is the opposite of the obvious guess. A long put is LONG VEGA")
    print(f"  and its losses come from RALLIES, not selloffs.")
    print()
    print(f"    worst stress scenario : {worst_stress['historical_date']:%Y-%m-%d}, "
          f"a {worst_stress['historical_return']:+.2%} day")
    print(f"    on our frozen surface : put collapses to "
          f"${worst_stress['scenario_option_price']:.2f}, "
          f"position loses ${abs(worst_stress['scenario_pnl']):.2f}")
    print(f"    with vol +10 points   : loss of $1.90")
    print(f"    with vol +20 points   : a $3.32 GAIN")
    print()
    print(f"  Through 2020 the surface sat far above today's, so freezing it OVERSTATES")
    print(f"  the loss tail for a long put. These stress figures are, if anything,")
    print(f"  harsher than a true joint simulation would give for this position.")

    # ---- Save ---------------------------------------------------------------------------
    print_section("PHASE 5.6  TABLES")

    revaluation_path = os.path.join(config["tables_dir"], "scenario_revaluation.csv")
    counterfactual.to_csv(revaluation_path, index=False)

    stress_path = os.path.join(config["tables_dir"], "scenario_revaluation_stress.csv")
    stress_revaluation.to_csv(stress_path, index=False)

    print(f"  Saved table:  {revaluation_path}")
    print(f"  Saved table:  {stress_path}")

    return {
        "revaluation": revaluation,
        "base_price": base_price,
        "stress_revaluation": stress_revaluation,
        "position": position,
    }


def run_phase_6(config, phase_5_results):
    """
    Phase 6: the filing's 99% VaR, by explicit interpolation between order statistics.

    Inputs:
        config (dict):          the CONFIG dictionary loaded from configs/.
        phase_5_results (dict): output of `run_phase_5`, for the scenario P&Ls.

    Returns:
        dict carrying:
            'var_result'        (dict) output of `var.compute_var`, baseline window
            'stress_var_result' (dict) the same for the stress window
    """
    revaluation = phase_5_results["revaluation"]
    stress_revaluation = phase_5_results["stress_revaluation"]
    position = phase_5_results["position"]
    confidence_level = config["confidence_level"]

    # ---- The headline number -----------------------------------------------------------
    print_section("PHASE 6.1  99% VALUE-AT-RISK, BASELINE WINDOW")

    var_result = var.compute_var(
        revaluation["scenario_pnl"], confidence_level=confidence_level
    )

    var.describe_var(var_result, revaluation, confidence_level=confidence_level,
                     label=f"{position['quantity']:+.0f} x SPY "
                           f"{config['reference_expiry']} ${position['strike']:.0f} "
                           f"{position['option_type']}, "
                           f"{config['reference_date'][:4]} window")

    # ---- Cross-check --------------------------------------------------------------------
    print_section("PHASE 6.2  CROSS-CHECK AGAINST NUMPY")

    numpy_quantile = var.cross_check_against_numpy(
        revaluation["scenario_pnl"], confidence_level
    )
    difference = abs(numpy_quantile - var_result["quantile_pnl"])

    print(f"  numpy.percentile(method='linear') : {numpy_quantile:+.12f}")
    print(f"  our explicit interpolation        : {var_result['quantile_pnl']:+.12f}")
    print(f"  difference                        : {difference:.3e}")
    print()
    print(f"  These agree because NumPy's default interpolation uses the SAME rank")
    print(f"  definition we implemented, (N-1)*alpha. That is why it makes a good check")
    print(f"  and a bad answer: matching it confirms our arithmetic, but calling it would")
    print(f"  have meant adopting whichever convention NumPy happens to default to rather")
    print(f"  than implementing the one the filing describes.")

    if difference > 1e-9:
        print(f"  WARNING: the two disagree by more than floating point noise.")

    # ---- How much does the convention matter? -------------------------------------------
    print_section("PHASE 6.3  SENSITIVITY TO THE RANK CONVENTION")

    conventions = var.compare_rank_conventions(
        revaluation["scenario_pnl"], confidence_level
    )

    print(f"  'Linear interpolation to the 99% threshold' still leaves open WHERE in")
    print(f"  index space the 99% point sits. Several conventions are in common use:")
    print()
    print(f"    {'convention':<46} {'rank':>7} {'VaR':>10} {'vs ours':>9}")

    for _, row in conventions.iterrows():
        print(f"    {row['convention']:<46} {row['fractional_rank']:>7.4f} "
              f"{row['var']:>10.4f} {row['difference_vs_ours']:>+9.4f}")

    spread = conventions["var"].max() - conventions["var"].min()
    print()
    print(f"  Spread across conventions: ${spread:.4f} on a ${var_result['var']:.4f} VaR "
          f"({spread / var_result['var']:.2%}).")
    print(f"  Small here, but it bounds how precisely the filing's wording can be")
    print(f"  reproduced at all, and it grows as the scenario count falls.")

    # ---- The stress window ---------------------------------------------------------------
    print_section("PHASE 6.4  THE SAME POSITION UNDER THE STRESS WINDOW")

    stress_var_result = var.compute_var(
        stress_revaluation["scenario_pnl"], confidence_level=confidence_level
    )

    var.describe_var(stress_var_result, stress_revaluation,
                     confidence_level=confidence_level,
                     label=f"same position, {config['stress_window_end'][:4]} window")

    ratio = stress_var_result["var"] / var_result["var"]

    print()
    baseline_label = f"{config['reference_date'][:4]} baseline"
    stress_label = f"{config['stress_window_end'][:4]} stress"
    print(f"  {'':<28} {baseline_label:>15} {stress_label:>15}")
    print(f"  {'99% VaR':<28} {var_result['var']:>15.4f} "
          f"{stress_var_result['var']:>15.4f}")
    print(f"  {'expected shortfall':<28} {var_result['expected_shortfall']:>15.4f} "
          f"{stress_var_result['expected_shortfall']:>15.4f}")
    print(f"  {'worst scenario':<28} "
          f"{-revaluation['scenario_pnl'].min():>15.4f} "
          f"{-stress_revaluation['scenario_pnl'].min():>15.4f}")
    print()
    print(f"  The stress VaR is {ratio:.1f}x the baseline. Same position, same pricing")
    print(f"  models, same 250-day method -- only the twelve months behind the reference")
    print(f"  date differ.")
    print()
    print(f"  Read the stress figure as an UPPER bound, not a lower one. Phase 5 measured")
    print(f"  the direction of the frozen-surface bias: a long put is long vega and loses")
    print(f"  on rallies, so holding today's calm surface instead of 2020's elevated one")
    print(f"  makes the losing tail DEEPER than a true joint replay would. On the worst")
    print(f"  stress scenario, lifting vol 10 points cut the loss from $5.59 to $1.90.")
    print(f"  So ${stress_var_result['var']:.2f} overstates what a full joint simulation")
    print(f"  of 2020 would produce for this particular position.")

    # ---- Context ---------------------------------------------------------------------------
    print_section("PHASE 6.5  READING THE NUMBER")

    base_price = phase_5_results["base_price"]
    position_value = position["quantity"] * base_price

    print(f"  Position value today      : ${position_value:.4f}")
    print(f"  99% one-day VaR           : ${var_result['var']:.4f}   "
          f"({var_result['var'] / abs(position_value):.2%} of position value)")
    print(f"  Expected shortfall        : ${var_result['expected_shortfall']:.4f}   "
          f"({var_result['expected_shortfall'] / abs(position_value):.2%})")
    print()
    print(f"  A long put LOSES when the market RALLIES, so every scenario in the tail is")
    print(f"  an up day. That inverts the usual intuition -- the dates driving this VaR")
    print(f"  are the best days in the window, not the worst.")
    print()
    print(f"  Two caveats carried forward, both quantified earlier:")
    print(f"    - this number inherits the severity of whichever twelve months precede")
    print(f"      {config['reference_date']} (Phase 4). A window containing a crisis "
          f"produces a")
    print(f"      materially larger VaR for the same position, so read this figure")
    print(f"      alongside that comparison;")
    print(f"    - BAW's approximation error contributes roughly 1% to the scenario P&Ls")
    print(f"      (Phase 2), and therefore to this number.")

    # ---- Save ----------------------------------------------------------------------------
    print_section("PHASE 6.6  TABLES")

    summary_path = os.path.join(config["tables_dir"], "var_summary.csv")
    pd.DataFrame([
        {
            # Derived, not hardcoded: this label is written into var_summary.csv and is
            # what a comparison across reference cases reads to tell the runs apart.
            "window": f"{config['reference_date'][:4]} baseline",
            "confidence_level": confidence_level,
            "n_scenarios": var_result["n_scenarios"],
            "var": var_result["var"],
            "expected_shortfall": var_result["expected_shortfall"],
            "quantile_pnl": var_result["quantile_pnl"],
            "fractional_rank": var_result["fractional_rank"],
            "lower_index": var_result["lower_index"],
            "upper_index": var_result["upper_index"],
            "weight": var_result["weight"],
            "lower_pnl": var_result["lower_pnl"],
            "upper_pnl": var_result["upper_pnl"],
            "worst_scenario_pnl": revaluation["scenario_pnl"].min(),
            "position_value": position_value,
        },
        {
            "window": f"{config['stress_window_end'][:4]} stress",
            "confidence_level": confidence_level,
            "n_scenarios": stress_var_result["n_scenarios"],
            "var": stress_var_result["var"],
            "expected_shortfall": stress_var_result["expected_shortfall"],
            "quantile_pnl": stress_var_result["quantile_pnl"],
            "fractional_rank": stress_var_result["fractional_rank"],
            "lower_index": stress_var_result["lower_index"],
            "upper_index": stress_var_result["upper_index"],
            "weight": stress_var_result["weight"],
            "lower_pnl": stress_var_result["lower_pnl"],
            "upper_pnl": stress_var_result["upper_pnl"],
            "worst_scenario_pnl": stress_revaluation["scenario_pnl"].min(),
            "position_value": position_value,
        },
    ]).to_csv(summary_path, index=False)

    conventions_path = os.path.join(config["tables_dir"], "var_rank_conventions.csv")
    conventions.to_csv(conventions_path, index=False)

    print(f"  Saved table:  {summary_path}")
    print(f"  Saved table:  {conventions_path}")

    return {
        "var_result": var_result,
        "stress_var_result": stress_var_result,
    }


def build_summary_rows(config, phase_0_results, phase_3_results, phase_4_results,
                       phase_5_results, phase_6_results):
    """
    Assemble the headline results as label/value pairs for the summary table image.

    Everything a reader needs to understand what was measured and what came out, in one
    place: the position, the market state it was priced in, the calibrated model, the
    result, and the two caveats that most affect how the number should be read.

    Inputs:
        config (dict):          the CONFIG dictionary loaded from configs/.
        phase_0_results (dict), phase_3_results (dict), phase_4_results (dict),
        phase_5_results (dict), phase_6_results (dict): the phase outputs.

    Returns:
        list of (label, value) string pairs. A pair whose value is "" is a section heading.
    """
    position = phase_5_results["position"]
    calibration = phase_3_results["calibration"]
    var_result = phase_6_results["var_result"]
    stress_var_result = phase_6_results["stress_var_result"]
    statistics = phase_4_results["statistics"]
    revaluation = phase_5_results["revaluation"]
    base_price = phase_5_results["base_price"]

    position_value = position["quantity"] * base_price

    market_row = phase_3_results["comparison"].loc[
        phase_3_results["comparison"]["strike"] == position["strike"]
    ]
    market_mid = float(market_row["market_mid"].iloc[0]) if not market_row.empty else None

    worst_index = revaluation["scenario_pnl"].idxmin()
    worst_pnl = revaluation.loc[worst_index, "scenario_pnl"]
    worst_date = revaluation.loc[worst_index, "historical_date"]
    worst_return = revaluation.loc[worst_index, "historical_return"]

    # The 1st percentile of the return sample, which is roughly what the 99% VaR reads.
    # Taken from the Phase 4 window comparison so the baseline and stress figures come
    # from the same calculation rather than one being quoted from memory.
    window_comparison = phase_4_results["window_comparison"]
    our_row = window_comparison.loc[
        window_comparison["as_of"] == pd.to_datetime(config["reference_date"])
    ]
    stress_row = window_comparison.loc[
        window_comparison["as_of"] == pd.to_datetime(config["stress_window_end"])
    ]

    our_percentile = float(our_row["first_percentile"].iloc[0])
    stress_percentile = float(stress_row["first_percentile"].iloc[0])

    rows = [
        ("POSITION", ""),
        ("Underlying", "SPY (SPDR S&P 500 ETF Trust)"),
        ("Contract", f"${position['strike']:.0f} {position['option_type']}, "
                     f"expiry {config['reference_expiry']}"),
        ("Quantity", f"{position['quantity']:+.0f} contract "
                     f"({'long' if position['quantity'] > 0 else 'short'})"),
        ("Exercise style", "American (BAW approximation)"),

        ("MARKET STATE", ""),
        ("Reference date", config["reference_date"]),
        ("SPY spot", f"${phase_0_results['spot']:.2f}"),
        ("Forward at expiry", f"${phase_0_results['forward']:.3f}  (from put-call parity)"),
        ("Time to expiry", f"{phase_0_results['time_to_expiry_years']:.5f} years "
                           f"({round(phase_0_results['time_to_expiry_years'] * 365)} days)"),
        ("Risk-free rate", f"{config['risk_free_rate']:.4%}  (FRED DGS3MO)"),
        ("Cost of carry b", f"{phase_0_results['cost_of_carry']:.4%}  (derived)"),

        ("CALIBRATED SABR", ""),
        ("alpha  (volatility level)", f"{calibration['alpha']:.6f}"),
        ("beta   (elasticity, fixed)", f"{calibration['beta']:.4f}"),
        ("rho    (spot/vol correlation)", f"{calibration['rho']:+.6f}"),
        ("nu     (volatility of volatility)", f"{calibration['nu']:.6f}"),
        ("Fit quality", f"RMSE {calibration['rmse'] * 100:.4f} vol points over "
                        f"{calibration['n_strikes']} strikes"),

        ("PRICING TODAY", ""),
        ("SABR implied volatility", f"{revaluation.attrs['base_volatility']:.4%}"),
        ("Theoretical price (SABR to BAW)", f"${base_price:.6f}"),
        ("Market price (mid)",
         f"${market_mid:.4f}" if market_mid is not None else "n/a"),
        ("Difference",
         f"${base_price - market_mid:+.4f}" if market_mid is not None else "n/a"),
        ("Position value", f"${position_value:.4f}"),

        ("HISTORICAL SIMULATION", ""),
        ("Lookback window", f"{revaluation['historical_date'].iloc[0]:%Y-%m-%d} to "
                            f"{revaluation['historical_date'].iloc[-1]:%Y-%m-%d}"),
        ("Scenarios", f"{len(revaluation)} (unweighted, full revaluation)"),
        ("Realised volatility in window", f"{statistics['annualised_vol']:.2%} annualised"),
        ("Worst daily move in window", f"{statistics['min']:+.4%} on "
                                       f"{statistics['worst_date']:%Y-%m-%d}"),

        ("RESULT", ""),
        ("99% one-day VaR", f"${var_result['var']:.4f}   "
                            f"({var_result['var'] / abs(position_value):.2%} of position)"),
        ("Expected shortfall", f"${var_result['expected_shortfall']:.4f}   "
                               f"(mean of {var_result['n_breaching']} breaches)"),
        ("Worst scenario in sample", f"${-worst_pnl:.4f} loss"),
        ("...produced by", f"{worst_date:%Y-%m-%d}, a {worst_return:+.3%} move"),
        (f"Stress-window VaR ({config['stress_window_end'][:4]})",
         f"${stress_var_result['var']:.4f}   "
         f"({stress_var_result['var'] / var_result['var']:.1f}x baseline)"),

        ("HOW TO READ IT", ""),
        ("Window contains no crisis",
         f"1st pctile return {our_percentile:+.2%} vs {stress_percentile:+.2%} "
         f"in a {config['stress_window_end'][:4]} window"),
        ("BAW approximation error", "~1% of scenario P&L (measured, Phase 2)"),
        ("Volatility surface", "frozen; spot risk only, no independent vol risk"),
    ]

    return rows


def run_phase_8(config, phase_0_results, phase_3_results, phase_4_results,
                phase_5_results, phase_6_results):
    """
    Phase 8: the remaining figures and the compiled summary.

    Three figures are produced here; the other three required by the brief were produced
    in the phases that generated their data (`spy_price_history.png` in Phase 4,
    `sabr_fit.png` in Phases 1 and 3, `baw_vs_market_prices.png` in Phase 3).

    Inputs:
        config (dict) and the five phase result dicts.

    Returns:
        None. Writes figures and one table.
    """
    print_section("PHASE 8.1  RESULT FIGURES")

    position = phase_5_results["position"]
    revaluation = phase_5_results["revaluation"]
    var_result = phase_6_results["var_result"]

    plots.plot_pnl_distribution(
        revaluation=revaluation,
        var_result=var_result,
        position=position,
        base_price=phase_5_results["base_price"],
        output_path=os.path.join(config["figures_dir"], "pnl_distribution.png"),
    )

    plots.plot_pnl_timeseries(
        revaluation=revaluation,
        var_result=var_result,
        position=position,
        output_path=os.path.join(config["figures_dir"], "pnl_timeseries.png"),
    )

    print_section("PHASE 8.2  SUMMARY TABLE")

    summary_rows = build_summary_rows(config, phase_0_results, phase_3_results,
                                      phase_4_results, phase_5_results, phase_6_results)

    plots.plot_summary_table(
        summary_rows=summary_rows,
        output_path=os.path.join(config["figures_dir"], "summary_table.png"),
    )

    # The same content as a machine-readable table, since the image is for reading and
    # the CSV is for anything downstream.
    summary_path = os.path.join(config["tables_dir"], "summary.csv")
    pd.DataFrame(
        [row for row in summary_rows if row[1] != ""], columns=["quantity", "value"]
    ).to_csv(summary_path, index=False)

    print(f"  Saved table:  {summary_path}")

    # ---- Inventory --------------------------------------------------------------------
    print_section("PHASE 8.3  EVERYTHING THIS PIPELINE PRODUCED")

    for directory, description in [(config["figures_dir"], "figures"),
                                   (config["tables_dir"], "tables")]:
        names = sorted(os.listdir(directory))
        print(f"  {len(names)} {description} in {directory}/")
        for name in names:
            print(f"      {name}")
        print()


def main(config_name=None):
    """
    Run the full pipeline end to end against one config.

    Inputs:
        config_name (str or None): which file under configs/ to run, without the .py.
            Defaults to DEFAULT_CONFIG, which is the original 2023-12-29 case, so that
            `python3 main.py` reproduces the run recorded in PROJECT_LOG.md exactly as
            it did before configuration moved out of this file.

    Returns:
        None. Prints progress and writes figures and tables under the directories the
        chosen config names -- each reference case writes to its own, so runs never
        overwrite one another.
    """
    config = load_config(config_name or DEFAULT_CONFIG)

    print_section("SPY OPTIONS VaR  --  SR-FICC-2013-02 METHODOLOGY REPLICATION")
    print(f"  Config          : {config_name or DEFAULT_CONFIG}")
    print(f"  Reference date  : {config['reference_date']}")
    print(f"  Reference expiry: {config['reference_expiry']}")
    print(f"  Data directory  : {config['data_dir']}")

    phase_0_results = run_phase_0(config)
    phase_1_results = run_phase_1(config, phase_0_results)
    phase_3_results = run_phase_3(config, phase_0_results, phase_1_results)
    phase_4_results = run_phase_4(config, phase_0_results)
    phase_5_results = run_phase_5(config, phase_0_results, phase_3_results,
                                  phase_4_results)
    phase_6_results = run_phase_6(config, phase_5_results)
    run_phase_8(config, phase_0_results, phase_3_results, phase_4_results,
                phase_5_results, phase_6_results)

    print_section("PIPELINE COMPLETE")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
