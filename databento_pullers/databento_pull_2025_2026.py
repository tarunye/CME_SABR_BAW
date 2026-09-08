"""
Bulk pull of daily EOD-equivalent SPY option quotes, 2025-01-01 -> 2026-06-01.

Pulled in two passes: 2025-05-01 -> 2026-06-01 first, then extended back to
2025-01-01. Both land in the same folder tree, keyed by trade date.

Pull-and-save only. Nothing here joins, reshapes, or touches the existing pipeline
or the 2010-2023 data.

  - trading days come from data_loader.nyse_trading_days (not a fixed count)
  - the 3-minute pre-close UTC window comes from the DST-aware helper validated in
    the earlier cost-check scripts, imported rather than rewritten
  - definitions are pulled quarterly (largely static), cbbo-1m once per trading day
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


import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import databento as db

from data_loader import nyse_trading_days
# The exact DST-handling logic validated in Phase 9; not reimplemented here.
from databento_cost_check_2025 import close_window_utc, CLOSE_MINUTES

DATASET = "OPRA.PILLAR"
SYMBOLS = "SPY.OPT"
STYPE_IN = "parent"
SCHEMA = "cbbo-1m"

RANGE_START = "2025-01-01"
RANGE_END = "2026-06-01"

OUT_ROOT = _os.path.join(_ROOT, "data", "raw_databento_2025_2026")
DEFN_DIR = f"{OUT_ROOT}/definitions"
CBBO_DIR = f"{OUT_ROOT}/cbbo"
JOBS_CSV = f"{OUT_ROOT}/jobs.csv"

SUBMIT_PACE_SECONDS = 3.5          # ~17/min, safely under the 20/min limit

# NYSE EARLY CLOSES (1:00 PM ET, not 4:00 PM).
#
# nyse_trading_days() knows which days the exchange is CLOSED, but not which days
# it closes EARLY -- those are still trading days, just short ones. On these dates
# a 15:57-16:00 ET window sits three hours after the bell and Databento correctly
# returns no data at all. The EOD-equivalent snapshot has to come from the 13:00 ET
# close instead.
#
# The NYSE early-close rule is: 3 July (when Independence Day falls on a weekday),
# the day after Thanksgiving, and Christmas Eve. Listed explicitly rather than
# derived, because the rule has exceptions and a wrong guess silently produces a
# snapshot from a closed market.
EARLY_CLOSE_DAYS = {
    "2025-07-03",   # day before Independence Day
    "2025-11-28",   # day after Thanksgiving
    "2025-12-24",   # Christmas Eve
}
EARLY_CLOSE_HOUR_ET = 13

# ONE-OFF FULL CLOSURES the derived calendar does not know about.
#
# nyse_trading_days() applies the federal holiday rules; it cannot know about
# ad-hoc closures such as state funerals or weather. PROJECT_LOG assumption #6
# flags this class deliberately, so that such days surface for a human rather
# than being silently absorbed. This one surfaced exactly that way: Databento
# rejected 2025-01-09 with symbology_invalid_request -- "none of the symbols
# could be resolved" -- because no SPY option was active at all that day.
#
# Note the error differs from an early close: on a short session the symbols
# resolve and the window is merely empty (data_no_data_found_for_request); on a
# full closure there is no symbology to resolve.
ONE_OFF_CLOSURES = {
    "2025-01-09",   # national day of mourning, President Carter -- NYSE closed
}
UTC = ZoneInfo("UTC")


def client():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("DATABENTO_API_KEY is not set.")
    return db.Historical(key=key)


def trading_days():
    """Sessions the NYSE actually held in the range, in ascending date order.

    The derived calendar is filtered by ONE_OFF_CLOSURES, so a day the exchange
    did not open is not counted as a missing pull.
    """
    days = [d.strftime("%Y-%m-%d") for d in nyse_trading_days(RANGE_START, RANGE_END)]
    return [d for d in days if d not in ONE_OFF_CLOSURES]


def ensure_dirs():
    for d in (OUT_ROOT, DEFN_DIR, CBBO_DIR):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------
# tracking file: written incrementally, one row per submitted job
# --------------------------------------------------------------------------
JOB_FIELDS = ["trade_date", "job_id", "window_start_utc", "window_end_utc",
              "submitted_at_utc", "cost_usd", "state"]


def append_job_row(row):
    new = not os.path.exists(JOBS_CSV)
    with open(JOBS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=JOB_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def load_jobs():
    if not os.path.exists(JOBS_CSV):
        return []
    with open(JOBS_CSV) as f:
        return list(csv.DictReader(f))


def submitted_dates():
    return {r["trade_date"] for r in load_jobs() if r.get("job_id")}


def early_close_window_utc(day, minutes_before):
    """The last `minutes_before` minutes before a 13:00 ET early close, in UTC.

    Same DST-aware construction as close_window_utc, at the early-close hour.
    """
    d = datetime.strptime(day, "%Y-%m-%d").date()
    close_et = datetime(d.year, d.month, d.day, EARLY_CLOSE_HOUR_ET, 0,
                        tzinfo=ZoneInfo("America/New_York"))
    return ((close_et - timedelta(minutes=minutes_before)).astimezone(UTC),
            close_et.astimezone(UTC))


def submit_one(c, day):
    """Submit the cbbo-1m batch job for one trading day's pre-close window."""
    if day in EARLY_CLOSE_DAYS:
        s, e = early_close_window_utc(day, CLOSE_MINUTES)
    else:
        s, e, _ = close_window_utc(day, CLOSE_MINUTES)
    job = c.batch.submit_job(
        dataset=DATASET,
        symbols=SYMBOLS,
        schema=SCHEMA,
        start=s,
        end=e,
        stype_in=STYPE_IN,
        encoding="dbn",
        compression="zstd",
        # DBN is binary; map_symbols is rejected with it. The instrument_id ->
        # strike/expiry/type mapping comes from the definition files instead,
        # which is exactly the join validated in Phase 9.
        map_symbols=False,
        split_duration="day",
    )
    row = {
        "trade_date": day,
        "job_id": job.get("id"),
        "window_start_utc": s.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_end_utc": e.strftime("%Y-%m-%dT%H:%M:%S"),
        "submitted_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "cost_usd": job.get("cost_usd", ""),
        "state": job.get("state", ""),
    }
    append_job_row(row)
    return job, row


