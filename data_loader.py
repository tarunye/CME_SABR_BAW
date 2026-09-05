"""
Data loading and validation for the SPY options VaR replication.

Everything in this project is built on two datasets, both of which come out of the
OptionsDX SPY end-of-day option chain files sitting in `data/`:

  1. A daily SPY price series, used to build the historical return sample (Phase 4).
  2. A single-day option chain snapshot, used to calibrate the volatility smile (Phase 1)
     and to price against the market (Phase 3).

The OptionsDX files are one row per (quote date, expiry, strike), carrying BOTH the call
and the put for that strike side by side. Column names in the raw files are wrapped in
square brackets -- e.g. "[QUOTE_DATE]" -- which is a vendor quirk we strip on load.

Nothing here reaches out to a network. The raw parquet files are the source of truth, and
the derived pulls are cached to CSV in `data/` so that repeated runs are fast and so that
anyone reviewing this can open the exact inputs in a spreadsheet.
"""

import os

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (GoodFriday, Holiday, USColumbusDay,
                                    USFederalHolidayCalendar, nearest_workday)


# The vendor's raw column names (minus the square brackets) mapped onto the plain,
# spelled-out names we use everywhere else in the codebase.
COLUMN_NAMES = {
    "QUOTE_DATE": "quote_date",
    "EXPIRE_DATE": "expiry_date",
    "DTE": "days_to_expiry",
    "UNDERLYING_LAST": "underlying_price",
    "STRIKE": "strike",
    "C_BID": "call_bid",
    "C_ASK": "call_ask",
    "C_LAST": "call_last",
    "C_IV": "call_iv",
    "C_VOLUME": "call_volume",
    "P_BID": "put_bid",
    "P_ASK": "put_ask",
    "P_LAST": "put_last",
    "P_IV": "put_iv",
    "P_VOLUME": "put_volume",
}

# Implied vols outside this range are treated as vendor junk rather than real market
# information. 2% is below anything SPY has ever realised over a meaningful horizon;
# 200% is above even the March 2020 panic on short-dated wings.
MIN_PLAUSIBLE_IMPLIED_VOL = 0.02
MAX_PLAUSIBLE_IMPLIED_VOL = 2.00

# A single-day move larger than this gets flagged for a human to eyeball. It is not an
# error -- SPY genuinely moved -10.9% on 2020-03-16 -- but it should never pass silently.
EXTREME_DAILY_RETURN_THRESHOLD = 0.20


def _read_year_file(data_dir, year, columns=None):
    """
    Read one OptionsDX year file and rename its bracketed columns to our plain names.

    Inputs:
        data_dir (str):  folder holding the `spy_eod_YYYY.parquet` files.
        year (int):      calendar year to read.
        columns (list or None): plain (already-renamed) column names to read. Reading
            only the columns we need matters here -- the full 2021 file is 85 MB, but
            pulling two columns out of it takes a fraction of a second.

    Returns:
        pandas.DataFrame with plain lowercase column names.
    """
    path = os.path.join(data_dir, f"spy_eod_{year}.parquet")

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Expected OptionsDX file not found: {path}\n"
            f"The project needs the spy_eod_YYYY.parquet files in '{data_dir}'."
        )

    # Translate our plain names back into the vendor's bracketed names so that we can
    # push the column selection down into the parquet reader itself.
    if columns is not None:
        reverse_names = {plain: raw for raw, plain in COLUMN_NAMES.items()}
        raw_columns = [f"[{reverse_names[name]}]" for name in columns]
    else:
        raw_columns = None

    frame = pd.read_parquet(path, columns=raw_columns)

    # Strip the vendor's square brackets, then map onto our plain names.
    frame.columns = [name.strip("[]") for name in frame.columns]
    frame = frame.rename(columns=COLUMN_NAMES)

    return frame


