"""
Value-at-Risk from a set of scenario P&Ls, computed the way the filing specifies.

WHAT VaR IS, AND WHAT IT REFUSES TO TELL YOU
--------------------------------------------
The 99% one-day VaR is a QUANTILE of the P&L distribution: the loss level that only 1% of
scenarios are worse than. It answers "how bad is a bad day?" for a particular definition
of bad.

It does not answer "how bad can it get?". A quantile is a threshold, and by construction it
says nothing whatsoever about the shape of the distribution beyond it. Two positions with
identical 99% VaR can have completely different worst cases -- one losing slightly more
than the VaR in its tail, the other losing ten times that. This is the standard criticism
of VaR as a risk measure, and it is why `compute_var` also returns the EXPECTED SHORTFALL:
the average loss across the scenarios that did breach the threshold. VaR is where the tail
begins; expected shortfall is how deep it goes.

SIGN CONVENTION -- STATED ONCE, USED EVERYWHERE
-----------------------------------------------
**VaR and expected shortfall are reported as POSITIVE LOSS MAGNITUDES.**

A 99% VaR of 2.45 means "a loss of $2.45". It does not mean a gain, and it is not a
negative number. The underlying scenario P&Ls keep their natural signs -- a loss is a
negative P&L -- and the quantile taken from them is therefore negative; we negate it once,
at the point of reporting, and every VaR figure in this project is positive thereafter.

The one edge case worth naming: if a position were so favourably skewed that even its 1st
percentile scenario made money, the quantile would be positive and the reported VaR would
come out NEGATIVE. That is not a bug, it is the honest statement that the position did not
lose money in 99% of the sampled scenarios. `compute_var` flags it rather than clipping it
to zero.

Run this file directly to compute the VaR from the Phase 5 revaluation table:

    python3 var.py
"""

import numpy as np
import pandas as pd


