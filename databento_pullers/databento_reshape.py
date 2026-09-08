"""
Reshape the raw Databento pull into the column layout data_loader.py already reads.

Per trading day: join that day's cbbo-1m quotes to that day's instrument
definitions on instrument_id, collapse the three minute bars to one
EOD-equivalent row per contract, and pivot calls and puts side by side into the
vendor's bracketed schema.

Reads only from data/raw_databento_2025_2026/. Writes only to
data/databento_2025_2026/. Touches no existing data and no pipeline code.

    python3 databento_pullers/databento_reshape.py

WHY YEAR FILES IN THEIR OWN FOLDER
----------------------------------
data_loader._read_year_file looks for `spy_eod_YYYY.parquet` inside whatever
`data_dir` it is handed. Writing this dataset under the same names in a separate
folder means the existing loader reads it with no code change at all -- the only
thing that moves is CONFIG['data_dir']. A single differently-named file would
have forced an edit to the loader, which is precisely what this rebuild is
trying to avoid.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import glob
import re

import numpy as np
import pandas as pd
import databento as db
from scipy.special import erf

import databento_pull_2025_2026 as P

OUT_DIR = _os.path.join(_ROOT, "data", "databento_2025_2026")

# Rate used ONLY to discount the parity relation while backing spot out of the
# option quotes below. It is not a pricing input -- Stage C's CONFIG supplies
# the rate the pricer uses. The derived spot is almost completely insensitive to
# it: near the money (C - P) is a few dollars, so a full percentage point of
# error in r moves the implied forward by well under a cent at these tenors.
RESHAPE_RATE = 0.0430

# Expiry tenors used for the spot solve. Too short and the forward is dominated
# by quote noise; too long and the constant-carry assumption stops holding.
MIN_T_DAYS, MAX_T_DAYS = 7, 180
MIN_STRIKES_PER_EXPIRY = 5
MIN_EXPIRIES_FOR_FIT = 3

RAW_COLUMNS = ["QUOTE_DATE", "EXPIRE_DATE", "DTE", "UNDERLYING_LAST", "STRIKE",
               "C_BID", "C_ASK", "C_LAST", "C_IV", "C_VOLUME",
               "P_BID", "P_ASK", "P_LAST", "P_IV", "P_VOLUME"]


def observed_close():
    """SPY's consolidated daily close, one value per session.

    This is what lands in UNDERLYING_LAST. The parity-derived spot below is
    retained as a diagnostic and as a fallback, but the observed close is the
    better input: it takes the model out of the return series that drives the
    VaR entirely, rather than asking the reader to trust that the derivation's
    error is small.

    Measured against this series, the derived spot was good to a median $0.16
    with a return correlation of 0.9954 -- so the switch changes little, but it
    changes it from an argument into a measurement.
    """
    path = _os.path.join(P.EQUITY_DIR,
                         f"spy_ohlcv1d_{P.EQUITY_CLOSE_DATASET.replace('.', '_')}.dbn.zst")
    if not _os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Pull it first:\n"
            f"  python3 databento_pullers/databento_pull_2025_2026.py equities"
        )
    s = db.DBNStore.from_file(path).to_df()["close"]
    s.index = pd.to_datetime(s.index).tz_convert(None).normalize()
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}


def definition_index():
    """Map trade date -> that day's definition file (one file per session)."""
    fs = glob.glob(f"{P.OUT_ROOT}/definitions_fullrange/**/*.dbn.zst", recursive=True)
    out = {}
    for f in fs:
        m = re.search(r"(\d{8})", _os.path.basename(f))
        if m:
            d = m.group(1)
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = f
    return out


def cbbo_path(day):
    fs = glob.glob(f"{P.CBBO_DIR}/{day}/**/*.dbn.zst", recursive=True)
    return fs[0] if fs else None