def load_spy_price_history(data_dir, start_date, end_date, cache_path=None):
    """
    Build the daily SPY closing price series from the option chain files.

    We take the price from the `UNDERLYING_LAST` field of the option files rather than
    from a separate equity data source. The reason is synchronicity: `UNDERLYING_LAST` is
    the SPY level stamped at the same 16:00 instant as the option quotes on that row, so
    the spot we shock in Phase 5 is exactly the spot the option market was looking at.
    A price pulled from a different vendor would be a few seconds or minutes out, and
    that mismatch would quietly contaminate every scenario price.

    # NOTE (approximation): `UNDERLYING_LAST` is the UNADJUSTED SPY price, not a
    # dividend-adjusted close. SPY goes ex-dividend four times a year (third Friday of
    # March, June, September, December) for roughly 0.35%-0.45% of its value. Those four
    # days therefore appear in our return sample as small spurious DOWN moves that no
    # investor actually suffered -- a total-return holder was made whole by the dividend.
    # We accept this because (a) the effect is an order of magnitude smaller than the
    # daily moves that drive the 99% tail, and (b) the option contracts themselves are
    # written on the unadjusted price, so shocking the unadjusted price is the internally
    # consistent choice. The alternative -- using a dividend-adjusted series -- would
    # remove those four dips at the cost of desynchronising spot from the option quotes.

    Inputs:
        data_dir (str):    folder holding the parquet files.
        start_date (str):  first quote date to include, "YYYY-MM-DD" (inclusive).
        end_date (str):    last quote date to include, "YYYY-MM-DD" (inclusive).
        cache_path (str or None): if given, write the result to this CSV and read from it
            on subsequent runs instead of re-parsing the parquet files.

    Returns:
        pandas.DataFrame with columns ['date' (datetime64), 'spy_close' (float)],
        sorted ascending by date, one row per trading day.
    """
    if cache_path is not None and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path, parse_dates=["date"])

        # A cache is only usable if it actually covers what was asked for. Without this
        # check, changing a date in CONFIG would silently return the previous range and
        # every downstream number would be computed from data the config no longer
        # describes -- the worst kind of bug, because nothing fails and the output looks
        # entirely reasonable. We compare against the first and last TRADING days in the
        # requested range rather than the raw dates, since a request starting on a weekend
        # can never be matched exactly.
        wanted_days = nyse_trading_days(start_date, end_date)

        if len(wanted_days) > 0:
            covers_start = cached["date"].min() <= wanted_days[0]
            covers_end = cached["date"].max() >= wanted_days[-1]
        else:
            covers_start = covers_end = True

        if covers_start and covers_end:
            in_window = (cached["date"] >= start_date) & (cached["date"] <= end_date)
            return cached.loc[in_window].reset_index(drop=True)

        print(f"  Cache at {cache_path} does not cover {start_date} to {end_date} "
              f"(it holds {cached['date'].min():%Y-%m-%d} to "
              f"{cached['date'].max():%Y-%m-%d}); rebuilding from the parquet files.")

    first_year = int(start_date[:4])
    last_year = int(end_date[:4])

    # Each year file contains thousands of rows per trading day (one per strike/expiry),
    # all repeating the same underlying price. We pull the two columns we care about and
    # collapse to one row per date.
    daily_frames = []

    for year in range(first_year, last_year + 1):
        year_frame = _read_year_file(
            data_dir, year, columns=["quote_date", "underlying_price"]
        )
        collapsed = year_frame.groupby("quote_date", as_index=False)["underlying_price"].first()
        daily_frames.append(collapsed)

    prices = pd.concat(daily_frames, ignore_index=True)
    prices = prices.rename(columns={"quote_date": "date", "underlying_price": "spy_close"})
    prices["date"] = pd.to_datetime(prices["date"])

    # Trim to the requested window and put the series in chronological order, which every
    # downstream return calculation assumes.
    in_window = (prices["date"] >= start_date) & (prices["date"] <= end_date)
    prices = prices.loc[in_window].sort_values("date").reset_index(drop=True)

    if cache_path is not None:
        prices.to_csv(cache_path, index=False)

    return prices


def load_options_chain(data_dir, reference_date, cache_path=None):
    """
    Load the full SPY option chain snapshot for a single quote date (all expiries).

    We load every expiry rather than just the one we intend to use, because the snapshot
    is small (a few thousand rows) and because a portfolio across several expiries is a
    live possibility later on. Selecting a single expiry is a separate, explicit step --
    see `select_expiry`.

    Inputs:
        data_dir (str):        folder holding the parquet files.
        reference_date (str):  the quote date, "YYYY-MM-DD".
        cache_path (str or None): if given, cache the snapshot to this CSV.

    Returns:
        pandas.DataFrame, one row per (expiry, strike), carrying both the call and the
        put side for that strike. Columns are the plain names listed in COLUMN_NAMES.
    """
    if cache_path is not None and os.path.exists(cache_path):
        chain = pd.read_csv(cache_path, parse_dates=["quote_date", "expiry_date"])
        return chain

    year = int(reference_date[:4])
    year_frame = _read_year_file(data_dir, year, columns=list(COLUMN_NAMES.values()))

    chain = year_frame.loc[year_frame["quote_date"] == reference_date].copy()

    if chain.empty:
        available = sorted(year_frame["quote_date"].unique())
        raise ValueError(
            f"No option chain rows found for quote date {reference_date}. "
            f"That year's file runs {available[0]} to {available[-1]}. "
            f"Is {reference_date} a trading day?"
        )

    chain["quote_date"] = pd.to_datetime(chain["quote_date"])
    chain["expiry_date"] = pd.to_datetime(chain["expiry_date"])
    chain = chain.sort_values(["expiry_date", "strike"]).reset_index(drop=True)

    if cache_path is not None:
        chain.to_csv(cache_path, index=False)

    return chain


