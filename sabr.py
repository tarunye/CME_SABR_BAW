"""
SABR stochastic volatility model: implied volatility formula and smile calibration.

WHAT SABR IS FOR
----------------
Black-Scholes assumes one volatility for all strikes. The market disagrees: plot implied
vol against strike and you get a smile or a skew, not a flat line. We need a model that
reproduces that shape, because our whole VaR exercise depends on knowing what implied vol
our option would have if SPY moved -- and to know that, we need the whole curve, not one
number.

SABR (Hagan, Kumar, Lesniewski & Woodward, "Managing Smile Risk", Wilmott, September 2002)
models the forward and its volatility as a coupled pair of stochastic processes:

    dF = alpha_t * F^beta  dW1
    d(alpha_t) = nu * alpha_t  dW2
    correlation(dW1, dW2) = rho

Four parameters, each doing a distinct job:

    alpha  the initial volatility level -- sets how HIGH the smile sits
    beta   the elasticity of variance -- how the vol level responds to the forward level.
           beta = 1 gives lognormal dynamics, beta = 0 gives normal (Bachelier) dynamics
    rho    the correlation between the forward and its own volatility. Negative rho means
           vol rises when the market falls, which is what TILTS the smile into a skew
    nu     "vol of vol" -- how erratic the volatility itself is, which sets the CURVATURE

The reason SABR became the market standard is Hagan's closed-form asymptotic expansion for
the Black implied volatility it generates. No Monte Carlo, no PDE -- one formula. That
formula is `sabr_implied_vol` below.

Run this file directly to calibrate against the Phase 0 smile and produce the fit plot:

    python3 sabr.py
"""

import numpy as np
from scipy.optimize import least_squares


# When the strike is this close to the forward (measured in |log(F/K)|), we switch to the
# closed-form at-the-money limit of the formula. See the long comment in
# `sabr_implied_vol` for why the general formula cannot be evaluated there directly.
AT_THE_MONEY_TOLERANCE = 1e-7


