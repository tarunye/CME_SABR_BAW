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
