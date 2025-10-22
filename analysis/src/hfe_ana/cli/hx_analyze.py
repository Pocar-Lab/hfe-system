"""Command line interface for heat-exchanger analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from hfe_ana.io import load_tc_csv
from hfe_ana.filters import rolling_slope
from hfe_ana.hx import (
    apply_corrections,
    apparent_power,
    bath_capacity_j_per_k,
    fit_heat_leak_and_UA,
    integrate_energy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute corrected heat-exchanger power from a temperature log."
    )
    parser.add_argument("--input", required=True, help="Path to CSV log produced by the logger.")
    parser.add_argument("--volume-L", type=float, default=5.4, help="Bath volume in litres.")
    parser.add_argument("--rho", type=float, default=1.07, help="Fluid density [kg/L].")
    parser.add_argument("--cp-kJkgK", type=float, default=3.5, help="Specific heat [kJ/kg-K].")
    parser.add_argument(
        "--tmin-window",
        type=float,
        nargs=2,
        default=(1.0, 5.0),
        metavar=("TMIN0", "TMIN1"),
        help="Time window [min] for the early UA regression.",
    )
    parser.add_argument(
        "--deltaT-range",
        type=float,
        nargs=2,
        default=(1.0, 12.0),
        metavar=("DT_MIN", "DT_MAX"),
        help="Delta-T range [°C] to use for the early UA regression.",
    )
    parser.add_argument("--window-s", type=float, default=45.0, help="Rolling window for dT/dt [s].")
    parser.add_argument(
        "--out-ts",
        default="data/processed/hx_timeseries.csv",
        help="Output CSV for the corrected time series.",
    )
    parser.add_argument(
        "--out-summary",
        default="data/reports/hx_summary.csv",
        help="Output CSV for summary metrics.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    df = load_tc_csv(Path(args.input))
    cp = bath_capacity_j_per_k(args.volume_L, args.rho, args.cp_kJkgK)
    df = apparent_power(df, cp, args.window_s, slope_func=rolling_slope)

    result = fit_heat_leak_and_UA(
        df,
        tmin_window=tuple(args.tmin_window),
        deltaT_range=tuple(args.deltaT_range),
    )
    dfc = apply_corrections(df, result.heat_leak_W)

    sel = (dfc["t_min"] >= 2) & (dfc["t_min"] <= 14) & (~dfc["P_HX_W"].isna())
    energy_J = integrate_energy(dfc.loc[sel, "time_s"], dfc.loc[sel, "P_HX_W"])

    # Save outputs
    ts_path = Path(args.out_ts)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    dfc.to_csv(ts_path, index=False)

    summary = pd.DataFrame(
        [
            {
                "UA_early_WK": result.UA_W_per_K,
                "H_W": result.heat_leak_W,
                "R2": result.r_squared,
                "N_regression": result.n_points,
                "E_2_14_kJ": energy_J / 1000.0,
            }
        ]
    )
    summary_path = Path(args.out_summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)

    print(
        f"UA_early={result.UA_W_per_K:.2f} W/K, "
        f"H={result.heat_leak_W:.0f} W, "
        f"R2={result.r_squared:.3f}, "
        f"N={result.n_points}, "
        f"E_2_14={energy_J/1000.0:.0f} kJ"
    )


if __name__ == "__main__":
    main()
