"""Shared helper utilities for heat-exchanger analysis notebooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

from .hx import apparent_power, integrate_energy
from .io import load_tc_csv


def prepare_dataset(
    path: str | Path,
    Cp_JK: float,
    *,
    label: str,
    window_s: float = 45.0,
    valve_column: str = "valve",
) -> Tuple[pd.DataFrame, float]:
    """
    Load a temperature-controller CSV and add convenience columns for analysis.

    Returns the prepared dataframe and the original start time (seconds).
    """
    df = load_tc_csv(path).copy()
    start_time = float(df["time_s"].iloc[0])

    df["time_s_raw"] = df["time_s"]
    df["time_s"] = df["time_s"] - start_time
    df["t_min"] = df["time_s"] / 60.0
    if valve_column in df.columns:
        df["valve_state"] = pd.to_numeric(df[valve_column], errors="coerce")
    df["dataset"] = label

    df = apparent_power(df, Cp_JK, window_s=window_s)
    return df, start_time


def linear_trend(time_s: Iterable[float], values: Iterable[float]) -> Tuple[float, float]:
    """Return a simple least-squares slope and intercept for the provided samples."""
    t = np.asarray(list(time_s), dtype=float)
    y = np.asarray(list(values), dtype=float)
    if t.size == 0 or y.size == 0:
        raise ValueError("Cannot fit a trend with no samples.")
    t_centered = t - t.mean()
    denom = float(np.dot(t_centered, t_centered))
    if denom == 0.0:
        raise ValueError("Cannot fit a trend when all time stamps are identical.")
    slope = float(np.dot(t_centered, y - y.mean()) / denom)
    intercept = float(y.mean() - slope * t.mean())
    return slope, intercept


def heat_leak_subset(
    df: pd.DataFrame,
    *,
    tmin_start_min: float = 0.0,
    require_valve_closed: bool = True,
) -> pd.DataFrame:
    """
    Return a filtered dataframe suitable for heat-leak analysis.
    """
    subset = (
        df[(df["t_min"] >= tmin_start_min) & (~df["T_bulk_mean_C"].isna()) & (~df["P_bath_W"].isna())]
        .copy()
        .sort_values("time_s")
    )
    if subset.empty:
        return subset
    if require_valve_closed and "valve_state" in subset.columns:
        closed = subset[subset["valve_state"].fillna(0.0) < 0.5]
        if not closed.empty:
            subset = closed
    return subset


def heat_leak_windows(
    df: pd.DataFrame,
    Cp_JK: float,
    *,
    windows_min: Sequence[Tuple[float, float]],
    require_valve_closed: bool = True,
) -> pd.DataFrame:
    """
    Evaluate heat-leak statistics for each (start_min, end_min) window.

    Returns a dataframe with slope-based and median-based estimates.
    """
    rows: List[Dict[str, float | str | int]] = []
    for t0_min, t1_min in windows_min:
        base_subset = heat_leak_subset(
            df,
            tmin_start_min=t0_min,
            require_valve_closed=require_valve_closed,
        )
        subset = base_subset[base_subset["t_min"] <= t1_min].copy()

        result: Dict[str, float | str | int] = {
            "Window": f"{t0_min:.2f}-{t1_min:.2f}",
            "Window_start_min": float(t0_min),
            "Window_end_min": float(t1_min),
            "Window_span_min": float(t1_min - t0_min),
            "Samples": int(subset.shape[0]),
            "dT_dt_C_per_s": float("nan"),
            "Heat_leak_from_trend_W": float("nan"),
            "Ambient_gain_median_W": float("nan"),
            "Ambient_gain_p10_W": float("nan"),
            "Ambient_gain_p90_W": float("nan"),
        }
        if subset.empty:
            rows.append(result)
            continue

        try:
            slope, _ = linear_trend(subset["time_s"], subset["T_bulk_mean_C"])
        except ValueError:
            rows.append(result)
            continue

        ambient = -subset["P_bath_W"].to_numpy()
        result.update(
            {
                "dT_dt_C_per_s": slope,
                "Heat_leak_from_trend_W": float(Cp_JK * slope),
                "Ambient_gain_median_W": float(np.nanmedian(ambient)),
                "Ambient_gain_p10_W": float(np.nanpercentile(ambient, 10)),
                "Ambient_gain_p90_W": float(np.nanpercentile(ambient, 90)),
            }
        )
        rows.append(result)
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class HeatLeakFit:
    """Summary of a linear heat-leak fit."""

    slope_C_per_s: float
    intercept_C: float
    heat_leak_W: float
    heat_leak_sigma_W: float
    ci95_low_W: float
    ci95_high_W: float
    residual_std_C: float
    r_squared: float
    n_samples: int
    t_min_start: float
    t_min_end: float
    t_mean_s: float
    sum_t_center_sq_s2: float
    ambient_median_W: float
    ambient_p10_W: float
    ambient_p90_W: float


def fit_heat_leak_linear(
    df: pd.DataFrame,
    Cp_JK: float,
    *,
    tmin_start_min: float = 0.0,
    require_valve_closed: bool = True,
) -> HeatLeakFit:
    """
    Fit a straight line to the bulk temperature for t_min >= tmin_start_min.

    Returns :class:`HeatLeakFit` with uncertainty estimates (1σ and 95% CI).
    """
    subset = heat_leak_subset(
        df,
        tmin_start_min=tmin_start_min,
        require_valve_closed=require_valve_closed,
    )
    if subset.empty:
        raise ValueError("No samples available for the requested start time.")

    if subset.shape[0] < 3:
        raise ValueError("At least three samples are required to fit a trend.")

    times_s = subset["time_s"].to_numpy()
    temps_C = subset["T_bulk_mean_C"].to_numpy()
    slope, intercept = linear_trend(times_s, temps_C)
    predicted = intercept + slope * times_s
    residuals = temps_C - predicted
    t_mean = float(times_s.mean())
    t_centered = times_s - t_mean
    denom = float(np.dot(t_centered, t_centered))

    ss_res = float(np.dot(residuals, residuals))
    sigma2 = float(ss_res / (subset.shape[0] - 2))
    temps_centered = temps_C - temps_C.mean()
    ss_tot = float(np.dot(temps_centered, temps_centered))
    if ss_tot > 0.0:
        r_squared = 1.0 - (ss_res / ss_tot)
    else:
        r_squared = float("nan")
    se_slope = float(np.sqrt(sigma2 / denom))

    heat_leak = float(Cp_JK * slope)
    heat_sigma = float(Cp_JK * se_slope)
    ci95 = 1.96 * heat_sigma

    ambient = -subset["P_bath_W"].to_numpy()

    return HeatLeakFit(
        slope_C_per_s=float(slope),
        intercept_C=float(intercept),
        heat_leak_W=heat_leak,
        heat_leak_sigma_W=heat_sigma,
        ci95_low_W=heat_leak - ci95,
        ci95_high_W=heat_leak + ci95,
        residual_std_C=float(np.sqrt(sigma2)),
        r_squared=float(r_squared),
        n_samples=int(subset.shape[0]),
        t_min_start=float(subset["t_min"].min()),
        t_min_end=float(subset["t_min"].max()),
        t_mean_s=t_mean,
        sum_t_center_sq_s2=denom,
        ambient_median_W=float(np.nanmedian(ambient)),
        ambient_p10_W=float(np.nanpercentile(ambient, 10)),
        ambient_p90_W=float(np.nanpercentile(ambient, 90)),
    )


def predict_heat_leak_fit(
    fit: HeatLeakFit,
    times_s: Iterable[float],
    *,
    confidence_sigma: float = 1.96,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Return fitted bulk-temperature values and confidence bands for the given times.

    Parameters
    ----------
    fit:
        Linear fit result from :func:`fit_heat_leak_linear`.
    times_s:
        Sequence of timestamps (seconds) to evaluate.
    confidence_sigma:
        Multiplier for the confidence band (1.96 → ~95 %).

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        Predicted temperature, lower band, upper band, and residual-standard-error array
        (i.e., the standard error for the mean prediction).
    """

    times = np.asarray(list(times_s), dtype=float)
    predicted = fit.intercept_C + fit.slope_C_per_s * times
    sigma2 = fit.residual_std_C**2
    if fit.n_samples <= 0 or fit.sum_t_center_sq_s2 <= 0.0:
        se = np.full_like(predicted, float("nan"))
    else:
        se = np.sqrt(
            sigma2 * (
                (1.0 / fit.n_samples)
                + ((times - fit.t_mean_s) ** 2) / fit.sum_t_center_sq_s2
            )
        )
    band = confidence_sigma * se
    lower = predicted - band
    upper = predicted + band
    return predicted, lower, upper, se