def select_expiry(chain, expiry_date):
    """
    Pull a single expiry out of a full chain snapshot.

    Inputs:
        chain (DataFrame):   output of `load_options_chain`.
        expiry_date (str):   the expiry to keep, "YYYY-MM-DD".

    Returns:
        pandas.DataFrame containing only that expiry, sorted by strike.
    """
    wanted = pd.to_datetime(expiry_date)
    single = chain.loc[chain["expiry_date"] == wanted].copy()

    if single.empty:
        available = sorted(chain["expiry_date"].dt.strftime("%Y-%m-%d").unique())
        raise ValueError(
            f"Expiry {expiry_date} is not in this snapshot.\n"
            f"Available expiries: {', '.join(available)}"
        )

    return single.sort_values("strike").reset_index(drop=True)


def year_fraction_to_expiry(reference_date, expiry_date):
    """
    Time to expiry in years, on an ACT/365 basis.

    # NOTE (approximation): we use calendar days divided by 365. The two common
    # alternatives are ACT/252 (trading days, on the argument that variance accumulates
    # only while the market is open) and ACT/365.25. At a 49-day horizon the choice moves
    # implied vol by well under a vol point and is absorbed almost entirely by the SABR
    # calibration, which fits to whatever T we hand it. What matters far more is that we
    # use the SAME convention everywhere -- calibration, pricing, and scenario repricing --
    # so that the T that went into the fit is the T that comes back out of the pricer.

    Inputs:
        reference_date (str or Timestamp): the quote date.
        expiry_date (str or Timestamp):    the expiry date.

    Returns:
        float, time to expiry in years. Positive for a future expiry.
    """
    start = pd.to_datetime(reference_date)
    end = pd.to_datetime(expiry_date)

    calendar_days = (end - start).days

    return calendar_days / 365.0


def imply_forward_from_parity(single_expiry_chain, underlying_price, risk_free_rate,
                              time_to_expiry_years, moneyness_band=0.05,
                              max_spread=0.30):
    """
    Infer the forward price of SPY at the option expiry from put-call parity.

    WHY WE DO THIS RATHER THAN ASSUME A DIVIDEND YIELD
    --------------------------------------------------
    SABR is parameterised on the FORWARD, not on spot. If we assume a dividend yield and
    compute F = S * exp((r - q) * T), we are imposing our guess about dividends onto the
    market's own quotes -- and any error goes straight into the smile as a spurious skew,
    because getting F wrong slides the whole moneyness axis sideways.

    Put-call parity lets the option market tell us its own forward. For European options,

        C - P = discount_factor * (F - K)

    so, rearranged, every strike gives us an independent estimate:

        F = K + (C - P) / discount_factor

    We pin the discount factor from an external risk-free rate (a Treasury yield is
    observable and uncontroversial) and let the quotes supply F. We then read the implied
    dividend yield off as a DIAGNOSTIC rather than an input:  q = r - ln(F / S) / T.

    # NOTE (approximation): parity holds exactly only for EUROPEAN options, and SPY
    # options are American. The American put carries an early-exercise premium the
    # European one does not, so (C - P) is slightly too small and F is biased slightly
    # low. At 49 days, near the money, with roughly 12% vol, that premium is on the order
    # of a cent or two -- comfortably inside the bid-ask noise, as the tightness of the
    # per-strike estimates below confirms.

    Inputs:
        single_expiry_chain (DataFrame): one expiry, from `select_expiry`.
        underlying_price (float):        spot SPY at the quote time, in dollars.
        risk_free_rate (float):          continuously-compounded annual rate, decimal.
        time_to_expiry_years (float):    T, in years.
        moneyness_band (float):          keep strikes within this fraction of spot. Far
            from the money, one of the two legs is deep in the money, thinly traded and
            stale, which poisons the estimate.
        max_spread (float):              reject a strike if either leg's bid-ask is wider
            than this many dollars. A wide spread means the mid is not a real price.

    Returns:
        dict with keys:
            'forward'              (float) the implied forward, in dollars
            'implied_dividend_yield' (float) q backed out of F, decimal per annum
            'cost_of_carry'        (float) b such that F = S * exp(b * T); this is what
                                   BAW needs, and it equals r - q by construction
            'n_strikes_used'       (int)   how many strikes contributed
            'forward_std'          (float) dispersion of the per-strike estimates; small
                                   dispersion is the evidence that the method worked
    """
    chain = single_expiry_chain.copy()

    call_mid = (chain["call_bid"] + chain["call_ask"]) / 2.0
    put_mid = (chain["put_bid"] + chain["put_ask"]) / 2.0
    call_spread = chain["call_ask"] - chain["call_bid"]
    put_spread = chain["put_ask"] - chain["put_bid"]

    # Keep only strikes where BOTH legs are genuinely two-sided and tightly quoted.
    # A crossed or locked market (ask <= bid) is a stale-quote artefact, not a price.
    near_the_money = (chain["strike"] / underlying_price - 1.0).abs() <= moneyness_band
    call_is_tradeable = (call_spread > 0) & (call_spread <= max_spread) & (chain["call_bid"] > 0)
    put_is_tradeable = (put_spread > 0) & (put_spread <= max_spread) & (chain["put_bid"] > 0)

    usable = near_the_money & call_is_tradeable & put_is_tradeable

    if usable.sum() < 4:
        raise ValueError(
            f"Only {usable.sum()} strikes survived the liquidity filter for the forward "
            f"calculation; need at least 4. Widen `moneyness_band` or `max_spread`, or "
            f"check whether this expiry is quoted at all."
        )

    discount_factor = np.exp(-risk_free_rate * time_to_expiry_years)

    # One independent estimate of the forward per surviving strike.
    per_strike_forward = (
        chain.loc[usable, "strike"] + (call_mid[usable] - put_mid[usable]) / discount_factor
    )

    # We take the MEDIAN rather than the mean: a single stale quote produces one wild
    # estimate, and the median simply ignores it where a mean would be dragged along.
    forward = float(per_strike_forward.median())

    # Cost of carry b is defined by F = S * exp(b * T), so measuring the forward gives us
    # b directly. Deriving b from the forward we observed, rather than from an assumed
    # r - q, guarantees the BAW pricer and the SABR parameterisation agree about where the
    # forward is. b is also what decides whether early exercise is ever optimal for a
    # call -- see the Phase 2 comments.
    cost_of_carry = np.log(forward / underlying_price) / time_to_expiry_years

    # Read the market's implied dividend yield off the same quantity. This is a
    # diagnostic, not an input: if it comes out far from SPY's ~1.4% trailing yield --
    # after adjusting for whether an ex-dividend date actually falls inside the window --
    # something is wrong upstream.
    implied_dividend_yield = risk_free_rate - cost_of_carry

    return {
        "forward": forward,
        "implied_dividend_yield": float(implied_dividend_yield),
        "cost_of_carry": float(cost_of_carry),
        "n_strikes_used": int(usable.sum()),
        "forward_std": float(per_strike_forward.std()),
    }


