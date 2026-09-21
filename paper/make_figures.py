"""
The eight publication figures for the SR-FICC-2013-02 methodology paper.

Reads only the committed CSVs under outputs/, the two cached price histories under
data/, and (for the BAW accuracy figure) baw.py's own pricers. Writes vector PDF plus
a 300-dpi PNG preview into paper/figures/. Touches no pipeline code and no pipeline
output.

    python3 paper/make_figures.py            # all eight
    python3 paper/make_figures.py sabr_fit   # one, by name
"""
import os, sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper.paperstyle import (apply_style, tables, save, panel_tag, CASES, ROOT,
                              FIGDIR, INK, RISK, REF, TEXTWIDTH)
# sabr and baw are imported lazily, inside the two functions that need them:
# they pull in scipy, and every other figure here reads only committed CSVs.
apply_style()
CASE_KEYS = ["2023-12-29", "2026-06-01"]
D = r"\$"          # matplotlib parses a bare $ as mathtext; always escape it


# ============================================================ 1. SABR calibration
def fig_sabr_fit():
    """Calibrated SABR against the re-implied smile, both cases, on a shared K/F axis."""
    fig = plt.figure(figsize=(TEXTWIDTH, 4.3))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[2.3, 1.0], hspace=0.12, wspace=0.30)

    for j, case in enumerate(CASE_KEYS):
        read = tables(case)
        cal = read("sabr_calibration.csv").iloc[0]
        diag = read("sabr_fit_diagnostics.csv")
        col, light = CASES[case]["colour"], CASES[case]["light"]
        F = cal["forward"]

        ax = fig.add_subplot(gs[0, j])
        axr = fig.add_subplot(gs[1, j], sharex=ax)

        puts, calls = diag[diag.option_type == "put"], diag[diag.option_type == "call"]
        ax.plot(puts.strike / F, 100 * puts.reimplied_iv, "o", ms=2.6, mfc=col,
                mec="none", label="market, put wing")
        ax.plot(calls.strike / F, 100 * calls.reimplied_iv, "o", ms=3.4, mfc="none",
                mec=light, mew=0.9, label="market, call wing")

        import sabr
        grid = np.linspace(diag.strike.min(), diag.strike.max(), 400)
        curve = sabr.sabr_vol_curve(F, grid, cal["time_to_expiry_years"],
                                    cal["alpha"], cal["beta"], cal["rho"], cal["nu"])
        ax.plot(grid / F, 100 * np.asarray(curve), "-", color=INK, lw=1.1,
                label="calibrated SABR")

        for a in (ax, axr):
            a.axvline(1.0, color=REF, lw=0.7, ls=(0, (4, 2)), zorder=0)
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + 0.30 * (hi - lo))
        ax.text(1.0, ax.get_ylim()[1], r"  $K\!=\!F$", fontsize=7.5, color=REF,
                ha="left", va="top")

        ax.set_ylabel("implied volatility (%)")
        ax.tick_params(labelbottom=False)
        params = (f"{chr(945)} = {cal['alpha']:.6f}, "
                  f"{chr(961)} = {cal['rho']:.6f}, "
                  f"{chr(957)} = {cal['nu']:.6f}").replace("-", "\u2212")
        ax.set_title(f"{CASES[case]['label']}\n{params}\n"
                     f"RMSE {cal['rmse_vol_points']:.4f} vol pts, "
                     f"{int(cal['n_strikes'])} strikes", fontsize=8)
        panel_tag(ax, "AB"[j])
        ax.legend(loc="upper right", handletextpad=0.4, labelspacing=0.3)

        axr.axhline(0.0, color=REF, lw=0.7)
        axr.plot(puts.strike / F, puts.residual_vol_points, "o", ms=2.4, mfc=col, mec="none")
        axr.plot(calls.strike / F, calls.residual_vol_points, "o", ms=3.2, mfc="none",
                 mec=light, mew=0.9)
        axr.set_xlabel("moneyness $K/F$")
        axr.set_ylabel("residual\n(vol pts)")
        axr.set_ylim(-0.95, 0.95)
        axr.set_xlim(0.82, 1.18)

    save(fig, "fig_sabr_fit")


