#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd
from hfe_ana.io import load_tc_csv
from hfe_ana.filters import rolling_slope
from hfe_ana.hx import bath_capacity_j_per_k, apparent_power, fit_heat_leak_and_UA, apply_corrections, integrate_energy

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="CSV log")
    ap.add_argument("--volume-L", type=float, default=5.4)
    ap.add_argument("--rho", type=float, default=1.07)
    ap.add_argument("--cp-kJkgK", type=float, default=3.5)
    ap.add_argument("--out-ts", default="data/processed/hx_timeseries.csv")
    ap.add_argument("--out-summary", default="data/reports/hx_summary.csv")
    args = ap.parse_args()

    df = load_tc_csv(Path(args.input))
    Cp = bath_capacity_j_per_k(args.volume_L, args.rho, args.cp_kJkgK)
    df = apparent_power(df, Cp, 45.0, rolling_slope)
    UA, H, R2 = fit_heat_leak_and_UA(df)
    dfc = apply_corrections(df, H)

    sel = (dfc["t_min"]>=2) & (dfc["t_min"]<=14) & (~dfc["P_HX_W"].isna())
    E = integrate_energy(dfc.loc[sel,"time_s"], dfc.loc[sel,"P_HX_W"])

    # Save
    Path(args.out_ts).parent.mkdir(parents=True, exist_ok=True)
    dfc.to_csv(args.out_ts, index=False)
    summary = pd.DataFrame([{"UA_early_WK":UA, "H_W":H, "R2":R2, "E_2_14_kJ":E/1000.0}])
    Path(args.out_summary).parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out_summary, index=False)

    print(f"UA_early={UA:.2f} W/K, H={H:.0f} W, R2={R2:.3f}, E_2_14={E/1000:.0f} kJ")
if __name__ == "__main__":
    main()