def day_chain(day, defn_path):
    """One day's quotes, joined to definitions and collapsed to one row per contract.

    The pull captured three one-minute bars ending at the close. We keep each
    contract's LAST bar -- its final observation at or before the close -- which
    is the closest thing this data has to an end-of-day snapshot. A contract
    quoted only in an earlier bar keeps that earlier quote rather than being
    dropped, which is why this is a per-instrument last rather than a filter on
    the final timestamp.
    """
    q = db.DBNStore.from_file(cbbo_path(day)).to_df()
    d = db.DBNStore.from_file(defn_path).to_df()
    d = d.drop_duplicates("instrument_id").set_index("instrument_id")

    q = q.sort_index()                                   # ts_recv ascending
    q = q.groupby("instrument_id", sort=False).tail(1)   # last bar per contract

    j = q.join(d[["strike_price", "expiration", "instrument_class"]],
               on="instrument_id", how="inner")
    j = j.rename(columns={"strike_price": "strike",
                          "expiration": "expiry",
                          "bid_px_00": "bid", "ask_px_00": "ask"})
    j["expiry"] = j["expiry"].dt.tz_convert(None).dt.normalize()
    j = j[["strike", "expiry", "instrument_class", "bid", "ask"]]

    calls = (j[j["instrument_class"] == "C"]
             .drop(columns="instrument_class")
             .rename(columns={"bid": "C_BID", "ask": "C_ASK"})
             .drop_duplicates(["expiry", "strike"]))
    puts = (j[j["instrument_class"] == "P"]
            .drop(columns="instrument_class")
            .rename(columns={"bid": "P_BID", "ask": "P_ASK"})
            .drop_duplicates(["expiry", "strike"]))

    chain = calls.merge(puts, on=["expiry", "strike"], how="outer")
    return chain.sort_values(["expiry", "strike"]).reset_index(drop=True)


def implied_spot(chain, quote_date):
    """Back the underlying out of the option quotes by term structure of forwards.

    OPRA carries no underlying price, so spot has to come from the options
    themselves. Put-call parity gives a forward per expiry:

        F = K + (C - P) / exp(-rT)

    and the forwards across expiries satisfy F(T) = S * exp(bT). Regressing
    log F on T therefore yields BOTH unknowns at once -- the intercept is log S
    and the slope is the cost of carry -- without having to assume a dividend
    yield. Using a single expiry instead would require assuming one.

    Returns (spot, carry, n_expiries_used) or (nan, nan, 0).
    """
    q = pd.Timestamp(quote_date)
    c = chain.dropna(subset=["C_BID", "C_ASK", "P_BID", "P_ASK"]).copy()
    c = c[(c["C_BID"] > 0) & (c["P_BID"] > 0)
          & (c["C_ASK"] > c["C_BID"]) & (c["P_ASK"] > c["P_BID"])]
    if c.empty:
        return np.nan, np.nan, 0

    c["dte"] = (c["expiry"] - q).dt.days
    c = c[(c["dte"] >= MIN_T_DAYS) & (c["dte"] <= MAX_T_DAYS)]
    c["cmid"] = (c["C_BID"] + c["C_ASK"]) / 2.0
    c["pmid"] = (c["P_BID"] + c["P_ASK"]) / 2.0

    ts, logfs = [], []
    for expiry, g in c.groupby("expiry"):
        if len(g) < MIN_STRIKES_PER_EXPIRY:
            continue
        # The at-the-money strike is where the call and put are worth the same;
        # that is where parity is best determined, because both legs are liquid.
        atm = g.loc[(g["cmid"] - g["pmid"]).abs().idxmin(), "strike"]
        near = g[(g["strike"] - atm).abs() / atm <= 0.03]
        if len(near) < MIN_STRIKES_PER_EXPIRY:
            continue
        T = float(near["dte"].iloc[0]) / 365.0
        fwd = near["strike"] + (near["cmid"] - near["pmid"]) * np.exp(RESHAPE_RATE * T)
        f = float(np.median(fwd))
        if f > 0:
            ts.append(T)
            logfs.append(np.log(f))

    if len(ts) < MIN_EXPIRIES_FOR_FIT:
        return np.nan, np.nan, len(ts)
    b, a = np.polyfit(np.asarray(ts), np.asarray(logfs), 1)
    return float(np.exp(a)), float(b), len(ts)


