"""
Shared publication figure style for the SR-FICC-2013-02 methodology paper.

Not part of the pipeline. Reads only the committed CSVs under outputs/ and
writes vector PDFs (plus 300-dpi PNG previews) into paper/figures/.

Typeface is STIX Two Text so the figures match a LaTeX document set in
stix2 / Times. Colour discipline, used identically in every figure:

    CASE1  deep blue   the 2023-12-29 / OptionsDX case
    CASE2  rust        the 2026-06-01 / Databento case
    INK    near-black  fitted model curves
    RISK   wine        VaR / ES thresholds and breaching scenarios
    REF    grey        comparison baselines (normal density, parity lines)

Within one case the put wing carries the case colour solid and the call wing
carries a lighter tint with open markers, so case identity always dominates.
"""

import os
import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIGDIR = os.path.join(ROOT, "paper", "figures")

CASE1 = "#1F4E79"   # 2023-12-29, OptionsDX
CASE2 = "#C0561F"   # 2026-06-01, Databento
CASE1_L = "#7BA7CC"
CASE2_L = "#E3A379"
INK = "#1B1B1B"
RISK = "#8C1D40"
REF = "#9A9A9A"
GRID = "#DCDCDC"

CASES = {
    "2023-12-29": dict(colour=CASE1, light=CASE1_L, label="2023-12-29 (OptionsDX)"),
    "2026-06-01": dict(colour=CASE2, light=CASE2_L, label="2026-06-01 (Databento)"),
}

TEXTWIDTH = 6.20  # inches, matches the LaTeX \textwidth set in the preamble


def apply_style():
    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIX Two Text", "STIXGeneral", "Times New Roman", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 9,
        "axes.titlesize": 9,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "axes.edgecolor": INK,
        "axes.linewidth": 0.6,
        "axes.labelcolor": INK,
        "axes.titlelocation": "left",
        "axes.titlepad": 5.0,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.5,
        "grid.alpha": 1.0,
        "axes.axisbelow": True,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 3.0,
        "ytick.major.size": 3.0,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "text.color": INK,
        "legend.frameon": False,
        "legend.handlelength": 1.6,
        "legend.columnspacing": 1.2,
        "legend.borderpad": 0.2,
        "lines.linewidth": 1.2,
        "lines.markersize": 3.0,
        "figure.dpi": 110,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def tables(case):
    """Return a loader for one case's committed output tables."""
    base = os.path.join(ROOT, "outputs", case, "tables")

    def read(name):
        return pd.read_csv(os.path.join(base, name))

    return read


def panel_tag(ax, letter, dx=-0.085, dy=1.045):
    """Bold panel letter in the top-left corner, outside the axes box."""
    ax.text(dx, dy, letter, transform=ax.transAxes, fontsize=9.5,
            fontweight="bold", va="bottom", ha="left", color=INK)


def save(fig, stem):
    os.makedirs(FIGDIR, exist_ok=True)
    pdf = os.path.join(FIGDIR, stem + ".pdf")
    png = os.path.join(FIGDIR, stem + ".png")
    fig.savefig(pdf)
    fig.savefig(png)
    plt.close(fig)
    print(f"  wrote {os.path.relpath(pdf, ROOT)} and .png")
    return pdf