def compute_var(scenario_pnls, confidence_level=0.99):
    """
    Compute Value-at-Risk by explicit linear interpolation between order statistics.

    THE INTERPOLATION, AND WHY THE FILING BOTHERS TO SPECIFY IT
    -----------------------------------------------------------
    With 250 scenarios, the 99% point does not land on an observation. One percent of 250
    is 2.5, so the threshold falls BETWEEN the 2nd and 3rd worst scenarios, and something
    has to decide what value to report. The crude answers are to round down (take the 3rd
    worst) or round up (take the 2nd worst); either throws away information and makes the
    VaR jump discontinuously as scenarios shuffle. The filing instead specifies linear
    interpolation between the two adjacent order statistics, and that is what this
    implements, arithmetically and in the open.

    The index arithmetic, spelled out for N scenarios at confidence level c:

        alpha         = 1 - c                    the tail probability, 0.01
        fractional    = (N - 1) * alpha          where the threshold sits, in index space
        lower_index   = floor(fractional)        the order statistic just below it
        upper_index   = lower_index + 1          the one just above
        weight        = fractional - lower_index how far between them, in [0, 1)
        quantile      = sorted[lower] + weight * (sorted[upper] - sorted[lower])

    For N = 250 and c = 0.99: fractional = 249 * 0.01 = 2.49, so we blend the 3rd and 4th
    worst scenarios (indices 2 and 3, zero-based) with 49% weight on the gap between them.

    # NOTE (approximation): the `(N - 1) * alpha` rank definition is one of several in
    # common use -- statisticians count at least nine -- and the filing says "linear
    # interpolation to the 99% threshold" without pinning down which. We use this one
    # because it is the most widely implemented convention (it is R's type 7 and NumPy's
    # default), which makes our result directly checkable against a standard library. The
    # alternatives shift the answer by a few cents at this sample size;
    # `compare_rank_conventions` quantifies exactly how much, so the choice is visible
    # rather than buried.

    Inputs:
        scenario_pnls (array-like): profit and loss per scenario, in dollars, with their
            natural signs -- losses negative. Order does not matter; this function sorts.
        confidence_level (float):   e.g. 0.99 for a 99% VaR. Must be in (0, 1).

    Returns:
        dict with keys:
            'var'                (float) VaR as a POSITIVE loss magnitude, in dollars
            'quantile_pnl'       (float) the underlying P&L quantile, natural sign
            'expected_shortfall' (float) mean loss across breaching scenarios, POSITIVE
            'n_scenarios'        (int)
            'fractional_rank'    (float) the 2.49 above
            'lower_index'        (int)   zero-based index of the order statistic below
            'upper_index'        (int)   zero-based index of the one above
            'weight'             (float) the interpolation weight, in [0, 1)
            'lower_pnl'          (float) the bracketing P&L below, natural sign
            'upper_pnl'          (float) the bracketing P&L above, natural sign
            'n_breaching'        (int)   scenarios at or beyond the threshold
            'sort_order'         (ndarray) indices that sort the input ascending, so the
                                 caller can map order statistics back to their dates
    """
    pnls = np.asarray(scenario_pnls, dtype=float)

    if not 0.0 < confidence_level < 1.0:
        raise ValueError(
            f"confidence_level must be strictly between 0 and 1, got {confidence_level}"
        )

    if len(pnls) < 2:
        raise ValueError(
            f"Need at least 2 scenarios to interpolate between order statistics, "
            f"got {len(pnls)}."
        )

    if np.isnan(pnls).any():
        raise ValueError(
            f"{int(np.isnan(pnls).sum())} scenario P&Ls are NaN. A silently dropped "
            f"scenario would shift every order statistic and therefore the VaR."
        )

    n_scenarios = len(pnls)

    # Sort ascending, so index 0 is the WORST loss and the last index the best gain. We
    # keep the sort order as well as the sorted values, because the caller needs to say
    # WHICH historical dates produced the two scenarios bracketing the threshold.
    sort_order = np.argsort(pnls, kind="stable")
    sorted_pnls = pnls[sort_order]

    # ---- The index arithmetic, done explicitly ----------------------------------------
    tail_probability = 1.0 - confidence_level

    # Where the threshold sits in index space. With 250 scenarios the valid indices run
    # 0 to 249, so we scale by (N - 1) rather than N: the 0th percentile must land on
    # index 0 and the 100th on index 249.
    fractional_rank = (n_scenarios - 1) * tail_probability

    lower_index = int(np.floor(fractional_rank))
    upper_index = min(lower_index + 1, n_scenarios - 1)

    # How far between the two order statistics the threshold falls. Zero means it lands
    # exactly on `lower_index`; 0.49 means 49% of the way toward the next one up.
    weight = fractional_rank - lower_index

    lower_pnl = sorted_pnls[lower_index]
    upper_pnl = sorted_pnls[upper_index]

    # The blend itself. Written as lower + weight * gap rather than as a weighted average
    # of the two, because that form makes it obvious the result lies between them.
    quantile_pnl = lower_pnl + weight * (upper_pnl - lower_pnl)

    # ---- Expected shortfall -----------------------------------------------------------
    # The average P&L across scenarios that actually breached the threshold. This is the
    # question VaR cannot answer: given that we are having a bad day, how bad?
    #
    # # NOTE (approximation): with 250 scenarios and a 99% level only about 2.5 scenarios
    # # sit beyond the threshold, so this average is taken over a handful of observations
    # # and is correspondingly noisy -- one scenario moving in or out changes it visibly.
    # # That is a limitation of the sample size, not of the estimator. We take the simple
    # # mean of the breaching scenarios rather than an integrated tail expectation,
    # # because it is what the reader can verify by eye against the printed worst cases.
    breaching = sorted_pnls[sorted_pnls <= quantile_pnl]
    expected_shortfall_pnl = breaching.mean() if len(breaching) > 0 else quantile_pnl

    return {
        "var": -quantile_pnl,
        "quantile_pnl": float(quantile_pnl),
        "expected_shortfall": -float(expected_shortfall_pnl),
        "n_scenarios": n_scenarios,
        "fractional_rank": float(fractional_rank),
        "lower_index": lower_index,
        "upper_index": upper_index,
        "weight": float(weight),
        "lower_pnl": float(lower_pnl),
        "upper_pnl": float(upper_pnl),
        "n_breaching": int(len(breaching)),
        "sort_order": sort_order,
    }