def expiry_forwards(chain, quote_date, spot, carry):
    """A forward per expiry: from parity where the quotes allow, else from carry.

    The IV inversion below needs a forward for EVERY expiry, including the thin
    ones the spot fit deliberately skips. Where parity is well determined we use
    it; otherwise we fall back to S*exp(bT) with the carry fitted for that day,
    which is the same relation the fit itself established.
    """
    q = pd.Timestamp(quote_date)
    out = {}
    c = chain.dropna(subset=["C_BID", "C_ASK", "P_BID", "P_ASK"]).copy()
    if not c.empty:
        c = c[(c["C_BID"] > 0) & (c["P_BID"] > 0)
              & (c["C_ASK"] > c["C_BID"]) & (c["P_ASK"] > c["P_BID"])]
        c["cmid"] = (c["C_BID"] + c["C_ASK"]) / 2.0
        c["pmid"] = (c["P_BID"] + c["P_ASK"]) / 2.0
        for expiry, g in c.groupby("expiry"):
            dte = (expiry - q).days
            if dte <= 0 or len(g) < MIN_STRIKES_PER_EXPIRY:
                continue
            atm = g.loc[(g["cmid"] - g["pmid"]).abs().idxmin(), "strike"]
            near = g[(g["strike"] - atm).abs() / atm <= 0.03]
            if len(near) < MIN_STRIKES_PER_EXPIRY:
                continue
            T = dte / 365.0
            f = float(np.median(near["strike"]
                                + (near["cmid"] - near["pmid"]) * np.exp(RESHAPE_RATE * T)))
            if f > 0:
                out[expiry] = f

    for expiry in chain["expiry"].unique():
        if expiry not in out:
            dte = (pd.Timestamp(expiry) - q).days
            if dte > 0 and np.isfinite(spot) and np.isfinite(carry):
                out[expiry] = float(spot * np.exp(carry * dte / 365.0))
    return out


def _black76(sigma, F, K, T, DF, is_call):
    """European option price on a forward. Vectorised over every argument."""
    v = sigma * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    nd = lambda x: 0.5 * (1.0 + erf(x / np.sqrt(2.0)))
    call = DF * (F * nd(d1) - K * nd(d2))
    put = DF * (K * nd(-d2) - F * nd(-d1))
    return np.where(is_call, call, put)


def black76_iv(price, F, K, T, DF, is_call, lo=1e-4, hi=5.0, iters=64):
    """Invert Black-76 for volatility by vectorised bisection.

    Bisection rather than Newton: option price is monotone in volatility, so a
    bracketed search cannot diverge, and the far wings -- where vega is nearly
    zero and Newton stalls -- are exactly where this data has its penny quotes.
    64 halvings of [1e-4, 5] resolve sigma to about 3e-16.

    Quotes no volatility can reproduce (price below intrinsic, or above the
    forward) return NaN rather than a clipped bound: an unmatchable quote is a
    fact about that strike, not a number to invent.
    """
    price, F, K, T, DF = (np.asarray(x, dtype=float) for x in (price, F, K, T, DF))
    is_call = np.asarray(is_call, dtype=bool)

    intrinsic = np.where(is_call, DF * np.maximum(F - K, 0.0),
                         DF * np.maximum(K - F, 0.0))
    upper = np.where(is_call, DF * F, DF * K)
    ok = (np.isfinite(price) & np.isfinite(F) & (T > 0) & (price > 0)
          & (price > intrinsic + 1e-12) & (price < upper - 1e-12))

    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    for _ in range(iters):
        m = 0.5 * (a + b)
        hit = _black76(m, F, K, T, DF, is_call) < price
        a = np.where(hit, m, a)
        b = np.where(hit, b, m)
    out = 0.5 * (a + b)
    return np.where(ok, out, np.nan)


