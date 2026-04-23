"""Helpers for static cryogenic dip tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from .logbook import canonicalize_tc_columns

DEFAULT_REFERENCE_TC_COLUMNS: tuple[str, ...] = ("TFO_C", "TTI_C", "TTO_C", "TMI_C", "THM_C", "THI_C")


@dataclass(frozen=True)
class CryogenicDipStudy:
    """Prepared static dip-test view with derived rates and phase labels."""

    log_path: Path
    data: pd.DataFrame
    probe_column: str
    reference_columns: tuple[str, ...]
    plunge_start_index: int
    turnaround_index: int
    ln2_reference_c: float
    smoothing_window_s: float

    @property
    def probe_label(self) -> str:
        return self.probe_column.removesuffix("_C")

    @property
    def plunge_time_min(self) -> float:
        return float(self.data.loc[self.plunge_start_index, "t_rel_min"])

    @property
    def plunge_temp_c(self) -> float:
        return float(self.data.loc[self.plunge_start_index, "probe_smooth_c"])

    @property
    def turnaround_time_min(self) -> float:
        return float(self.data.loc[self.turnaround_index, "t_rel_min"])

    @property
    def turnaround_temp_c(self) -> float:
        return float(self.data.loc[self.turnaround_index, "probe_smooth_c"])

    @property
    def final_time_min(self) -> float:
        return float(self.data["t_rel_min"].iloc[-1])

    @property
    def final_temp_c(self) -> float:
        return float(self.data["probe_smooth_c"].iloc[-1])


def _rolling_mean(values: pd.Series, window_samples: int) -> pd.Series:
    return values.rolling(window=window_samples, center=True, min_periods=1).mean()


def _window_samples(time_s: pd.Series, window_s: float) -> int:
    dt_s = float(np.nanmedian(np.diff(time_s.to_numpy())))
    if not np.isfinite(dt_s) or dt_s <= 0.0:
        return 5
    samples = max(5, int(round(window_s / dt_s)))
    if samples % 2 == 0:
        samples += 1
    return samples


def _detect_plunge_start(
    data: pd.DataFrame,
    *,
    baseline_window_s: float = 300.0,
    min_time_s: float = 120.0,
    min_drop_c: float = 5.0,
    plunge_rate_threshold_c_s: float = -0.2,
) -> int:
    baseline_mask = data["t_rel_s"] <= baseline_window_s
    baseline_temp_c = float(np.nanmedian(data.loc[baseline_mask, "probe_smooth_c"]))

    candidate_mask = (
        (data["t_rel_s"] >= min_time_s)
        & (data["probe_smooth_c"] <= baseline_temp_c - min_drop_c)
        & (data["probe_rate_c_s"] <= plunge_rate_threshold_c_s)
    )
    candidate_indices = data.index[candidate_mask]
    if len(candidate_indices) > 0:
        return int(candidate_indices[0])

    fallback_mask = (data["t_rel_s"] >= min_time_s) & (data["probe_smooth_c"] <= baseline_temp_c - min_drop_c)
    fallback_indices = data.index[fallback_mask]
    if len(fallback_indices) > 0:
        return int(fallback_indices[0])

    return 0


def prepare_cryogenic_dip_study(
    path: str | Path,
    *,
    probe_column: str = "TTEST_C",
    reference_columns: Sequence[str] = DEFAULT_REFERENCE_TC_COLUMNS,
    smoothing_window_s: float = 45.0,
    ln2_reference_c: float = -196.0,
) -> CryogenicDipStudy:
    """Load a static cryogenic dip log and derive smoothed rates plus phase labels."""

    log_path = Path(path)
    data = canonicalize_tc_columns(pd.read_csv(log_path, comment="#"))
    if probe_column not in data.columns:
        raise ValueError(f"Probe column {probe_column!r} not found in {log_path}.")

    prepared = data.copy()
    prepared["time_s"] = pd.to_numeric(prepared["time_s"], errors="coerce")
    prepared = prepared.dropna(subset=["time_s"]).sort_values("time_s").reset_index(drop=True)
    prepared[probe_column] = pd.to_numeric(prepared[probe_column], errors="coerce")
    prepared["probe_raw_c"] = prepared[probe_column]
    prepared["probe_filled_c"] = prepared["probe_raw_c"].interpolate(limit_direction="both")
    prepared["t_rel_s"] = prepared["time_s"] - float(prepared["time_s"].iloc[0])
    prepared["t_rel_min"] = prepared["t_rel_s"] / 60.0

    window_samples = _window_samples(prepared["t_rel_s"], smoothing_window_s)
    prepared["probe_smooth_c"] = _rolling_mean(prepared["probe_filled_c"], window_samples)
    prepared["probe_rate_c_s"] = np.gradient(
        prepared["probe_smooth_c"].to_numpy(),
        prepared["t_rel_s"].to_numpy(),
    )

    available_reference_columns = tuple(column for column in reference_columns if column in prepared.columns)
    if available_reference_columns:
        for column in available_reference_columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared["reference_mean_c"] = prepared.loc[:, list(available_reference_columns)].mean(axis=1)
    else:
        prepared["reference_mean_c"] = np.nan

    plunge_start_index = _detect_plunge_start(prepared)
    turnaround_index = int(prepared["probe_smooth_c"].idxmin())

    prepared["phase"] = "pre-plunge"
    prepared.loc[plunge_start_index : turnaround_index, "phase"] = "cooldown"
    prepared.loc[turnaround_index:, "phase"] = "warmup"
    prepared["rate_magnitude_c_s"] = np.abs(prepared["probe_rate_c_s"])

    cooling_delta_c = prepared["probe_smooth_c"] - ln2_reference_c
    prepared["cooldown_norm_inv_s"] = np.where(
        cooling_delta_c > 0.0,
        (-prepared["probe_rate_c_s"]) / cooling_delta_c,
        np.nan,
    )

    warmup_delta_c = prepared["reference_mean_c"] - prepared["probe_smooth_c"]
    prepared["warmup_norm_inv_s"] = np.where(
        warmup_delta_c > 0.0,
        prepared["probe_rate_c_s"] / warmup_delta_c,
        np.nan,
    )

    return CryogenicDipStudy(
        log_path=log_path,
        data=prepared,
        probe_column=probe_column,
        reference_columns=available_reference_columns,
        plunge_start_index=plunge_start_index,
        turnaround_index=turnaround_index,
        ln2_reference_c=ln2_reference_c,
        smoothing_window_s=smoothing_window_s,
    )


def summarize_temperature_bands(
    study: CryogenicDipStudy,
    *,
    phase: str,
    band_width_c: float = 5.0,
    min_count: int = 5,
) -> pd.DataFrame:
    """Aggregate rate and dwell statistics in fixed-width temperature bands."""

    subset = study.data.loc[study.data["phase"] == phase].copy()
    if subset.empty:
        return pd.DataFrame()

    lower_edge_c = band_width_c * np.floor(float(subset["probe_smooth_c"].min()) / band_width_c)
    upper_edge_c = band_width_c * np.ceil(float(subset["probe_smooth_c"].max()) / band_width_c)
    bins = np.arange(lower_edge_c, upper_edge_c + band_width_c, band_width_c)
    if bins.size < 2:
        bins = np.array([lower_edge_c, lower_edge_c + band_width_c], dtype=float)

    subset["temperature_band"] = pd.cut(subset["probe_smooth_c"], bins=bins, include_lowest=True)
    grouped = subset.groupby("temperature_band", observed=False)
    summary = grouped.agg(
        temperature_mid_c=("probe_smooth_c", "mean"),
        temperature_min_c=("probe_smooth_c", "min"),
        temperature_max_c=("probe_smooth_c", "max"),
        median_rate_c_s=("rate_magnitude_c_s", "median"),
        median_signed_rate_c_s=("probe_rate_c_s", "median"),
        median_warmup_norm_inv_s=("warmup_norm_inv_s", "median"),
        median_cooldown_norm_inv_s=("cooldown_norm_inv_s", "median"),
        dwell_s=("t_rel_s", lambda values: float(values.max() - values.min()) if len(values) > 0 else np.nan),
        count=("probe_smooth_c", "size"),
    ).reset_index()

    summary["band_center_c"] = summary["temperature_band"].apply(
        lambda interval: float(interval.mid) if pd.notna(interval) else np.nan
    )
    summary["phase"] = phase
    summary = summary.loc[summary["count"] >= min_count].reset_index(drop=True)
    return summary
