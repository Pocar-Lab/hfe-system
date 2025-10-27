from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

SlopeFunc = Callable[[Iterable[float], Iterable[float], float], np.ndarray]

__all__ = [
    "bath_capacity_j_per_k",
    "apparent_power",
    "fit_heat_leak_and_UA",
    "apply_corrections",
    "integrate_energy",
    "HeatLeakResult",
]


def bath_capacity_j_per_k(volume_L: float, rho_kgL: float = 1.07, cp_kJkgK: float = 3.5) -> float:
    """Return the thermal capacity (J/K) of the bath."""
    return float(volume_L * rho_kgL * cp_kJkgK * 1000.0)


def apparent_power(
    df: pd.DataFrame,
    Cp_JK: float,
    window_s: float = 45.0,
    slope_func: SlopeFunc | None = None,
) -> pd.DataFrame:
    """
    Compute the apparent bath power by multiplying the rolling slope of the bulk
    temperature with the bath capacity.
    """
    if slope_func is None:
        from hfe_ana.filters import rolling_slope

        slope_func = rolling_slope
    d = df.copy()
    s = slope_func(d["time_s"].to_numpy(), d["T_bulk_mean_C"].to_numpy(), window_s)
    d["dTbulk_dt_C_per_s"] = s
    d["P_bath_W"] = -Cp_JK * s
    return d


@dataclass(frozen=True)
class HeatLeakResult:
    UA_W_per_K: float
    heat_leak_W: float
    r_squared: float
    n_points: int


def fit_heat_leak_and_UA(
    df: pd.DataFrame,
    *,
    tmin_window: Tuple[float, float] = (1.0, 5.0),
    deltaT_range: Tuple[float, float] = (1.0, 12.0),
) -> HeatLeakResult:
    """
    Fit early-time data to estimate the heat exchanger UA and ambient heat leak.
    """
    d = df.copy()
    t0, t1 = tmin_window
    dTmin, dTmax = deltaT_range
    mask = (
        (d["t_min"] > t0)
        & (d["t_min"] < t1)
        & (d["DeltaT_C"] > dTmin)
        & (d["DeltaT_C"] < dTmax)
        & (~d["P_bath_W"].isna())
    )
    if not mask.any():
        raise ValueError("No data points matched the regression window; adjust filters.")
    X = d.loc[mask, ["DeltaT_C"]].to_numpy()
    y = d.loc[mask, "P_bath_W"].to_numpy()
    reg = LinearRegression().fit(X, y)
    UA = float(reg.coef_[0])
    heat_leak = float(-reg.intercept_)
    r2 = float(reg.score(X, y))
    return HeatLeakResult(UA, heat_leak, r2, int(mask.sum()))


def apply_corrections(df: pd.DataFrame, heat_leak_W: float) -> pd.DataFrame:
    """Add corrected HX power and UA columns to the dataframe."""
    d = df.copy()
    d["P_HX_W"] = d["P_bath_W"] + heat_leak_W
    d["UA_corr_W_per_K"] = d["P_HX_W"] / d["DeltaT_C"]
    return d


def integrate_energy(t_s: Iterable[float], power_W: Iterable[float]) -> float:
    """Integrate power over time and return the resulting energy in Joules."""
    return float(np.trapz(power_W, t_s))
