"""
Cross-vendor validation of the existing reference date, 2023-12-29, expiry 2024-02-16.

Compares Databento OPRA.PILLAR (cbbo-1m + definition) against the project's existing
Kaggle/OptionsDX-derived numbers. Reuses the project's own parity and BAW-inversion
logic unchanged, so any difference is attributable to the DATA, not to the method.

Reads pre-pulled DBN files from the scratchpad; performs no network I/O.
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

import numpy as np
import pandas as pd
import databento as db

import baw
from data_loader import (imply_forward_from_parity, MIN_PLAUSIBLE_IMPLIED_VOL,
                         MAX_PLAUSIBLE_IMPLIED_VOL)

SCRATCH = ("/private/tmp/claude-502/-Users-tarun02-Desktop-MFE-CME/"
           "a2bc1f98-7d6a-49bb-b4eb-be1eaf779231/scratchpad")

REF_DATE = "2023-12-29"
REF_EXPIRY = "2024-02-16"

# Established project values, from outputs/tables/reference_market_state.csv
PROJ_SPOT = 475.31
PROJ_FORWARD = 478.8920167222817
PROJ_T = 0.13424657534246576
PROJ_R = 0.0540
PROJ_B = 0.05592630141602003

# The reference position, from PROJECT_LOG Phase 3 / Phase 5
PROJ_PUT_STRIKE = 475.0
PROJ_PUT_THEO = 6.639087
PROJ_PUT_MID = 6.5250

def rule(c="-"):
    print(c * 78)


def build_databento_chain():
    """Join definition -> cbbo on instrument_id and pivot into the project's chain shape."""
    defs = db.DBNStore.from_file(f"{SCRATCH}/defn_20231229.dbn.zst").to_df()
    cbbo = db.DBNStore.from_file(f"{SCRATCH}/cbbo_20231229.dbn.zst").to_df()

    # Expiry of interest only.
    exp = pd.Timestamp(REF_EXPIRY, tz="UTC")
    d = defs[defs["expiration"] == exp][
        ["instrument_id", "raw_symbol", "instrument_class", "strike_price"]
    ].drop_duplicates("instrument_id")

    # The closing minute bar: ts_recv 20:59 UTC covers 15:59-16:00 ET.
    last_bar = cbbo.index.max()
    q = cbbo[cbbo.index == last_bar][
        ["instrument_id", "symbol", "bid_px_00", "ask_px_00", "bid_sz_00", "ask_sz_00"]
    ]

    m = d.merge(q, on="instrument_id", how="inner")

    # Independent check that the instrument_id join is sound: the symbol carried on the
    # quote must equal the symbol carried on the definition.
    mismatched = (m["raw_symbol"].str.strip() != m["symbol"].str.strip()).sum()
    print(f"  definitions for {REF_EXPIRY}      : {len(d)}")
    print(f"  quotes in closing bar {last_bar:%H:%M} UTC : {len(q)}")
    print(f"  joined on instrument_id          : {len(m)}")
    print(f"  symbol cross-check mismatches    : {mismatched}  "
          f"{'(join verified)' if mismatched == 0 else '(JOIN SUSPECT)'}")

    calls = m[m["instrument_class"] == "C"].set_index("strike_price")
    puts = m[m["instrument_class"] == "P"].set_index("strike_price")

    chain = pd.DataFrame({
        "strike": sorted(set(calls.index) & set(puts.index)),
    })
    chain["call_bid"] = chain["strike"].map(calls["bid_px_00"])
    chain["call_ask"] = chain["strike"].map(calls["ask_px_00"])
    chain["put_bid"] = chain["strike"].map(puts["bid_px_00"])
    chain["put_ask"] = chain["strike"].map(puts["ask_px_00"])
    return chain.dropna().reset_index(drop=True)