def build_otm_smile(single_expiry_chain, forward, min_volume=0):
    """
    Construct the market implied-volatility smile using out-of-the-money quotes only.

    WHY OUT-OF-THE-MONEY ONLY
    -------------------------
    At any strike we have two quotes for the same underlying uncertainty: a call and a
    put. In principle put-call parity means they carry identical implied vols. In practice
    they do not, because the IN-the-money leg is the illiquid one -- it ties up far more
    capital for the same exposure, so almost nobody trades it, and its quote goes stale
    and wide. In this dataset the divergence is stark: at the 405 strike on our reference
    date the deep-ITM call shows 27.2% vol while the liquid OTM put at the same strike
    shows 23.2%.

    Market convention, which we follow, is to take the OTM leg at every strike: puts below
    the forward, calls above it. The two wings meet smoothly at the money and the result
    is a single clean smile.

    We split at the FORWARD rather than at spot, because "at the money" for an option
    means the strike the forward is heading towards, not today's spot level. With SPY
    forwards trading above spot in a high-rate environment the two differ by several
    dollars, which is more than one strike increment.

    Inputs:
        single_expiry_chain (DataFrame): one expiry, from `select_expiry`.
        forward (float):     the forward price used as the call/put crossover, in dollars.
        min_volume (float):  require at least this much traded volume on the day. Zero by
            default -- a zero-volume strike can still carry a perfectly good quote, and
            filtering on volume would gut the wings, which are the part of the smile that
            actually pins down the SABR skew and curvature.

    Returns:
        pandas.DataFrame sorted by strike, with columns:
            'strike'        (float) strike in dollars
            'option_type'   (str)   'put' below the forward, 'call' above it
            'market_iv'     (float) the vendor's implied vol, decimal (0.12 = 12%)
            'bid', 'ask'    (float) the quotes for the OTM leg
            'mid'           (float) midpoint, our proxy for the fair market price
            'volume'        (float) contracts traded that day
    """
    chain = single_expiry_chain.copy()

    is_put_side = chain["strike"] < forward

    # Pick the out-of-the-money leg strike by strike. `numpy.where` here is just an
    # element-wise if/else -- put values below the forward, call values above it.
    smile = pd.DataFrame({
        "strike": chain["strike"].to_numpy(),
        "option_type": np.where(is_put_side, "put", "call"),
        "market_iv": np.where(is_put_side, chain["put_iv"], chain["call_iv"]),
        "bid": np.where(is_put_side, chain["put_bid"], chain["call_bid"]),
        "ask": np.where(is_put_side, chain["put_ask"], chain["call_ask"]),
        "volume": np.where(is_put_side, chain["put_volume"], chain["call_volume"]),
    })

    smile["mid"] = (smile["bid"] + smile["ask"]) / 2.0

    # Quality filters, each removing a specific known defect in end-of-day option data:
    has_two_sided_market = smile["bid"] > 0                      # a zero bid is "no market"
    is_not_crossed = smile["ask"] > smile["bid"]                 # ask <= bid is a stale artefact
    has_plausible_vol = (
        (smile["market_iv"] >= MIN_PLAUSIBLE_IMPLIED_VOL)
        & (smile["market_iv"] <= MAX_PLAUSIBLE_IMPLIED_VOL)
    )
    has_volume = smile["volume"].fillna(0.0) >= min_volume

    keep = has_two_sided_market & is_not_crossed & has_plausible_vol & has_volume
    smile = smile.loc[keep].sort_values("strike").reset_index(drop=True)

    return smile