def summarize_windows(
    df: pd.DataFrame,
    windows: Sequence[Tuple[float, float]],
    *,
    require_positive_deltaT: bool = True,
) -> pd.DataFrame:
    """
    Compute windowed medians/percentiles for corrected HX metrics.
    """
    records: List[Dict[str, float | str | int]] = []
    for t0, t1 in windows:
        mask = (df["t_min"] >= t0) & (df["t_min"] <= t1) & (~df["P_HX_W"].isna())
        if require_positive_deltaT:
            mask &= df["DeltaT_C"] > 0.5

        subset = df.loc[mask].copy()
        if subset.empty:
            records.append({"window_min": f"{t0}-{t1}", "samples": 0})
            continue

        records.append(
            {
                "window_min": f"{t0}-{t1}",
                "samples": int(subset.shape[0]),
                "P_HX_median_W": float(np.nanmedian(subset["P_HX_W"])),
                "P_HX_p10_W": float(np.nanpercentile(subset["P_HX_W"], 10)),
                "P_HX_p90_W": float(np.nanpercentile(subset["P_HX_W"], 90)),
                "Heat_flux_median_W_m2": float(np.nanmedian(subset.get("P_HX_W_m2", np.nan))),
                "UA_median_W_per_K": float(np.nanmedian(subset["UA_corr_W_per_K"])),
                "UA_flux_median_W_per_m2K": float(np.nanmedian(subset.get("UA_per_area_W_per_m2K", np.nan))),
                "DeltaT_median_C": float(np.nanmedian(subset["DeltaT_C"])),
            }
        )
    return pd.DataFrame(records)