def sabr_implied_vol(forward, strike, time_to_expiry, alpha, beta, rho, nu):
    """
    Hagan et al. (2002) lognormal (Black) implied volatility for one strike.

    This is equation (A.69a) of the Wilmott paper -- the standard SABR implied vol
    asymptotic expansion. It is built from three factors multiplied together, and the code
    below computes them in that order:

        implied_vol  =  (alpha / DENOMINATOR)  *  (z / x_z)  *  CORRECTION

    Inputs:
        forward (float):          forward price of the underlying at expiry, in dollars.
                                  NOTE this is the FORWARD, not spot. Passing spot here is
                                  the single most common SABR bug.
        strike (float):           option strike, in dollars.
        time_to_expiry (float):   T, in years.
        alpha (float):            initial volatility level, > 0. Roughly the at-the-money
                                  vol when beta = 1.
        beta (float):             elasticity, in [0, 1]. Fixed, not fitted -- see
                                  `calibrate_sabr`.
        rho (float):              forward/volatility correlation, strictly in (-1, 1).
        nu (float):               volatility of volatility, > 0.

    Returns:
        float: Black implied volatility as a decimal (0.12 means 12%).
    """
    # Guard the inputs that would produce complex numbers or division by zero rather than
    # a wrong answer we might not notice.
    if forward <= 0 or strike <= 0:
        raise ValueError(f"Forward and strike must be positive, got F={forward}, K={strike}")

    if time_to_expiry <= 0:
        raise ValueError(f"Time to expiry must be positive, got T={time_to_expiry}")

    log_forward_over_strike = np.log(forward / strike)

    # These two products appear repeatedly. (F*K)^((1-beta)/2) is the geometric mean of
    # forward and strike raised to the elasticity power -- it is the model's notion of
    # "the level at which this option lives", halfway between the two in log space.
    forward_strike_product = forward * strike
    geometric_level = forward_strike_product ** ((1.0 - beta) / 2.0)

    # ---- FACTOR 3: the (1 + [...] * T) correction term --------------------------------
    # This collects the first-order-in-T corrections. It is common to both the general and
    # the at-the-money branch, so we build it once. The three bracketed pieces are:
    #   - a beta-driven convexity adjustment from the CEV backbone,
    #   - a cross term in rho*beta*nu*alpha, the interaction of skew and vol-of-vol,
    #   - a pure vol-of-vol variance term. The (2 - 3*rho^2) shape is why a large |rho|
    #     actually REDUCES this contribution: strongly correlated vol is less "random".
    beta_convexity_term = ((1.0 - beta) ** 2 / 24.0) * (alpha ** 2) / (
        forward_strike_product ** (1.0 - beta)
    )
    skew_vol_cross_term = 0.25 * rho * beta * nu * alpha / geometric_level
    vol_of_vol_term = ((2.0 - 3.0 * rho ** 2) / 24.0) * nu ** 2

    correction = 1.0 + (
        beta_convexity_term + skew_vol_cross_term + vol_of_vol_term
    ) * time_to_expiry

    # ---- THE AT-THE-MONEY BRANCH ------------------------------------------------------
    # NOTE (approximation): at F = K we have log(F/K) = 0, therefore z = 0, and x(z) = 0
    # as well -- so the z/x(z) factor is literally 0/0. The limit is well defined and
    # equals 1 (x(z) -> z as z -> 0), but floating point cannot evaluate it: near the money
    # both numerator and denominator are tiny and their ratio is destroyed by cancellation.
    # We therefore branch explicitly below a tolerance on |log(F/K)| and use the closed-form
    # ATM limit, in which the denominator collapses to F^(1-beta) and z/x(z) collapses to 1.
    # The tolerance is 1e-7, chosen so that the cancellation error in the general branch is
    # still far below double precision -- the two branches agree to roughly 1e-13 vol
    # points at the boundary, which `check_atm_branch_continuity` verifies.
    if abs(log_forward_over_strike) < AT_THE_MONEY_TOLERANCE:
        return (alpha / (forward ** (1.0 - beta))) * correction

    # ---- FACTOR 1: the denominator expansion -------------------------------------------
    # alpha divided by this is the leading-order vol. The two log terms are a series
    # correction that matters only when beta < 1 and the strike is far from the forward;
    # at beta = 1 they vanish identically and the denominator is just 1.
    denominator = geometric_level * (
        1.0
        + ((1.0 - beta) ** 2 / 24.0) * log_forward_over_strike ** 2
        + ((1.0 - beta) ** 4 / 1920.0) * log_forward_over_strike ** 4
    )

    # ---- FACTOR 2: the z / x(z) skew factor --------------------------------------------
    # z measures how far this strike is from the forward, scaled by vol-of-vol over vol.
    # It is the natural "distance" coordinate of the model: a strike is far away not in
    # dollars but in units of how much the volatility could plausibly wander.
    z = (nu / alpha) * geometric_level * log_forward_over_strike

    # x(z) is the integral of the inverse of the local vol along the path from F to K.
    # The ratio z/x(z) is what bends the flat Black vol into a smile: it is 1 at the money
    # and departs from 1 asymmetrically as rho pulls one wing up and the other down.
    x_z = np.log((np.sqrt(1.0 - 2.0 * rho * z + z ** 2) + z - rho) / (1.0 - rho))

    return (alpha / denominator) * (z / x_z) * correction


def sabr_vol_curve(forward, strikes, time_to_expiry, alpha, beta, rho, nu):
    """
    Evaluate `sabr_implied_vol` across a list of strikes.

    A plain loop rather than a vectorised implementation. The at-the-money branch inside
    `sabr_implied_vol` is a genuine `if`, and expressing it with array masking would make
    the formula materially harder to read for no benefit we care about -- we evaluate a
    few hundred strikes, not a few million.

    Inputs:
        forward (float):        forward price, in dollars.
        strikes (array-like):   strikes, in dollars.
        time_to_expiry (float): T, in years.
        alpha, beta, rho, nu:   SABR parameters, as in `sabr_implied_vol`.

    Returns:
        numpy.ndarray of implied volatilities, decimal, same length as `strikes`.
    """
    vols = [
        sabr_implied_vol(forward, strike, time_to_expiry, alpha, beta, rho, nu)
        for strike in strikes
    ]

    return np.array(vols)


