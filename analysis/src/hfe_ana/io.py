"""Input/output helpers for temperature-controller logs."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, MutableMapping

import pandas as pd

DEFAULT_TC_MAP: Mapping[str, str] = {
    "temp1_C": "U1_bottom_C",
    "temp2_C": "U2_coilTop_C",
    "temp3_C": "U3_top_C",
    "temp4_C": "U4_coilMid_C",
}

TC_MAP: MutableMapping[str, str] = dict(DEFAULT_TC_MAP)


def load_tc_csv(path: Path | str, *, rename_map: Mapping[str, str] | None = None) -> pd.DataFrame:
    """
    Load a CSV produced by the logger, add convenience columns, and return a DataFrame.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    df = pd.read_csv(csv_path)
    mapping = dict(TC_MAP)
    if rename_map:
        mapping.update(rename_map)
    df = df.rename(columns=mapping)

    required = {"time_s"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required column(s): {sorted(missing)}")

    df["t_min"] = df["time_s"] / 60.0

    # Derive bulk/coil averages when sensors are present; skip gracefully otherwise.
    if {"U1_bottom_C", "U3_top_C"}.issubset(df.columns):
        df["T_bulk_mean_C"] = df[["U1_bottom_C", "U3_top_C"]].mean(axis=1)
    if {"U2_coilTop_C", "U4_coilMid_C"}.issubset(df.columns):
        df["T_coil_mean_C"] = df[["U2_coilTop_C", "U4_coilMid_C"]].mean(axis=1)
    if {"T_bulk_mean_C", "T_coil_mean_C"}.issubset(df.columns):
        df["DeltaT_C"] = df["T_bulk_mean_C"] - df["T_coil_mean_C"]
    if {"U3_top_C", "U1_bottom_C"}.issubset(df.columns):
        df["Strat_top_minus_bottom_C"] = df["U3_top_C"] - df["U1_bottom_C"]
    return df