# ================================================== 2. re-implying and the crossover
def fig_reimplied():
    """The correction applied to the input vols, and the put/call crossover it fixes."""
    fig = plt.figure(figsize=(TEXTWIDTH, 2.55))
    gs = GridSpec(1, 3, figure=fig, width_ratios=[1.35, 1.0, 1.0], wspace=0.40)

    ax = fig.add_subplot(gs[0, 0])
    ax.axhline(0.0, color=REF, lw=0.7)
    for case in CASE_KEYS:
        sm = tables(case)("reimplied_smile.csv")
        F = tables(case)("reference_market_state.csv").iloc[0]["forward"]
        col = CASES[case]["colour"]
        d = 100.0 * (sm.reimplied_iv - sm.vendor_iv)
        ax.plot(sm.strike / F, d, "o", ms=2.4, mfc=col, mec="none",
                label=CASES[case]["label"])
    ax.axvline(1.0, color=REF, lw=0.7, ls=(0, (4, 2)), zorder=0)
    ax.set_xlim(0.55, 1.45)
    ax.set_ylim(-0.42, 0.42)
    ax.set_xlabel("moneyness $K/F$")
    ax.set_ylabel("re-implied $-$ input (vol pts)")
    ax.set_title("The correction applied\nto the input volatilities", fontsize=8)
    panel_tag(ax, "A", dx=-0.24)
    ax.legend(loc="lower left", handletextpad=0.4, labelspacing=0.3)

    # the crossover itself, one panel per case, zoomed on the last put / first call
    for j, case in enumerate(CASE_KEYS):
        sm = tables(case)("reimplied_smile.csv")
        F = tables(case)("reference_market_state.csv").iloc[0]["forward"]
        col, light = CASES[case]["colour"], CASES[case]["light"]
        ax = fig.add_subplot(gs[0, 1 + j])

        gaps = []
        last_put = sm[sm.option_type == "put"].strike.max()
        first_call = sm[sm.option_type == "call"].strike.min()
        lo, hi = last_put - 9.0, first_call + 9.0
        w = sm[(sm.strike >= lo) & (sm.strike <= hi)]

        for src, colour, marker, lab in (("vendor_iv", REF, "s", "input vols"),
                                         ("reimplied_iv", col, "o", "re-implied")):
            p = w[w.option_type == "put"]
            c = w[w.option_type == "call"]
            ax.plot(p.strike, 100 * p[src], marker, ms=3.0, color=colour, mfc=colour,
                    mec="none", label=lab)
            ax.plot(c.strike, 100 * c[src], marker, ms=3.6, color=colour, mfc="none",
                    mec=colour, mew=0.9)
            gap = 100 * (c[src].iloc[0] - p[src].iloc[-1])
            ax.annotate("", xy=(first_call, 100 * c[src].iloc[0]),
                        xytext=(last_put, 100 * p[src].iloc[-1]),
                        arrowprops=dict(arrowstyle="->", lw=0.7, color=colour,
                                        shrinkA=2.5, shrinkB=2.5))
            gaps.append((colour, lab, gap))

        for k, (colour, lab, gap) in enumerate(gaps):
            ax.text(0.04, 0.13 - 0.085 * k, f"{lab}: {gap:+.3f}", color=colour,
                    transform=ax.transAxes, fontsize=7.0, ha="left", va="center")

        ax.set_xlabel(f"strike ({D})")
        if j == 0:
            ax.set_ylabel("implied volatility (%)")
        ax.set_title(f"{CASES[case]['label']}\nthe put/call crossover", fontsize=8)
        panel_tag(ax, "BC"[j], dx=-0.26)
        if j == 0:
            ax.legend(loc="upper right", handletextpad=0.4, labelspacing=0.3)

    save(fig, "fig_reimplied")