def integrate_corrected_power(df: pd.DataFrame, t0_min: float, t1_min: float) -> float:
    """
    Integrate the corrected HX power over the requested window (minutes → Joules).
    """
    mask = (df["t_min"] >= t0_min) & (df["t_min"] <= t1_min) & (~df["P_HX_W"].isna())
    if mask.sum() == 0:
        return float("nan")
    energy_J = integrate_energy(df.loc[mask, "time_s"], df.loc[mask, "P_HX_W"])
    return float(energy_J) / 1000.0


def fit_ua_from_corrected(
    df: pd.DataFrame,
    *,
    tmin_window: Tuple[float, float],
    deltaT_range: Tuple[float, float],
) -> Dict[str, float]:
    """
    Fast UA estimate using corrected power and a constrained window.
    """
    t0, t1 = tmin_window
    dTmin, dTmax = deltaT_range

    mask = (df["t_min"] > t0) & (df["t_min"] < t1)
    mask &= (df["DeltaT_C"] > dTmin) & (df["DeltaT_C"] < dTmax)
    mask &= (~df["P_HX_W"].isna())
    subset = df.loc[mask]
    if subset.empty:
        return {"UA_W_per_K": float("nan"), "r_squared": float("nan"), "points": 0}

    x = subset["DeltaT_C"].to_numpy()
    y = subset["P_HX_W"].to_numpy()
    ua = float(np.dot(x, y) / np.dot(x, x))
    residuals = y - ua * x
    ss_res = float(np.dot(residuals, residuals))
    ss_tot = float(np.dot(y - y.mean(), y - y.mean()))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {"UA_W_per_K": ua, "r_squared": r2, "points": int(subset.shape[0])}


@dataclass(frozen=True)
class WindowTemperatureFit:
    """Linear fit of bulk temperature over a specific time window."""

    dataset: str
    window_start_min: float
    window_end_min: float
    samples: int
    slope_C_per_min: float
    intercept_C: float
    slope_C_per_s: float
    r_squared: float
    P_bath_fit_W: float
    P_HX_fit_W: float
    UA_fit_W_per_K: float
    heat_flux_fit_W_m2: float
    UA_area_fit_W_per_m2K: float
    DeltaT_mean_C: float
    time_min: np.ndarray
    temps_C: np.ndarray
    fitted_C: np.ndarray


