"""
Databento cost check for a proposed 2025-06-25 -> 2026-06-25 reference period.

COST-ESTIMATION ONLY. metadata.get_cost / get_dataset_range are free endpoints.
No data is streamed and no batch job is submitted.

Trading-day count is taken from data_loader.nyse_trading_days (the project's own
derived NYSE calendar) rather than assuming 250.
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


import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import databento as db

from data_loader import nyse_trading_days

DATASET = "OPRA.PILLAR"
SYMBOLS = "SPY.OPT"
STYPE_IN = "parent"
CLOSE_MINUTES = 3

RANGE_START = "2025-06-25"
RANGE_END = "2026-06-25"

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# One ordinary mid-quarter Wednesday per quarter of the range. Chosen away from
# month-end and away from quad-witching (3rd Friday of Mar/Jun/Sep/Dec).
SAMPLE_DAYS = ["2025-08-13", "2025-11-12", "2026-02-11", "2026-05-13"]

DEFINITION_DAY = "2025-08-13"


def close_window_utc(day: str, minutes_before: int):
    """Last `minutes_before` minutes before the 16:00 ET close, converted to UTC.

    zoneinfo resolves EDT vs EST per date; the UTC hour is never hardcoded.
    """
    d = datetime.strptime(day, "%Y-%m-%d").date()
    close_et = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
    start_et = close_et - timedelta(minutes=minutes_before)
    return start_et.astimezone(UTC), close_et.astimezone(UTC), close_et.utcoffset()


def main() -> int:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("DATABENTO_API_KEY is not set.", file=sys.stderr)
        return 1

    client = db.Historical(key=key)

    print("=" * 78)
    print(f"DATABENTO COST ESTIMATE  —  {RANGE_START} to {RANGE_END}")
    print("metadata.get_cost only. No data pulled, no batch jobs.")
    print("=" * 78)

    rng = client.metadata.get_dataset_range(dataset=DATASET)
    cbbo_avail = rng["schema"]["cbbo-1m"]
    print(f"cbbo-1m availability : {str(cbbo_avail['start'])[:10]} -> {str(cbbo_avail['end'])[:10]}")
    avail_end = str(cbbo_avail["end"])[:10]
    if RANGE_END > avail_end:
        print(f"  !! requested end {RANGE_END} is BEYOND available data ({avail_end})")
    print()

    # ---- 1. quarterly sampled per-day cbbo-1m -------------------------------
    print("-" * 78)
    print(f"[1] cbbo-1m PER-DAY, {CLOSE_MINUTES}-MIN PRE-CLOSE WINDOW, ONE DAY PER QUARTER")
    print("-" * 78)
    print(f"    {'date':<12} {'day':<5} {'tz':<5} {'UTC window':<18} {'cost':>12}")

    per_day = []
    for day in SAMPLE_DAYS:
        s, e, off = close_window_utc(day, CLOSE_MINUTES)
        tz = "EDT" if off == timedelta(hours=-4) else "EST"
        is_open = len(nyse_trading_days(day, day)) > 0
        if not is_open:
            print(f"    {day:<12} NOT AN NYSE TRADING DAY — skipped")
            continue
        c = client.metadata.get_cost(
            dataset=DATASET, start=s, end=e,
            symbols=SYMBOLS, schema="cbbo-1m", stype_in=STYPE_IN,
        )
        per_day.append((day, c))
        wd = s.astimezone(ET).strftime("%a")
        print(f"    {day:<12} {wd:<5} {tz:<5} {f'{s:%H:%M}-{e:%H:%M}':<18} ${c:>11.6f}")

    if not per_day:
        print("    no samples returned")
        return 1

    costs = [c for _, c in per_day]
    mean = sum(costs) / len(costs)
    print()
    print(f"    mean : ${mean:.6f}")
    print(f"    range: ${min(costs):.6f}  ->  ${max(costs):.6f}"
          f"   (spread {(max(costs)/min(costs)-1)*100:+.1f}%)")
    print()

    # ---- 2. definition, one full unnarrowed day -----------------------------
    print("-" * 78)
    print("[2] definition, ONE FULL 24h DAY (unnarrowed)")
    print("-" * 78)
    d = datetime.strptime(DEFINITION_DAY, "%Y-%m-%d").replace(tzinfo=UTC)
    def_day = client.metadata.get_cost(
        dataset=DATASET, start=d, end=d + timedelta(days=1),
        symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN,
    )
    print(f"    {DEFINITION_DAY}  00:00 -> 24:00 UTC : ${def_day:.6f}")
    print()

    # ---- 3. scale cbbo across the real NYSE trading-day count ---------------
    print("-" * 78)
    print("[3] cbbo-1m SCALED OVER THE REAL NYSE TRADING-DAY COUNT")
    print("-" * 78)
    tdays = nyse_trading_days(RANGE_START, RANGE_END)
    n_days = len(tdays)
    cbbo_total = mean * n_days
    print(f"    nyse_trading_days('{RANGE_START}', '{RANGE_END}')")
    print(f"    trading days : {n_days}   (not the assumed 250)")
    print(f"    first / last : {tdays[0]:%Y-%m-%d} / {tdays[-1]:%Y-%m-%d}")
    print(f"    {n_days} x ${mean:.6f} = ${cbbo_total:,.2f}")
    print()

    # ---- 4. definition at 4-12 pulls ----------------------------------------
    print("-" * 78)
    print("[4] definition AT 4-12 PULLS ACROSS THE YEAR (not daily)")
    print("-" * 78)
    def_costs = {}
    for n in (4, 6, 12):
        def_costs[n] = def_day * n
        print(f"    {n:>2} pulls x ${def_day:.6f} = ${def_costs[n]:.4f}")
    print()

    print("    COMBINED SCALED ESTIMATE (daily pre-close snapshots):")
    for n in (4, 6, 12):
        print(f"      cbbo {n_days}d + defn {n:>2}x : ${cbbo_total + def_costs[n]:,.2f}")
    print()

    # ---- 5. one authoritative full-range call, both schemas -----------------
    print("-" * 78)
    print("[5] SINGLE FULL-RANGE get_cost CALL, BOTH SCHEMAS")
    print("    NOTE: a contiguous range prices EVERY HOUR of every day, not just the")
    print("    pre-close window. This is a different, much larger pull than [3]+[4].")
    print("-" * 78)
    full_cbbo = client.metadata.get_cost(
        dataset=DATASET, start=RANGE_START, end=RANGE_END,
        symbols=SYMBOLS, schema="cbbo-1m", stype_in=STYPE_IN,
    )
    full_def = client.metadata.get_cost(
        dataset=DATASET, start=RANGE_START, end=RANGE_END,
        symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN,
    )
    print(f"    cbbo-1m,    {RANGE_START} -> {RANGE_END}, all hours : ${full_cbbo:,.2f}")
    print(f"    definition, {RANGE_START} -> {RANGE_END}            : ${full_def:,.2f}")
    print(f"    COMBINED                                            : ${full_cbbo + full_def:,.2f}")
    print()

    # ---- summary ------------------------------------------------------------
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  single-day cbbo-1m, mean of {len(per_day)} quarterly samples : ${mean:.6f}")
    print(f"  single-day definition (full 24h)                    : ${def_day:.6f}")
    print(f"  cbbo-1m scaled over {n_days} NYSE trading days         : ${cbbo_total:,.2f}")
    print(f"  definition, 4-12 pulls                              : "
          f"${def_costs[4]:.4f} - ${def_costs[12]:.4f}")
    print()
    print(f"  ==> DAILY-SNAPSHOT TOTAL   : ${cbbo_total + def_costs[4]:,.2f}"
          f"  -  ${cbbo_total + def_costs[12]:,.2f}")
    print(f"  ==> PULL-EVERYTHING TOTAL  : ${full_cbbo + full_def:,.2f}"
          f"   ({(full_cbbo + full_def) / (cbbo_total + def_costs[4]):.1f}x more)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