def nyse_trading_days(start_date, end_date):
    """
    Build the set of days the New York Stock Exchange was open, inclusive of both ends.

    We derive this rather than hardcode it. The NYSE calendar is the US federal holiday
    calendar with three adjustments, each of which we apply explicitly below:

      - the NYSE TRADES on Columbus Day and Veterans Day, which are federal holidays;
      - the NYSE CLOSES on Good Friday, which is not a federal holiday.

    (This ignores one-off closures such as the state funeral of George H. W. Bush on
    2018-12-05 or the Hurricane Sandy closures in October 2012. Those show up as
    "missing" days in the validation report, which is the right outcome -- a human should
    look at them rather than have the code silently absorb them.)

    Inputs:
        start_date (str or Timestamp): first date to consider.
        end_date (str or Timestamp):   last date to consider.

    Returns:
        pandas.DatetimeIndex of open trading days.
    """
    # Veterans Day has no ready-made rule in pandas, so we state it: 11 November, moved
    # to the nearest weekday when it falls on a Saturday or Sunday.
    veterans_day = Holiday("Veterans Day", month=11, day=11, observance=nearest_workday)

    holidays = USFederalHolidayCalendar().holidays(start=start_date, end=end_date)
    holidays = holidays.difference(USColumbusDay.dates(start_date, end_date))
    holidays = holidays.difference(veterans_day.dates(start_date, end_date))
    holidays = holidays.union(GoodFriday.dates(start_date, end_date))

    weekdays = pd.bdate_range(start_date, end_date)

    return weekdays.difference(holidays)


def smile_local_roughness(smile):
    """
    Measure how noisy the observed smile is, strike by strike.

    WHY THIS EXISTS
    ---------------
    When we fit SABR and get an RMSE, the obvious question is "is that good?" -- and the
    honest answer depends entirely on how clean the data was. If the quoted implied vols
    themselves jitter by 0.2 vol points from one strike to the next, then a model fitting
    them to 0.2 vol points has done as well as anything possibly could, and chasing a
    lower number would just be fitting noise.

    A true volatility smile is a smooth function of strike. So for any interior strike,
    the observed vol should sit almost exactly on the straight line between its two
    neighbours; the amount by which it does not is curvature plus noise. At $1 strike
    spacing the genuine curvature contribution is tiny, so this statistic is dominated by
    quote noise -- tick granularity, stale marks, and the vendor's IV inversion.

    We report it per side as well as overall, because in practice the two wings are
    quoted with very different care.

    Inputs:
        smile (DataFrame): a smile with 'strike', 'market_iv' and 'option_type', sorted
            ascending by strike.

    Returns:
        dict with keys 'overall', 'put', 'call' -- each the RMS local roughness as a
        decimal vol (multiply by 100 for vol points). A side with fewer than 3 strikes
        returns numpy.nan for that entry.
    """
    def rms_roughness(vols):
        """RMS deviation of each interior point from the midpoint of its neighbours."""
        vols = np.asarray(vols, dtype=float)

        if len(vols) < 3:
            return float("nan")

        deviations = vols[1:-1] - 0.5 * (vols[:-2] + vols[2:])

        return float(np.sqrt(np.mean(deviations ** 2)))

    return {
        "overall": rms_roughness(smile["market_iv"]),
        "put": rms_roughness(smile.loc[smile["option_type"] == "put", "market_iv"]),
        "call": rms_roughness(smile.loc[smile["option_type"] == "call", "market_iv"]),
    }