def cross_check_against_numpy(scenario_pnls, confidence_level=0.99):
    """
    Recompute the same quantile with `numpy.percentile`, as an independent check.

    The brief is explicit that NumPy must not produce the answer -- the point of the
    exercise is to implement the filing's stated methodology rather than to call a library
    and hope its convention matches. But NumPy is an excellent CHECK: its default
    interpolation mode is `linear`, which uses exactly the `(N - 1) * alpha` rank
    definition implemented in `compute_var`, so the two should agree to floating point
    precision. A disagreement would mean an error in our index arithmetic.

    Inputs:
        scenario_pnls (array-like): P&L per scenario, natural signs.
        confidence_level (float):   e.g. 0.99.

    Returns:
        float: the P&L quantile according to NumPy, natural sign (negative for a loss).
    """
    pnls = np.asarray(scenario_pnls, dtype=float)

    # percentile takes a percentage, not a probability, hence the factor of 100.
    return float(np.percentile(pnls, (1.0 - confidence_level) * 100.0, method="linear"))


def compare_rank_conventions(scenario_pnls, confidence_level=0.99):
    """
    Show how much the answer depends on the choice of rank definition.

    "Linear interpolation to the 99% threshold" does not fully specify a calculation: it
    still leaves open where in index space the 99% point is deemed to sit. Several
    conventions are in common use and they disagree, by an amount that grows as the sample
    shrinks. With 250 scenarios and a 1% tail the disagreement is only a few cents, but it
    is worth showing rather than asserting, because it bounds how precisely the filing's
    wording can be reproduced at all.

    The conventions compared, all interpolating linearly between neighbours:

        (N-1)*alpha    R type 7, NumPy's default. What `compute_var` uses.
        N*alpha        treats the sample as covering (0, 1] in N equal steps.
        (N+1)*alpha-1  R type 6 (Weibull), which targets the population quantile of a
                       distribution the sample is drawn from rather than of the sample.

    Inputs:
        scenario_pnls (array-like): P&L per scenario, natural signs.
        confidence_level (float):   e.g. 0.99.

    Returns:
        pandas.DataFrame with one row per convention: 'convention', 'fractional_rank',
        'var' (positive loss magnitude), and 'difference_vs_ours' in dollars.
    """
    pnls = np.sort(np.asarray(scenario_pnls, dtype=float))
    n_scenarios = len(pnls)
    tail_probability = 1.0 - confidence_level

    def interpolate(fractional_rank):
        """Blend the two order statistics straddling a fractional rank."""
        clamped = min(max(fractional_rank, 0.0), n_scenarios - 1.0)

        lower_index = int(np.floor(clamped))
        upper_index = min(lower_index + 1, n_scenarios - 1)
        weight = clamped - lower_index

        return pnls[lower_index] + weight * (pnls[upper_index] - pnls[lower_index])

    conventions = [
        ("(N-1)*alpha  [R type 7, NumPy default, OURS]",
         (n_scenarios - 1) * tail_probability),
        ("N*alpha", n_scenarios * tail_probability),
        ("(N+1)*alpha - 1  [R type 6, Weibull]",
         (n_scenarios + 1) * tail_probability - 1.0),
    ]

    ours = -interpolate((n_scenarios - 1) * tail_probability)

    rows = []
    for name, fractional_rank in conventions:
        value = -interpolate(fractional_rank)
        rows.append({
            "convention": name,
            "fractional_rank": fractional_rank,
            "var": value,
            "difference_vs_ours": value - ours,
        })

    return pd.DataFrame(rows)