# ================================================= 3. BAW accuracy and its P&L cost
def _baw_accuracy_data():
    """BAW minus a converged lattice: the boundary profile, and the P&L propagation."""
    cache = os.path.join(FIGDIR, "_cache_baw_accuracy.csv")
    cache_pnl = os.path.join(FIGDIR, "_cache_baw_pnl.csv")
    if os.path.exists(cache) and os.path.exists(cache_pnl):
        return pd.read_csv(cache), pd.read_csv(cache_pnl)
    import baw

    # (a) the BAW paper's own stress grid: K=100, r=8%, b=-4%, call, sigma=40%, T=0.25.
    #     Error is smallest at the money, peaks just below the exercise boundary S*,
    #     and is exactly zero past it.
    K, r, b, vol, T = 100.0, 0.08, -0.04, 0.40, 0.25
    star = baw.solve_critical_price(K, T, r, b, vol, "call")
    rows = []
    for S in np.linspace(85.0, 150.0, 40):
        a = baw.baw_price(S, K, T, r, b, vol, "call")
        n = baw.binomial_american_price(S, K, T, r, b, vol, "call", n_steps=8000)
        rows.append(dict(spot=S, ratio=S / star, baw=a, lattice=n, error=a - n))
    prof = pd.DataFrame(rows)

    # (b) the reference put, over the range of daily moves a 250-day SPY sample holds.
    S0, Kp, Tp, rp, bp, vp = 475.31, 475.0, 0.13424657534246576, 0.0540, 0.055926, 0.115
    base_b = baw.baw_price(S0, Kp, Tp, rp, bp, vp, "put")
    base_l = baw.binomial_american_price(S0, Kp, Tp, rp, bp, vp, "put", n_steps=8000)
    rows = []
    for shock in np.linspace(-0.03, 0.03, 25):
        S = S0 * (1.0 + shock)
        pb = baw.baw_price(S, Kp, Tp, rp, bp, vp, "put") - base_b
        pl = baw.binomial_american_price(S, Kp, Tp, rp, bp, vp, "put",
                                         n_steps=8000) - base_l
        rows.append(dict(shock=shock, pnl_baw=pb, pnl_true=pl, error=pb - pl,
                         pct=100.0 * (pb - pl) / pl if abs(pl) > 1e-9 else np.nan))
    pnl = pd.DataFrame(rows)
    pnl.attrs, prof.attrs = {}, {}
    prof["critical_price"] = star
    prof.to_csv(cache, index=False)
    pnl.to_csv(cache_pnl, index=False)
    return prof, pnl


def fig_baw_accuracy():
    """What the quadratic approximation costs, at the price level and in the P&L."""
    prof, pnl = _baw_accuracy_data()
    col = CASES["2023-12-29"]["colour"]
    fig, axes = plt.subplots(1, 2, figsize=(TEXTWIDTH, 2.5))

    ax = axes[0]
    ax.axhline(0.0, color=REF, lw=0.7)
    ax.plot(prof.ratio, prof.error, "-", color=INK, lw=1.1)
    ax.axvline(1.0, color=RISK, lw=0.8, ls=(0, (4, 2)))
    ax.text(1.0, ax.get_ylim()[1], r"  $S=S^{*}$", fontsize=7.5, color=RISK,
            ha="left", va="top")
    worst = prof.loc[prof.error.abs().idxmax()]
    ax.plot([worst.ratio], [worst.error], "o", ms=4.0, mfc=RISK, mec="none")
    lo_c, hi_c = ax.get_ylim()
    ax.set_ylim(lo_c - 0.34 * (hi_c - lo_c), hi_c)   # headroom below the curve
    ax.annotate("worst {}{:.4f} at $S/S^{{*}}$ = {:.3f}".format(
                    D, worst.error, worst.ratio).replace("-", "\u2212"),
                xy=(worst.ratio, worst.error), xytext=(6, -26),
                textcoords="offset points", ha="right", va="top",
                fontsize=7.5, color=INK,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK, shrinkA=2, shrinkB=3))
    ax.set_xlabel("$S/S^{*}$, distance to the exercise boundary")
    ax.set_ylabel(f"BAW $-$ lattice ({D})")
    ax.set_title("Price level: American call, $K$=100, $r$=8%,\n"
                 "$b$=$-$4%, $\\sigma$=40%, $T$=0.25 (BAW's own grid)", fontsize=8)
    panel_tag(ax, "A")

    ax = axes[1]
    ax.axhline(0.0, color=REF, lw=0.7)
    ok = pnl.pnl_true.abs() > 0.05
    ax.plot(100 * pnl.shock[ok], pnl.pct[ok], "-", color=col, lw=1.2)
    ax.plot(100 * pnl.shock[ok], pnl.pct[ok], "o", ms=2.6, mfc=col, mec="none")
    ax.set_xlabel("scenario move in SPY (%)")
    ax.set_ylabel("P&L error (% of true P&L)")
    ax.set_title("P&L: the reference put, BAW P&L against\n"
                 "a converged lattice. The error does not cancel", fontsize=8)
    ax.text(0.04, 0.94, "price-level error {}0.0155\nworst P&L error {}0.0852 "
            "($5.5\\times$ larger)".format(D, D), transform=ax.transAxes,
            fontsize=7.5, color=INK, va="top")
    panel_tag(ax, "B")

    save(fig, "fig_baw_accuracy")


