"""Core data loading and heat-exchanger analysis helpers for ORCA."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, MutableMapping

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

DEFAULT_TC_MAP: Mapping[str, str] = {
    "temp1_C": "U1_bottom_C",
    "temp2_C": "U2_coilTop_C",
    "temp3_C": "U3_top_C",
    "temp4_C": "U4_coilMid_C",
}

TC_MAP: MutableMapping[str, str] = dict(DEFAULT_TC_MAP)
SlopeFunc = Callable[[Iterable[float], Iterable[float], float], np.ndarray]


def load_tc_csv(path: Path | str, *, rename_map: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Load a logger CSV and add the derived temperature columns used by ORCA."""

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    data = pd.read_csv(csv_path)
    mapping = dict(TC_MAP)
    if rename_map:
        mapping.update(rename_map)
    data = data.rename(columns=mapping)

    required = {"time_s"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing required column(s): {sorted(missing)}")

    data["t_min"] = data["time_s"] / 60.0

    if {"U1_bottom_C", "U3_top_C"}.issubset(data.columns):
        data["T_bulk_mean_C"] = data[["U1_bottom_C", "U3_top_C"]].mean(axis=1)
    if {"U2_coilTop_C", "U4_coilMid_C"}.issubset(data.columns):
        data["T_coil_mean_C"] = data[["U2_coilTop_C", "U4_coilMid_C"]].mean(axis=1)
    if {"T_bulk_mean_C", "T_coil_mean_C"}.issubset(data.columns):
        data["DeltaT_C"] = data["T_bulk_mean_C"] - data["T_coil_mean_C"]
    if {"U3_top_C", "U1_bottom_C"}.issubset(data.columns):
        data["Strat_top_minus_bottom_C"] = data["U3_top_C"] - data["U1_bottom_C"]

    return data


def rolling_slope(
    t_s: Iterable[float],
    values: Iterable[float],
    window_s: float = 45.0,
    min_pts: int = 5,
) -> np.ndarray:
    """Compute a local least-squares slope in a rolling time window."""

    time_s = np.asarray(t_s, dtype=float)
    samples = np.asarray(values, dtype=float)
    count = len(time_s)
    slopes = np.full(count, np.nan)
    half_window_s = window_s / 2.0

    for index in range(count):
        start_s = time_s[index] - half_window_s
        end_s = time_s[index] + half_window_s
        mask = (time_s >= start_s) & (time_s <= end_s)
        if mask.sum() >= min_pts:
            design = np.vstack([time_s[mask], np.ones(mask.sum())]).T
            slopes[index] = np.linalg.lstsq(design, samples[mask], rcond=None)[0][0]

    return slopes


def bath_capacity_j_per_k(volume_L: float, rho_kgL: float = 1.07, cp_kJkgK: float = 3.5) -> float:
    """Return the thermal capacity of the bath in J/K."""

    return float(volume_L * rho_kgL * cp_kJkgK * 1000.0)


def apparent_power(
    df: pd.DataFrame,
    Cp_JK: float,
    window_s: float = 45.0,
    slope_func: SlopeFunc | None = None,
) -> pd.DataFrame:
    """Estimate bath power from the bulk-temperature time derivative."""

    if slope_func is None:
        slope_func = rolling_slope

    data = df.copy()
    slopes = slope_func(
        data["time_s"].to_numpy(),
        data["T_bulk_mean_C"].to_numpy(),
        window_s,
    )
    data["dTbulk_dt_C_per_s"] = slopes
    data["P_bath_W"] = -Cp_JK * slopes
    return data


@dataclass(frozen=True)
class HeatLeakResult:
    """Early-time regression result for UA and ambient heat leak."""

    UA_W_per_K: float
    heat_leak_W: float
    r_squared: float
    n_points: int


def fit_heat_leak_and_UA(
    df: pd.DataFrame,
    *,
    tmin_window: tuple[float, float] = (1.0, 5.0),
    deltaT_range: tuple[float, float] = (1.0, 12.0),
) -> HeatLeakResult:
    """Fit early-time data to estimate the heat exchanger UA and ambient heat leak."""

    data = df.copy()
    t0_min, t1_min = tmin_window
    delta_t_min, delta_t_max = deltaT_range
    mask = (
        (data["t_min"] > t0_min)
        & (data["t_min"] < t1_min)
        & (data["DeltaT_C"] > delta_t_min)
        & (data["DeltaT_C"] < delta_t_max)
        & (~data["P_bath_W"].isna())
    )
    if not mask.any():
        raise ValueError("No data points matched the regression window; adjust filters.")

    x_values = data.loc[mask, ["DeltaT_C"]].to_numpy()
    y_values = data.loc[mask, "P_bath_W"].to_numpy()
    regression = LinearRegression().fit(x_values, y_values)
    return HeatLeakResult(
        UA_W_per_K=float(regression.coef_[0]),
        heat_leak_W=float(-regression.intercept_),
        r_squared=float(regression.score(x_values, y_values)),
        n_points=int(mask.sum()),
    )


def apply_corrections(df: pd.DataFrame, heat_leak_W: float) -> pd.DataFrame:
    """Add corrected HX power and UA columns to a dataframe."""

    data = df.copy()
    data["P_HX_W"] = data["P_bath_W"] + heat_leak_W
    data["UA_corr_W_per_K"] = data["P_HX_W"] / data["DeltaT_C"]
    return data


def integrate_energy(time_s: Iterable[float], power_w: Iterable[float]) -> float:
    """Integrate power over time and return the resulting energy in Joules."""

    return float(np.trapz(power_w, time_s))
