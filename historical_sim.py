"""
Historical simulation: turning last year's market moves into tomorrow's possible states.

WHAT HISTORICAL SIMULATION ASSERTS, AND WHAT IT REFUSES TO ASSERT
-----------------------------------------------------------------
There are broadly three ways to answer "what might the market do tomorrow?".

  - PARAMETRIC: assume returns follow some distribution -- usually a normal -- estimate its
    parameters, and read the tail off the fitted curve. Clean, but it imposes a shape the
    data may not have, and the normal distribution in particular is badly wrong about how
    often large moves happen.
  - MONTE CARLO: assume a stochastic process, simulate many paths from it. Flexible, but
    you are still asserting a model.
  - HISTORICAL SIMULATION: take the moves that actually happened over some window and
    replay each one from today's starting point.

The filing specifies the third, and this module implements it. Its appeal is that it makes
NO distributional assumption whatsoever. If last year contained a fat left tail, a
volatility cluster, a gap down on a Monday morning, all of that is in the sample already,
at exactly the frequency with which it occurred. Nothing is smoothed into a bell curve.

Its weakness is the mirror image. The sample is the only thing it knows. A crash that did
not happen in the window is assigned probability zero, and a crash that did happen is
assigned a probability of exactly 1-in-250 forever -- until the day it drops out of the
window, at which point the risk estimate falls off a cliff for no reason connected to the
market. The method cannot extrapolate beyond its worst observed day.

# NOTE (deviation from filing): the simulation is UNWEIGHTED, matching the filing. Every
# day in the lookback counts equally: a move from 250 trading days ago carries exactly the
# same weight as yesterday's. This is worth pausing on, because volatility is strongly
# clustered in reality -- a turbulent yesterday genuinely does say more about tomorrow than
# a calm day from a year ago. Practitioners often address this with exponentially weighted
# schemes (BRW, or filtered historical simulation that rescales past returns by the ratio
# of current to historical volatility). We do none of that, because the filing does not.

Run this file directly to generate and summarise the scenarios on their own:

    python3 historical_sim.py
"""

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew


def compute_daily_returns(prices):
    """
    Compute daily returns from a SPY closing price series.

    # NOTE (approximation): we use SIMPLE returns, P_t / P_{t-1} - 1, rather than log
    # returns, log(P_t / P_{t-1}). The reason is directness: `generate_scenarios` applies
    # these multiplicatively to today's price as `S * (1 + r)`, and the simple return is
    # exactly the quantity that operation needs. Using log returns would require
    # exponentiating, `S * exp(r)`, which is equivalent but adds a step and an opportunity
    # to get it wrong.
    #
    # The two differ by second order: log(1 + r) = r - r^2/2 + ..., so at a daily horizon
    # where |r| is typically under 2% the discrepancy is under 2 basis points. It becomes
    # material only over longer horizons or in genuinely extreme moves -- SPY's worst day
    # in this sample is a simple return of about -2.5%, whose log return is -2.53%, a
    # difference of 3bp on the scenario price.
    #
    # The real argument for log returns is that they aggregate additively across time,
    # which matters for multi-day horizons. Our horizon is one day, so it does not.

    Inputs:
        prices (DataFrame): columns 'date' (datetime64) and 'spy_close' (float), sorted
            ascending by date, one row per trading day.

    Returns:
        pandas.DataFrame with columns:
            'date'          (datetime64) the day the return was REALISED, i.e. the later
                            of the two days whose closes it compares.
            'spy_close'     (float) that day's close, in dollars.
            'daily_return'  (float) simple return as a decimal (0.01 means +1%).
        The first row of the input is dropped, since a return needs two prices.
    """
    if len(prices) < 2:
        raise ValueError(
            f"Need at least 2 prices to compute a return, got {len(prices)}."
        )

    if not prices["date"].is_monotonic_increasing:
        raise ValueError(
            "Price series must be sorted ascending by date before computing returns; "
            "otherwise the returns are between arbitrary pairs of days."
        )

    returns = prices.copy()

    # pct_change compares each row to the one above it, which -- given the sort order we
    # just checked -- is the previous trading day. The first row has no predecessor and
    # comes out NaN.
    returns["daily_return"] = returns["spy_close"].pct_change()

    returns = returns.loc[returns["daily_return"].notna()].reset_index(drop=True)

    return returns[["date", "spy_close", "daily_return"]]