# ============================================== 4. the SABR -> BAW junction, attributed
def fig_price_attribution():
    """Theoretical prices against the market, and where the residual error comes from."""
    fig, axes = plt.subplots(1, 2, figsize=(TEXTWIDTH, 2.5))

    ax = axes[0]
    ax.axhline(0.0, color=REF, lw=0.7)
    for case in CASE_KEYS:
        bm = tables(case)("baw_vs_market_prices.csv")
        F = tables(case)("reference_market_state.csv").iloc[0]["forward"]
        col = CASES[case]["colour"]
        half = 0.5 * (bm.market_ask - bm.market_bid)
        order = np.argsort((bm.strike / F).values)
        x = (bm.strike / F).values[order]
        ax.fill_between(x, -half.values[order], half.values[order], color=col,
                        alpha=0.18, lw=0)
        ax.plot(bm.strike / F, bm.baw_price - bm.market_mid, "o", ms=2.4, mfc=col,
                mec="none", label=CASES[case]["label"])
    ax.set_xlabel("moneyness $K/F$")
    ax.set_ylabel(f"theoretical $-$ market mid ({D})")
    ax.set_title("Pricing error against the market,\n"
                 "shaded band = the quoted half-spread", fontsize=8)
    panel_tag(ax, "A")
    ax.legend(loc="lower left", handletextpad=0.4, labelspacing=0.3,
              bbox_to_anchor=(0.0, 0.0))

    ax = axes[1]
    lims = []
    for case in CASE_KEYS:
        bm = tables(case)("baw_vs_market_prices.csv")
        col = CASES[case]["colour"]
        obs, pred = bm.absolute_diff.values, bm.predicted_price_error.values
        rho = np.corrcoef(obs, pred)[0, 1]
        share = 1.0 - np.abs(obs - pred).mean() / np.abs(obs).mean()
        ax.plot(pred, obs, "o", ms=2.6, mfc=col, mec="none",
                label=f"{case}: $r$ = {rho:.6f}, {100 * share:.2f}% explained")
        lims += [obs.min(), obs.max(), pred.min(), pred.max()]
    lo, hi = min(lims) * 1.08, max(lims) * 1.08
    ax.plot([lo, hi], [lo, hi], "-", color=INK, lw=0.8, zorder=0)
    ax.text(hi, hi, "  $45^{\\circ}$", fontsize=7.5, color=INK, ha="left", va="center")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel(f"predicted: vol error $\\times$ vega ({D})")
    ax.set_ylabel(f"observed price error ({D})")
    ax.set_title("Attribution: the residual is the smile fit\n"
                 "seen through vega, not a wiring error", fontsize=8)
    panel_tag(ax, "B")
    ax.legend(loc="upper left", handletextpad=0.4, labelspacing=0.3)

    save(fig, "fig_price_attribution")


