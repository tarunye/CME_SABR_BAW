"""
Databento cost check for a potential 2024-2026 second reference period.

COST-ESTIMATION ONLY. This script calls Historical.metadata.get_cost, which is a
free metadata endpoint and does not incur any charge. It does not stream data and
it does not submit batch jobs.

Two checks, both for a single representative trading day:
  1. cbbo-1m over a tight window before the 16:00 ET close
  2. definition over the full 24h day (Databento docs: definition cost is only
     accurate in 24-hour multiples, so this one is deliberately not narrowed)
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import databento as db

DATASET = "OPRA.PILLAR"
SYMBOLS = "SPY.OPT"       # OPRA parent symbology: {underlying}.OPT
STYPE_IN = "parent"
REF_DAY = "2024-06-17"    # a mid-year Monday

CLOSE_MINUTES = 3         # minutes before the close to price

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def close_window_utc(day: str, minutes_before: int):
    """The last `minutes_before` minutes before the 16:00 ET equity close, in UTC.

    zoneinfo resolves whether the date falls in EDT (UTC-4) or EST (UTC-5), so the
    UTC hour is derived rather than hardcoded.
    """
    d = datetime.strptime(day, "%Y-%m-%d").date()
    close_et = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
    start_et = close_et - timedelta(minutes=minutes_before)
    return start_et.astimezone(UTC), close_et.astimezone(UTC), close_et.utcoffset()


def full_day_utc(day: str):
    """Midnight-to-midnight UTC for the given date (definition is billed in 24h units)."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def main() -> int:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("DATABENTO_API_KEY is not set in this environment.", file=sys.stderr)
        return 1

    client = db.Historical(key=key)

    start_utc, end_utc, offset = close_window_utc(REF_DAY, CLOSE_MINUTES)
    dst = "EDT (DST in effect)" if offset == timedelta(hours=-4) else "EST (no DST)"

    print("=" * 72)
    print("DATABENTO COST ESTIMATE  —  metadata.get_cost only, no data pulled")
    print("=" * 72)
    print(f"dataset   : {DATASET}")
    print(f"symbols   : {SYMBOLS}   (stype_in={STYPE_IN})")
    print(f"ref day   : {REF_DAY}")
    print(f"tz        : 16:00 ET on this date is {dst}")
    print()

    # ---- 1. CBBO-1m, tight pre-close window --------------------------------
    print("-" * 72)
    print(f"[1] cbbo-1m  —  last {CLOSE_MINUTES} minutes before the 16:00 ET close")
    print("-" * 72)
    print(f"    window ET  : {start_utc.astimezone(ET):%Y-%m-%d %H:%M:%S %Z}"
          f"  ->  {end_utc.astimezone(ET):%H:%M:%S %Z}")
    print(f"    window UTC : {start_utc:%Y-%m-%dT%H:%M:%S}"
          f"  ->  {end_utc:%Y-%m-%dT%H:%M:%S}")

    cbbo_cost = client.metadata.get_cost(
        dataset=DATASET,
        start=start_utc,
        end=end_utc,
        symbols=SYMBOLS,
        schema="cbbo-1m",
        stype_in=STYPE_IN,
    )
    print(f"    COST       : ${cbbo_cost:.6f}")
    print()

    # ---- 2. definition, full 24h day ---------------------------------------
    print("-" * 72)
    print("[2] definition  —  full 24h day (not narrowed; billed in 24h multiples)")
    print("-" * 72)
    def_start, def_end = full_day_utc(REF_DAY)
    print(f"    window UTC : {def_start:%Y-%m-%dT%H:%M:%S}"
          f"  ->  {def_end:%Y-%m-%dT%H:%M:%S}")

    def_cost = client.metadata.get_cost(
        dataset=DATASET,
        start=def_start,
        end=def_end,
        symbols=SYMBOLS,
        schema="definition",
        stype_in=STYPE_IN,
    )
    print(f"    COST       : ${def_cost:.6f}")
    print()

    # ---- 3. scale up --------------------------------------------------------
    print("=" * 72)
    print("SCALED ESTIMATES")
    print("=" * 72)
    print(f"  single-day cbbo-1m ({CLOSE_MINUTES}m window) : ${cbbo_cost:>12.4f}")
    print(f"  single-day definition (full day)         : ${def_cost:>12.4f}")
    print()

    days_per_year = 250
    cbbo_year = cbbo_cost * days_per_year
    print(f"  cbbo-1m x {days_per_year} trading days           : ${cbbo_year:>12.2f}  / year")
    print()
    print("  definition, pulled a handful of times (static instrument reference):")
    for n in (4, 6, 12):
        print(f"    {n:>2} pulls/yr                            : ${def_cost * n:>12.2f}  / year")
    print()

    print("  FULL 2024-2026 TOTALS (3 calendar years):")
    for n in (4, 6, 12):
        annual = cbbo_year + def_cost * n
        print(f"    defn {n:>2}x/yr  ->  ${annual:>10.2f} / yr"
              f"   ->  3-yr total ${annual * 3:>11.2f}")
    print()
    print("NOTE: 2026 is only complete through today's date, so a true 2024-2026")
    print("      pull is ~2.7 years, not 3.0. The 3-yr figures above are an upper bound.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