def generate_scenarios(current_price, historical_returns, lookback_days=250):
    """
    Build the set of possible "tomorrow" prices by replaying historical daily moves.

    The operation is deliberately simple: take each of the last `lookback_days` daily
    returns and apply it to TODAY's price. If SPY fell 2.1% on some day last March, one of
    our scenarios is "SPY falls 2.1% from where it closed today".

    Note what is NOT happening here: we are not replaying the historical PRICES. A scenario
    is not "SPY goes back to $410". Every scenario starts from today's level and applies a
    historical percentage move to it. The historical price level is irrelevant; only the
    size of the move carries over.

    Inputs:
        current_price (float):          today's SPY close, in dollars. Every scenario is
            built from this level.
        historical_returns (DataFrame): output of `compute_daily_returns`, with columns
            'date' and 'daily_return'.
        lookback_days (int):            how many of the most recent trading days to use.
            250 is one year and is what the filing specifies.

    Returns:
        pandas.DataFrame with one row per scenario, ordered oldest to newest, columns:
            'historical_date'   (datetime64) the day this move actually happened. Carried
                                through so Phase 8 can plot P&L against the episode that
                                produced it.
            'historical_return' (float) that day's simple return, decimal.
            'scenario_price'    (float) today's price after applying that move, dollars.
    """
    if current_price <= 0:
        raise ValueError(f"current_price must be positive, got {current_price}")

    if len(historical_returns) < lookback_days:
        raise ValueError(
            f"Asked for a {lookback_days}-day lookback but only "
            f"{len(historical_returns)} returns are available. Load more price history: "
            f"the window would otherwise silently be shorter than the filing specifies."
        )

    # `.tail` takes the most RECENT rows, which is what a trailing one-year window means.
    window = historical_returns.tail(lookback_days).reset_index(drop=True)

    scenarios = pd.DataFrame({
        "historical_date": window["date"],
        "historical_return": window["daily_return"],
        "scenario_price": current_price * (1.0 + window["daily_return"]),
    })

    return scenarios


def summarise_returns(scenarios, trading_days_per_year=252):
    """
    Compute and print descriptive statistics for the sampled returns.

    The statistics that matter most here are the third and fourth moments, because they are
    precisely what a normal distribution would get wrong and precisely what historical
    simulation preserves:

      - SKEWNESS measures asymmetry. Equity index returns are typically negatively skewed:
        the market takes the stairs up and the lift down. A negative value means the large
        moves are disproportionately downward, which is exactly the tail a long position
        cares about.
      - EXCESS KURTOSIS measures tail weight relative to a normal distribution, which
        scores zero by construction. A positive value means large moves of either sign
        happen more often than a bell curve would predict.

    Long samples of daily equity returns essentially always show fat tails, and that is the
    standard argument against a parametric normal VaR. Do not assume it holds here. Over a
    single 250-day window the answer depends entirely on whether that year contained a
    crisis, and a calm year can genuinely produce THINNER tails than a normal -- because a
    normal fitted to a calm year still has to accommodate the handful of moderately large
    days, which stretches it wider than the data actually is. Read the numbers this
    function prints rather than assuming the textbook result; `compare_tails_against_normal`
    and `rolling_window_statistics` exist to make the real answer visible.

    Inputs:
        scenarios (DataFrame):        output of `generate_scenarios`.
        trading_days_per_year (int):  used only to annualise the volatility for context.

    Returns:
        dict of the computed statistics, so callers can save them without re-deriving.
    """
    returns = scenarios["historical_return"]

    # `bias=False` applies the small-sample correction. With 250 observations it barely
    # matters, but the corrected estimator is the right default.
    # `fisher=True` (the default) reports EXCESS kurtosis, so that a normal scores 0
    # rather than 3. We state this explicitly because the two conventions are easy to
    # confuse and differ by exactly 3.
    statistics = {
        "n_scenarios": len(returns),
        "mean": float(returns.mean()),
        "std": float(returns.std(ddof=1)),
        "annualised_vol": float(returns.std(ddof=1) * np.sqrt(trading_days_per_year)),
        "skewness": float(skew(returns, bias=False)),
        "excess_kurtosis": float(kurtosis(returns, fisher=True, bias=False)),
        "min": float(returns.min()),
        "max": float(returns.max()),
    }

    worst_row = scenarios.loc[returns.idxmin()]
    best_row = scenarios.loc[returns.idxmax()]

    statistics["worst_date"] = worst_row["historical_date"]
    statistics["best_date"] = best_row["historical_date"]

    print("HISTORICAL RETURN SAMPLE")
    print(f"  Scenarios                 : {statistics['n_scenarios']}")
    print(f"  Window                    : "
          f"{scenarios['historical_date'].iloc[0]:%Y-%m-%d} to "
          f"{scenarios['historical_date'].iloc[-1]:%Y-%m-%d}")
    print(f"  Mean daily return         : {statistics['mean']:+.4%}")
    print(f"  Daily volatility          : {statistics['std']:.4%}")
    print(f"  Annualised volatility     : {statistics['annualised_vol']:.4%}  "
          f"(daily vol x sqrt({trading_days_per_year}))")
    print(f"  Skewness                  : {statistics['skewness']:+.4f}  "
          f"(0 = symmetric; negative = large moves skew downward)")
    print(f"  Excess kurtosis           : {statistics['excess_kurtosis']:+.4f}  "
          f"(0 = normal tails; positive = fatter tails)")
    print(f"  Worst day                 : {statistics['min']:+.4%} on "
          f"{statistics['worst_date']:%Y-%m-%d}")
    print(f"  Best day                  : {statistics['max']:+.4%} on "
          f"{statistics['best_date']:%Y-%m-%d}")

    return statistics