def otm_smile_with_our_ivs(chain, forward, spot, label):
    """Replicate build_otm_smile's OTM split and quality filters, then invert BAW.

    build_otm_smile is not called directly because it requires a vendor `market_iv`
    column to filter on, which Databento does not supply. The OTM split rule (put
    below the forward, call above), the two-sided/uncrossed filters and the
    plausibility band are the same, and the inversion is baw.implied_volatility
    unchanged.
    """
    c = chain.copy()
    is_put = c["strike"] < forward
    s = pd.DataFrame({
        "strike": c["strike"].to_numpy(),
        "option_type": np.where(is_put, "put", "call"),
        "bid": np.where(is_put, c["put_bid"], c["call_bid"]),
        "ask": np.where(is_put, c["put_ask"], c["call_ask"]),
    })
    s["mid"] = (s["bid"] + s["ask"]) / 2.0

    crossed = int(((s["ask"] <= s["bid"]) & (s["bid"] > 0)).sum())
    keep = (s["bid"] > 0) & (s["ask"] > s["bid"])
    s = s.loc[keep].sort_values("strike").reset_index(drop=True)

    s["reimplied_iv"] = [
        baw.implied_volatility(
            market_price=row.mid, underlying_price=spot, strike=row.strike,
            time_to_expiry=PROJ_T, risk_free_rate=PROJ_R, cost_of_carry=PROJ_B,
            option_type=row.option_type,
        )
        for row in s.itertuples()
    ]
    ok = s["reimplied_iv"].between(MIN_PLAUSIBLE_IMPLIED_VOL, MAX_PLAUSIBLE_IMPLIED_VOL)
    print(f"  {label}: {len(s)} OTM strikes passed quote filters, "
          f"{int(ok.sum())} produced a plausible IV, {crossed} crossed quotes dropped")
    return s.loc[ok].reset_index(drop=True), crossed


def adjacent_gap(smile, forward):
    """Robust crossover metric: last put below the forward vs first call above it."""
    p = smile[(smile.option_type == "put") & (smile.strike < forward)]
    c = smile[(smile.option_type == "call") & (smile.strike > forward)]
    if p.empty or c.empty:
        return None
    lp, fc = p.iloc[-1], c.iloc[0]
    return lp.strike, lp.reimplied_iv, fc.strike, fc.reimplied_iv, (fc.reimplied_iv - lp.reimplied_iv) * 100