def put_call_crossover_step(smile, n_strikes=10):
    """
    Measure the discontinuity in implied vol where the put wing hands over to the call wing.

    WHY THIS MATTERS
    ----------------
    Put-call parity says a put and a call at the same strike must carry the same implied
    volatility. Our smile is built from puts below the forward and calls above it, so if
    the vendor computed the two sides on a consistent forward and discount curve, the
    wings should join seamlessly and the combined smile should be smooth through the
    crossover.

    If instead there is a STEP, the two wings disagree about the level of volatility, and
    no smooth model curve can fit both. The optimiser is then forced to split the
    difference, which shows up as a band of same-signed residuals on each side of the
    forward -- a pattern easily mistaken for the model failing when it is really the data
    disagreeing with itself.

    TWO MEASURES, AND WHY THE SECOND ONE IS THE TRUSTWORTHY ONE
    -----------------------------------------------------------
    The obvious measure extrapolates the put wing to the first call strike and takes the
    difference. That is `extrapolated_step` below, and it should be read with caution: it
    depends on how far the extrapolation has to reach and on how curved the smile is over
    that reach. On our reference date the two strikes nearest the forward (477 and 478)
    both have CROSSED put quotes -- bid above ask -- so the quality filters remove them and
    leave a $3 hole exactly at the crossover, in the most curved part of the smile.
    Extrapolating across that hole gives answers ranging from +0.21 to +0.41 vol points
    depending on the fit used, which is not a measurement.

    The robust measure is `adjacent_gap`: simply the observed call vol at the first call
    strike minus the observed put vol at the last put strike, with no fitting at all. Its
    SIGN is the diagnostic. In this strike region the smile slopes downward, so a
    consistent smile must show a NEGATIVE gap. A positive one means the two wings disagree
    badly enough to reverse the local slope of the smile -- a kink pointing the wrong way,
    which no smooth model can reproduce.

    Inputs:
        smile (DataFrame): a smile with 'strike', 'market_iv' and 'option_type', sorted
            ascending by strike.
        n_strikes (int):   how many put strikes to use for the extrapolated measure.

    Returns:
        dict with keys:
            'adjacent_gap'      (float) observed call vol minus observed put vol at the
                                two strikes either side of the crossover, decimal. The
                                robust measure -- no fitting.
            'strike_gap'        (float) dollars between those two strikes. A large value
                                warns that the crossover is poorly observed.
            'slope_implied_gap' (float) what the local put-wing slope predicts the gap
                                should be over that distance, decimal. Comparing this
                                against 'adjacent_gap' isolates the inconsistency from
                                the genuine slope of the smile.
            'extrapolated_step' (float) the fitted measure described above, decimal.
            'last_put_strike'   (float)
            'first_call_strike' (float)
            'extrapolated_vol'  (float) where the put wing was heading, decimal
            'observed_call_vol' (float) decimal
        Returns None if either side has too few strikes to measure.
    """
    puts = smile.loc[smile["option_type"] == "put"]
    calls = smile.loc[smile["option_type"] == "call"]

    if len(puts) < 2 or len(calls) < 1:
        return None

    tail_puts = puts.tail(n_strikes)

    # A straight line is the right extrapolator over the handful of strikes immediately
    # below the forward: the smile's curvature over a $10 span is negligible compared
    # with the step we are trying to detect.
    slope, intercept = np.polyfit(tail_puts["strike"], tail_puts["market_iv"], 1)

    first_call_strike = float(calls["strike"].iloc[0])
    last_put_strike = float(puts["strike"].iloc[-1])
    extrapolated_vol = float(slope * first_call_strike + intercept)
    observed_call_vol = float(calls["market_iv"].iloc[0])
    observed_put_vol = float(puts["market_iv"].iloc[-1])

    strike_gap = first_call_strike - last_put_strike

    # The LOCAL slope of the put wing, taken from just the last two quotes, tells us how
    # much of the observed gap is simply the smile sloping. Using two points rather than a
    # fitted line keeps this measure local, which matters because the smile flattens as it
    # approaches its minimum and a longer fit would overstate the slope here.
    if len(puts) >= 2:
        local_slope = (
            (puts["market_iv"].iloc[-1] - puts["market_iv"].iloc[-2])
            / (puts["strike"].iloc[-1] - puts["strike"].iloc[-2])
        )
        slope_implied_gap = float(local_slope * strike_gap)
    else:
        slope_implied_gap = float("nan")

    return {
        "adjacent_gap": observed_call_vol - observed_put_vol,
        "strike_gap": strike_gap,
        "slope_implied_gap": slope_implied_gap,
        "extrapolated_step": observed_call_vol - extrapolated_vol,
        "last_put_strike": last_put_strike,
        "first_call_strike": first_call_strike,
        "extrapolated_vol": extrapolated_vol,
        "observed_call_vol": observed_call_vol,
    }