def largest_moves(scenarios, n=5):
    """
    Return the largest up and down moves in the sample, for eyeballing against real events.

    This is a sanity check rather than a calculation. The extreme days in any equity return
    sample should be recognisable -- they should line up with dated market events, not fall
    on quiet Tuesdays. If the biggest move in the window is a day nothing happened, the
    price series has a data problem, most likely a missing day compressing two sessions
    into one return.

    Inputs:
        scenarios (DataFrame): output of `generate_scenarios`.
        n (int):               how many of each to return.

    Returns:
        tuple (largest_down, largest_up), each a DataFrame of n rows sorted by the size of
        the move, most extreme first.
    """
    ordered = scenarios.sort_values("historical_return")

    largest_down = ordered.head(n).reset_index(drop=True)
    largest_up = ordered.tail(n).iloc[::-1].reset_index(drop=True)

    return largest_down, largest_up


def compare_tails_against_normal(scenarios):
    """
    Count how often large moves actually occurred, against how often a normal predicts.

    This makes the fat-tail point concrete rather than rhetorical. We fit a normal
    distribution to the sample -- same mean, same standard deviation -- and then count how
    many days exceeded two, three and four standard deviations, against how many such days
    that fitted normal says we should have seen in a sample this size.

    The ratio is the whole argument for historical simulation over a parametric normal VaR.

    Inputs:
        scenarios (DataFrame): output of `generate_scenarios`.

    Returns:
        pandas.DataFrame with one row per threshold, columns 'threshold_sigma',
        'observed', 'normal_expected', 'ratio'.
    """
    from scipy.stats import norm

    returns = scenarios["historical_return"]
    mean = returns.mean()
    standard_deviation = returns.std(ddof=1)

    standardised = (returns - mean) / standard_deviation

    rows = []

    for threshold in [2.0, 3.0, 4.0]:
        observed = int((standardised.abs() > threshold).sum())

        # Two-tailed probability of exceeding the threshold under a standard normal,
        # multiplied by the sample size to get an expected count.
        expected = 2.0 * norm.sf(threshold) * len(returns)

        rows.append({
            "threshold_sigma": threshold,
            "observed": observed,
            "normal_expected": expected,
            "ratio": observed / expected if expected > 0 else np.nan,
        })

    return pd.DataFrame(rows)