def calibrate_sabr(strikes, market_vols, forward, time_to_expiry, beta=1.0):
    """
    Fit alpha, rho and nu to an observed volatility smile by least squares.

    WHY BETA IS FIXED RATHER THAN FITTED
    ------------------------------------
    beta and rho are very nearly redundant. Both of them tilt the smile: lowering beta
    steepens the skew through the CEV backbone (vol rises as the forward falls), and
    making rho more negative steepens it through the correlation channel. Fit them jointly
    and the optimiser wanders along a long flat valley in which many (beta, rho) pairs
    give almost identical smiles -- the fit "succeeds" but the parameters are arbitrary and
    will jump around from one day to the next.

    Market practice is therefore to fix beta by convention and fit the other three. We
    default to beta = 1.0 for two reasons specific to equity index options:

      1. It makes the forward process lognormal, which is the convention equity implied
         vols are quoted and reasoned about in.
      2. It leaves rho as the sole driver of skew, which makes the calibrated rho directly
         interpretable rather than confounded with beta.

    # NOTE (deviation from filing): the filing specifies SABR but does not state a beta.
    # 1.0 is our choice. It has a real consequence for Phase 5: with beta = 1 the SABR
    # "backbone" is flat in lognormal terms, meaning at-the-money vol does not change as
    # the forward moves. With beta < 1 it would rise as the forward falls, adding a
    # leverage effect. beta is a CONFIG knob so this can be revisited.

    WHY EACH BOUND EXISTS
    ---------------------
      alpha > 0   alpha is a volatility level. A negative volatility is not a bad fit, it
                  is a meaningless quantity, and the formula would return nonsense.
      -1 < rho < 1  rho is a correlation. Outside (-1, 1) it is not a correlation at all,
                  and the model becomes arbitrage-generating. The bound is also numerical:
                  x(z) divides by (1 - rho), which blows up as rho approaches 1.
      nu > 0      nu is the volatility of volatility, a magnitude. nu = 0 collapses SABR
                  to a flat CEV smile with no curvature; negative is meaningless, and the
                  sign of the skew is rho's job, not nu's.

    Inputs:
        strikes (array-like):     strikes to fit, in dollars.
        market_vols (array-like): observed implied vols at those strikes, decimal.
        forward (float):          forward price, in dollars.
        time_to_expiry (float):   T, in years.
        beta (float):             the fixed elasticity. Not fitted.

    Returns:
        dict with keys:
            'alpha', 'beta', 'rho', 'nu' (float) the calibrated parameters
            'rmse'            (float) root mean squared error, in VOL POINTS (0.01 = 1%)
            'max_abs_error'   (float) largest absolute residual, in vol points
            'residuals'       (ndarray) fitted minus market, per strike, in vol points
            'fitted_vols'     (ndarray) the model vols at the input strikes
            'n_strikes'       (int)   how many strikes were fitted
            'starting_point'  (tuple) the (alpha, rho, nu) seed that produced this fit
            'n_starts_tried'  (int)   how many seeds were attempted
    """
    strikes = np.asarray(strikes, dtype=float)
    market_vols = np.asarray(market_vols, dtype=float)

    if len(strikes) != len(market_vols):
        raise ValueError(
            f"strikes and market_vols must be the same length, got "
            f"{len(strikes)} and {len(market_vols)}"
        )

    if len(strikes) < 3:
        raise ValueError(
            f"Need at least 3 strikes to fit 3 parameters, got {len(strikes)}."
        )

    def residual_function(parameters):
        """Fitted minus market vol, per strike. This is what least_squares minimises."""
        alpha, rho, nu = parameters
        fitted = sabr_vol_curve(forward, strikes, time_to_expiry, alpha, beta, rho, nu)
        return fitted - market_vols

    # A sensible seed for alpha: at beta = 1 the at-the-money vol is approximately alpha,
    # so the vol nearest the forward is a good first guess. At beta < 1 we scale it by
    # F^(1-beta) to put it on the right footing.
    at_the_money_index = int(np.argmin(np.abs(strikes - forward)))
    alpha_seed = market_vols[at_the_money_index] * forward ** (1.0 - beta)

    # Least squares on SABR is not convex -- it has local minima, particularly in rho.
    # We try a spread of starting points and keep the best. The rho seeds span from
    # strongly negative (a steep equity-style skew) to zero (a symmetric smile), and the
    # nu seeds span a calm to a very jumpy vol-of-vol.
    starting_points = []
    for rho_seed in [-0.9, -0.6, -0.3, 0.0]:
        for nu_seed in [0.3, 1.0, 2.0]:
            starting_points.append((alpha_seed, rho_seed, nu_seed))

    # alpha and nu are bounded away from exactly zero, and rho away from exactly +-1,
    # because the formula divides by alpha and by (1 - rho).
    #
    # The upper bound on alpha has to SCALE WITH beta, which is easy to get wrong. At
    # beta = 1 the model is lognormal and alpha is a proportional volatility, so it sits
    # around 0.1 and a bound of 5.0 is generous. At beta = 0 the model is normal
    # (Bachelier) and alpha is an ABSOLUTE volatility measured in dollars, so for a $479
    # forward at 11% vol alpha is around 53 -- a fixed bound of 5.0 would make the fit
    # impossible. Scaling by forward^(1-beta) puts the bound on the right footing for
    # whatever beta we are given.
    alpha_upper_bound = 5.0 * forward ** (1.0 - beta)

    lower_bounds = [1e-6, -0.9999, 1e-6]
    upper_bounds = [alpha_upper_bound, 0.9999, 10.0]

    best_result = None
    best_seed = None

    for seed in starting_points:
        # Keep the seed strictly inside the bounds; least_squares rejects one that sits
        # on or outside them.
        seed = (
            float(np.clip(seed[0], lower_bounds[0] * 10, upper_bounds[0] * 0.99)),
            float(np.clip(seed[1], lower_bounds[1] * 0.99, upper_bounds[1] * 0.99)),
            float(np.clip(seed[2], lower_bounds[2] * 10, upper_bounds[2] * 0.99)),
        )

        result = least_squares(
            residual_function,
            x0=seed,
            bounds=(lower_bounds, upper_bounds),
            method="trf",
        )

        # `cost` is half the sum of squared residuals. Smaller is better.
        if best_result is None or result.cost < best_result.cost:
            best_result = result
            best_seed = seed

    alpha, rho, nu = best_result.x

    fitted_vols = sabr_vol_curve(forward, strikes, time_to_expiry, alpha, beta, rho, nu)
    residuals = fitted_vols - market_vols

    return {
        "alpha": float(alpha),
        "beta": float(beta),
        "rho": float(rho),
        "nu": float(nu),
        "rmse": float(np.sqrt(np.mean(residuals ** 2))),
        "max_abs_error": float(np.max(np.abs(residuals))),
        "residuals": residuals,
        "fitted_vols": fitted_vols,
        "n_strikes": len(strikes),
        "starting_point": best_seed,
        "n_starts_tried": len(starting_points),
    }


