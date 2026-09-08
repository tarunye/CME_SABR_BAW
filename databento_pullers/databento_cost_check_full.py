"""
Databento cost check, part 2: full-range quote + sampled per-day windows.

COST-ESTIMATION ONLY. Calls metadata.get_dataset_range and metadata.get_cost,
both free metadata endpoints. No data is streamed, no batch job is submitted.

Answers two questions the single-day check could not:
  A. What does one contiguous 2024-2026 pull cost (all hours, everything)?
  B. Does per-day cost grow across the period as the SPY chain grows?
     (Sampled quarterly, using the same 3-minute pre-close window as check 1.)
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import databento as db

DATASET = "OPRA.PILLAR"
SYMBOLS = "SPY.OPT"
STYPE_IN = "parent"
CLOSE_MINUTES = 3

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Mid-quarter weekdays, deliberately avoiding month-end, quad-witching and
# US market holidays, so each is an ordinary session.
SAMPLE_DAYS = [
    "2024-01-17", "2024-04-17", "2024-07-17", "2024-10-16",
    "2025-01-15", "2025-04-16", "2025-07-16", "2025-10-15",
    "2026-01-14", "2026-04-15", "2026-07-15",
]


def close_window_utc(day: str, minutes_before: int):
    d = datetime.strptime(day, "%Y-%m-%d").date()
    close_et = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
    start_et = close_et - timedelta(minutes=minutes_before)
    return start_et.astimezone(UTC), close_et.astimezone(UTC)


def main() -> int:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("DATABENTO_API_KEY is not set.", file=sys.stderr)
        return 1

    client = db.Historical(key=key)

    print("=" * 78)
    print("DATABENTO COST ESTIMATE, PART 2  —  metadata endpoints only, no data pulled")
    print("=" * 78)

    # ---- available range ----------------------------------------------------
    rng = client.metadata.get_dataset_range(dataset=DATASET)
    print(f"{DATASET} available range: {rng}")
    print()

    avail_end = str(rng.get("end", ""))[:10]
    start_day = "2024-01-01"
    end_day = min("2026-09-07", avail_end) if avail_end else "2026-09-07"
    print(f"Pricing range: {start_day}  ->  {end_day}  (exclusive end)")
    print()

    # ---- A. one contiguous full-range pull, ALL hours -----------------------
    print("-" * 78)
    print("[A] ONE CONTIGUOUS PULL, ALL HOURS  (not just the pre-close window)")
    print("-" * 78)

    full_cbbo = client.metadata.get_cost(
        dataset=DATASET, start=start_day, end=end_day,
        symbols=SYMBOLS, schema="cbbo-1m", stype_in=STYPE_IN,
    )
    print(f"    cbbo-1m, entire range, every hour : ${full_cbbo:,.2f}")

    full_def = client.metadata.get_cost(
        dataset=DATASET, start=start_day, end=end_day,
        symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN,
    )
    print(f"    definition, entire range          : ${full_def:,.2f}")
    print(f"    COMBINED                          : ${full_cbbo + full_def:,.2f}")
    print()

    # ---- B. sampled per-day pre-close windows -------------------------------
    print("-" * 78)
    print(f"[B] PER-DAY COST, {CLOSE_MINUTES}-MIN PRE-CLOSE WINDOW, SAMPLED QUARTERLY")
    print("-" * 78)
    print(f"    {'date':<12} {'weekday':<10} {'UTC window':<20} {'cbbo-1m cost':>14}")

    per_day = []
    for day in SAMPLE_DAYS:
        s, e = close_window_utc(day, CLOSE_MINUTES)
        if s.astimezone(ET).weekday() >= 5:
            print(f"    {day:<12} WEEKEND — skipped")
            continue
        c = client.metadata.get_cost(
            dataset=DATASET, start=s, end=e,
            symbols=SYMBOLS, schema="cbbo-1m", stype_in=STYPE_IN,
        )
        per_day.append((day, c))
        wd = s.astimezone(ET).strftime("%a")
        win = f"{s:%H:%M}-{e:%H:%M} UTC"
        print(f"    {day:<12} {wd:<10} {win:<20} ${c:>13.6f}")

    if not per_day:
        print("    no samples returned")
        return 1

    print()
    costs = [c for _, c in per_day]
    first_year = [c for d, c in per_day if d.startswith("2024")]
    last_year = [c for d, c in per_day if d.startswith("2026")]
    mean = sum(costs) / len(costs)

    print(f"    mean per-day    : ${mean:.6f}")
    print(f"    min / max       : ${min(costs):.6f} / ${max(costs):.6f}")
    if first_year and last_year:
        m24 = sum(first_year) / len(first_year)
        m26 = sum(last_year) / len(last_year)
        print(f"    2024 mean       : ${m24:.6f}")
        print(f"    2026 mean       : ${m26:.6f}")
        growth = (m26 / m24 - 1) * 100 if m24 else float("nan")
        print(f"    chain growth    : {growth:+.1f}%  (2026 vs 2024 per-day cost)")
    print()

    # ---- C. daily-snapshot approach, costed off the samples ------------------
    print("-" * 78)
    print("[C] DAILY-SNAPSHOT APPROACH, SCALED FROM THE SAMPLED MEAN")
    print("-" * 78)
    days_per_year = 250
    years = 2.68  # 2024-01-01 to 2026-09-07
    total_days = int(days_per_year * years)
    cbbo_total = mean * total_days
    print(f"    trading days 2024-01-01 -> {end_day} : ~{total_days}")
    print(f"    cbbo-1m  {total_days} x ${mean:.6f}        : ${cbbo_total:,.2f}")
    # definition priced per single day, re-measured here:
    d0s = datetime(2024, 6, 17, tzinfo=UTC)
    def_day = client.metadata.get_cost(
        dataset=DATASET, start=d0s, end=d0s + timedelta(days=1),
        symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN,
    )
    print(f"    definition, single day               : ${def_day:.6f}")
    for n in (4, 12):
        pulls = int(n * years)
        print(f"    definition x {n}/yr ({pulls} pulls total)     : ${def_day * pulls:,.2f}")
    print()
    for n in (4, 12):
        pulls = int(n * years)
        print(f"    TOTAL 2024-2026, defn {n:>2}x/yr        : ${cbbo_total + def_day * pulls:,.2f}")
    print()
    print("=" * 78)
    print("COMPARISON")
    print("=" * 78)
    print(f"    [A] pull everything, all hours       : ${full_cbbo + full_def:,.2f}")
    print(f"    [C] daily pre-close snapshots only   : ${cbbo_total + def_day * int(4*years):,.2f}"
          f"  -  ${cbbo_total + def_day * int(12*years):,.2f}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