# ================================================ 5. full revaluation vs the shortcut
def fig_full_revaluation():
    """The filing's prohibition, priced: full revaluation against delta and delta+gamma."""
    fig = plt.figure(figsize=(TEXTWIDTH, 4.1))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[2.1, 1.0], hspace=0.14, wspace=0.28)

    for j, case in enumerate(CASE_KEYS):
        read = tables(case)
        rev = read("scenario_revaluation.csv").sort_values("historical_return")
        vs = read("var_summary.csv").iloc[0]
        col = CASES[case]["colour"]
        x = 100 * rev.historical_return

        ax = fig.add_subplot(gs[0, j])
        axr = fig.add_subplot(gs[1, j], sharex=ax)

        breach = rev.scenario_pnl <= vs["quantile_pnl"]
        ax.plot(x[~breach], rev.scenario_pnl[~breach], "o", ms=2.8, mfc=col, mec="none",
                label="full revaluation")
        ax.plot(x[breach], rev.scenario_pnl[breach], "o", ms=4.0, mfc=RISK, mec="none",
                label="99% VaR breaches")
        ax.plot(x, rev.delta_only_pnl, "-", color=INK, lw=1.0, ls=(0, (5, 2)),
                label="delta only")
        ax.plot(x, rev.delta_gamma_pnl, "-", color=INK, lw=1.0, label="delta + gamma")
        ax.axhline(0.0, color=REF, lw=0.7, zorder=0)
        ax.axvline(0.0, color=REF, lw=0.7, zorder=0)
        ax.set_ylabel(f"scenario P&L ({D})")
        ax.tick_params(labelbottom=False)

        d_only = (rev.delta_only_pnl - rev.scenario_pnl).abs()
        d_gam = (rev.delta_gamma_pnl - rev.scenario_pnl).abs()
        ax.set_title(f"{CASES[case]['label']}\n"
                     f"max error: delta {D}{d_only.max():.4f}, "
                     f"delta+gamma {D}{d_gam.max():.4f}", fontsize=8)
        panel_tag(ax, "AB"[j])
        if j == 0:
            ax.legend(loc="upper right", handletextpad=0.4, labelspacing=0.3)

        axr.axhline(0.0, color=REF, lw=0.7)
        axr.plot(x, rev.delta_only_pnl - rev.scenario_pnl, "-", color=INK, lw=1.0,
                 ls=(0, (5, 2)))
        axr.plot(x, rev.delta_gamma_pnl - rev.scenario_pnl, "-", color=INK, lw=1.0)
        axr.set_xlabel("historical daily move applied to spot (%)")
        axr.set_ylabel(f"approximation\nerror ({D})")

    save(fig, "fig_full_revaluation")


# ======================================================= 6. the scenario P&L and VaR
def fig_pnl_distribution():
    """Full-revaluation scenario P&L, with the interpolated 99% threshold and ES."""
    fig, axes = plt.subplots(1, 2, figsize=(TEXTWIDTH, 2.55))

    for j, case in enumerate(CASE_KEYS):
        read = tables(case)
        rev, vs = read("scenario_revaluation.csv"), read("var_summary.csv").iloc[0]
        col, ax = CASES[case]["colour"], axes[j]

        pnl = rev.scenario_pnl.values
        thr, es = vs["quantile_pnl"], -vs["expected_shortfall"]
        breach = pnl <= thr
        bins = np.linspace(pnl.min(), pnl.max(), 34)
        ax.hist(pnl[~breach], bins=bins, color=col, alpha=0.80, lw=0)
        ax.hist(pnl[breach], bins=bins, color=RISK, lw=0,
                label=f"{int(breach.sum())} breaching scenarios")
        ax.axvline(thr, color=RISK, lw=1.0, ymax=0.60)
        ax.axvline(es, color=RISK, lw=1.0, ls=(0, (3, 2)), ymax=0.60)

        ax.set_ylim(0, ax.get_ylim()[1] * 1.30)
        for k, (lab, val, style) in enumerate(
                [("99% VaR", vs["var"], "solid"),
                 ("ES", vs["expected_shortfall"], "dashed")]):
            y = 0.90 - 0.085 * k
            ax.text(0.04, y, lab, transform=ax.transAxes, ha="left", va="top",
                    fontsize=7.5, color=RISK)
            ax.text(0.30, y, f"{D}{val:.4f}  ({style})", transform=ax.transAxes,
                    ha="left", va="top", fontsize=7.5, color=RISK)

        ax.set_xlabel(f"scenario P&L ({D})")
        if j == 0:
            ax.set_ylabel("scenarios")
        ax.set_title(f"{CASES[case]['label']}\n"
                     f"VaR = {100 * vs['var'] / vs['position_value']:.2f}% of the "
                     f"{D}{vs['position_value']:.4f} position", fontsize=8, pad=24)
        panel_tag(ax, "AB"[j], dy=1.16)
        ax.legend(loc="upper right", handletextpad=0.4)

        pv = vs["position_value"]
        sec = ax.secondary_xaxis("top", functions=(lambda x, p=pv: 100.0 * x / p,
                                                   lambda x, p=pv: p * x / 100.0))
        sec.set_xlabel("P&L as % of position value", fontsize=8, labelpad=3)
        sec.tick_params(labelsize=7.5)
        sec.spines["top"].set_linewidth(0.6)

    save(fig, "fig_pnl_distribution")