def describe_calibration(calibration):
    """
    Print the calibrated parameters with a plain-English reading of each, plus fit quality.

    Inputs:
        calibration (dict): output of `calibrate_sabr`.

    Returns:
        None. Prints to stdout.
    """
    print("CALIBRATED SABR PARAMETERS")
    print(f"  alpha = {calibration['alpha']:.6f}   volatility level: sets how high the "
          f"smile sits.")
    print(f"                       At beta=1 this is close to the at-the-money vol, so "
          f"~{calibration['alpha']:.1%}.")
    print(f"  beta  = {calibration['beta']:.4f}     FIXED, not fitted. Elasticity of "
          f"variance;")
    print(f"                       1.0 means lognormal forward dynamics.")
    print(f"  rho   = {calibration['rho']:+.6f}  spot/vol correlation: controls SKEW.")

    if calibration["rho"] < 0:
        print(f"                       Negative, as expected for an equity index -- vol "
              f"rises when SPY falls,")
        print(f"                       which lifts the put wing and creates the downward "
              f"skew.")
    else:
        print(f"                       POSITIVE, which is unusual for an equity index and "
              f"worth investigating.")

    print(f"  nu    = {calibration['nu']:.6f}   vol-of-vol: controls smile CURVATURE.")
    print(f"                       Higher nu bends both wings further up away from the "
          f"at-the-money point.")
    print()
    print("FIT QUALITY")
    print(f"  Strikes fitted    : {calibration['n_strikes']}")
    print(f"  RMSE              : {calibration['rmse'] * 100:.4f} vol points")
    print(f"  Max abs error     : {calibration['max_abs_error'] * 100:.4f} vol points")
    print(f"  Starting points   : {calibration['n_starts_tried']} tried; winner was "
          f"alpha={calibration['starting_point'][0]:.4f}, "
          f"rho={calibration['starting_point'][1]:+.2f}, "
          f"nu={calibration['starting_point'][2]:.2f}")