def pull_definitions(c, days):
    """Quarterly definition pulls spanning the period. Static-ish, so not daily."""
    ensure_dirs()
    # one anchor day per calendar quarter present in the range
    seen, anchors = set(), []
    for d in days:
        y, m = int(d[:4]), int(d[5:7])
        q = (y, (m - 1) // 3)
        if q not in seen:
            seen.add(q)
            anchors.append(d)
    print(f"definition anchor days ({len(anchors)}): {anchors}")

    total = 0.0
    for day in anchors:
        path = f"{DEFN_DIR}/definition_{day}.dbn.zst"
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  {day}: already present, skipping")
            continue
        d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
        cost = c.metadata.get_cost(dataset=DATASET, start=d0, end=d0 + timedelta(days=1),
                                   symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN)
        data = c.timeseries.get_range(dataset=DATASET, start=d0, end=d0 + timedelta(days=1),
                                      symbols=SYMBOLS, schema="definition", stype_in=STYPE_IN)
        data.to_file(path)
        total += cost
        print(f"  {day}: ${cost:.6f}  ->  {path}  ({os.path.getsize(path):,} bytes)")
    print(f"definition subtotal: ${total:.4f}")
    return total


# ===========================================================================
# EXECUTION
# ===========================================================================
# Everything above is the pull's definition -- calendar, windows, cost, one
# submission. What follows drives it: submit every outstanding session, wait,
# download, verify. These began as throwaway scripts during the actual pull and
# are folded in here so the pull is reproducible from this file alone.
#
#   python3 databento_pullers/databento_pull_2025_2026.py definitions
#   python3 databento_pullers/databento_pull_2025_2026.py submit
#   python3 databento_pullers/databento_pull_2025_2026.py download
#   python3 databento_pullers/databento_pull_2025_2026.py verify
#   python3 databento_pullers/databento_pull_2025_2026.py all
#
# Every step is resumable: `submit` skips sessions already in jobs.csv,
# `download` skips days whose files are already on disk. An interrupted run is
# resumed by running the same command again -- which matters, because a
# half-finished submission that restarted from scratch would be billed twice.

import glob
import json

MAX_ATTEMPTS = 6
BACKOFF = [5, 15, 30, 60, 120, 300]      # seconds between submission retries

DEFN_FULLRANGE_DIR = _os.path.join(OUT_ROOT, "definitions_fullrange")
DEFN_FULLRANGE_JOB = _os.path.join(OUT_ROOT, "definition_fullrange_job.json")


def _is_transient(exc):
    """True for failures worth retrying rather than giving up on.

    A dropped connection should pause the run, not consume the remaining
    sessions -- during the original pull an outage burned through 183 days of
    retries in seconds because this distinction was not being made.
    """
    return any(s in repr(exc) for s in
               ("ConnectionError", "NameResolution", "Timeout", "MaxRetry",
                "502", "503", "504", "429"))


def submit_all(c):
    """Submit a cbbo-1m job per outstanding session, paced under the rate limit."""
    days = trading_days()
    already = submitted_dates()
    todo = [d for d in days if d not in already]
    print(f"sessions {len(days)} | already submitted {len(already)} | to submit {len(todo)}")

    failed = []
    for i, day in enumerate(todo, 1):
        for attempt in range(MAX_ATTEMPTS):
            try:
                _, row = submit_one(c, day)
                if i % 10 == 0 or i in (1, len(todo)):
                    print(f"  [{i:>3}/{len(todo)}] {day} -> {row['job_id']}", flush=True)
                break
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1 or not _is_transient(exc):
                    failed.append((day, repr(exc)))
                    print(f"  [{i:>3}/{len(todo)}] {day} GAVE UP: {exc!r}", flush=True)
                    break
                wait = BACKOFF[attempt]
                print(f"  [{i:>3}/{len(todo)}] {day} attempt {attempt+1} failed; "
                      f"retry in {wait}s", flush=True)
                time.sleep(wait)
        time.sleep(SUBMIT_PACE_SECONDS)

    print(f"\nsubmission complete; gave up on {len(failed)}")
    for d, e in failed:
        print(f"  {d}: {e}")
    # A day the exchange did not open fails here rather than silently going
    # missing. Add full closures to ONE_OFF_CLOSURES and short sessions to
    # EARLY_CLOSE_DAYS; the two are distinguishable by the error returned.
    return failed


def wait_for_jobs(c, poll_seconds=15, max_polls=400):
    """Block until every tracked job reports done."""
    for i in range(max_polls):
        try:
            ours = {r["job_id"] for r in load_jobs() if r.get("job_id")}
            jobs = {j["id"]: j for j in c.batch.list_jobs()}
            states = {}
            for jid in ours:
                st = jobs.get(jid, {}).get("state", "unlisted")
                states[st] = states.get(st, 0) + 1
            done = states.get("done", 0)
            print(f"[{i*poll_seconds:>5}s] done {done}/{len(ours)}  {states}", flush=True)
            if done >= len(ours):
                return True
        except Exception as exc:
            print(f"[{i*poll_seconds:>5}s] poll error ({type(exc).__name__}); retrying")
        time.sleep(poll_seconds)
    return False


def download_all(c):
    """Download every completed job into cbbo/<trade date>/, skipping what is present."""
    rows = load_jobs()
    jobs = {j["id"]: j for j in c.batch.list_jobs(states="done,queued,processing,expired")}
    final, got, skipped, pending, failed = [], 0, 0, [], []

    for r in rows:
        jid, day = r["job_id"], r["trade_date"]
        j = jobs.get(jid, {})
        state = j.get("state", "unlisted")
        final.append({"trade_date": day, "job_id": jid, "state": state,
                      "cost_usd": j.get("cost_usd"),
                      "record_count": j.get("record_count"),
                      "billed_size": j.get("billed_size"),
                      "window_start_utc": r["window_start_utc"],
                      "window_end_utc": r["window_end_utc"]})
        if state != "done":
            pending.append((day, jid, state))
            continue
        dest = _os.path.join(CBBO_DIR, day)
        # batch.download nests output under a job-id subfolder, so this check
        # has to recurse; a non-recursive glob silently never matches and every
        # pass re-downloads the lot.
        if glob.glob(f"{dest}/**/*.dbn.zst", recursive=True):
            skipped += 1
            continue
        _os.makedirs(dest, exist_ok=True)
        try:
            c.batch.download(job_id=jid, output_dir=dest)
            got += 1
            if got % 25 == 0:
                print(f"  downloaded {got} ...", flush=True)
        except Exception as exc:
            failed.append((day, jid, repr(exc)))
            print(f"  {day} DOWNLOAD FAILED: {exc!r}")

    if final:
        with open(_os.path.join(OUT_ROOT, "jobs_final.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(final[0].keys()))
            w.writeheader()
            w.writerows(final)
    print(f"\ndownloaded {got}, already present {skipped}, "
          f"not done {len(pending)}, failures {len(failed)}")
    return pending, failed


def pull_definitions_fullrange(c):
    """One batch job covering the whole range, split into one file per day.

    Quarterly definition anchors leave newly listed contracts unmatched -- the
    match rate decayed to 89% by early 2026. A contiguous range job costs about
    the same as one job per session (which is itself the evidence that it
    contains a per-day snapshot rather than a deduplicated list) and closes the
    gap completely. The job id is recorded before any waiting, so an
    interruption resumes instead of resubmitting and being billed twice.
    """
    days = trading_days()
    start, end = days[0], "2026-06-02"          # end is exclusive
    _os.makedirs(DEFN_FULLRANGE_DIR, exist_ok=True)

    if _os.path.exists(DEFN_FULLRANGE_JOB):
        rec = json.load(open(DEFN_FULLRANGE_JOB))
        job_id = rec["job_id"]
        print(f"resuming definition job {job_id}")
    else:
        job = c.batch.submit_job(
            dataset=DATASET, symbols=SYMBOLS, schema="definition",
            start=start, end=end, stype_in=STYPE_IN,
            encoding="dbn", compression="zstd", map_symbols=False,
            split_duration="day",
        )
        job_id = job["id"]
        with open(DEFN_FULLRANGE_JOB, "w") as f:
            json.dump({"job_id": job_id, "start": start, "end": end,
                       "submitted_at": datetime.now(UTC).isoformat()}, f, indent=2)
            f.flush()
            _os.fsync(f.fileno())
        print(f"submitted {job_id}  ({start} -> {end}, exclusive end)")

    for _ in range(400):
        j = {x["id"]: x for x in c.batch.list_jobs()}.get(job_id, {})
        if j.get("state") == "done":
            print(f"BILLED ${float(j['cost_usd']):.4f}, "
                  f"{int(j['record_count']):,} records")
            break
        time.sleep(15)

    if not glob.glob(f"{DEFN_FULLRANGE_DIR}/**/*.dbn.zst", recursive=True):
        c.batch.download(job_id=job_id, output_dir=DEFN_FULLRANGE_DIR)
    fs = glob.glob(f"{DEFN_FULLRANGE_DIR}/**/*.dbn.zst", recursive=True)
    print(f"definition files: {len(fs)}")
    return fs


EQUITY_DIR = _os.path.join(OUT_ROOT, "equities")

# The underlying close. OPRA carries no underlying price, so SPY's daily close
# comes from an equities dataset. EQUS.SUMMARY is the CONSOLIDATED national
# close, which is what "SPY closed at X" conventionally means and what the
# OptionsDX UNDERLYING_LAST field approximates.
#
# ARCX.PILLAR was pulled alongside it as a cross-check on the theory that SPY's
# primary listing venue gives the most official close. It does not: an
# ohlcv-1d bar from one venue is the last print in that venue's bar window, not
# the closing-auction price, and it diverged from the consolidated close by up
# to $19.62 with a return correlation of only 0.910. Kept for the record; not
# used.
EQUITY_DATASETS = ["EQUS.SUMMARY", "ARCX.PILLAR"]
EQUITY_CLOSE_DATASET = "EQUS.SUMMARY"


def pull_spy_daily_close(c):
    """One ohlcv-1d bar per session for SPY, from each equities dataset."""
    _os.makedirs(EQUITY_DIR, exist_ok=True)
    days = trading_days()
    start, end = days[0], "2026-06-02"          # exclusive end
    out = []
    for ds in EQUITY_DATASETS:
        path = _os.path.join(EQUITY_DIR, f"spy_ohlcv1d_{ds.replace('.', '_')}.dbn.zst")
        if _os.path.exists(path) and _os.path.getsize(path) > 0:
            print(f"  {ds}: already present, skipping")
            out.append(path)
            continue
        cost = c.metadata.get_cost(dataset=ds, start=start, end=end, symbols="SPY",
                                   schema="ohlcv-1d", stype_in="raw_symbol")
        c.timeseries.get_range(dataset=ds, start=start, end=end, symbols="SPY",
                               schema="ohlcv-1d", stype_in="raw_symbol").to_file(path)
        print(f"  {ds}: ${cost:.6f} -> {_os.path.basename(path)}")
        out.append(path)
    return out


def verify():
    """Coverage, cost and record counts, read back from disk and job details."""
    days = trading_days()
    path = _os.path.join(OUT_ROOT, "jobs_final.csv")
    rows = list(csv.DictReader(open(path))) if _os.path.exists(path) else []
    by_date = {r["trade_date"]: r for r in rows}

    print("=" * 70)
    print(f"PULL SUMMARY  {RANGE_START} -> {RANGE_END}")
    print("=" * 70)
    print(f"sessions targeted   : {len(days)}")
    print(f"jobs tracked        : {len(rows)}")
    print(f"jobs done           : {sum(r['state'] == 'done' for r in rows)}")
    missing = [d for d in days if d not in by_date]
    print(f"sessions with no job: {len(missing)} {missing or ''}")

    costs = [float(r["cost_usd"]) for r in rows
             if r.get("cost_usd") not in (None, "", "None")]
    if costs:
        print(f"\ncbbo-1m billed      : ${sum(costs):.4f} across {len(costs)} jobs")
        print(f"  per session  min ${min(costs):.6f}  mean ${sum(costs)/len(costs):.6f}"
              f"  max ${max(costs):.6f}")

    files = glob.glob(f"{CBBO_DIR}/**/*.dbn.zst", recursive=True)
    nofile = [d for d in days if not
              glob.glob(f"{CBBO_DIR}/{d}/**/*.dbn.zst", recursive=True)]
    size = sum(_os.path.getsize(f) for f in files)
    print(f"\ncbbo files          : {len(files)}  ({size/1e6:.1f} MB)")
    print(f"sessions with no file: {len(nofile)} {nofile or ''}")
    rc = [int(r["record_count"]) for r in rows
          if r.get("record_count") not in (None, "", "None")]
    if rc:
        print(f"records             : {sum(rc):,}")
    print("=" * 70)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "all"
    if cmd == "verify":
        return verify()
    c = client()
    if cmd in ("definitions", "all"):
        pull_definitions(c, trading_days())
        pull_definitions_fullrange(c)
    if cmd in ("equities", "all"):
        pull_spy_daily_close(c)
    if cmd in ("submit", "all"):
        submit_all(c)
    if cmd in ("download", "all"):
        wait_for_jobs(c)
        download_all(c)
    if cmd in ("verify", "all"):
        verify()


if __name__ == "__main__":
    main(_sys.argv)