def add_implied_vols(chain, quote_date, spot, carry):
    """Attach a European Black-76 implied vol to each leg.

    WHY THIS COLUMN EXISTS AT ALL
    -----------------------------
    The pipeline re-implies its own volatilities in Phase 3 by inverting BAW,
    so these values are not the ones any result rests on. But Phase 0 runs
    first, and build_otm_smile filters on the vendor's IV column: NaN fails the
    plausibility test, so an absent column empties the smile and Phase 0 dies
    before Phase 3 can replace anything.

    So this fills the slot the OptionsDX vendor IV used to fill, and does it the
    same way a vendor would -- a European model on the parity forward. It is
    genuinely computed, not a placeholder, and Phase 3 overwrites it exactly as
    it overwrote the vendor's. Being European it carries no early-exercise
    premium, which is precisely the inconsistency Phase 3 exists to remove.
    """
    q = pd.Timestamp(quote_date)
    fwd = expiry_forwards(chain, quote_date, spot, carry)
    F = chain["expiry"].map(fwd).to_numpy(dtype=float)
    dte = (chain["expiry"] - q).dt.days.to_numpy(dtype=float)
    T = np.where(dte > 0, dte / 365.0, np.nan)
    DF = np.exp(-RESHAPE_RATE * T)
    K = chain["strike"].to_numpy(dtype=float)

    out = {}
    for leg, bid, ask, is_call in (("C_IV", "C_BID", "C_ASK", True),
                                   ("P_IV", "P_BID", "P_ASK", False)):
        b_ = chain[bid].to_numpy(dtype=float)
        a_ = chain[ask].to_numpy(dtype=float)
        mid = np.where((b_ > 0) & (a_ > b_), (b_ + a_) / 2.0, np.nan)
        out[leg] = black76_iv(mid, F, K, T, DF, np.full(len(chain), is_call))
    return out["C_IV"], out["P_IV"]


def main():
    _os.makedirs(OUT_DIR, exist_ok=True)
    days = P.trading_days()
    defs = definition_index()
    closes = observed_close()

    frames, diag, fellback = [], [], []
    for i, day in enumerate(days, 1):
        if day not in defs or cbbo_path(day) is None:
            diag.append((day, 0, np.nan, np.nan, np.nan, 0))
            continue
        chain = day_chain(day, defs[day])
        derived, carry, nexp = implied_spot(chain, day)

        # Observed close is authoritative; the derived spot only stands in if a
        # session is missing from the equity feed, which would otherwise leave a
        # hole in the return series.
        spot = closes.get(day)
        if spot is None:
            spot = derived
            fellback.append(day)

        c_iv, p_iv = add_implied_vols(chain, day, spot, carry)

        out = pd.DataFrame({
            "QUOTE_DATE": day,
            "EXPIRE_DATE": chain["expiry"].dt.strftime("%Y-%m-%d"),
            "DTE": (chain["expiry"] - pd.Timestamp(day)).dt.days.astype("float64"),
            "UNDERLYING_LAST": spot,
            "STRIKE": chain["strike"].astype("float64"),
            "C_BID": chain["C_BID"], "C_ASK": chain["C_ASK"],
            "C_LAST": np.nan, "C_IV": c_iv, "C_VOLUME": np.nan,
            "P_BID": chain["P_BID"], "P_ASK": chain["P_ASK"],
            "P_LAST": np.nan, "P_IV": p_iv, "P_VOLUME": np.nan,
        })
        frames.append(out)
        diag.append((day, len(out), spot, derived, carry, nexp))
        if i % 50 == 0:
            print(f"  reshaped {i}/{len(days)} days", flush=True)

    all_rows = pd.concat(frames, ignore_index=True)
    all_rows.columns = [f"[{c}]" for c in RAW_COLUMNS]

    for year, g in all_rows.groupby(all_rows["[QUOTE_DATE]"].str[:4]):
        path = _os.path.join(OUT_DIR, f"spy_eod_{year}.parquet")
        g.reset_index(drop=True).to_parquet(path, index=False)
        print(f"wrote {path}  ({len(g):,} rows, {_os.path.getsize(path)/1e6:.1f} MB)")

    d = pd.DataFrame(diag, columns=["quote_date", "rows", "underlying_last",
                                    "derived_spot", "carry", "n_expiries"])
    d["diff"] = d["underlying_last"] - d["derived_spot"]
    d.to_csv(_os.path.join(OUT_DIR, "reshape_diagnostics.csv"), index=False)
    print(f"wrote diagnostics for {len(d)} sessions")
    print(f"UNDERLYING_LAST source: {P.EQUITY_CLOSE_DATASET} observed close "
          f"({len(d) - len(fellback)}/{len(d)} sessions)")
    if fellback:
        print(f"  fell back to derived spot on {len(fellback)}: {fellback}")
    print(f"  observed - derived: median ${d['diff'].median():+.4f}, "
          f"sd ${d['diff'].std():.4f}, max |${d['diff'].abs().max():.4f}|")
    return all_rows, d


if __name__ == "__main__":
    main()
