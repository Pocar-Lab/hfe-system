
"""Matplotlib-based visualisations for HFE analysis notebooks."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _ensure_means(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "T_bulk_mean_C" not in d.columns and {"U1_bottom_C", "U3_top_C"}.issubset(d.columns):
        d["T_bulk_mean_C"] = d[["U1_bottom_C", "U3_top_C"]].mean(axis=1)
    if "T_coil_mean_C" not in d.columns and {"U2_coilTop_C", "U4_coilMid_C"}.issubset(d.columns):
        d["T_coil_mean_C"] = d[["U2_coilTop_C", "U4_coilMid_C"]].mean(axis=1)
    return d


def plot_temperatures(
    df: pd.DataFrame,
    *,
    title: str,
    include_valve: bool = False,
    valve_label: str = "Valve state",
    height_scale: float = 1.0,
) -> Tuple[plt.Figure, plt.Axes]:
    """Plot the primary temperature channels for a dataset."""
    data = _ensure_means(df)
    fig, ax = plt.subplots(figsize=(8, 4 * height_scale * 1.15))
    ax.plot(data["t_min"], data["U1_bottom_C"], label="U1 bottom")
    ax.plot(data["t_min"], data["U3_top_C"], label="U3 top")
    ax.plot(data["t_min"], data["U2_coilTop_C"], label="U2 coil top")
    ax.plot(data["t_min"], data["U4_coilMid_C"], label="U4 coil mid")
    if "T_bulk_mean_C" in data.columns:
        ax.plot(data['t_min'], data['T_bulk_mean_C'], color='tab:purple', linewidth=1, linestyle="--", label='Bulk mean')
    if "T_coil_mean_C" in data.columns:
        ax.plot(data["t_min"], data["T_coil_mean_C"], color='tab:brown', linestyle="--", linewidth=1, label='Coil mean')
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    handles, labels = ax.get_legend_handles_labels()
    if include_valve and "valve_state" in data.columns:
        times = data["t_min"].to_numpy()
        valve = data["valve_state"].to_numpy()
        shaded_label_used = False
        if times.size > 1:
            dt_tail = times[-1] - times[-2]
        else:
            dt_tail = 1.0
        t_edges = np.concatenate([times, [times[-1] + dt_tail if times.size else 0.0]])

        def add_span(t0: float, t1: float, label: str | None) -> None:
            span = ax.axvspan(t0, t1, color="skyblue", alpha=0.35, label=label)
            if label is not None:
                handles.append(span)
                labels.append(label)

        if times.size > 0:
            segment_start = 0
            for idx in range(1, times.size):
                if valve[idx] != valve[segment_start]:
                    if valve[segment_start] >= 0.5:
                        label_text = f"{valve_label} open" if valve_label and not shaded_label_used else None
                        label = label_text if label_text else None
                        add_span(t_edges[segment_start], t_edges[idx], label)
                        shaded_label_used = True
                    segment_start = idx
            if valve[segment_start] >= 0.5:
                label_text = f"{valve_label} open" if valve_label and not shaded_label_used else None
                label = label_text if label_text else None
                add_span(t_edges[segment_start], t_edges[-1], label)

    ax.legend(handles, labels, loc="best")

    fig.tight_layout()
    return fig, ax


def plot_power_and_flux(
    df: pd.DataFrame,
    *,
    title_prefix: str,
) -> Tuple[plt.Figure, plt.Axes, plt.Axes, plt.Axes]:
    """Plot corrected HX power/UA and per-area fluxes side by side."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharex=True)
    ax_power, ax_flux = axes

    ax_power.plot(df["t_min"], df["P_HX_W"], label="P_HX (W)")
    ax_power.set_xlabel("Time (min)")
    ax_power.set_ylabel("HX power (W)")

    ax_power2 = ax_power.twinx()
    ax_power2.plot(df["t_min"], df["UA_corr_W_per_K"], color="tab:orange", label="UA (W/K)")
    ax_power2.set_ylabel("UA (W/K)")
    ax_power.set_title(f"{title_prefix}: corrected power & UA")
    ax_power.grid(True, alpha=0.3)

    handles_left, labels_left = ax_power.get_legend_handles_labels()
    handles_right, labels_right = ax_power2.get_legend_handles_labels()
    combined_handles = handles_left + handles_right
    combined_labels = labels_left + labels_right
    legend = ax_power.legend(combined_handles, combined_labels, loc="best")

    # Force a draw so Matplotlib resolves the 'best' location before we inspect it.
    try:
        fig.canvas.draw()
    except Exception:
        pass

    top_like_loc_codes = {
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
    legend_loc = getattr(legend, "_loc", None)
    loc_name = top_like_loc_codes.get(legend_loc, legend_loc)
    if loc_name in {"upper right", "upper left", "upper center", "right", "center right"}:
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
        ax_flux.plot(df["t_min"], df["P_HX_W_m2"], label="Heat flux (W/m²)")
    if "UA_per_area_W_per_m2K" in df.columns:
        ax_flux.plot(df["t_min"], df["UA_per_area_W_per_m2K"], label="UA/area (W/m²-K)")
    ax_flux.set_xlabel("Time (min)")
    ax_flux.set_ylabel("Per-area values")
    ax_flux.set_title(f"{title_prefix}: heat & UA flux")
    ax_flux.grid(True, alpha=0.3)
    ax_flux.legend(loc="best")

    if legend_at_bottom:
        fig.tight_layout(rect=(0, 0.18, 1, 1))
    else:
        fig.tight_layout()
    return fig, ax_power, ax_flux, ax_power2


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
    """Visualise a heat-leak linear fit with optional confidence band and residuals."""

    t_min_arr = np.asarray(list(t_min), dtype=float)
    temps = np.asarray(list(temperatures_C), dtype=float)
    pred = np.asarray(list(predicted_C), dtype=float)

    fig, (ax, ax_res) = plt.subplots(
        2,
        1,
        figsize=(8, 4.8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    ax.plot(t_min_arr, temps, label="Bulk mean", alpha=0.6)
    ax.plot(t_min_arr, pred, color="tab:red", linewidth=2, label="Linear fit")
    if band_lower_C is not None and band_upper_C is not None:
        lower = np.asarray(list(band_lower_C), dtype=float)
        upper = np.asarray(list(band_upper_C), dtype=float)
        ax.fill_between(t_min_arr, lower, upper, color="tab:red", alpha=0.2, label=band_label)

    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Warm-up bulk temperature fit")
    if r_squared is None:
        temps_centered = temps - temps.mean()
        ss_tot = float(np.dot(temps_centered, temps_centered))
        ss_res = float(np.dot(temps - pred, temps - pred))
        if ss_tot > 0.0:
            r_val = 1.0 - (ss_res / ss_tot)
        else:
            r_val = float("nan")
    else:
        r_val = float(r_squared)
    if np.isfinite(r_val):
        ax.text(
            0.02,
            0.05,
            f"$R^2 = {r_val:.4f}$",
            transform=ax.transAxes,
            fontsize=10,
            ha="left",
            va="bottom",
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
        )
    ax.legend(loc="best")

    if residuals_C is not None:
        residuals = np.asarray(list(residuals_C), dtype=float)
    else:
        residuals = temps - pred
    ax_res.plot(t_min_arr, residuals, color="tab:gray", linewidth=1)
    ax_res.axhline(0.0, color="black", linestyle="--", linewidth=1)
    ax_res.set_xlabel("Time (min)")
    ax_res.set_ylabel("Residual (°C)")
    ax_res.set_title("Fit residuals")

    fig.tight_layout()
    return fig, ax, ax_res