def main():
    print("=" * 78)
    print(f"DATABENTO CROSS-CHECK  —  {REF_DATE}, expiry {REF_EXPIRY}")
    print("=" * 78)

    print("\n[JOIN] definition x cbbo-1m")
    rule()
    db_chain = build_databento_chain()
    print(f"  chain strikes with both legs     : {len(db_chain)}")

    proj_chain = pd.read_csv(_os.path.join(_ROOT, "data", "options_chain_2023-12-29.csv"))
    proj_chain = proj_chain[proj_chain.expiry_date == REF_EXPIRY].reset_index(drop=True)
    print(f"  project chain strikes            : {len(proj_chain)}")

    # ---- forward from parity, Databento quotes, project logic unchanged ----
    print(f"\n[1] PARITY FORWARD  (imply_forward_from_parity, unchanged)")
    rule()
    db_fwd = imply_forward_from_parity(
        db_chain, underlying_price=PROJ_SPOT, risk_free_rate=PROJ_R,
        time_to_expiry_years=PROJ_T,
    )
    print(f"  project  : F = ${PROJ_FORWARD:.4f}")
    print(f"  databento: F = ${db_fwd['forward']:.4f}   "
          f"({db_fwd['n_strikes_used']} strikes, std ${db_fwd['forward_std']:.4f})")
    d_fwd = db_fwd["forward"] - PROJ_FORWARD
    print(f"  difference: ${d_fwd:+.4f}  ({d_fwd / PROJ_FORWARD * 1e4:+.2f} bp of forward)")
    print(f"  implied q : project {PROJ_R - PROJ_B:+.4%}  vs  databento "
          f"{db_fwd['implied_dividend_yield']:+.4%}")

    # ---- implied spot ----
    print(f"\n[2] UNDERLYING SPOT")
    rule()
    print("  OPRA carries no underlying equity price, so spot cannot be read directly.")
    print("  Backing it out of Databento's forward at the project's carry b:")
    implied_spot = db_fwd["forward"] / np.exp(PROJ_B * PROJ_T)
    print(f"    project  spot          : ${PROJ_SPOT:.4f}")
    print(f"    databento implied spot : ${implied_spot:.4f}")
    print(f"    difference             : ${implied_spot - PROJ_SPOT:+.4f}")

    # ---- the 475 put ----
    print(f"\n[3] THE ${PROJ_PUT_STRIKE:.0f} PUT")
    rule()
    dbp = db_chain[db_chain.strike == PROJ_PUT_STRIKE].iloc[0]
    pp = proj_chain[proj_chain.strike == PROJ_PUT_STRIKE].iloc[0]
    db_mid = (dbp.put_bid + dbp.put_ask) / 2.0
    print(f"  project   bid/ask : {pp.put_bid:.4f} / {pp.put_ask:.4f}   mid ${PROJ_PUT_MID:.4f}")
    print(f"  databento bid/ask : {dbp.put_bid:.4f} / {dbp.put_ask:.4f}   mid ${db_mid:.4f}")
    print(f"  mid difference    : ${db_mid - PROJ_PUT_MID:+.4f}")
    print(f"  project theoretical BAW price : ${PROJ_PUT_THEO:.4f}")
    print(f"    vs project   market mid     : ${PROJ_PUT_THEO - PROJ_PUT_MID:+.4f}")
    print(f"    vs databento market mid     : ${PROJ_PUT_THEO - db_mid:+.4f}")

    # ---- IVs ----
    print(f"\n[4] RE-IMPLIED IVs  (baw.implied_volatility, unchanged)")
    rule()
    db_smile, db_crossed = otm_smile_with_our_ivs(db_chain, db_fwd["forward"], PROJ_SPOT,
                                                  "databento")
    proj_smile = pd.read_csv(_os.path.join(_ROOT, "outputs", "tables",
                                       "reimplied_smile.csv"))
    print(f"  project (Phase 3) smile          : {len(proj_smile)} strikes")

    merged = db_smile.merge(
        proj_smile[["strike", "option_type", "reimplied_iv", "vendor_iv", "mid"]],
        on=["strike", "option_type"], suffixes=("_db", "_proj"),
    )
    merged["diff_vp"] = (merged.reimplied_iv_db - merged.reimplied_iv_proj) * 100
    print(f"  strikes common to both           : {len(merged)}")
    print()
    print(f"  IV difference (Databento - project), vol points:")
    print(f"    mean {merged.diff_vp.mean():+.4f}   median {merged.diff_vp.median():+.4f}"
          f"   std {merged.diff_vp.std():.4f}")
    print(f"    min  {merged.diff_vp.min():+.4f}   max    {merged.diff_vp.max():+.4f}")
    print(f"    |diff| > 1.0 vol point : {(merged.diff_vp.abs() > 1.0).sum()} of {len(merged)}")
    for side in ("put", "call"):
        sub = merged[merged.option_type == side]
        if len(sub):
            print(f"    {side:<5} ({len(sub):>3}) mean {sub.diff_vp.mean():+.4f} vol points")

    # ---- crossover ----
    print(f"\n[5] PUT/CALL CROSSOVER — real phenomenon or vendor artefact?")
    rule()
    g = adjacent_gap(db_smile, db_fwd["forward"])
    print("                          last put    first call    adjacent gap")
    print(f"  project vendor IV     K={476:<10} K={479:<11} {+0.291:+.3f} vol pts  (wrong sign)")
    print(f"  project re-implied    K={476:<10} K={479:<11} {-0.209:+.3f} vol pts  (correct sign)")
    if g:
        print(f"  databento re-implied  K={g[0]:<10.0f} K={g[2]:<11.0f} {g[4]:+.3f} vol pts"
              f"  ({'correct sign' if g[4] < 0 else 'WRONG SIGN'})")
        print(f"    (put IV {g[1]:.4%} at K={g[0]:.0f}, call IV {g[3]:.4%} at K={g[2]:.0f})")

    print(f"\n  Crossed/locked quotes (ask <= bid) among OTM strikes:")
    pc = proj_chain.copy()
    is_put = pc["strike"] < PROJ_FORWARD
    pbid = np.where(is_put, pc.put_bid, pc.call_bid)
    pask = np.where(is_put, pc.put_ask, pc.call_ask)
    proj_crossed = int(((pask <= pbid) & (pbid > 0)).sum())
    print(f"    project   : {proj_crossed}")
    print(f"    databento : {db_crossed}")
    print(f"  The project's crossed puts sat at K=477 and K=478. Databento at those strikes:")
    for k in (477.0, 478.0):
        r = db_chain[db_chain.strike == k]
        if not r.empty:
            r = r.iloc[0]
            state = "CROSSED" if r.put_ask <= r.put_bid else "clean two-sided"
            print(f"    K={k:.0f}: put {r.put_bid:.2f} / {r.put_ask:.2f}   -> {state}")

    merged.to_csv(_os.path.join(_ROOT, "outputs", "tables",
                            "databento_iv_comparison.csv"), index=False)
    print(f"\n  wrote outputs/tables/databento_iv_comparison.csv")
    print("=" * 78)


if __name__ == "__main__":
    main()
