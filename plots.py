"""
Figure generation for the SPY options VaR replication.

One function per figure, each saving a single .png to outputs/figures/. Figures are
deliberately kept to one idea each -- no subplot grids combining unrelated things --
because each one is meant to answer a specific question on its own.
"""

import os

import matplotlib
import numpy as np

# Use the non-interactive Agg backend so the pipeline runs identically whether or not a
# display is attached (e.g. over SSH, or in a scheduled run).
matplotlib.use("Agg")

import matplotlib.pyplot as plt


# Every figure is written at this resolution. 150 DPI is the point at which a chart stays
# legible when dropped into a document or viewed on a high-density screen.
FIGURE_DPI = 150


def _ensure_directory(path):
    """
    Create the directory holding `path` if it does not already exist.

    Inputs:
        path (str): a file path whose parent directory should exist.

    Returns:
        None.
    """
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)


def plot_raw_vol_smile(smile, spot, forward, reference_date, expiry_date, output_path):
    """
    Plot the raw market-observed implied volatility smile for one expiry.

    This is the Phase 0 sanity check on the data. Equity index options display a
    pronounced downward SKEW: out-of-the-money puts (low strikes) trade at materially
    higher implied vols than out-of-the-money calls (high strikes). The economic reason is
    that index investors are structurally long the market and buy downside puts as
    insurance, bidding up the left wing, while the right wing is damped by call
    overwriting. If this plot came out flat, or looked like unstructured noise, we would
    have a data problem and the project would stop here.

    Puts and calls are drawn in different colours so it is visible that the two OTM wings
    join smoothly at the money -- which is the evidence that the OTM construction in
    `build_otm_smile` did the right thing.

    Inputs:
        smile (DataFrame):     output of `data_loader.build_otm_smile`, with columns
                               'strike', 'option_type', 'market_iv'.
        spot (float):          SPY spot price at the quote time, dollars.
        forward (float):       implied forward price at expiry, dollars.
        reference_date (str):  quote date, "YYYY-MM-DD", used in the title.
        expiry_date (str):     option expiry, "YYYY-MM-DD", used in the title.
        output_path (str):     where to write the .png.

    Returns:
        None. Writes a file to `output_path`.
    """
    _ensure_directory(output_path)

    figure, axes = plt.subplots(figsize=(10, 6))

    puts = smile.loc[smile["option_type"] == "put"]
    calls = smile.loc[smile["option_type"] == "call"]

    axes.scatter(puts["strike"], puts["market_iv"] * 100, s=26, color="#1f77b4",
                 label=f"OTM puts ({len(puts)} strikes)", zorder=3)
    axes.scatter(calls["strike"], calls["market_iv"] * 100, s=26, color="#d62728",
                 label=f"OTM calls ({len(calls)} strikes)", zorder=3)

    # Spot and forward are the two reference levels a reader needs to interpret the
    # horizontal axis: everything left of the forward is a put quote, everything right
    # of it is a call quote.
    axes.axvline(spot, color="black", linestyle="--", linewidth=1.2,
                 label=f"Spot = ${spot:.2f}", zorder=2)
    axes.axvline(forward, color="grey", linestyle=":", linewidth=1.4,
                 label=f"Forward = ${forward:.2f}", zorder=2)

    axes.set_xlabel("Strike ($)")
    axes.set_ylabel("Market implied volatility (%)")
    axes.set_title(
        f"SPY raw implied volatility smile\n"
        f"Quote date {reference_date}, expiry {expiry_date}, out-of-the-money quotes only"
    )
    axes.legend(loc="upper right", frameon=True)
    axes.grid(alpha=0.3, zorder=1)

    figure.tight_layout()
    figure.savefig(output_path, dpi=FIGURE_DPI)
    plt.close(figure)

    print(f"  Saved figure: {output_path}")