# ========================================== 7. the lookback window drives the answer
def fig_lookback_window():
    """What the 250-day window includes, and what including it is worth."""
    paths = {
        "2023-12-29": os.path.join(ROOT, "data", "spy_price_history_full.csv"),
        "2026-06-01": os.path.join(ROOT, "data", "databento_2025_2026",
                                   "spy_price_history_full.csv"),
    }
    fig = plt.figure(figsize=(TEXTWIDTH, 4.2))
    gs = GridSpec(2, 2, figure=fig, hspace=0.52, wspace=0.26)

    for j, case in enumerate(CASE_KEYS):
        px = pd.read_csv(paths[case], parse_dates=["date"])
        sc = tables(case)("scenarios.csv", )
        sc["historical_date"] = pd.to_datetime(sc.historical_date)
        col = CASES[case]["colour"]
        start, end = sc.historical_date.min(), sc.historical_date.max()

        ax = fig.add_subplot(gs[0, j])
        ax.plot(px.date, px.spy_close, "-", color=REF, lw=0.8)
        inside = (px.date >= start) & (px.date <= end)
        ax.plot(px.date[inside], px.spy_close[inside], "-", color=col, lw=1.0)
        ax.axvspan(start, end, color=col, alpha=0.10, lw=0)
        ax.set_ylabel(f"SPY close ({D})")
        ax.set_title(f"{CASES[case]['label']}\n"
                     f"250-day window: {start:%Y-%m-%d} to {end:%Y-%m-%d}", fontsize=8)
        panel_tag(ax, "AB"[j])

        if case == "2023-12-29":
            ax.xaxis.set_major_locator(mdates.YearLocator(2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.annotate("the entire 2022 bear market\nsits outside the window",
                        xy=(pd.Timestamp("2022-06-16"), 366.0), xytext=(0.03, 0.93),
                        textcoords="axes fraction", va="top", fontsize=7.2, color=INK,
                        arrowprops=dict(arrowstyle="-", lw=0.5, color=INK,
                                        shrinkA=2, shrinkB=3))
        else:
            ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax.annotate("April 2025: $-$5.85%, then +10.50%.\n"
                        "Aged out of this window by 2026-06",
                        xy=(pd.Timestamp("2025-04-08"), 496.0), xytext=(0.30, 0.30),
                        textcoords="axes fraction", va="top", fontsize=7.2, color=INK,
                        arrowprops=dict(arrowstyle="-", lw=0.5, color=INK,
                                        shrinkA=2, shrinkB=3))

    for j, case in enumerate(CASE_KEYS):
        lw = tables(case)("lookback_window_comparison.csv")
        lw["as_of"] = pd.to_datetime(lw.as_of)
        col = CASES[case]["colour"]
        ax = fig.add_subplot(gs[1, j])
        pos = np.arange(len(lw))
        vals = (100 * lw.first_percentile).values
        ours = (lw.as_of.dt.strftime("%Y-%m-%d") == case).values
        ax.bar(pos, vals, color=col, alpha=0.85, width=0.62, lw=0)
        ax.bar(pos[ours], vals[ours], color=RISK, width=0.62, lw=0)
        for q, v in zip(pos, vals):
            ax.annotate(f"{v:.2f}", xy=(q, v), xytext=(0, -3),
                        textcoords="offset points", ha="center", va="top",
                        fontsize=6.8, color=INK)
        ax.set_ylim(vals.min() * 1.34, 0.0)
        ax.set_xticks(pos)
        ax.set_xticklabels(lw.as_of.dt.strftime("%Y-%m-%d"), rotation=45,
                           ha="right", fontsize=7.0)
        ax.set_ylabel("1st percentile\nreturn (%)")
        ax.set_title(f"{CASES[case]['label']}: the window's 1st\n"
                     "percentile, by reference date "
                     "(wine = ours)", fontsize=8)
        panel_tag(ax, "CD"[j])
        if case == "2026-06-01":
            ax.annotate("April 2025 leaves\nthe window here",
                        xy=(2.5, vals.min() * 0.62), xytext=(0.58, 0.40),
                        textcoords="axes fraction", ha="left", va="top",
                        fontsize=7.2, color=INK,
                        arrowprops=dict(arrowstyle="->", lw=0.6, color=INK,
                                        shrinkA=2, shrinkB=3))

    save(fig, "fig_lookback_window")


# ================================================== 8. the cross-vendor validation
def fig_crossvendor():
    """Phase 9: the same reference date priced from a second, independent vendor."""
    read = tables("2023-12-29")
    cv = read("databento_iv_comparison.csv")
    col, light = CASES["2023-12-29"]["colour"], CASES["2023-12-29"]["light"]
    put, call = cv.option_type == "put", cv.option_type == "call"

    fig, axes = plt.subplots(1, 2, figsize=(TEXTWIDTH, 2.45))

    ax = axes[0]
    dm = cv.mid_db - cv.mid_proj
    ax.axhline(0.0, color=REF, lw=0.7)
    ax.plot(cv.strike[put], dm[put], "o", ms=2.6, mfc=col, mec="none", label="put wing")
    ax.plot(cv.strike[call], dm[call], "o", ms=3.4, mfc="none", mec=light, mew=0.9,
            label="call wing")
    lo_a, hi_a = ax.get_ylim()
    ax.set_ylim(lo_a, hi_a + 0.34 * (hi_a - lo_a))   # headroom for the label
    ax.annotate(f"{D}475 put: +{D}0.0700,\nlargest positive in sample",
                xy=(475.0, 0.070), xytext=(0.04, 0.86), textcoords="axes fraction",
                ha="left", va="top", fontsize=7.5, color=INK,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK,
                                shrinkA=2, shrinkB=3))
    ax.set_xlabel(f"strike ({D})")
    ax.set_ylabel(f"mid price difference ({D})")
    ax.set_title("Mid price, Databento $-$ OptionsDX\n"
                 f"median {D}0.0000 over 151 common strikes", fontsize=8)
    panel_tag(ax, "A")
    ax.legend(loc="lower left", handletextpad=0.4)

    ax = axes[1]
    dv = 100.0 * (cv.reimplied_iv_db - cv.reimplied_iv_proj)
    ax.axhline(0.0, color=REF, lw=0.7)
    ax.plot(cv.strike[put], dv[put], "o", ms=2.6, mfc=col, mec="none")
    ax.plot(cv.strike[call], dv[call], "o", ms=3.4, mfc="none", mec=light, mew=0.9)
    lo, hi = -0.48, 0.34
    ax.set_ylim(lo, hi)
    off = dv < lo
    ax.plot(cv.strike[off], np.full(off.sum(), lo + 0.012), marker="v", ls="none",
            ms=4.0, color=RISK, clip_on=False)
    ax.annotate(f"$K=260$, off scale at ${dv.min():.2f}$ vol pts",
                xy=(float(cv.strike[off].iloc[0]), lo + 0.012), xytext=(9, 1),
                textcoords="offset points", ha="left", va="center",
                fontsize=7.5, color=INK)
    ax.set_xlabel(f"strike ({D})")
    ax.set_ylabel("IV difference (vol pts)")
    ax.set_title("Re-implied volatility, Databento $-$ OptionsDX\n"
                 "median 0.0000, 95th pctile $|$diff$|$ 0.2286", fontsize=8)
    panel_tag(ax, "B")

    save(fig, "fig_crossvendor")


FIGURES = [fig_sabr_fit, fig_reimplied, fig_baw_accuracy, fig_price_attribution,
           fig_full_revaluation, fig_pnl_distribution, fig_lookback_window,
           fig_crossvendor]

if __name__ == "__main__":
    wanted = sys.argv[1:]
    for f in FIGURES:
        if not wanted or any(w in f.__name__ for w in wanted):
            print(f.__name__)
            f()