def describe_var(result, scenarios, confidence_level=0.99, label="VaR"):
    """
    Print a VaR result in full, including the order statistics it was built from.

    A VaR figure on its own is unauditable. Everything needed to reconstruct it by hand is
    printed here: the two bracketing scenarios with the historical dates that produced
    them, the interpolation weight, and the arithmetic that combines them.

    Inputs:
        result (dict):            output of `compute_var`.
        scenarios (DataFrame):    the per-scenario table, carrying at least
            'historical_date', 'historical_return' and 'scenario_pnl', in the SAME row
            order as the P&L array passed to `compute_var`.
        confidence_level (float): used only for labelling.
        label (str):              a name for this run, e.g. "2023 baseline".

    Returns:
        None. Prints to stdout.
    """
    ordered = scenarios.iloc[result["sort_order"]].reset_index(drop=True)

    lower_row = ordered.iloc[result["lower_index"]]
    upper_row = ordered.iloc[result["upper_index"]]
    worst_row = ordered.iloc[0]

    def ordinal(number):
        """Render 1, 2, 3, 4 as '1st', '2nd', '3rd', '4th'."""
        # 11th, 12th and 13th are the exceptions that break the last-digit rule.
        if 10 <= number % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")

        return f"{number}{suffix}"

    print(f"  {label.upper()}")
    print(f"    Scenarios                 : {result['n_scenarios']}")
    print(f"    Confidence level          : {confidence_level:.1%}")
    print()
    print(f"    THE INTERPOLATION, STEP BY STEP")
    print(f"      tail probability alpha  : {1 - confidence_level:.4f}")
    print(f"      fractional rank         : ({result['n_scenarios']} - 1) x "
          f"{1 - confidence_level:.4f} = {result['fractional_rank']:.4f}")
    print(f"      lower order statistic   : index {result['lower_index']} "
          f"(the {ordinal(result['lower_index'] + 1)} worst) = "
          f"{result['lower_pnl']:+.6f}")
    print(f"                                from {lower_row['historical_date']:%Y-%m-%d}, "
          f"a {lower_row['historical_return']:+.3%} move")
    print(f"      upper order statistic   : index {result['upper_index']} "
          f"(the {ordinal(result['upper_index'] + 1)} worst) = "
          f"{result['upper_pnl']:+.6f}")
    print(f"                                from {upper_row['historical_date']:%Y-%m-%d}, "
          f"a {upper_row['historical_return']:+.3%} move")
    print(f"      interpolation weight    : {result['fractional_rank']:.4f} - "
          f"{result['lower_index']} = {result['weight']:.4f}")
    print(f"      blended quantile        : {result['lower_pnl']:+.6f} + "
          f"{result['weight']:.4f} x ({result['upper_pnl']:+.6f} - "
          f"{result['lower_pnl']:+.6f})")
    print(f"                              = {result['quantile_pnl']:+.6f}")
    print()
    print(f"    99% VALUE-AT-RISK         : ${result['var']:.4f}   "
          f"(reported as a positive loss)")
    print(f"    Expected shortfall        : ${result['expected_shortfall']:.4f}   "
          f"(mean of the {result['n_breaching']} breaching scenarios)")
    print(f"    Worst single scenario     : ${-worst_row['scenario_pnl']:.4f} loss on "
          f"{worst_row['historical_date']:%Y-%m-%d} "
          f"({worst_row['historical_return']:+.3%})")

    if result["var"] < 0:
        print()
        print(f"    NOTE: the VaR is NEGATIVE, meaning the position made money even in "
              f"its 1st percentile scenario.")


def _run_standalone():
    """
    Compute the VaR from the Phase 5 revaluation table. Entry point for `python3 var.py`.

    Returns:
        None. Prints the result.
    """
    import os

    path = os.path.join("outputs", "tables", "scenario_revaluation.csv")

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing pipeline output. Run `python3 main.py` first to produce\n  {path}"
        )

    revaluation = pd.read_csv(path, parse_dates=["historical_date"])

    result = compute_var(revaluation["scenario_pnl"], confidence_level=0.99)

    describe_var(result, revaluation, confidence_level=0.99, label="2023 baseline window")

    print()
    numpy_quantile = cross_check_against_numpy(revaluation["scenario_pnl"], 0.99)
    print(f"  Cross-check against numpy.percentile: {numpy_quantile:+.10f}")
    print(f"  Our own arithmetic                  : {result['quantile_pnl']:+.10f}")
    print(f"  Difference                          : "
          f"{abs(numpy_quantile - result['quantile_pnl']):.3e}")


if __name__ == "__main__":
    _run_standalone()