def plot_sabr_fit(fit_smile, full_smile, calibration, forward, time_to_expiry, spot,
                  reference_date, expiry_date, output_path):
    """
    Plot the calibrated SABR curve against the observed market smile.

    The figure has two panels sharing a strike axis:

      - the top panel is the fit itself: market vols as scatter points, the SABR curve as
        a smooth line over a dense strike grid. Strikes inside the calibration range are
        drawn solid and those outside it hollow, so it is immediately visible which points
        the optimiser actually saw and how the model extrapolates beyond them.
      - the bottom panel is the residuals, fitted minus market, in vol points. Residuals
        are where a bad fit confesses: a good calibration scatters them randomly about
        zero, while a systematic arc means the model cannot reproduce the shape and the
        parameters are being distorted to compromise between the wings and the middle.

    Inputs:
        fit_smile (DataFrame):   the strikes actually calibrated to, with 'strike' and
                                 'market_iv'.
        full_smile (DataFrame):  every quoted strike, for context beyond the fit range.
        calibration (dict):      output of `sabr.calibrate_sabr`.
        forward (float):         forward price, in dollars.
        time_to_expiry (float):  T, in years.
        spot (float):            SPY spot, in dollars.
        reference_date (str):    quote date, for the title.
        expiry_date (str):       option expiry, for the title.
        output_path (str):       where to write the .png.

    Returns:
        None. Writes a file to `output_path`.
    """
    # Imported here rather than at module scope to keep the import graph acyclic: sabr.py
    # imports plots.py for its standalone block, so plots.py must not import sabr.py at
    # the top level.
    import sabr

    _ensure_directory(output_path)

    figure, (fit_axes, residual_axes) = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    # A dense grid so the fitted curve reads as smooth rather than as joined-up dots.
    # We extend it a little past the fitted strikes to show the extrapolation behaviour.
    grid_low = fit_smile["strike"].min() * 0.97
    grid_high = fit_smile["strike"].max() * 1.03
    strike_grid = np.linspace(grid_low, grid_high, 400)

    fitted_curve = sabr.sabr_vol_curve(
        forward=forward,
        strikes=strike_grid,
        time_to_expiry=time_to_expiry,
        alpha=calibration["alpha"],
        beta=calibration["beta"],
        rho=calibration["rho"],
        nu=calibration["nu"],
    )

    # ---- top panel: the fit ----------------------------------------------------------
    outside_range = full_smile.loc[~full_smile["strike"].isin(fit_smile["strike"])]
    in_view = (outside_range["strike"] >= grid_low) & (outside_range["strike"] <= grid_high)
    outside_range = outside_range.loc[in_view]

    fit_axes.scatter(fit_smile["strike"], fit_smile["market_iv"] * 100, s=28,
                     color="#1f77b4", label=f"Market IV, calibrated ({len(fit_smile)})",
                     zorder=3)

    if not outside_range.empty:
        fit_axes.scatter(outside_range["strike"], outside_range["market_iv"] * 100, s=28,
                         facecolors="none", edgecolors="#8c8c8c",
                         label="Market IV, outside fit range", zorder=3)

    fit_axes.plot(strike_grid, fitted_curve * 100, color="#d62728", linewidth=2.0,
                  label="SABR fitted curve", zorder=4)

    fit_axes.axvline(forward, color="grey", linestyle=":", linewidth=1.4,
                     label=f"Forward = ${forward:.2f}", zorder=2)
    fit_axes.axvline(spot, color="black", linestyle="--", linewidth=1.0,
                     label=f"Spot = ${spot:.2f}", zorder=2)

    parameter_text = (
        f"alpha = {calibration['alpha']:.4f}\n"
        f"beta  = {calibration['beta']:.2f} (fixed)\n"
        f"rho   = {calibration['rho']:+.4f}\n"
        f"nu    = {calibration['nu']:.4f}"
    )
    fit_axes.text(
        0.015, 0.05, parameter_text, transform=fit_axes.transAxes,
        fontsize=9, family="monospace", verticalalignment="bottom",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85, "edgecolor": "grey"},
    )

    fit_axes.set_ylabel("Implied volatility (%)")
    fit_axes.set_title(
        f"SABR calibration to the SPY volatility smile\n"
        f"Quote date {reference_date}, expiry {expiry_date}  |  "
        f"RMSE = {calibration['rmse'] * 100:.4f} vol points, "
        f"max error = {calibration['max_abs_error'] * 100:.4f}"
    )
    fit_axes.legend(loc="upper right", frameon=True, fontsize=9)
    fit_axes.grid(alpha=0.3, zorder=1)

    # ---- bottom panel: residuals -----------------------------------------------------
    residuals_in_vol_points = calibration["residuals"] * 100

    residual_axes.axhline(0, color="black", linewidth=1.0, zorder=2)
    residual_axes.scatter(fit_smile["strike"], residuals_in_vol_points, s=24,
                          color="#1f77b4", zorder=3)
    residual_axes.vlines(fit_smile["strike"], 0, residuals_in_vol_points,
                         color="#1f77b4", alpha=0.4, zorder=2)
    residual_axes.axvline(forward, color="grey", linestyle=":", linewidth=1.4, zorder=2)

    residual_axes.set_xlabel("Strike ($)")
    residual_axes.set_ylabel("Residual\n(vol points)")
    residual_axes.grid(alpha=0.3, zorder=1)

    figure.tight_layout()
    figure.savefig(output_path, dpi=FIGURE_DPI)
    plt.close(figure)

    print(f"  Saved figure: {output_path}")
