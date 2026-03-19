"""ORCA command line entry points."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .core import (
    apply_corrections,
    apparent_power,
    bath_capacity_j_per_k,
    fit_heat_leak_and_UA,
    integrate_energy,
    load_tc_csv,
    rolling_slope,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the HX analysis command."""

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
        help="Delta-T range [degC] to use for the early UA regression.",
    )
    parser.add_argument("--window-s", type=float, default=45.0, help="Rolling window for dT/dt [s].")
    parser.add_argument(
        "--out-ts",
        default="data/processed/hx_timeseries.csv",
        help="Output CSV for the corrected time series.",
    )
    parser.add_argument(
        "--out-summary",
        default="data/processed/hx_summary.csv",
        help="Output CSV for summary metrics.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the ORCA heat-exchanger CLI workflow."""

    args = build_parser().parse_args(argv)

    data = load_tc_csv(Path(args.input))
    bath_capacity = bath_capacity_j_per_k(args.volume_L, args.rho, args.cp_kJkgK)
    data = apparent_power(data, bath_capacity, args.window_s, slope_func=rolling_slope)

    result = fit_heat_leak_and_UA(
        data,
        tmin_window=tuple(args.tmin_window),
        deltaT_range=tuple(args.deltaT_range),
    )
    corrected = apply_corrections(data, result.heat_leak_W)

    selection = (corrected["t_min"] >= 2) & (corrected["t_min"] <= 14) & (~corrected["P_HX_W"].isna())
    energy_j = integrate_energy(corrected.loc[selection, "time_s"], corrected.loc[selection, "P_HX_W"])

    timeseries_path = Path(args.out_ts)
    timeseries_path.parent.mkdir(parents=True, exist_ok=True)
    corrected.to_csv(timeseries_path, index=False)

    summary = pd.DataFrame(
        [
            {
                "UA_early_WK": result.UA_W_per_K,
                "H_W": result.heat_leak_W,
                "R2": result.r_squared,
                "N_regression": result.n_points,
                "E_2_14_kJ": energy_j / 1000.0,
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
        f"E_2_14={energy_j / 1000.0:.0f} kJ"
    )