def fit_temperature_window(
    df: pd.DataFrame,
    *,
    Cp_JK: float,
    heat_leak_W: float,
    hx_area_m2: float,
    t_window_min: Tuple[float, float],
    dataset: str = "",
) -> WindowTemperatureFit:
    """
    Fit the bulk-mean temperature within the requested window and derive HX metrics.
    """
    t0, t1 = t_window_min
    subset = (
        df[(df["t_min"] >= t0) & (df["t_min"] <= t1)]
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["t_min", "time_s", "T_bulk_mean_C", "DeltaT_C"])
        .copy()
    )
    if subset.empty or subset.shape[0] < 2:
        raise ValueError("Insufficient samples for temperature fit.")

    time_min = subset["t_min"].to_numpy()
    temps_C = subset["T_bulk_mean_C"].to_numpy()
    A = np.vstack([time_min, np.ones_like(time_min)]).T
    slope_C_per_min, intercept_C = np.linalg.lstsq(A, temps_C, rcond=None)[0]
    fitted_C = slope_C_per_min * time_min + intercept_C

    residuals = temps_C - fitted_C
    temps_centered = temps_C - temps_C.mean()
    ss_tot = float(np.dot(temps_centered, temps_centered))
    ss_res = float(np.dot(residuals, residuals))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")

    slope_C_per_s = slope_C_per_min / 60.0
    P_bath_fit_W = -Cp_JK * slope_C_per_s
    P_HX_fit_W = P_bath_fit_W + heat_leak_W

    deltaT_mean_C = float(np.nanmean(subset["DeltaT_C"]))
    UA_fit_W_per_K = P_HX_fit_W / deltaT_mean_C if abs(deltaT_mean_C) > 1e-6 else float("nan")
    heat_flux_fit_W_m2 = P_HX_fit_W / hx_area_m2
    UA_area_fit_W_per_m2K = (
        UA_fit_W_per_K / hx_area_m2 if np.isfinite(UA_fit_W_per_K) else float("nan")
    )

    return WindowTemperatureFit(
        dataset=dataset,
        window_start_min=float(t0),
        window_end_min=float(t1),
        samples=int(subset.shape[0]),
        slope_C_per_min=float(slope_C_per_min),
        intercept_C=float(intercept_C),
        slope_C_per_s=float(slope_C_per_s),
        r_squared=float(r_squared),
        P_bath_fit_W=float(P_bath_fit_W),
        P_HX_fit_W=float(P_HX_fit_W),
        UA_fit_W_per_K=float(UA_fit_W_per_K),
        heat_flux_fit_W_m2=float(heat_flux_fit_W_m2),
        UA_area_fit_W_per_m2K=float(UA_area_fit_W_per_m2K),
        DeltaT_mean_C=deltaT_mean_C,
        time_min=time_min,
        temps_C=temps_C,
        fitted_C=fitted_C,
    )


def plot_temperature_window_fit(
    fit: WindowTemperatureFit,
    *,
    title: str | None = None,
    annotate: bool = True,
) -> "plt.Figure":
    """
    Plot the windowed bulk-temperature samples with the corresponding best-fit line.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.scatter(fit.time_min, fit.temps_C, s=14, alpha=0.6, label="Bulk mean samples")
    order = np.argsort(fit.time_min)
    ax.plot(
        fit.time_min[order],
        fit.fitted_C[order],
        color="tab:red",
        linewidth=2,
        label=f"Fit slope = {fit.slope_C_per_min:.3f} °C/min",
    )
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Bulk mean temperature (°C)")
    ax.set_title(title or fit.dataset)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    if annotate:
        metrics_text = "\n".join(
            [
                f"N = {fit.samples} samples",
                f"Slope = {fit.slope_C_per_min:.3f} °C/min",
                f"P_HX (fit) = {fit.P_HX_fit_W:.0f} W",
                f"UA (fit) = {fit.UA_fit_W_per_K:.1f} W/K",
                f"Heat flux = {fit.heat_flux_fit_W_m2:.0f} W/m²",
                f"UA/area = {fit.UA_area_fit_W_per_m2K:.1f} W/m²-K",
                f"R² = {fit.r_squared:.3f}",
            ]
        )
        ax.text(
            0.98,
            0.02,
            metrics_text,
            transform=ax.transAxes,
            fontsize=9,
            va="bottom",
            ha="right",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
        )
    fig.tight_layout()
    return fig