def validate_price_history(prices, lookback_days=None):
    """
    Check the SPY price series for the defects that would silently corrupt Phase 4.

    Fatal problems raise a ValueError with an explanation. Suspicious-but-legitimate
    findings (a genuine crash day, a vendor date-stamping error) print a WARNING and let
    the run continue, because they need a human to look rather than a program to decide.

    Two calendar defects are checked for separately, because they damage the return
    sample in opposite directions:

      - a MISSING trading day compresses two days of market movement into a single
        return, manufacturing an outlier that never happened and fattening the tail we
        are trying to measure;
      - a PHANTOM row on a day the market was shut carries the previous close forward,
        injecting a spurious near-zero return and diluting the tail instead.

    Inputs:
        prices (DataFrame):        output of `load_spy_price_history`.
        lookback_days (int or None): if given, the report says whether each defect lands
            inside the trailing window the VaR calculation will actually consume. A
            defect outside that window is noise in the log; one inside it is a problem.

    Returns:
        None. Raises ValueError on a fatal problem; prints warnings otherwise.
    """
    print("Validating SPY price history...")

    if prices.empty:
        raise ValueError("Price history is empty -- nothing was loaded.")

    # A zero or negative price is not a data quirk, it is a broken file. Every return and
    # every log in this project would blow up downstream.
    non_positive = prices.loc[prices["spy_close"] <= 0]
    if not non_positive.empty:
        raise ValueError(
            f"Found {len(non_positive)} rows with a non-positive SPY price:\n"
            f"{non_positive.to_string(index=False)}"
        )

    if prices["date"].duplicated().any():
        duplicated_dates = prices.loc[prices["date"].duplicated(), "date"]
        raise ValueError(f"Duplicate quote dates in the price series: {list(duplicated_dates)}")

    if not prices["date"].is_monotonic_increasing:
        raise ValueError("Price series is not sorted ascending by date.")

    # Calendar check against the real NYSE trading calendar, in both directions.
    dates_present = pd.DatetimeIndex(prices["date"])
    expected_days = nyse_trading_days(dates_present.min(), dates_present.max())

    missing_days = expected_days.difference(dates_present)
    phantom_days = dates_present.difference(expected_days)

    # Work out which days the VaR calculation will actually consume, so we can say
    # whether a defect matters or is merely present somewhere in the loaded history.
    if lookback_days is not None and len(prices) > lookback_days:
        window_start = prices["date"].iloc[-(lookback_days + 1)]
    else:
        window_start = dates_present.min()

    window_end = dates_present.max()

    def describe(days):
        """Format a date list, marking any that fall inside the VaR lookback window."""
        labels = []
        for day in days:
            inside = window_start <= day <= window_end
            labels.append(day.strftime("%Y-%m-%d") + (" [IN WINDOW]" if inside else ""))
        return ", ".join(labels)

    if len(missing_days) > 0:
        print(
            f"  WARNING: {len(missing_days)} NYSE trading day(s) absent from the vendor "
            f"data.\n"
            f"           Each one compresses two days of market movement into a single\n"
            f"           return, manufacturing an outlier. Dates: {describe(missing_days)}"
        )

    if len(phantom_days) > 0:
        print(
            f"  WARNING: {len(phantom_days)} row(s) stamped on days the NYSE was CLOSED.\n"
            f"           These carry a stale price forward and inject a spurious\n"
            f"           near-zero return. Dates: {describe(phantom_days)}"
        )

    if len(missing_days) == 0 and len(phantom_days) == 0:
        print("  OK: calendar matches the NYSE trading calendar exactly.")
    else:
        in_window = [
            day for day in missing_days.union(phantom_days)
            if window_start <= day <= window_end
        ]
        print(
            f"  Calendar defects inside the {lookback_days}-day VaR window "
            f"({window_start:%Y-%m-%d} to {window_end:%Y-%m-%d}): {len(in_window)}"
        )

    # Extreme move check. We flag rather than fail: a -10.9% day in March 2020 is real
    # data, and throwing it away would understate exactly the tail we are trying to
    # measure. But an unflagged 300% jump would be a corrupted price.
    returns = prices["spy_close"].pct_change()
    extreme = prices.loc[returns.abs() > EXTREME_DAILY_RETURN_THRESHOLD].copy()
    if not extreme.empty:
        extreme["daily_return"] = returns.loc[extreme.index]
        print(
            f"  WARNING: {len(extreme)} day(s) moved more than "
            f"{EXTREME_DAILY_RETURN_THRESHOLD:.0%}. Inspect these manually:\n"
            f"{extreme.to_string(index=False)}"
        )

    print(f"  OK: {len(prices)} trading days, no fatal problems.")


