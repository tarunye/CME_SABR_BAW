"""Full sanity check of the combined Databento pull, 2025-01-01 -> 2026-06-01.

Read-only. Decodes every downloaded CBBO file and every definition file, checks
coverage, content, window correctness and data quality, and prints a pass/fail
summary. Nothing is joined into the pipeline's schema here.
"""

import os as _os
import sys as _sys

# These modules live in databento_pullers/ but import the pipeline's own code
# (data_loader, baw) from the repository root, and write under <root>/data.
# Put both directories on the path and anchor output paths to the root, so the
# scripts behave identically whatever directory they are run from.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import glob
import os
from collections import defaultdict
from datetime import timedelta

import numpy as np
import pandas as pd
import databento as db

import databento_pull_2025_2026 as P

from databento_cost_check_2025 import ET
UTC = P.UTC
FAILS, WARNS = [], []


def fail(msg):
    FAILS.append(msg)
    print(f"   FAIL  {msg}")


def warn(msg):
    WARNS.append(msg)
    print(f"   WARN  {msg}")


def ok(msg):
    print(f"   ok    {msg}")


def head(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def cbbo_path(day):
    fs = glob.glob(f"{P.CBBO_DIR}/{day}/**/*.dbn.zst", recursive=True)
    return fs[0] if fs else None


def expected_window(day):
    """The UTC window this day's snapshot should occupy."""
    if day in P.EARLY_CLOSE_DAYS:
        return P.early_close_window_utc(day, P.CLOSE_MINUTES)
    s, e, _ = P.close_window_utc(day, P.CLOSE_MINUTES)
    return s, e


def main():
    days = P.trading_days()
    print(f"Combined range : {P.RANGE_START} -> {P.RANGE_END}")
    print(f"Trading days   : {len(days)}  ({days[0]} .. {days[-1]})")

    # ---------------------------------------------------------------- 3. COVERAGE
    head("3. COVERAGE ACROSS THE FULL COMBINED PERIOD")
    missing = [d for d in days if cbbo_path(d) is None]
    if missing:
        fail(f"{len(missing)} trading day(s) with no CBBO file: {missing}")
    else:
        ok(f"all {len(days)} trading days have a CBBO file")

    # boundary between the original pull and the extension
    boundary = [d for d in days if "2025-04-28" <= d <= "2025-05-05"]
    have = [d for d in boundary if cbbo_path(d)]
    if len(have) == len(boundary):
        ok(f"pull boundary contiguous: {' '.join(boundary)}")
    else:
        fail(f"gap at pull boundary: missing {set(boundary) - set(have)}")

    dbn_files = glob.glob(f"{P.CBBO_DIR}/**/*.dbn.zst", recursive=True)
    defn_files = sorted(glob.glob(f"{P.DEFN_DIR}/*.dbn.zst"))
    dup_days = [d for d in days
                if len(glob.glob(f"{P.CBBO_DIR}/{d}/**/*.dbn.zst", recursive=True)) > 1]
    if dup_days:
        warn(f"{len(dup_days)} day(s) hold more than one CBBO file: {dup_days[:5]}")
    else:
        ok(f"exactly one CBBO file per day ({len(dbn_files)} files)")

    # ------------------------------------------------------- 1b. DEFINITIONS DECODE
    head("1b. DEFINITION FILES DECODE (all, old + new)")
    defn_frames = {}
    need = ["instrument_id", "raw_symbol", "instrument_class", "strike_price", "expiration"]
    for f in defn_files:
        tag = os.path.basename(f).replace("definition_", "").replace(".dbn.zst", "")
        try:
            d = db.DBNStore.from_file(f).to_df()
        except Exception as exc:
            fail(f"{tag}: could not decode ({exc!r})")
            continue
        miss = [c for c in need if c not in d.columns]
        if miss:
            fail(f"{tag}: missing fields {miss}")
            continue
        bad_strike = int((~np.isfinite(d["strike_price"])).sum() + (d["strike_price"] <= 0).sum())
        classes = set(d["instrument_class"].unique())
        non_spy = int((~d["raw_symbol"].str.strip().str.startswith("SPY")).sum())
        if bad_strike:
            fail(f"{tag}: {bad_strike} non-positive/non-finite strikes")
        if not classes <= {"C", "P"}:
            fail(f"{tag}: unexpected instrument_class values {classes}")
        if non_spy:
            fail(f"{tag}: {non_spy} instruments whose symbol is not SPY")
        defn_frames[tag] = d
        ok(f"{tag}: {len(d):>6} instruments, classes {sorted(classes)}, "
           f"strikes ${d['strike_price'].min():.0f}-${d['strike_price'].max():.0f}, "
           f"{d['expiration'].dt.date.nunique()} expiries")

    # merged definition lookup across all anchors
    alldefs = pd.concat(defn_frames.values()).drop_duplicates("instrument_id")
    alldefs = alldefs.set_index("instrument_id")
    ok(f"merged definition lookup: {len(alldefs):,} unique instrument_ids")

    # ------------------------------------------------- 1a/2/4 FULL SWEEP OF CBBO
    head("1a / 2 / 4. CBBO DECODE, CONTENT, WINDOW AND QUALITY (every day)")
    need_cols = ["instrument_id", "bid_px_00", "ask_px_00"]
    tot_rows = tot_cross = tot_dup = 0
    tot_bid_na = tot_ask_na = tot_both_na = 0
    tot_nonpos = 0
    win_bad, col_bad, decode_bad = [], [], []
    per_day = {}

    for i, day in enumerate(days):
        f = cbbo_path(day)
        if f is None:
            continue
        try:
            df = db.DBNStore.from_file(f).to_df()
        except Exception as exc:
            decode_bad.append((day, repr(exc)))
            continue

        miss = [c for c in need_cols if c not in df.columns]
        if miss or df.index.name != "ts_recv":
            col_bad.append((day, miss, df.index.name))
            continue

        n = len(df)
        tot_rows += n

        # window: every ts_recv must sit inside the intended pre-close window
        ws, we = expected_window(day)
        idx = df.index
        outside = int(((idx < ws) | (idx >= we)).sum())
        if outside:
            win_bad.append((day, outside, str(idx.min()), str(idx.max())))

        # duplicates: one row per instrument per minute bar
        dup = int(df.duplicated(subset=["instrument_id"], keep=False).sum()
                  - df.groupby(level=0)["instrument_id"].nunique().sum()
                  + df.index.nunique() * 0)
        dupes = int(len(df) - len(df.reset_index()[["ts_recv", "instrument_id"]]
                                  .drop_duplicates()))
        tot_dup += dupes

        bid, ask = df["bid_px_00"], df["ask_px_00"]
        bna, ana = int(bid.isna().sum()), int(ask.isna().sum())
        both = int((bid.isna() & ask.isna()).sum())
        tot_bid_na += bna; tot_ask_na += ana; tot_both_na += both
        two = bid.notna() & ask.notna()
        crossed = int((bid[two] > ask[two]).sum())
        tot_cross += crossed
        nonpos = int((ask[ask.notna()] <= 0).sum() + (bid[bid.notna()] < 0).sum())
        tot_nonpos += nonpos

        per_day[day] = dict(rows=n, inst=df["instrument_id"].nunique(),
                            bars=idx.nunique(), crossed=crossed, dupes=dupes,
                            two_sided=int(two.sum()))
        if (i + 1) % 60 == 0:
            print(f"   ... swept {i+1}/{len(days)} days")

    if decode_bad:
        fail(f"{len(decode_bad)} file(s) failed to decode: {decode_bad[:3]}")
    else:
        ok(f"all {len(per_day)} CBBO files decoded")
    if col_bad:
        fail(f"{len(col_bad)} file(s) missing expected fields/index: {col_bad[:3]}")
    else:
        ok("bid_px_00, ask_px_00, instrument_id present; ts_recv is the index, everywhere")
    if win_bad:
        fail(f"{len(win_bad)} day(s) with timestamps outside the intended window: {win_bad[:5]}")
    else:
        ok("every timestamp on every day falls inside its intended pre-close window")
    if tot_dup:
        fail(f"{tot_dup} duplicate (ts_recv, instrument_id) rows across the range")
    else:
        ok("no duplicate (ts_recv, instrument_id) rows anywhere")
    if tot_cross:
        warn(f"{tot_cross} crossed quotes (bid > ask) out of {tot_rows:,} rows "
             f"({tot_cross/tot_rows:.4%})")
    else:
        ok("no crossed quotes anywhere")
    if tot_nonpos:
        warn(f"{tot_nonpos} rows with non-positive ask or negative bid")
    else:
        ok("no negative bids or non-positive asks")

    print(f"\n   total rows swept        : {tot_rows:,}")
    print(f"   rows with no bid quote  : {tot_bid_na:,} ({tot_bid_na/tot_rows:.2%})")
    print(f"   rows with no ask quote  : {tot_ask_na:,} ({tot_ask_na/tot_rows:.2%})")
    print(f"   rows with neither side  : {tot_both_na:,} ({tot_both_na/tot_rows:.2%})")
    print("   (a missing side is 'no market' on an illiquid strike, not corruption)")

    bars = {d: v["bars"] for d, v in per_day.items()}
    odd = {d: b for d, b in bars.items() if b != P.CLOSE_MINUTES}
    if odd:
        warn(f"{len(odd)} day(s) without exactly {P.CLOSE_MINUTES} minute bars: "
             f"{list(odd.items())[:5]}")
    else:
        ok(f"every day carries exactly {P.CLOSE_MINUTES} minute bars")

    # ------------------------------------------------- 2. JOIN TO DEFINITIONS
    head("2. CONTENT: JOIN TO DEFINITIONS ON SAMPLE DAYS ACROSS THE FULL RANGE")
    sample = [days[0], "2025-03-03", "2025-04-30", "2025-05-01",
              days[len(days)//2], "2025-12-24", "2026-03-02", days[-1]]
    sample = [d for d in dict.fromkeys(sample) if d in per_day]
    for day in sample:
        df = db.DBNStore.from_file(cbbo_path(day)).to_df()
        j = df.join(alldefs[["raw_symbol", "instrument_class", "strike_price", "expiration"]],
                    on="instrument_id", how="left")
        unmatched = int(j["raw_symbol"].isna().sum())
        m = j[j["raw_symbol"].notna()]
        non_spy = int((~m["raw_symbol"].str.strip().str.startswith("SPY")).sum())
        classes = sorted(set(m["instrument_class"].dropna().unique()))
        two = m["bid_px_00"].notna() & m["ask_px_00"].notna()
        tag = " (EARLY CLOSE)" if day in P.EARLY_CLOSE_DAYS else ""
        print(f"\n   {day}{tag}")
        print(f"     rows {len(df):,} | matched to definitions {len(m):,} "
              f"({len(m)/len(df):.1%}) | unmatched {unmatched:,}")
        if len(m):
            print(f"     strikes ${m['strike_price'].min():.0f}-${m['strike_price'].max():.0f} "
                  f"({m['strike_price'].nunique()} distinct) | "
                  f"expiries {m['expiration'].dt.date.nunique()} "
                  f"({m['expiration'].dt.date.min()} .. {m['expiration'].dt.date.max()})")
            print(f"     option types {classes} | two-sided quotes {int(two.sum()):,} "
                  f"({two.mean():.1%})")
            if two.sum():
                bb, aa = m.loc[two, "bid_px_00"], m.loc[two, "ask_px_00"]
                print(f"     bid range ${bb.min():.2f}-${bb.max():.2f} | "
                      f"spread median ${(aa-bb).median():.2f}")
        if non_spy:
            fail(f"{day}: {non_spy} matched instruments are not SPY")
        if classes and set(classes) - {"C", "P"}:
            fail(f"{day}: unexpected option types {classes}")
        if len(m) and len(m) / len(df) < 0.80:
            warn(f"{day}: only {len(m)/len(df):.1%} of rows matched a definition "
                 f"(definitions are quarterly snapshots; newly listed strikes can miss)")

    # ------------------------------------------------- 3b. EARLY CLOSES
    head("3b. EARLY-CLOSE DAYS ACROSS THE FULL PERIOD")
    ec_in_range = sorted(d for d in P.EARLY_CLOSE_DAYS if d in per_day)
    print(f"   configured early closes in range: {ec_in_range}")
    for d in ec_in_range:
        v = per_day[d]
        ws, we = expected_window(d)
        print(f"     {d}: {v['rows']:,} rows, {v['inst']:,} instruments, "
              f"window {ws:%H:%M}-{we:%H:%M} UTC (13:00 ET)")
    # compare each early close against the median ordinary day
    med = int(np.median([v["rows"] for d, v in per_day.items()
                         if d not in P.EARLY_CLOSE_DAYS]))
    print(f"   median rows on an ordinary day: {med:,}")
    for d in ec_in_range:
        r = per_day[d]["rows"]
        if r < med * 0.4:
            warn(f"{d}: {r:,} rows is unusually thin vs median {med:,}")
        else:
            ok(f"{d}: {r:,} rows, in line with ordinary days")

    # any suspiciously thin day anywhere could be an unhandled short session
    thin = sorted((d for d, v in per_day.items() if v["rows"] < med * 0.55),
                  key=lambda d: per_day[d]["rows"])
    if thin:
        warn(f"{len(thin)} unusually thin day(s) — check for unhandled short sessions:")
        for d in thin[:10]:
            print(f"       {d}: {per_day[d]['rows']:,} rows"
                  f"{'  (known early close)' if d in P.EARLY_CLOSE_DAYS else ''}")
    else:
        ok("no unexpectedly thin days (no unhandled short sessions)")

    # ------------------------------------------------- 3c. CHAIN SIZE
    head("3c. CHAIN SIZE ON SAMPLE DATES ACROSS THE RANGE")
    print(f"   {'date':<12} {'rows':>8} {'instruments':>12} {'two-sided':>10}")
    for d in sample:
        v = per_day[d]
        print(f"   {d:<12} {v['rows']:>8,} {v['inst']:>12,} {v['two_sided']:>10,}")

    # ------------------------------------------------- SUMMARY
    head("SUMMARY")
    print(f"   trading days       : {len(days)}")
    print(f"   days with data     : {len(per_day)}")
    print(f"   total rows         : {tot_rows:,}")
    print(f"   definition files   : {len(defn_frames)}")
    print(f"   FAILURES           : {len(FAILS)}")
    print(f"   WARNINGS           : {len(WARNS)}")
    print()
    if FAILS:
        print("   >>> FAIL")
        for m in FAILS:
            print(f"       - {m}")
    else:
        print("   >>> PASS — no failures")
    if WARNS:
        print("   warnings (not failures):")
        for m in WARNS:
            print(f"       - {m}")
    print("=" * 78)


if __name__ == "__main__":
    main()