def check_atm_branch_continuity(forward, time_to_expiry, alpha, beta, rho, nu):
    """
    Verify the at-the-money branch joins the general formula smoothly.

    A branch in a formula is a place where a bug can hide in plain sight: if the two sides
    disagree, the smile has an invisible step in it exactly where our option usually sits.

    We probe strikes just OUTSIDE the tolerance boundary, where the general z/x(z) branch
    is taken, and compare against the at-the-money branch value. The probe offsets have to
    be chosen carefully. The smile has a genuine slope -- roughly half a vol point per 1%
    of strike for an equity index -- so probing at, say, 0.1% away from the forward would
    report a "gap" of 0.05 vol points that is simply the real skew, not a discontinuity.
    Probing within a few multiples of the 1e-7 tolerance makes the genuine slope
    contribution around 1e-5 vol points, so anything materially larger than that is a
    real discontinuity in the implementation.

    Inputs:
        forward (float):        forward price, in dollars.
        time_to_expiry (float): T, in years.
        alpha, beta, rho, nu:   SABR parameters.

    Returns:
        dict with keys:
            'observed_gap' (float) largest |general branch - ATM branch| just outside the
                           tolerance, as a decimal vol
            'expected_gap' (float) how much of that gap the genuine smile slope accounts
                           for, computed from the model's own at-the-money skew
            'excess'       (float) observed minus expected. This is the number that
                           matters: a real discontinuity shows up here, and it should be
                           at the level of floating point noise
    """
    at_the_money_vol = sabr_implied_vol(
        forward, forward, time_to_expiry, alpha, beta, rho, nu
    )

    # Measure the model's own at-the-money skew, in decimal vol per unit RELATIVE change
    # in strike, using a step far enough out to be numerically safe. This tells us how
    # much of any observed gap is simply the smile doing what a smile does.
    slope_step = 1e-4
    vol_above = sabr_implied_vol(
        forward, forward * (1.0 + slope_step), time_to_expiry, alpha, beta, rho, nu
    )
    vol_below = sabr_implied_vol(
        forward, forward * (1.0 - slope_step), time_to_expiry, alpha, beta, rho, nu
    )
    skew_per_relative_move = (vol_above - vol_below) / (2.0 * slope_step)

    largest_gap = 0.0
    largest_offset = 0.0

    # Offsets from just above the tolerance out to a hundred times it. All of these take
    # the general branch; the at-the-money value they are compared against does not.
    for offset in [2e-7, 1e-6, 1e-5]:
        for strike in [forward * (1.0 + offset), forward * (1.0 - offset)]:
            vol = sabr_implied_vol(
                forward, strike, time_to_expiry, alpha, beta, rho, nu
            )
            gap = abs(vol - at_the_money_vol)

            if gap > largest_gap:
                largest_gap = gap
                largest_offset = offset

    expected_gap = abs(skew_per_relative_move) * largest_offset

    return {
        "observed_gap": largest_gap,
        "expected_gap": expected_gap,
        "excess": largest_gap - expected_gap,
    }


