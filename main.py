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
    run_phase_1(CONFIG, phase_0_results)

    print_section("PHASE 1 COMPLETE")


if __name__ == "__main__":
    main()
