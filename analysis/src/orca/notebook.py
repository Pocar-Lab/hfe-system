"""Shared ORCA helper utilities for heat-exchanger analysis notebooks."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from .cooldown import (
    SystemModel,
    ambient_leak_ua_w_per_k,
    default_system_model,
    hfe_density_kg_m3,
    hfe_specific_heat_j_kgk,
)
from .core import apparent_power, integrate_energy, load_tc_csv, rolling_slope


def _fmt_val_err(val: float, err: float, *, n_sig: int = 4) -> str:
    """Format 'value ± error': value to n_sig sig figs, error to same decimal places."""
    import math
    if not (np.isfinite(val) and np.isfinite(err) and err > 0.0):
        return f"{val:.{n_sig}g} ± n/a"
    mag = math.floor(math.log10(abs(val))) if val != 0.0 else 0
    decimals = max(0, n_sig - 1 - mag)
    fmt = f"{{:.{decimals}f}}"
    return f"{fmt.format(val)} ± {fmt.format(err)}"


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


@dataclass(frozen=True)
class ExponentialHeatLeakFit:
    """Summary of an exponential ambient-return heat-leak fit."""

    ambient_c: float
    capacity_j_per_k: float
    initial_temp_c: float
    ua_w_per_k: float
    ua_sigma_w_per_k: float
    tau_s: float
    requested_fit_start_min: float
    fit_start_min: float
    fit_end_min: float
    fit_duration_min: float
    n_samples: int
    median_pump_input_w: float
    heat_leak_start_w: float
    heat_leak_median_w: float
    heat_leak_end_w: float
    heat_leak_sigma_w: float
    residual_std_C: float
    rmse_C: float
    r_squared: float
    fit_time_s: np.ndarray
    fit_elapsed_s: np.ndarray
    fit_temperature_C: np.ndarray
    predicted_temperature_C: np.ndarray

    def predict_temperature_C(self, elapsed_s: Iterable[float]) -> np.ndarray:
        """Return the fitted temperature for elapsed times measured from fit start."""
        return _exponential_heat_leak_temperature_model(
            elapsed_s,
            self.ua_w_per_k,
            ambient_c=self.ambient_c,
            capacity_j_per_k=self.capacity_j_per_k,
            initial_temp_c=self.initial_temp_c,
        )

    def predict_heat_leak_W(self, elapsed_s: Iterable[float]) -> np.ndarray:
        """Return the fitted ambient heat leak for elapsed times measured from fit start."""
        predicted_temp_C = self.predict_temperature_C(elapsed_s)
        return self.ua_w_per_k * np.clip(self.ambient_c - predicted_temp_C, 0.0, None)

    def heat_leak_at_temperature_C(self, temperature_c: float) -> float:
        """Return the fitted heat leak at a specific temperature."""
        return float(self.ua_w_per_k * max(self.ambient_c - float(temperature_c), 0.0))

    def heat_leak_sigma_at_temperature_C(self, temperature_c: float) -> float:
        """Return the 1σ uncertainty for the fitted heat leak at a specific temperature."""
        return float(self.ua_sigma_w_per_k * max(self.ambient_c - float(temperature_c), 0.0))


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


def fit_warmup_segment(
    df: pd.DataFrame,
    *,
    active_hfe_liquid_kg: float,
    temperature_col: str,
    time_col: str = "time_s",
    pump_power_col: str | None = "pump_input_power_w",
    fit_start_min: float = 0.0,
    min_samples: int = 10,
    model: SystemModel | None = None,
    sigma_mass_kg: float = 0.3,
    sigma_ambient_c: float = 2.0,
) -> "ExponentialHeatLeakFit":
    """
    Fit an exponential heat-leak model to a warmup segment.

    Combines capacity computation (HFE + steel hardware), ambient reference, and
    UA seeding from the system model into one call.  ``active_hfe_liquid_kg`` is
    the recirculating liquid mass; all hardware geometry comes from ``model``
    (defaults to ``default_system_model()``).
    """
    if model is None:
        model = default_system_model()

    ambient_c = float(model.ambient_temp_k - 273.15)
    ua_seed = float(ambient_leak_ua_w_per_k(model, use_insulation=True))
    steel_cp_j_per_k = float(model.steel_mass_kg * model.steel_cp_j_kgk)

    # Loop volume is fixed, so as HFE densifies the circulating mass increases:
    # m_active(T) = m_room * rho(T) / rho(T_room)
    rho_room = hfe_density_kg_m3(model.ambient_temp_k)
    work = df.copy()
    work["_capacity_j_per_k"] = [
        active_hfe_liquid_kg * (hfe_density_kg_m3(float(T) + 273.15) / rho_room)
        * hfe_specific_heat_j_kgk(float(T) + 273.15) + steel_cp_j_per_k
        for T in work[temperature_col]
    ]

    _fit_kwargs = dict(
        temperature_col=temperature_col,
        capacity_col="_capacity_j_per_k",
        time_col=time_col,
        pump_power_col=pump_power_col,
        fit_start_min=fit_start_min,
        ua_seed_w_per_k=ua_seed,
        min_samples=min_samples,
        use_fm_instrument_sigma=True,
    )
    result = fit_heat_leak_exponential(work, ambient_c=ambient_c, **_fit_kwargs)

    # Mass uncertainty: mass is conserved at all temperatures; C_eff = m·c_p(T) + m_steel·c_steel.
    # τ is constrained by the data shape, so UA = C_eff/τ scales with C_eff.
    # Only the HFE fraction of C_eff is affected by σ_m (steel mass is exact).
    hfe_fraction = (result.capacity_j_per_k - steel_cp_j_per_k) / result.capacity_j_per_k
    sigma_ua_mass = result.ua_w_per_k * hfe_fraction * sigma_mass_kg / active_hfe_liquid_kg

    # T_∞ uncertainty: numerical derivative via a perturbed fit (step = σ/4 for linearity).
    eps = sigma_ambient_c / 4.0
    work_pert = work.copy()
    result_pert = fit_heat_leak_exponential(work_pert, ambient_c=ambient_c + eps, **_fit_kwargs)
    sigma_ua_tinf = abs(result_pert.ua_w_per_k - result.ua_w_per_k) / eps * sigma_ambient_c

    sigma_ua_total = float(np.sqrt(
        result.ua_sigma_w_per_k**2 + sigma_ua_mass**2 + sigma_ua_tinf**2
    ))
    return dataclass_replace(result, ua_sigma_w_per_k=sigma_ua_total)


def plot_warmup_segment_fits(
    warmup_defs: List[Tuple],
    df: pd.DataFrame,
    *,
    active_hfe_liquid_kg: float,
    target_cold_temp_c: float = -110.0,
    temperature_col: str = "temperature_c_si",
    time_col: str = "time_s",
    pump_power_col: str | None = "pump_input_power_w",
    min_samples: int = 10,
    model: SystemModel | None = None,
    sigma_mass_kg: float = 0.3,
    sigma_ambient_c: float = 2.0,
) -> Tuple["plt.Figure", List[Dict]]:
    """
    Fit and plot warmup segments with a summary table.

    ``warmup_defs`` is a list of ``(label, color, description, t_start_s, t_end_s,
    fit_start_min)`` tuples.  Returns ``(fig, warmup_results)`` where
    ``warmup_results`` is a list of dicts with keys ``label``, ``color``,
    ``desc``, ``fit_start_min``, ``seg``, ``fit``.
    """
    warmup_results = []
    for label, color, desc, t_start, t_end, fit_start_min in warmup_defs:
        seg = df[df[time_col].between(t_start, t_end)].copy().reset_index(drop=True)
        seg["elapsed_min"] = (seg[time_col] - float(seg[time_col].iloc[0])) / 60.0
        fit = fit_warmup_segment(
            seg,
            active_hfe_liquid_kg=active_hfe_liquid_kg,
            temperature_col=temperature_col,
            time_col=time_col,
            pump_power_col=pump_power_col,
            fit_start_min=fit_start_min,
            min_samples=min_samples,
            model=model,
            sigma_mass_kg=sigma_mass_kg,
            sigma_ambient_c=sigma_ambient_c,
        )
        warmup_results.append({"label": label, "color": color, "desc": desc,
                                "fit_start_min": fit_start_min, "seg": seg, "fit": fit})

    x_max_min = max(float(r["seg"]["elapsed_min"].iloc[-1]) for r in warmup_results) * 1.04

    fig = plt.figure(figsize=(12, 9.0))
    grid = fig.add_gridspec(2, 1, height_ratios=[7.0, 1.6], hspace=0.22)
    ax = fig.add_subplot(grid[0])
    table_ax = fig.add_subplot(grid[1])
    table_ax.axis("off")

    for result in warmup_results:
        seg, fit, color = result["seg"], result["fit"], result["color"]
        fit_time_min = fit.fit_start_min + fit.fit_elapsed_s / 60.0
        fit_curve_elapsed_min = np.linspace(fit.fit_start_min, fit.fit_end_min, 250)
        fit_curve_temp_c = fit.predict_temperature_C((fit_curve_elapsed_min - fit.fit_start_min) * 60.0)
        q_w = fit.heat_leak_at_temperature_C(target_cold_temp_c)
        q_err_w = fit.heat_leak_sigma_at_temperature_C(target_cold_temp_c)

        raw_t = seg["elapsed_min"].to_numpy(float)
        raw_T = seg[temperature_col].to_numpy(float)
        sigma_raw = np.sqrt(0.5**2 + (0.005 * (raw_T + 273.15))**2)
        sigma_model = np.sqrt(0.5**2 + (0.005 * (fit_curve_temp_c + 273.15))**2)
        ax.fill_between(raw_t, raw_T - sigma_raw, raw_T + sigma_raw, color=color, alpha=0.12)
        ax.plot(raw_t, raw_T, color=color, alpha=0.25, lw=1.2)
        ax.plot(fit_time_min, fit.fit_temperature_C, color=color, alpha=0.55, lw=1.2)
        ax.fill_between(fit_curve_elapsed_min,
                        fit_curve_temp_c - sigma_model, fit_curve_temp_c + sigma_model,
                        color=color, alpha=0.18)
        ax.plot(fit_curve_elapsed_min, fit_curve_temp_c, color=color, lw=2.2, linestyle="--",
                label=f"{result['label']} ({result['desc']})")
        ax.axvline(result["fit_start_min"], color=color, lw=0.8, linestyle=":", alpha=0.7)

    ax.text(
        0.98, 0.97,
        r"$T(t) = T_\infty + (T_0 - T_\infty)\,e^{-t/\tau}$"
        "\n"
        r"$\tau = C_\mathrm{eff} / UA \qquad Q_\mathrm{leak}(T) = UA\,(T_\infty - T)$",
        transform=ax.transAxes, va="top", ha="right", fontsize=9,
        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="0.75", alpha=0.9),
    )
    ax.set_xlim(left=0.0, right=x_max_min * 1.10)
    ax.set_ylim(-60, 20)
    ax.set_xlabel("Elapsed warmup time [min]")
    ax.set_ylabel("Flow-meter temperature [°C]")
    ax.set_title("Heat leaks from warmup segments  —  bypass open")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)

    table_rows = [
        [
            r["label"], r["desc"],
            f"{r['fit_start_min']:.0f}",
            _fmt_val_err(r["fit"].ua_w_per_k, r["fit"].ua_sigma_w_per_k),
            _fmt_val_err(
                r["fit"].tau_s / 60.0,
                r["fit"].tau_s / 60.0 * r["fit"].ua_sigma_w_per_k / r["fit"].ua_w_per_k,
            ),
            _fmt_val_err(
                r["fit"].heat_leak_at_temperature_C(target_cold_temp_c),
                r["fit"].heat_leak_sigma_at_temperature_C(target_cold_temp_c),
            ),
        ]
        for r in warmup_results
    ]
    table = table_ax.table(
        cellText=table_rows,
        colLabels=["Segment", "Description", "Residual cooling cut (min)",
                   "UA (W/K)", "τ (min)", f"Q leak @ {target_cold_temp_c:.0f} °C (W)"],
        cellLoc="left", colLoc="left",
        bbox=(0.0, 0.02, 1.0, 0.88),
        colWidths=[0.07, 0.26, 0.17, 0.16, 0.12, 0.22],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.1)
    table.scale(1.0, 1.55)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("0.7")
        cell.PAD = 0.03
        if row == 0:
            cell.set_facecolor("#f2f2f2")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("white")

    fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.04)
    pos = table_ax.get_position()
    table_ax.set_position([0.02, pos.y0, 0.96, pos.height])

    return fig, warmup_results


def plot_cooldown_power_summary(
    phase_defs: List[Tuple],
    *,
    ua_ambient_w_per_k: float,
    active_hfe_liquid_kg: float,
    rolling_window_s: float = 180.0,
    target_cold_temp_c: float = -110.0,
    temperature_col: str = "temperature_c_si",
    tc_mean_col: str | None = "temp_mean_C",
    time_col: str = "time_s",
    pump_power_col: str = "pump_input_power_w",
    pump_cmd_col: str = "pump_cmd_pct",
    model: SystemModel | None = None,
) -> Tuple["plt.Figure", pd.DataFrame]:
    """
    Compute per-phase cooldown power and produce a plot with summary table.

    ``phase_defs`` is a list of ``(label, color, dataframe)`` tuples where each
    dataframe covers one phase.  ``ua_ambient_w_per_k`` should come from a
    warmup exponential fit (e.g. the W3 segment).

    Energy balance:  Q_HX = Q_net + Q_amb + Q_pump
      Q_net = -C_eff · dT/dt   (net energy removed from inventory)
      Q_amb = UA · (T_∞ - T)   (ambient heat leaking in)

    Returns ``(fig, phase_power_summary)`` where the DataFrame index matches the
    phase labels and includes ``cooldown_gross_hx_power_w`` and
    ``warmup_ambient_heat_leak_est_w`` for downstream cells.
    """
    if model is None:
        model = default_system_model()
    ambient_c = float(model.ambient_temp_k - 273.15)
    steel_cp = float(model.steel_mass_kg * model.steel_cp_j_kgk)
    rho_room = hfe_density_kg_m3(model.ambient_temp_k)

    rows: List[Dict] = []
    segments: List[Tuple] = []  # (label, color, cooldown_frame, full_work_frame, fit_start_min, desc)

    for phase_def in phase_defs:
        label, color, df = phase_def[0], phase_def[1], phase_def[2]
        fit_start_min = float(phase_def[3]) if len(phase_def) > 3 else 0.0
        desc = str(phase_def[4]) if len(phase_def) > 4 else ""
        work = df.copy().sort_values(time_col).reset_index(drop=True)

        if tc_mean_col and tc_mean_col in work.columns:
            bulk = work[tc_mean_col].where(work[tc_mean_col].notna(), work[temperature_col])
        else:
            bulk = work[temperature_col].copy()
        bulk_arr = bulk.to_numpy(float)

        capacity = np.array([
            active_hfe_liquid_kg * (hfe_density_kg_m3(float(T) + 273.15) / rho_room)
            * hfe_specific_heat_j_kgk(float(T) + 273.15) + steel_cp
            for T in bulk_arr
        ])
        rate = rolling_slope(work[time_col].to_numpy(float), bulk_arr, rolling_window_s)
        pump = pd.to_numeric(work[pump_power_col], errors="coerce").clip(lower=0.0).to_numpy(float)

        q_net = -capacity * rate
        q_amb = ua_ambient_w_per_k * np.clip(ambient_c - bulk_arr, 0.0, None)
        q_hx = np.clip(q_net + q_amb + pump, 0.0, None)

        work["elapsed_min"] = (work[time_col] - float(work[time_col].iloc[0])) / 60.0
        work["q_net_w"] = q_net
        work["q_hx_w"] = q_hx

        turn = int(bulk.idxmin())
        cd = work.iloc[: turn + 1].copy()
        segments.append((label, color, cd, work, fit_start_min, desc))

        rows.append({
            "phase": label,
            "cooldown_duration_min": float((cd[time_col].iloc[-1] - cd[time_col].iloc[0]) / 60.0) if len(cd) > 1 else float("nan"),
            "cooldown_rate_c_per_min": float(max(-rate[: turn + 1].mean() * 60.0, 0.0)),
            "cooldown_q_net_w": float(np.clip(q_net[: turn + 1], 0.0, None).mean()),
            "cooldown_q_amb_w": float(q_amb[: turn + 1].mean()),
            "cooldown_gross_hx_power_w": float(q_hx[: turn + 1].mean()),
            "warmup_ambient_heat_leak_est_w": float(q_amb[turn:].mean()),
        })

    summary = pd.DataFrame(rows).set_index("phase")

    fig = plt.figure(figsize=(12, 9.0))
    grid = fig.add_gridspec(2, 1, height_ratios=[6.5, 1.8], hspace=0.28)
    ax_temp = fig.add_subplot(grid[0])
    table_ax = fig.add_subplot(grid[1])
    table_ax.axis("off")

    q_target = float(ua_ambient_w_per_k * max(ambient_c - target_cold_temp_c, 0.0))
    table_data = []
    for label, color, cd, work, fit_start_min, desc in segments:
        T_arr = work[temperature_col].to_numpy(float)
        t_arr = work["elapsed_min"].to_numpy(float)
        t_s = work[time_col].to_numpy(float)

        # Full segment: faded raw data + FM error band (pre-fit visual)
        sigma_full = np.sqrt(0.5**2 + (0.005 * (T_arr + 273.15))**2)
        ax_temp.fill_between(t_arr, T_arr - sigma_full, T_arr + sigma_full, color=color, alpha=0.08)
        ax_temp.plot(t_arr, T_arr, color=color, lw=1.2, alpha=0.25)

        # Linear fit — apply start cut
        fit_mask = t_arr >= fit_start_min
        t_s_fit = t_s[fit_mask]
        T_arr_fit = T_arr[fit_mask]
        t_arr_fit = t_arr[fit_mask]
        if len(T_arr_fit) < 3:
            t_s_fit, T_arr_fit, t_arr_fit = t_s, T_arr, t_arr
        t_c = t_s_fit - t_s_fit.mean()
        denom = float(np.dot(t_c, t_c))
        slope_C_per_s = float(np.dot(t_c, T_arr_fit) / denom)
        intercept = float(T_arr_fit.mean() - slope_C_per_s * t_s_fit.mean())
        T_fit = slope_C_per_s * t_s_fit + intercept
        residual_std = float(np.std(T_arr_fit - T_fit, ddof=2)) if len(T_arr_fit) > 2 else float("nan")
        sigma_slope_C_per_s = residual_std / float(np.sqrt(denom)) if np.isfinite(residual_std) else float("nan")

        # Fit portion: brighter raw data overlay + FM error band
        sigma_fit = np.sqrt(0.5**2 + (0.005 * (T_arr_fit + 273.15))**2)
        ax_temp.fill_between(t_arr_fit, T_arr_fit - sigma_fit, T_arr_fit + sigma_fit, color=color, alpha=0.15)
        ax_temp.plot(t_arr_fit, T_arr_fit, color=color, lw=1.2, alpha=0.55)

        if fit_start_min > 0.0:
            ax_temp.axvline(fit_start_min, color=color, lw=1.2, linestyle=":", alpha=0.85)
        ax_temp.fill_between(t_arr_fit, T_fit - residual_std, T_fit + residual_std,
                             color=color, alpha=0.25)
        ax_temp.plot(t_arr_fit, T_fit, color=color, lw=2.0, linestyle="--")

        # Q_HX at target temperature
        C_eff_target = (
            active_hfe_liquid_kg * (hfe_density_kg_m3(target_cold_temp_c + 273.15) / rho_room)
            * hfe_specific_heat_j_kgk(target_cold_temp_c + 273.15) + steel_cp
        )
        q_net_target = float(C_eff_target * abs(slope_C_per_s))
        q_hx_target = q_net_target + q_target
        sigma_q_hx_target = float(C_eff_target * sigma_slope_C_per_s)

        # HX UA from average operating point of fit window
        T_LN2_C = -196.0
        T_avg_fit = float(T_arr_fit.mean())
        C_eff_avg = float(np.mean([
            active_hfe_liquid_kg * (hfe_density_kg_m3(float(T) + 273.15) / rho_room)
            * hfe_specific_heat_j_kgk(float(T) + 273.15) + steel_cp
            for T in T_arr_fit
        ]))
        q_net_avg = float(C_eff_avg * abs(slope_C_per_s))
        q_amb_avg = float(ua_ambient_w_per_k * max(ambient_c - T_avg_fit, 0.0))
        q_hx_avg = q_net_avg + q_amb_avg
        delta_T_hx = max(T_avg_fit - T_LN2_C, 1.0)
        ua_hx = q_hx_avg / delta_T_hx
        sigma_ua_hx = float(C_eff_avg * sigma_slope_C_per_s) / delta_T_hx

        slope_C_per_min = slope_C_per_s * 60.0
        sigma_slope_C_per_min = sigma_slope_C_per_s * 60.0

        table_data.append([
            label,
            desc,
            f"{fit_start_min:.1f}",
            _fmt_val_err(slope_C_per_min, sigma_slope_C_per_min),
            _fmt_val_err(ua_hx, sigma_ua_hx),
            _fmt_val_err(q_hx_target, sigma_q_hx_target),
        ])

    ax_temp.text(
        0.98, 0.97,
        r"$Q_\mathrm{HX} = C_\mathrm{eff}\,|\dot{T}| + UA_\mathrm{leak}\,(T_\infty - \bar{T})$"
        "\n"
        r"$UA_\mathrm{HX} = Q_\mathrm{HX}\,/\,({\bar{T}} - T_\mathrm{LN_2})$"
        rf"$\quad T_{{\mathrm{{LN_2}}}} = {-196.0:.0f}\,°C$"
        "\n"
        rf"$UA_{{\mathrm{{leak}}}} = {ua_ambient_w_per_k:.3f}$ W/K  (warmup calibration)",
        transform=ax_temp.transAxes, va="top", ha="right", fontsize=9,
        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="0.75", alpha=0.9),
    )
    ax_temp.set_xlabel("Elapsed phase time [min]")
    ax_temp.set_ylabel("Flow-meter temperature [°C]")
    ax_temp.set_title("Cooldown segments — temperature and linear fit")
    from matplotlib.lines import Line2D
    seg_handles = [
        Line2D([0], [0], color=color, lw=1.5, label=f"{label} ({desc})" if desc else label)
        for label, color, _cd, _work, _fs, desc in segments
    ]
    ax_temp.legend(handles=seg_handles, loc="center right", fontsize=9)
    ax_temp.grid(True, which="major", alpha=0.35)
    ax_temp.minorticks_on()
    ax_temp.grid(True, which="minor", alpha=0.15)

    table = table_ax.table(
        cellText=table_data,
        colLabels=["Phase", "Description", "Fit start (min)", "dT/dt (°C/min)",
                   "HX UA (W/K)", f"Q_HX @ {target_cold_temp_c:.0f}°C (W)"],
        cellLoc="left", colLoc="left",
        bbox=(0.0, 0.02, 1.0, 0.88),
        colWidths=[0.07, 0.28, 0.11, 0.22, 0.16, 0.22],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.1)
    table.scale(1.0, 1.55)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("0.7")
        cell.PAD = 0.03
        if row == 0:
            cell.set_facecolor("#f2f2f2")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("white")

    fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.04)
    pos = table_ax.get_position()
    table_ax.set_position([0.02, pos.y0, 0.96, pos.height])

    return fig, summary


def _exponential_heat_leak_temperature_model(
    elapsed_s: Iterable[float],
    ua_w_per_k: float,
    *,
    ambient_c: float,
    capacity_j_per_k: float,
    initial_temp_c: float,
) -> np.ndarray:
    """Return the ambient-return temperature model for a warmup segment."""
    elapsed = np.asarray(list(elapsed_s), dtype=float)
    ua = max(float(ua_w_per_k), 1e-9)
    capacity = max(float(capacity_j_per_k), 1e-9)
    return ambient_c - (ambient_c - initial_temp_c) * np.exp(-elapsed * ua / capacity)


def fit_heat_leak_exponential(
    df: pd.DataFrame,
    *,
    ambient_c: float,
    temperature_col: str,
    capacity_col: str,
    time_col: str = "time_s",
    pump_power_col: str | None = "pump_input_power_w",
    fit_start_min: float = 0.0,
    ua_seed_w_per_k: float = 1.0,
    max_ua_w_per_k: float = 20.0,
    min_samples: int = 10,
    use_fm_instrument_sigma: bool = False,
) -> ExponentialHeatLeakFit:
    """
    Fit an exponential ambient-return model to a warmup segment.

    Parameters
    ----------
    df:
        Dataframe containing a single warmup segment.
    ambient_c:
        Ambient reference temperature in degC.
    temperature_col:
        Column containing the temperature to fit.
    capacity_col:
        Column containing the effective thermal capacity [J/K].
    time_col:
        Timestamp column in seconds.
    pump_power_col:
        Optional pump-input column kept for reporting context.
    fit_start_min:
        Requested fit start, relative to the beginning of ``df``, in minutes.
    ua_seed_w_per_k:
        Initial guess for the fitted UA.
    max_ua_w_per_k:
        Upper fit bound for the UA parameter.
    min_samples:
        Minimum number of fit samples required.
    """
    required_columns = [time_col, temperature_col, capacity_col]
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        missing = ", ".join(repr(column) for column in missing_columns)
        raise ValueError(f"Missing required columns for exponential heat-leak fit: {missing}.")

    ordered = (
        df.copy()
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=required_columns)
        .sort_values(time_col)
        .reset_index(drop=True)
    )
    if ordered.empty:
        raise ValueError("No valid samples are available for the exponential heat-leak fit.")

    segment_start_time_s = float(ordered[time_col].iloc[0])
    ordered["elapsed_min"] = (ordered[time_col] - segment_start_time_s) / 60.0
    fit_subset = ordered[ordered["elapsed_min"] >= float(fit_start_min)].copy().reset_index(drop=True)
    if fit_subset.shape[0] < min_samples:
        raise ValueError(
            "Insufficient samples remain after the requested fit start; "
            f"need at least {min_samples}, found {fit_subset.shape[0]}."
        )

    capacity_j_per_k = float(np.nanmean(fit_subset[capacity_col]))
    if not np.isfinite(capacity_j_per_k) or capacity_j_per_k <= 0.0:
        raise ValueError("A positive finite effective heat capacity is required for the fit.")

    fit_time_s = fit_subset[time_col].to_numpy(dtype=float)
    fit_elapsed_s = fit_time_s - float(fit_time_s[0])
    fit_temperature_C = fit_subset[temperature_col].to_numpy(dtype=float)
    initial_temp_c = float(fit_temperature_C[0])

    median_pump_input_w = float("nan")
    if pump_power_col is not None and pump_power_col in fit_subset.columns:
        pump_series = pd.to_numeric(fit_subset[pump_power_col], errors="coerce")
        if pump_series.notna().any():
            median_pump_input_w = float(pump_series.median())

    # OPTIMASS 6000 spec: ±0.5 °C ± 0.5% of reading (K); computed from actual fit temps
    fit_sigma = (
        np.sqrt(0.5**2 + (0.005 * (fit_temperature_C + 273.15))**2)
        if use_fm_instrument_sigma else None
    )

    params, covariance = curve_fit(
        lambda elapsed_s, ua_w_per_k: _exponential_heat_leak_temperature_model(
            elapsed_s,
            ua_w_per_k,
            ambient_c=ambient_c,
            capacity_j_per_k=capacity_j_per_k,
            initial_temp_c=initial_temp_c,
        ),
        fit_elapsed_s,
        fit_temperature_C,
        p0=[max(float(ua_seed_w_per_k), 1e-6)],
        bounds=([1e-6], [max(float(max_ua_w_per_k), 1e-6)]),
        sigma=fit_sigma,
        absolute_sigma=True,
        maxfev=20_000,
    )

    ua_w_per_k = float(params[0])
    if np.ndim(covariance) == 2 and covariance.shape == (1, 1):
        ua_sigma_w_per_k = float(np.sqrt(np.clip(covariance[0, 0], 0.0, None)))
    else:
        ua_sigma_w_per_k = float("nan")

    predicted_temperature_C = _exponential_heat_leak_temperature_model(
        fit_elapsed_s,
        ua_w_per_k,
        ambient_c=ambient_c,
        capacity_j_per_k=capacity_j_per_k,
        initial_temp_c=initial_temp_c,
    )
    heat_leak_W = ua_w_per_k * np.clip(ambient_c - predicted_temperature_C, 0.0, None)
    residuals_C = fit_temperature_C - predicted_temperature_C
    ss_res = float(np.dot(residuals_C, residuals_C))
    ss_tot = float(np.dot(fit_temperature_C - fit_temperature_C.mean(), fit_temperature_C - fit_temperature_C.mean()))
    rmse_C = float(np.sqrt(np.mean(residuals_C**2)))
    residual_std_C = float(np.sqrt(ss_res / max(fit_subset.shape[0] - 1, 1)))
    r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")
    fit_time_min = (fit_time_s - segment_start_time_s) / 60.0
    fit_temp_median_c = float(np.nanmedian(predicted_temperature_C))

    return ExponentialHeatLeakFit(
        ambient_c=float(ambient_c),
        capacity_j_per_k=capacity_j_per_k,
        initial_temp_c=initial_temp_c,
        ua_w_per_k=ua_w_per_k,
        ua_sigma_w_per_k=ua_sigma_w_per_k,
        tau_s=float(capacity_j_per_k / ua_w_per_k),
        requested_fit_start_min=float(fit_start_min),
        fit_start_min=float(fit_time_min[0]),
        fit_end_min=float(fit_time_min[-1]),
        fit_duration_min=float(fit_time_min[-1] - fit_time_min[0]),
        n_samples=int(fit_subset.shape[0]),
        median_pump_input_w=median_pump_input_w,
        heat_leak_start_w=float(heat_leak_W[0]),
        heat_leak_median_w=float(np.nanmedian(heat_leak_W)),
        heat_leak_end_w=float(heat_leak_W[-1]),
        heat_leak_sigma_w=float(max(ambient_c - fit_temp_median_c, 0.0) * ua_sigma_w_per_k),
        residual_std_C=residual_std_C,
        rmse_C=rmse_C,
        r_squared=r_squared,
        fit_time_s=fit_time_s,
        fit_elapsed_s=fit_elapsed_s,
        fit_temperature_C=fit_temperature_C,
        predicted_temperature_C=predicted_temperature_C,
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
    slope_sigma_C_per_min: float
    slope_sigma_C_per_s: float
    P_bath_sigma_W: float
    P_HX_sigma_W: float
    UA_sigma_W_per_K: float
    heat_flux_sigma_W_m2: float
    UA_area_sigma_W_per_m2K: float
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
    time_centered = time_min - time_min.mean()
    A = np.vstack([time_min, np.ones_like(time_min)]).T
    slope_C_per_min, intercept_C = np.linalg.lstsq(A, temps_C, rcond=None)[0]
    fitted_C = slope_C_per_min * time_min + intercept_C

    residuals = temps_C - fitted_C
    temps_centered = temps_C - temps_C.mean()
    ss_tot = float(np.dot(temps_centered, temps_centered))
    ss_res = float(np.dot(residuals, residuals))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")
    sum_t_center_sq = float(np.dot(time_centered, time_centered))

    slope_C_per_s = slope_C_per_min / 60.0
    P_bath_fit_W = -Cp_JK * slope_C_per_s
    P_HX_fit_W = P_bath_fit_W + heat_leak_W

    deltaT_mean_C = float(np.nanmean(subset["DeltaT_C"]))
    UA_fit_W_per_K = P_HX_fit_W / deltaT_mean_C if abs(deltaT_mean_C) > 1e-6 else float("nan")
    heat_flux_fit_W_m2 = P_HX_fit_W / hx_area_m2
    UA_area_fit_W_per_m2K = (
        UA_fit_W_per_K / hx_area_m2 if np.isfinite(UA_fit_W_per_K) else float("nan")
    )

    slope_sigma_C_per_min = float("nan")
    slope_sigma_C_per_s = float("nan")
    P_bath_sigma_W = float("nan")
    P_HX_sigma_W = float("nan")
    UA_sigma_W_per_K = float("nan")
    heat_flux_sigma_W_m2 = float("nan")
    UA_area_sigma_W_per_m2K = float("nan")
    if subset.shape[0] > 2 and sum_t_center_sq > 0.0:
        sigma2 = ss_res / (subset.shape[0] - 2)
        slope_sigma_C_per_min = float(np.sqrt(sigma2 / sum_t_center_sq))
        slope_sigma_C_per_s = slope_sigma_C_per_min / 60.0
        P_bath_sigma_W = float(Cp_JK * slope_sigma_C_per_s)
        P_HX_sigma_W = P_bath_sigma_W
        if abs(deltaT_mean_C) > 1e-6:
            UA_sigma_W_per_K = float(P_HX_sigma_W / deltaT_mean_C)
        if hx_area_m2 > 0.0:
            heat_flux_sigma_W_m2 = float(P_HX_sigma_W / hx_area_m2)
            if np.isfinite(UA_sigma_W_per_K):
                UA_area_sigma_W_per_m2K = float(UA_sigma_W_per_K / hx_area_m2)

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
        slope_sigma_C_per_min=slope_sigma_C_per_min,
        slope_sigma_C_per_s=slope_sigma_C_per_s,
        P_bath_sigma_W=P_bath_sigma_W,
        P_HX_sigma_W=P_HX_sigma_W,
        UA_sigma_W_per_K=UA_sigma_W_per_K,
        heat_flux_sigma_W_m2=heat_flux_sigma_W_m2,
        UA_area_sigma_W_per_m2K=UA_area_sigma_W_per_m2K,
        time_min=time_min,
        temps_C=temps_C,
        fitted_C=fitted_C,
    )


def plot_temperature_window_fit(
    fit: WindowTemperatureFit,
    *,
    title: str | None = None,
    annotate: bool = True,
    figsize: Tuple[float, float] | None = None,
    axis_fontsize: float | None = None,
    legend_fontsize: float | None = None,
    annot_fontsize: float | None = None,
) -> "plt.Figure":
    """
    Plot the windowed bulk-temperature samples with the corresponding best-fit line.
    """
    if figsize is None:
        figsize = (7.0, 4.2)
    fig, ax = plt.subplots(figsize=figsize)
    ax.scatter(fit.time_min, fit.temps_C, s=14, alpha=0.6, label="Bulk mean samples")
    order = np.argsort(fit.time_min)
    ax.plot(
        fit.time_min[order],
        fit.fitted_C[order],
        color="tab:red",
        linewidth=2,
        label=f"Fit slope = {fit.slope_C_per_min:.3f} °C/min",
    )
    ax.set_xlabel("Time (min)", fontsize=axis_fontsize)
    ax.set_ylabel("Bulk mean temperature (°C)", fontsize=axis_fontsize)
    ax.set_title(title or fit.dataset)
    ax.grid(True, alpha=0.3)
    if axis_fontsize is not None:
        ax.tick_params(axis="both", labelsize=axis_fontsize)
    ax.legend(loc="best", fontsize=legend_fontsize)

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
            0.02,
            0.02,
            metrics_text,
            transform=ax.transAxes,
            fontsize=annot_fontsize if annot_fontsize is not None else 9,
            va="bottom",
            ha="left",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
        )
    fig.tight_layout()
    return fig


def _ensure_means(df: pd.DataFrame) -> pd.DataFrame:
    """Add bulk/coil mean columns if they are not already present."""

    data = df.copy()
    if "T_bulk_mean_C" not in data.columns and {"U1_bottom_C", "U3_top_C"}.issubset(data.columns):
        data["T_bulk_mean_C"] = data[["U1_bottom_C", "U3_top_C"]].mean(axis=1)
    if "T_coil_mean_C" not in data.columns and {"U2_coilTop_C", "U4_coilMid_C"}.issubset(data.columns):
        data["T_coil_mean_C"] = data[["U2_coilTop_C", "U4_coilMid_C"]].mean(axis=1)
    return data


def plot_temperatures(
    df: pd.DataFrame,
    *,
    title: str,
    include_valve: bool = False,
    valve_label: str = "Valve state",
    height_scale: float = 1.0,
    figsize: Tuple[float, float] | None = None,
    axis_fontsize: float | None = None,
    legend_fontsize: float | None = None,
    title_fontsize: float | None = None,
) -> Tuple[plt.Figure, plt.Axes]:
    """Plot the main temperature channels for a dataset."""

    data = _ensure_means(df)
    if figsize is None:
        figsize = (8, 4 * height_scale * 1.15)
    fig, axis = plt.subplots(figsize=figsize)
    axis.plot(data["t_min"], data["U1_bottom_C"], label="U1 bottom")
    axis.plot(data["t_min"], data["U3_top_C"], label="U3 top")
    axis.plot(data["t_min"], data["U2_coilTop_C"], label="U2 coil top")
    axis.plot(data["t_min"], data["U4_coilMid_C"], label="U4 coil mid")
    if "T_bulk_mean_C" in data.columns:
        axis.plot(
            data["t_min"],
            data["T_bulk_mean_C"],
            color="tab:purple",
            linewidth=1,
            linestyle="--",
            label="Bulk mean",
        )
    if "T_coil_mean_C" in data.columns:
        axis.plot(
            data["t_min"],
            data["T_coil_mean_C"],
            color="tab:brown",
            linestyle="--",
            linewidth=1,
            label="Coil mean",
        )
    axis.set_xlabel("Time (min)", fontsize=axis_fontsize)
    axis.set_ylabel("Temperature (degC)", fontsize=axis_fontsize)
    if title_fontsize is None:
        axis.set_title(title)
    else:
        axis.set_title(title, fontsize=title_fontsize)
    axis.grid(True, alpha=0.3)
    if axis_fontsize is not None:
        axis.tick_params(axis="both", labelsize=axis_fontsize)

    handles, labels = axis.get_legend_handles_labels()
    if include_valve and "valve_state" in data.columns:
        times = data["t_min"].to_numpy()
        valve = data["valve_state"].to_numpy()
        shaded_label_used = False
        dt_tail = times[-1] - times[-2] if times.size > 1 else 1.0
        edge_end = times[-1] + dt_tail if times.size else 0.0
        time_edges = np.concatenate([times, [edge_end]])

        def add_span(t0: float, t1: float, label: str | None) -> None:
            span = axis.axvspan(t0, t1, color="skyblue", alpha=0.35, label=label)
            if label is not None:
                handles.append(span)
                labels.append(label)

        if times.size > 0:
            segment_start = 0
            for index in range(1, times.size):
                if valve[index] != valve[segment_start]:
                    if valve[segment_start] >= 0.5:
                        label = f"{valve_label} open" if valve_label and not shaded_label_used else None
                        add_span(time_edges[segment_start], time_edges[index], label)
                        shaded_label_used = True
                    segment_start = index
            if valve[segment_start] >= 0.5:
                label = f"{valve_label} open" if valve_label and not shaded_label_used else None
                add_span(time_edges[segment_start], time_edges[-1], label)

    axis.legend(handles, labels, loc="best", fontsize=legend_fontsize)
    fig.tight_layout()
    return fig, axis


def plot_power_and_flux(
    df: pd.DataFrame,
    *,
    title_prefix: str,
) -> Tuple[plt.Figure, plt.Axes, plt.Axes, plt.Axes]:
    """Plot corrected HX power/UA and per-area flux side by side."""

    fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharex=True)
    ax_power, ax_flux = axes

    ax_power.plot(df["t_min"], df["P_HX_W"], label="P_HX (W)")
    ax_power.set_xlabel("Time (min)")
    ax_power.set_ylabel("HX power (W)")

    ax_power_right = ax_power.twinx()
    ax_power_right.plot(df["t_min"], df["UA_corr_W_per_K"], color="tab:orange", label="UA (W/K)")
    ax_power_right.set_ylabel("UA (W/K)")
    ax_power.set_title(f"{title_prefix}: corrected power & UA")
    ax_power.grid(True, alpha=0.3)

    handles_left, labels_left = ax_power.get_legend_handles_labels()
    handles_right, labels_right = ax_power_right.get_legend_handles_labels()
    combined_handles = handles_left + handles_right
    combined_labels = labels_left + labels_right
    legend = ax_power.legend(combined_handles, combined_labels, loc="best")

    try:
        fig.canvas.draw()
    except Exception:
        pass

    top_like_locs = {
        0: "best",
        1: "upper right",
        2: "upper left",
        5: "right",
        7: "center right",
        9: "upper center",
        "upper right": "upper right",
        "upper left": "upper left",
        "right": "right",
        "center right": "center right",
        "upper center": "upper center",
    }
    legend_at_bottom = False
    location_name = top_like_locs.get(getattr(legend, "_loc", None))
    if location_name in {"upper right", "upper left", "upper center", "right", "center right"}:
        legend.remove()
        ax_power.legend(
            combined_handles,
            combined_labels,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.25),
            ncol=max(1, len(combined_handles)),
            borderaxespad=0.0,
        )
        legend_at_bottom = True

    if "P_HX_W_m2" in df.columns:
        ax_flux.plot(df["t_min"], df["P_HX_W_m2"], label="Heat flux (W/m2)")
    if "UA_per_area_W_per_m2K" in df.columns:
        ax_flux.plot(df["t_min"], df["UA_per_area_W_per_m2K"], label="UA/area (W/m2-K)")
    ax_flux.set_xlabel("Time (min)")
    ax_flux.set_ylabel("Per-area values")
    ax_flux.set_title(f"{title_prefix}: heat & UA flux")
    ax_flux.grid(True, alpha=0.3)
    ax_flux.legend(loc="best")

    if legend_at_bottom:
        fig.tight_layout(rect=(0, 0.18, 1, 1))
    else:
        fig.tight_layout()
    return fig, ax_power, ax_flux, ax_power_right


def plot_heat_leak_fit(
    t_min: Iterable[float],
    temperatures_C: Iterable[float],
    predicted_C: Iterable[float],
    *,
    band_lower_C: Iterable[float] | None = None,
    band_upper_C: Iterable[float] | None = None,
    residuals_C: Iterable[float] | None = None,
    band_label: str = "95% CI",
    r_squared: float | None = None,
) -> Tuple[plt.Figure, plt.Axes, plt.Axes]:
    """Visualize a heat-leak fit with an optional confidence band and residuals."""

    t_min_values = np.asarray(list(t_min), dtype=float)
    temperatures = np.asarray(list(temperatures_C), dtype=float)
    predicted = np.asarray(list(predicted_C), dtype=float)

    fig, (axis, residual_axis) = plt.subplots(
        2,
        1,
        figsize=(8, 4.8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    axis.plot(t_min_values, temperatures, label="Bulk mean", alpha=0.6)
    axis.plot(t_min_values, predicted, color="tab:red", linewidth=2, label="Linear fit")
    if band_lower_C is not None and band_upper_C is not None:
        lower = np.asarray(list(band_lower_C), dtype=float)
        upper = np.asarray(list(band_upper_C), dtype=float)
        axis.fill_between(t_min_values, lower, upper, color="tab:red", alpha=0.2, label=band_label)

    axis.set_ylabel("Temperature (degC)")
    axis.set_title("Warm-up bulk temperature fit")

    if r_squared is None:
        centered = temperatures - temperatures.mean()
        total_sum_sq = float(np.dot(centered, centered))
        residual_sum_sq = float(np.dot(temperatures - predicted, temperatures - predicted))
        r_value = 1.0 - residual_sum_sq / total_sum_sq if total_sum_sq > 0.0 else float("nan")
    else:
        r_value = float(r_squared)
    if np.isfinite(r_value):
        axis.text(
            0.02,
            0.05,
            f"$R^2 = {r_value:.4f}$",
            transform=axis.transAxes,
            fontsize=10,
            ha="left",
            va="bottom",
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
        )
    axis.legend(loc="best")

    residuals = (
        np.asarray(list(residuals_C), dtype=float)
        if residuals_C is not None
        else temperatures - predicted
    )
    residual_axis.plot(t_min_values, residuals, color="tab:gray", linewidth=1)
    residual_axis.axhline(0.0, color="black", linestyle="--", linewidth=1)
    residual_axis.set_xlabel("Time (min)")
    residual_axis.set_ylabel("Residual (degC)")
    residual_axis.set_title("Fit residuals")

    fig.tight_layout()
    return fig, axis, residual_axis