def _run_standalone(config_name=None):
    """
    Calibrate against the Phase 0 smile and write the fit plot. Entry point for
    `python3 sabr.py`.

    Reads the two small tables Phase 0 leaves in the running config's `tables_dir`
    rather than re-deriving the market state, so this module stays independent of the
    pipeline in main.py. Takes the same argument main.py does:

        python3 sabr.py                     -> configs/config_2023_12_29.py (the default)
        python3 sabr.py config_2026_06_01   -> configs/config_2026_06_01.py

    Inputs:
        config_name (str or None): config module name without the .py. None means the
            default case.

    Returns:
        None. Prints the calibration and writes sabr_fit.png into the config's
        figures_dir.
    """
    import importlib.util
    import os

    import pandas as pd

    import plots

    # The config is loaded by file path, the same way main.py does it, rather than by
    # importing main. main.py imports this module, so importing it back would close the
    # import graph -- and the point of a standalone block is that it needs nothing from
    # the pipeline. The default below is main.DEFAULT_CONFIG; keep the two in step.
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
    config_name = config_name or "config_2023_12_29"
    config_path = os.path.join(config_dir, f"{config_name}.py")
    if not os.path.exists(config_path):
        available = sorted(f[:-3] for f in os.listdir(config_dir)
                           if f.endswith(".py") and not f.startswith("__"))
        raise SystemExit(f"No such config: {config_path}\n"
                         f"Available: {', '.join(available) or '(none)'}")
    spec = importlib.util.spec_from_file_location(config_name, config_path)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    config = config_module.CONFIG
    tables_dir = config["tables_dir"]
    figures_dir = config["figures_dir"]

    # Prefer the re-implied smile if the pipeline has produced one. Phase 3 replaces the
    # vendor's implied vols with volatilities we invert ourselves, and those are what the
    # pipeline calibrates to -- so running this file standalone should report the same
    # parameters as `python3 main.py`, not the superseded vendor-IV ones.
    reimplied_path = os.path.join(tables_dir, "reimplied_smile.csv")
    vendor_path = os.path.join(tables_dir, "market_smile.csv")
    state_path = os.path.join(tables_dir, "reference_market_state.csv")

    if os.path.exists(reimplied_path):
        smile_path = reimplied_path
        volatility_source = "re-implied from mid prices (Phase 3)"
    else:
        smile_path = vendor_path
        volatility_source = "vendor implied vols (Phase 0)"

    if not os.path.exists(smile_path) or not os.path.exists(state_path):
        command = "python3 main.py"
        if config_name != "config_2023_12_29":
            command += f" {config_name}"
        raise FileNotFoundError(
            f"Missing pipeline output. Run `{command}` first to produce\n"
            f"  {smile_path}\n  {state_path}"
        )

    smile = pd.read_csv(smile_path)
    state = pd.read_csv(state_path).iloc[0]

    forward = float(state["forward"])
    time_to_expiry = float(state["time_to_expiry_years"])
    beta = float(state["sabr_beta"])
    moneyness_band = float(state["calibration_moneyness_band"])

    # Restrict to the calibration range -- see the long note in `calibrate_sabr`.
    in_range = (smile["strike"] / forward - 1.0).abs() <= moneyness_band
    fit_smile = smile.loc[in_range]

    print(f"Calibrating SABR to {len(fit_smile)} strikes within "
          f"{moneyness_band:.0%} of the forward (${forward:.2f})")
    print(f"Volatility source: {volatility_source}")
    print()

    calibration = calibrate_sabr(
        strikes=fit_smile["strike"].to_numpy(),
        market_vols=fit_smile["market_iv"].to_numpy(),
        forward=forward,
        time_to_expiry=time_to_expiry,
        beta=beta,
    )

    describe_calibration(calibration)

    plots.plot_sabr_fit(
        fit_smile=fit_smile,
        full_smile=smile,
        calibration=calibration,
        forward=forward,
        time_to_expiry=time_to_expiry,
        spot=float(state["spot"]),
        reference_date=str(state["reference_date"]),
        expiry_date=str(state["expiry_date"]),
        output_path=os.path.join(figures_dir, "sabr_fit.png"),
    )


if __name__ == "__main__":
    import sys

    _run_standalone(sys.argv[1] if len(sys.argv) > 1 else None)