def validate_options_chain(single_expiry_chain, reference_date, expiry_date):
    """
    Check a single-expiry option chain for the defects that would corrupt the SABR fit.

    Inputs:
        single_expiry_chain (DataFrame): one expiry, from `select_expiry`.
        reference_date (str):            the quote date, "YYYY-MM-DD".
        expiry_date (str):               the expiry, "YYYY-MM-DD".

    Returns:
        None. Raises ValueError on a fatal problem; prints warnings otherwise.
    """
    print("Validating option chain snapshot...")

    chain = single_expiry_chain

    if chain.empty:
        raise ValueError(f"No option rows for {reference_date} / expiry {expiry_date}.")

    # The expiry must be in the future, or time to expiry is zero or negative and every
    # pricing formula in the project divides by sqrt(T).
    if pd.to_datetime(expiry_date) <= pd.to_datetime(reference_date):
        raise ValueError(
            f"Expiry {expiry_date} is not after the reference date {reference_date}. "
            f"Time to expiry must be strictly positive."
        )

    if (chain["strike"] <= 0).any():
        raise ValueError("Found non-positive strikes in the chain.")

    if not chain["strike"].is_monotonic_increasing:
        raise ValueError("Chain is not sorted ascending by strike.")

    if chain["strike"].duplicated().any():
        raise ValueError("Duplicate strikes within a single expiry.")

    # The underlying price should be identical on every row of a single snapshot, since
    # they all carry the same 16:00 stamp. If it is not, we have mixed quote times.
    distinct_spots = chain["underlying_price"].nunique()
    if distinct_spots != 1:
        raise ValueError(
            f"Expected one underlying price across the snapshot, found {distinct_spots}. "
            f"The rows may span more than one quote time."
        )

    # Implied vol sanity, reported per side so we can see which leg is the dirty one.
    for side in ["call_iv", "put_iv"]:
        implausible = chain.loc[
            (chain[side] < MIN_PLAUSIBLE_IMPLIED_VOL)
            | (chain[side] > MAX_PLAUSIBLE_IMPLIED_VOL)
            | chain[side].isna()
        ]
        if not implausible.empty:
            print(
                f"  WARNING: {len(implausible)} of {len(chain)} strikes have an "
                f"implausible or missing {side} (outside "
                f"{MIN_PLAUSIBLE_IMPLIED_VOL:.0%}-{MAX_PLAUSIBLE_IMPLIED_VOL:.0%}). "
                f"These are dropped by the OTM smile filter."
            )

    # Crossed markets: ask below bid. A real defect in end-of-day snapshots, caused by one
    # side of the quote updating after the other.
    crossed_calls = ((chain["call_ask"] < chain["call_bid"]) & (chain["call_bid"] > 0)).sum()
    crossed_puts = ((chain["put_ask"] < chain["put_bid"]) & (chain["put_bid"] > 0)).sum()
    if crossed_calls or crossed_puts:
        print(
            f"  WARNING: crossed quotes present ({crossed_calls} call, {crossed_puts} put). "
            f"These are dropped by the OTM smile filter."
        )

    print(f"  OK: {len(chain)} strikes, no fatal problems.")


def summarise_price_history(prices):
    """
    Print a compact human-readable summary of the SPY price series.

    Inputs:
        prices (DataFrame): output of `load_spy_price_history`.

    Returns:
        None. Prints to stdout.
    """
    first_date = prices["date"].iloc[0].strftime("%Y-%m-%d")
    last_date = prices["date"].iloc[-1].strftime("%Y-%m-%d")

    print("SPY PRICE HISTORY")
    print(f"  Date range        : {first_date} to {last_date}")
    print(f"  Trading days      : {len(prices)}")
    print(f"  First close       : ${prices['spy_close'].iloc[0]:.2f}")
    print(f"  Last close        : ${prices['spy_close'].iloc[-1]:.2f}")
    print(f"  Min / max close   : ${prices['spy_close'].min():.2f} / "
          f"${prices['spy_close'].max():.2f}")


def summarise_options_chain(full_chain, single_expiry_chain, smile, reference_date,
                            expiry_date):
    """
    Print a compact human-readable summary of the option chain snapshot.

    Inputs:
        full_chain (DataFrame):          all expiries for the reference date.
        single_expiry_chain (DataFrame): the reference expiry only.
        smile (DataFrame):               the filtered OTM smile.
        reference_date (str):            the quote date.
        expiry_date (str):               the reference expiry.

    Returns:
        None. Prints to stdout.
    """
    spot = single_expiry_chain["underlying_price"].iloc[0]
    expiries = full_chain["expiry_date"].dt.strftime("%Y-%m-%d").unique()

    print("OPTION CHAIN SNAPSHOT")
    print(f"  Reference date    : {reference_date}")
    print(f"  SPY spot          : ${spot:.2f}")
    print(f"  Expiries in snap  : {len(expiries)}")
    print(f"  Reference expiry  : {expiry_date}")
    print(f"  Strikes at expiry : {len(single_expiry_chain)} "
          f"(${single_expiry_chain['strike'].min():.0f} to "
          f"${single_expiry_chain['strike'].max():.0f})")
    print(f"  Strikes in smile  : {len(smile)} after OTM and quality filters "
          f"({(smile['option_type'] == 'put').sum()} puts, "
          f"{(smile['option_type'] == 'call').sum()} calls)")
    print(f"  Smile IV range    : {smile['market_iv'].min():.2%} to "
          f"{smile['market_iv'].max():.2%}")

    # Print the full list of expiries in wrapped rows so the reader can see what else was
    # available on the day -- useful when choosing a second expiry for a portfolio later.
    print("  All expiries      :")
    for start in range(0, len(expiries), 6):
        print("      " + "  ".join(expiries[start:start + 6]))