def rolling_window_statistics(returns, reference_dates, lookback_days=250):
    """
    Compute the VaR-relevant statistics of the trailing window as of several past dates.

    WHY THIS IS WORTH THE FEW LINES IT COSTS
    ----------------------------------------
    The single biggest weakness of unweighted historical simulation is that the answer is a
    property of the WINDOW as much as of the position. The same option, on the same day,
    with the same model, produces a very different risk number depending on which twelve
    months happen to sit behind it -- and the number moves not because the market changed
    but because an old crisis aged out of the sample.

    That is easy to say and easy to nod along to. This function makes it a table. For each
    reference date it reports what the trailing window looked like, including the 1st
    percentile of returns, which is essentially the quantity the 99% VaR reads.

    Inputs:
        returns (DataFrame):        output of `compute_daily_returns`, covering a long
            history.
        reference_dates (list):     dates to evaluate as of, each "YYYY-MM-DD" or
            Timestamp. A date with fewer than `lookback_days` of prior history is skipped.
        lookback_days (int):        window length.

    Returns:
        pandas.DataFrame, one row per reference date, with the window bounds, annualised
        volatility, skewness, excess kurtosis, worst day, and 1st percentile of returns.
    """
    rows = []

    for reference_date in reference_dates:
        as_of = pd.to_datetime(reference_date)

        # Everything strictly up to and including the reference date is available; the
        # window is the last `lookback_days` of that.
        available = returns.loc[returns["date"] <= as_of]

        if len(available) < lookback_days:
            continue

        window = available.tail(lookback_days)
        window_returns = window["daily_return"]

        rows.append({
            "as_of": as_of,
            "window_start": window["date"].iloc[0],
            "window_end": window["date"].iloc[-1],
            "annualised_vol": float(window_returns.std(ddof=1) * np.sqrt(252)),
            "skewness": float(skew(window_returns, bias=False)),
            "excess_kurtosis": float(kurtosis(window_returns, fisher=True, bias=False)),
            "worst_day": float(window_returns.min()),
            "first_percentile": float(np.percentile(window_returns, 1.0)),
        })

    return pd.DataFrame(rows)


def _run_standalone(config_name=None):
    """
    Generate and summarise scenarios on their own. Entry point for
    `python3 historical_sim.py`.

    Reads the cached price history Phase 0 leaves in the config's `data_dir`, so this
    module can be run and reviewed without touching the option-pricing side of the
    project at all. Takes the same argument main.py does:

        python3 historical_sim.py                    -> the default case
        python3 historical_sim.py config_2026_06_01  -> the Databento case

    Inputs:
        config_name (str or None): config module name without the .py. None means the
            default case.

    Returns:
        None. Prints the summary and writes no files.
    """
    import importlib.util
    import os

    # Same config loading as main.py, by file path rather than by importing main --
    # main.py imports this module, so importing it back would close the import graph.
    # Duplicated across the standalone blocks deliberately: each one stays runnable
    # with nothing from the pipeline behind it. Default matches main.DEFAULT_CONFIG.
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

    price_path = os.path.join(config["data_dir"], "spy_price_history.csv")
    state_path = os.path.join(config["tables_dir"], "reference_market_state.csv")

    if not os.path.exists(price_path) or not os.path.exists(state_path):
        command = "python3 main.py"
        if config_name != "config_2023_12_29":
            command += f" {config_name}"
        raise FileNotFoundError(
            f"Missing pipeline output. Run `{command}` first to produce\n"
            f"  {price_path}\n  {state_path}"
        )

    prices = pd.read_csv(price_path, parse_dates=["date"])
    state = pd.read_csv(state_path).iloc[0]

    current_price = float(state["spot"])

    returns = compute_daily_returns(prices)
    scenarios = generate_scenarios(current_price, returns,
                                   lookback_days=config["lookback_days"])

    print(f"Today's SPY price: ${current_price:.2f}")
    print(f"Scenario prices range from ${scenarios['scenario_price'].min():.2f} "
          f"to ${scenarios['scenario_price'].max():.2f}")
    print()

    summarise_returns(scenarios)

    print()
    print("TAIL FREQUENCY VS A FITTED NORMAL")
    print(compare_tails_against_normal(scenarios).to_string(index=False))


if __name__ == "__main__":
    import sys

    _run_standalone(sys.argv[1] if len(sys.argv) > 1 else None)
