"""Helpers for static cryogenic dip tests and HFE-7200 LN2 dip analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy.signal import savgol_filter

from .cooldown import hfe_specific_heat_j_kgk
from .leaks import hfe_liquid_density_kg_m3
from .logbook import (
    apply_legacy_tc_correction,
    canonicalize_tc_columns,
    is_legacy_wrong_type_log,
    read_tc_calibrated_csv,
)

DEFAULT_REFERENCE_TC_COLUMNS: tuple[str, ...] = ("TFO_C", "TTI_C", "TTO_C", "TMI_C", "THM_C", "THI_C")
DEFAULT_THREE_M_REFERENCE_POINTS: tuple[dict[str, str | float], ...] = (
    {"key": "glass_transition", "label": "3M glass transition", "temperature_c": -167.0},
    {"key": "cold_crystallization", "label": "3M cold crystallization", "temperature_c": -120.0},
    {"key": "melt_temperature", "label": "3M melt temperature", "temperature_c": -100.0},
)
DEFAULT_HFE7200_LN2_DIP_RUNS: tuple[dict[str, Any], ...] = (
    {
        "name": "Apr 8 HFE run",
        "filename": "log_20260408_103255.csv",
        "pre_label": "pre-plunge",
        "color": "C0",
        "fill_volume_ml": 10.0,
        "warmup_environment": "air",
    },
    {
        "name": "Apr 9 HFE run",
        "filename": "log_20260409_094317.csv",
        "pre_label": "pre-cycle",
        "color": "C1",
        "fill_volume_ml": 10.0,
        "warmup_environment": "air_then_insulation",
    },
    {
        "name": "Apr 10 HFE run",
        "filename": "log_20260410_112629.csv",
        "pre_label": "pre-plunge",
        "color": "C6",
        "fill_volume_ml": 10.0,
        "warmup_environment": "insulation",
    },
    {
        "name": "Apr 14 HFE run",
        "filename": "log_20260414_113915.csv",
        "pre_label": "pre-plunge",
        "color": "C4",
        "fill_volume_ml": 14.7,
        "warmup_environment": "air_then_insulation",
    },
    {
        "name": "Apr 14 PM HFE run",
        "filename": "log_20260414_154957.csv",
        "pre_label": "pre-plunge",
        "color": "C7",
        "fill_volume_ml": 14.7,
        "warmup_environment": "insulation",
    },
    {
        "name": "Apr 21 HFE run",
        "filename": "log_20260421_102512.csv",
        "pre_label": "log start",
        "color": "C8",
        "measured_hfe_mass_g": 15.02,
        "warmup_environment": "insulation",
    },
    {
        "name": "Apr 23 HFE run",
        "filename": "log_20260423_105420.csv",
        "pre_label": "log start",
        "color": "C9",
        "measured_hfe_mass_g": 11.01,
        "warmup_environment": "insulation",
    },
    {
        "name": "Apr 29 HFE run",
        "filename": "log_20260429_144336.csv",
        "pre_label": "pre-plunge",
        "color": "C2",
        "fill_volume_ml": 10.0,
        "warmup_environment": "insulation",
    },
)
DEFAULT_HFE7200_RATE_RUN_NAMES: tuple[str, ...] = (
    "Apr 14 PM HFE run",
    "Apr 21 HFE run",
    "Apr 23 HFE run",
    "Apr 29 HFE run",
)
# The two Apr 14 runs are the pair whose DSC-like event curves are most
# coherent with each other in the transition region (pairwise RMSE ~0.015 W/g
# over -160..-80 °C, tighter than any other pair in the comparison set), and
# are kept as the reference subset for the 3M comparison.
DEFAULT_HFE7200_THREE_M_COHERENT_RUN_NAMES: tuple[str, ...] = (
    "Apr 14 HFE run",
    "Apr 14 PM HFE run",
)
DEFAULT_HFE7200_COMPARISON_START_DATE = pd.Timestamp("2026-04-14")
DEFAULT_DSC_BASELINE_RANGE_C: tuple[float, float] = (-75.0, 0.0)
DEFAULT_DSC_BIN_WIDTH_C = 1.0
DEFAULT_DSC_MIN_COUNT = 5

# Type-T thermocouple (TTEST) read through the MAX31856: the ADC/cold-junction
# contribution is small compared with the TC wire itself, so the plotted
# reading error uses the Type-T standard-limits tolerance — the larger of a
# fixed floor and a fraction of |T|.
TTEST_TC_FIXED_TOLERANCE_C: float = 1.0
TTEST_TC_PROPORTIONAL_TOLERANCE: float = 0.0075


def _ttest_tc_tolerance_c(temperature_c: np.ndarray | pd.Series | float) -> np.ndarray:
    """Return the Type-T standard-limits tolerance at each temperature, in °C."""

    values = np.asarray(temperature_c, dtype=float)
    return np.maximum(
        TTEST_TC_FIXED_TOLERANCE_C,
        TTEST_TC_PROPORTIONAL_TOLERANCE * np.abs(values),
    )

PHASE_ORDER: tuple[str, ...] = ("pre", "cooldown", "warmup_air", "warmup_insulation")
PHASE_LABELS: dict[str, str] = {
    "pre": "pre-plunge / log start",
    "cooldown": "cooldown",
    "warmup_air": "warmup in air",
    "warmup_insulation": "warmup in insulation",
}
PHASE_LINESTYLES: dict[str, str] = {
    "pre": ":",
    "cooldown": "-",
    "warmup_air": "--",
    "warmup_insulation": "-.",
}
PHASE_LINEWIDTHS: dict[str, float] = {
    "pre": 1.2,
    "cooldown": 1.8,
    "warmup_air": 1.6,
    "warmup_insulation": 1.6,
}
PHASE_ALPHAS: dict[str, float] = {
    "pre": 0.55,
    "cooldown": 0.95,
    "warmup_air": 0.92,
    "warmup_insulation": 0.92,
}
THREE_M_TRANSITION_MARKERS: dict[str, str] = {
    "glass_transition": "o",
    "melt_temperature": "s",
    "cold_crystallization": "^",
}
THREE_M_TRANSITION_LABELS: dict[str, str] = {
    "glass_transition": "Glass transition",
    "melt_temperature": "Melt temperature",
    "cold_crystallization": "Cold crystallization",
}
GLASS_TRANSITION_SEARCH_RANGE_C: tuple[float, float] = (-175.0, -155.0)
MEASURED_GLASS_TRANSITION_TEMPERATURE_C = -163.0
COLD_CRYSTALLIZATION_MIN_TEMPERATURE_C = -150.0
HFE7200_PHASE_TRANSITION_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "glass_transition",
        "label": "Glass transition",
        "feature": "minimum",
        "temperature_range_c": GLASS_TRANSITION_SEARCH_RANGE_C,
    },
    {
        "key": "cold_crystallization",
        "label": "Cold crystallization",
        "feature": "maximum",
        "temperature_range_c": (-150.0, -110.0),
    },
    {
        "key": "melt_temperature",
        "label": "Melt temperature",
        "feature": "minimum",
        "temperature_range_c": (-125.0, -90.0),
    },
)


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


@dataclass(frozen=True)
class Hfe7200Ln2DipRun:
    """Prepared HFE-7200 LN2 dip run with phase labels and DSC-like reconstruction."""

    name: str
    log_path: Path
    timestamp: pd.Timestamp
    data: pd.DataFrame
    pre_label: str
    color: str
    warmup_environment: str
    hfe_fill_volume_ml: float
    hfe_mass_g: float
    room_temperature_c: float
    cooldown_start_index: int
    turnaround_index: int
    air_band_end_time_min: float | None
    insulation_band_start_time_min: float | None
    tc_correction_method: str
    tc_correction_note: str
    tc_room_anchor_samples: float
    tc_room_anchor_offsets_c: dict[str, float]
    selected_warmup: pd.DataFrame
    dsc_like_summary: pd.DataFrame
    room_coupling_w_gk: float
    probe_random_noise_c: float

    @property
    def selected_warmup_label(self) -> str:
        if self.insulation_band_start_time_min is not None:
            return "warmup in insulation"
        if self.warmup_environment == "air_then_insulation":
            return "warmup in air then insulation"
        if self.warmup_environment == "insulation":
            return "warmup in insulation"
        return "warmup in air"


@dataclass(frozen=True)
class Hfe7200Ln2DipReview:
    """Notebook-ready HFE-7200 LN2 dip dataset and derived comparison curves."""

    repo_root: Path
    runs: dict[str, Hfe7200Ln2DipRun]
    run_order: tuple[str, ...]
    rate_run_names: tuple[str, ...]
    comparison_run_names: tuple[str, ...]
    three_m_coherent_run_names: tuple[str, ...]
    setup_table: pd.DataFrame
    calibration_summary: pd.DataFrame
    phase_summary: pd.DataFrame
    phase_transition_summary: pd.DataFrame
    three_m_curve: pd.DataFrame
    combined_dsc_curve: pd.DataFrame
    smoothing_window_s: float
    ln2_reference_c: float
    room_reference_c: float
    comparison_baseline_low_c: float
    comparison_baseline_high_c: float
    three_m_reference_points: tuple[dict[str, str | float], ...]


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
    prepared.loc[plunge_start_index:turnaround_index, "phase"] = "cooldown"
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


def _find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "data" / "raw").exists() and (candidate / "analysis" / "src").exists():
            return candidate
    raise FileNotFoundError("Could not locate the repository root from the current working directory.")


def _load_hfe_run(
    path: Path,
    *,
    room_reference_c: float,
    smoothing_window_s: float,
    tc_calibration_path: str | Path | None = None,
) -> pd.DataFrame:
    frame = read_tc_calibrated_csv(path, calibration_path=tc_calibration_path).copy()
    if is_legacy_wrong_type_log(path):
        frame, correction_note = apply_legacy_tc_correction(
            frame,
            log_path=path,
            flow_reference_column="__no_flow_reference__",
            room_reference_c=room_reference_c,
        )
        correction_method = "legacy K-to-T reconstruction"
    else:
        if frame.attrs.get("tc_calibration_applied_from_log_metadata"):
            correction_note = (
                "Restored raw thermocouple values calibrated in memory from "
                f"{Path(str(frame.attrs.get('tc_calibration_source'))).name}."
            )
            correction_method = "restored-log calibration"
        else:
            correction_note = "Logged thermocouple values used as-is."
            correction_method = "logged calibration"
        frame.attrs["legacy_tc_room_anchor_samples"] = np.nan
        frame.attrs["legacy_tc_room_anchor_offsets_c"] = {}

    frame.attrs["tc_correction_method"] = correction_method
    frame.attrs["tc_correction_note"] = correction_note
    frame["time_s"] = pd.to_numeric(frame["time_s"], errors="coerce")
    frame = frame.dropna(subset=["time_s"]).sort_values("time_s").reset_index(drop=True)
    frame["TTEST_C"] = pd.to_numeric(frame["TTEST_C"], errors="coerce")
    frame["t_rel_s"] = frame["time_s"] - float(frame["time_s"].iloc[0])
    frame["t_rel_min"] = frame["t_rel_s"] / 60.0
    frame["probe_raw_c"] = frame["TTEST_C"].interpolate(limit_direction="both")
    samples = _window_samples(frame["t_rel_s"], smoothing_window_s)
    frame["probe_smooth_c"] = _rolling_mean(frame["probe_raw_c"], samples)
    frame["probe_rate_c_s"] = np.gradient(frame["probe_smooth_c"].to_numpy(), frame["t_rel_s"].to_numpy())
    residual = frame["probe_raw_c"].to_numpy() - frame["probe_smooth_c"].to_numpy()
    finite_residual = residual[np.isfinite(residual)]
    frame.attrs["probe_random_noise_c"] = (
        float(np.nanmedian(np.abs(finite_residual)) * 1.4826)
        if finite_residual.size > 0
        else float("nan")
    )
    return frame


def _apply_hfe_capacity_model(
    frame: pd.DataFrame,
    *,
    hfe_mass_g: float,
    hfe_mass_kg: float,
    glass_capacity_j_k: float,
) -> pd.DataFrame:
    calibrated = frame.copy()
    calibrated["probe_calibrated_raw_c"] = calibrated["probe_raw_c"]
    calibrated["probe_calibrated_smooth_c"] = calibrated["probe_smooth_c"]
    calibrated["probe_calibrated_rate_c_s"] = np.gradient(
        calibrated["probe_calibrated_smooth_c"].to_numpy(),
        calibrated["t_rel_s"].to_numpy(),
    )
    calibrated["hfe_cp_j_kgk"] = calibrated["probe_calibrated_smooth_c"].apply(
        lambda temp_c: hfe_specific_heat_j_kgk(float(temp_c) + 273.15)
    )
    calibrated["apparent_capacity_j_k"] = glass_capacity_j_k + hfe_mass_kg * calibrated["hfe_cp_j_kgk"]
    calibrated["estimated_heat_flow_w_g"] = -(
        calibrated["apparent_capacity_j_k"] * calibrated["probe_calibrated_rate_c_s"]
    ) / hfe_mass_g
    return calibrated


def _add_phase_labels(
    frame: pd.DataFrame,
    *,
    pre_label: str,
    cooldown_start_index: int,
    turnaround_index: int,
) -> pd.DataFrame:
    labelled = frame.copy()
    labelled["phase"] = pre_label
    labelled.loc[cooldown_start_index:turnaround_index, "phase"] = "cooldown"
    labelled.loc[turnaround_index:, "phase"] = "warmup"
    return labelled


def _first_sustained_rate_run_start_index(
    frame: pd.DataFrame,
    *,
    start_index: int,
    rate_threshold_c_s: float,
    confirm_samples: int,
    direction: str,
) -> int:
    rate_series = frame.loc[start_index:, "probe_calibrated_rate_c_s"]
    if direction == "positive":
        threshold_hits = rate_series.gt(rate_threshold_c_s)
    elif direction == "negative":
        threshold_hits = rate_series.lt(rate_threshold_c_s)
    else:
        raise ValueError(f"Unsupported direction: {direction!r}")

    sustained_hits = threshold_hits.rolling(confirm_samples, min_periods=confirm_samples).sum().eq(confirm_samples)
    if sustained_hits.any():
        first_hit_index = int(sustained_hits[sustained_hits].index[0])
        return first_hit_index - confirm_samples + 1
    return int(start_index)


def _interpolate_phase_marker(frame: pd.DataFrame, *, phase: str, target_temp_c: float) -> dict[str, float] | None:
    subset = frame.loc[
        frame["phase"] == phase,
        ["t_rel_min", "probe_calibrated_raw_c", "probe_calibrated_rate_c_s", "estimated_heat_flow_w_g"],
    ].dropna()
    if subset.empty:
        return None

    upper_candidates = subset.index[subset["probe_calibrated_raw_c"] >= target_temp_c]
    if len(upper_candidates) == 0:
        return None
    upper_index = int(upper_candidates[0])
    upper = subset.loc[upper_index]

    lower_candidates = subset.index[subset.index < upper_index]
    if len(lower_candidates) == 0:
        return {
            "time_min": float(upper["t_rel_min"]),
            "temperature_c": float(target_temp_c),
            "rate_c_s": float(upper["probe_calibrated_rate_c_s"]),
            "heat_flow_w_g": float(upper["estimated_heat_flow_w_g"]),
        }

    lower = subset.loc[int(lower_candidates[-1])]
    temp_span_c = float(upper["probe_calibrated_raw_c"] - lower["probe_calibrated_raw_c"])
    if abs(temp_span_c) < 1.0e-9:
        weight = 0.0
    else:
        weight = float(np.clip((target_temp_c - float(lower["probe_calibrated_raw_c"])) / temp_span_c, 0.0, 1.0))

    def lerp(column: str) -> float:
        return float(lower[column] + weight * (upper[column] - lower[column]))

    return {
        "time_min": lerp("t_rel_min"),
        "temperature_c": float(target_temp_c),
        "rate_c_s": lerp("probe_calibrated_rate_c_s"),
        "heat_flow_w_g": lerp("estimated_heat_flow_w_g"),
    }


def _phase_code_series(
    frame: pd.DataFrame,
    *,
    warmup_environment: str,
    insulation_band_start_time_min: float | None,
) -> pd.Series:
    phase_code = pd.Series("pre", index=frame.index, dtype="object")
    phase_code.loc[frame["phase"] == "cooldown"] = "cooldown"

    warmup_mask = frame["phase"] == "warmup"
    if not warmup_mask.any():
        return phase_code

    if insulation_band_start_time_min is not None:
        phase_code.loc[warmup_mask & frame["t_rel_min"].lt(insulation_band_start_time_min)] = "warmup_air"
        phase_code.loc[warmup_mask & frame["t_rel_min"].ge(insulation_band_start_time_min)] = "warmup_insulation"
    elif warmup_environment == "insulation":
        phase_code.loc[warmup_mask] = "warmup_insulation"
    else:
        phase_code.loc[warmup_mask] = "warmup_air"
    return phase_code


def _smooth_curve(values: np.ndarray, *, max_window_points: int = 9, polyorder: int = 2) -> np.ndarray:
    if len(values) == 0:
        return values
    window_points = min(max_window_points, len(values))
    if window_points % 2 == 0:
        window_points -= 1
    if window_points <= polyorder or window_points < 3:
        return values.copy()
    return savgol_filter(
        values,
        window_length=window_points,
        polyorder=min(polyorder, window_points - 1),
        mode="interp",
    )


def _odd_window_points_from_temperature_span(
    temperatures_c: pd.Series | np.ndarray,
    *,
    target_width_c: float,
    minimum_points: int,
    maximum_points: int,
) -> int:
    temperature_values = np.asarray(temperatures_c, dtype=float)
    temperature_values = temperature_values[np.isfinite(temperature_values)]
    if temperature_values.size < 3:
        return max(3, minimum_points | 1)

    positive_steps_c = np.diff(np.sort(temperature_values))
    positive_steps_c = positive_steps_c[positive_steps_c > 0.0]
    if positive_steps_c.size == 0:
        window_points = minimum_points
    else:
        median_step_c = float(np.median(positive_steps_c))
        if not np.isfinite(median_step_c) or median_step_c <= 0.0:
            window_points = minimum_points
        else:
            window_points = int(round(target_width_c / median_step_c))

    window_points = max(minimum_points, min(maximum_points, window_points))
    if window_points % 2 == 0:
        window_points += 1
    return window_points


def _raw_dsc_plot_curve(
    run: Hfe7200Ln2DipRun,
    *,
    value_column: str = "dsc_like_heat_flow_w_g",
    extra_columns: Sequence[str] = (),
) -> pd.DataFrame:
    columns: list[str] = ["probe_calibrated_raw_c", value_column]
    for extra in extra_columns:
        if extra and extra not in columns:
            columns.append(extra)
    curve = (
        run.selected_warmup.loc[:, columns]
        .dropna()
        .sort_values("probe_calibrated_raw_c")
        .reset_index(drop=True)
        .copy()
    )
    result_columns = ["temperature_c", "smoothed_heat_flow_w_g"] + [
        f"smoothed_{extra}" for extra in extra_columns
    ]
    if curve.empty:
        return pd.DataFrame(columns=result_columns)

    temperature_c = curve["probe_calibrated_raw_c"]
    median_window_points = _odd_window_points_from_temperature_span(
        temperature_c,
        target_width_c=3.0,
        minimum_points=11,
        maximum_points=121,
    )
    mean_window_points = _odd_window_points_from_temperature_span(
        temperature_c,
        target_width_c=2.0,
        minimum_points=7,
        maximum_points=61,
    )
    savgol_window_points = _odd_window_points_from_temperature_span(
        temperature_c,
        target_width_c=5.0,
        minimum_points=11,
        maximum_points=151,
    )

    def _smooth_column(source: pd.Series) -> np.ndarray:
        staged = source.rolling(median_window_points, center=True, min_periods=1).median()
        staged = staged.rolling(mean_window_points, center=True, min_periods=1).mean()
        return _smooth_curve(staged.to_numpy(dtype=float), max_window_points=savgol_window_points)

    curve["temperature_c"] = temperature_c
    curve["smoothed_heat_flow_w_g"] = _smooth_column(curve[value_column])
    for extra in extra_columns:
        curve[f"smoothed_{extra}"] = _smooth_column(curve[extra])
    return curve.loc[:, result_columns]


def _selected_warmup_frame(run: Hfe7200Ln2DipRun) -> pd.DataFrame:
    if run.insulation_band_start_time_min is not None:
        mask = run.data["phase_code"] == "warmup_insulation"
    else:
        mask = run.data["phase"] == "warmup"

    return (
        run.data.loc[
            mask,
            [
                "t_rel_min",
                "probe_calibrated_raw_c",
                "probe_calibrated_smooth_c",
                "probe_calibrated_rate_c_s",
                "estimated_heat_flow_w_g",
                "apparent_capacity_j_k",
            ],
        ]
        .dropna()
        .sort_values("probe_calibrated_raw_c")
        .reset_index(drop=True)
        .copy()
    )


def _summarize_curve_by_temperature(
    frame: pd.DataFrame,
    *,
    value_column: str,
    bin_width_c: float,
    min_count: int,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=["temperature_c", "heat_flow_w_g", "count", "smoothed_heat_flow_w_g", "smoothed_derivative_w_g_per_c"]
        )

    lower_edge_c = bin_width_c * np.floor(float(frame["probe_calibrated_raw_c"].min()) / bin_width_c)
    upper_edge_c = bin_width_c * np.ceil(float(frame["probe_calibrated_raw_c"].max()) / bin_width_c)
    bins = np.arange(lower_edge_c, upper_edge_c + bin_width_c, bin_width_c)
    if bins.size < 2:
        bins = np.array([lower_edge_c, lower_edge_c + bin_width_c], dtype=float)

    grouped = (
        frame.assign(temperature_bin=pd.cut(frame["probe_calibrated_raw_c"], bins=bins, include_lowest=True))
        .groupby("temperature_bin", observed=False)
    )
    summary = grouped.agg(
        heat_flow_w_g=(value_column, "median"),
        count=(value_column, "size"),
    ).reset_index(drop=True)
    summary["temperature_c"] = bins[:-1] + 0.5 * bin_width_c

    occupied_mask = summary["count"] >= max(1, int(min_count))
    if not occupied_mask.any():
        return pd.DataFrame(
            columns=["temperature_c", "heat_flow_w_g", "count", "smoothed_heat_flow_w_g", "smoothed_derivative_w_g_per_c"]
        )

    occupied = summary.loc[occupied_mask, ["temperature_c", "heat_flow_w_g"]].reset_index(drop=True)
    first_temperature_c = float(occupied["temperature_c"].iloc[0])
    last_temperature_c = float(occupied["temperature_c"].iloc[-1])
    summary = summary.loc[
        summary["temperature_c"].between(first_temperature_c, last_temperature_c)
    ].reset_index(drop=True)
    summary["heat_flow_w_g"] = np.interp(
        summary["temperature_c"],
        occupied["temperature_c"],
        occupied["heat_flow_w_g"],
    )
    summary["smoothed_heat_flow_w_g"] = _smooth_curve(summary["heat_flow_w_g"].to_numpy())
    summary["smoothed_derivative_w_g_per_c"] = np.gradient(
        summary["smoothed_heat_flow_w_g"].to_numpy(),
        summary["temperature_c"].to_numpy(),
    )
    return summary


def _compute_dsc_like_warmup(
    warmup_frame: pd.DataFrame,
    *,
    room_temperature_c: float,
    baseline_low_c: float,
    baseline_high_c: float,
    bin_width_c: float,
    min_count: int,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    prepared = warmup_frame.sort_values("probe_calibrated_raw_c").reset_index(drop=True).copy()
    if prepared.empty:
        return prepared, pd.DataFrame(), np.nan

    fit_frame = prepared.loc[
        prepared["probe_calibrated_raw_c"].between(baseline_low_c, baseline_high_c)
    ].copy()
    if fit_frame.empty:
        prepared["linear_background_w_g"] = np.nan
        prepared["dsc_like_heat_flow_w_g"] = np.nan
        return prepared, pd.DataFrame(), np.nan

    delta_t_fit_c = room_temperature_c - fit_frame["probe_calibrated_raw_c"]
    denominator = float((delta_t_fit_c**2).sum())
    if denominator <= 0.0 or not np.isfinite(denominator):
        prepared["linear_background_w_g"] = np.nan
        prepared["dsc_like_heat_flow_w_g"] = np.nan
        return prepared, pd.DataFrame(), np.nan

    room_coupling_w_gk = float(-(fit_frame["estimated_heat_flow_w_g"] * delta_t_fit_c).sum() / denominator)
    prepared["linear_background_w_g"] = -room_coupling_w_gk * (room_temperature_c - prepared["probe_calibrated_raw_c"])
    prepared["dsc_like_heat_flow_w_g"] = prepared["linear_background_w_g"] - prepared["estimated_heat_flow_w_g"]

    summary = _summarize_curve_by_temperature(
        prepared,
        value_column="dsc_like_heat_flow_w_g",
        bin_width_c=bin_width_c,
        min_count=min_count,
    )
    return prepared, summary, room_coupling_w_gk


def _combine_dsc_summaries(
    runs: Mapping[str, Hfe7200Ln2DipRun],
    run_names: Sequence[str],
    *,
    rate_sigma_window_s: float | None = None,
    bin_width_c: float | None = None,
    edge_blend_width_c: float = 6.0,
    extend_to_union: bool = False,
) -> pd.DataFrame:
    resolved_bin_width_c = DEFAULT_DSC_BIN_WIDTH_C if bin_width_c is None else float(bin_width_c)
    summaries: list[pd.DataFrame] = []
    for name in run_names:
        run = runs[name]
        summary = run.dsc_like_summary.copy()
        curve = _raw_dsc_plot_curve(
            run,
            extra_columns=("estimated_heat_flow_w_g", "apparent_capacity_j_k"),
        )
        if summary.empty and curve.empty:
            continue

        if extend_to_union and not curve.empty:
            curve_temperature_c = curve["temperature_c"].to_numpy(dtype=float)
            lower_edge_c = resolved_bin_width_c * np.floor(float(np.nanmin(curve_temperature_c)) / resolved_bin_width_c)
            upper_edge_c = resolved_bin_width_c * np.ceil(float(np.nanmax(curve_temperature_c)) / resolved_bin_width_c)
            temperature_grid_c = np.arange(
                lower_edge_c + 0.5 * resolved_bin_width_c,
                upper_edge_c,
                resolved_bin_width_c,
            )
            df = pd.DataFrame({"temperature_c": temperature_grid_c})
            df[f"heat_{name}"] = np.interp(
                temperature_grid_c,
                curve_temperature_c,
                curve["smoothed_heat_flow_w_g"].to_numpy(dtype=float),
                left=np.nan,
                right=np.nan,
            )
            # Fade union-only edges in/out so a run does not enter the mean at full weight on one bin.
            blend_width_c = max(float(edge_blend_width_c), resolved_bin_width_c)
            lower_weight = np.clip(
                (temperature_grid_c - float(np.nanmin(curve_temperature_c))) / blend_width_c,
                0.0,
                1.0,
            )
            upper_weight = np.clip(
                (float(np.nanmax(curve_temperature_c)) - temperature_grid_c) / blend_width_c,
                0.0,
                1.0,
            )
            df[f"weight_{name}"] = np.minimum(lower_weight, upper_weight)
        elif summary.empty:
            df = curve.loc[:, ["temperature_c", "smoothed_heat_flow_w_g"]].copy()
            df = df.rename(columns={"smoothed_heat_flow_w_g": f"heat_{name}"})
            df[f"weight_{name}"] = 1.0
        else:
            df = summary.loc[:, ["temperature_c", "smoothed_heat_flow_w_g"]].copy()
            df = df.rename(columns={"smoothed_heat_flow_w_g": f"heat_{name}"})
            df[f"weight_{name}"] = 1.0

        if not curve.empty:
            curve_temperature_c = curve["temperature_c"].to_numpy(dtype=float)
            target_temperature_c = df["temperature_c"].to_numpy(dtype=float)

            if "smoothed_estimated_heat_flow_w_g" in curve:
                pre_background_w_g = -curve["smoothed_estimated_heat_flow_w_g"].to_numpy(dtype=float)
                df[f"pre_{name}"] = np.interp(
                    target_temperature_c,
                    curve_temperature_c,
                    pre_background_w_g,
                    left=np.nan,
                    right=np.nan,
                )

            if (
                rate_sigma_window_s is not None
                and np.isfinite(rate_sigma_window_s)
                and rate_sigma_window_s > 0.0
                and np.isfinite(run.probe_random_noise_c)
                and run.hfe_mass_g > 0.0
                and "smoothed_apparent_capacity_j_k" in curve
            ):
                capacity_j_k = np.interp(
                    target_temperature_c,
                    curve_temperature_c,
                    curve["smoothed_apparent_capacity_j_k"].to_numpy(dtype=float),
                    left=np.nan,
                    right=np.nan,
                )
                sigma_rate_c_s = float(run.probe_random_noise_c) / float(rate_sigma_window_s)
                df[f"sigma_{name}"] = np.abs(capacity_j_k / float(run.hfe_mass_g) * sigma_rate_c_s)
            else:
                df[f"sigma_{name}"] = np.nan
        else:
            df[f"sigma_{name}"] = np.nan

        summaries.append(df)

    if not summaries:
        return pd.DataFrame()

    how = "outer" if extend_to_union else "inner"
    combined = summaries[0]
    for df in summaries[1:]:
        combined = combined.merge(df, on="temperature_c", how=how)

    heat_cols = [c for c in combined.columns if c.startswith("heat_")]
    sigma_cols = [c for c in combined.columns if c.startswith("sigma_")]
    pre_cols = [c for c in combined.columns if c.startswith("pre_")]
    weight_cols = {
        heat_col: f"weight_{heat_col.removeprefix('heat_')}"
        for heat_col in heat_cols
    }

    n_runs = combined[heat_cols].notna().sum(axis=1).astype(float)
    raw_heat_values = combined[heat_cols].to_numpy(dtype=float)
    finite_heat_mask = np.isfinite(raw_heat_values)
    heat_values = raw_heat_values.copy()
    weight_values = np.column_stack(
        [
            combined[weight_cols[heat_col]].to_numpy(dtype=float)
            if weight_cols[heat_col] in combined
            else np.ones(len(combined), dtype=float)
            for heat_col in heat_cols
        ]
    )
    weight_values = np.where(finite_heat_mask, weight_values, 0.0)
    heat_values = np.where(finite_heat_mask, heat_values, 0.0)
    weight_sum = weight_values.sum(axis=1)
    mean_heat = np.divide(
        (heat_values * weight_values).sum(axis=1),
        weight_sum,
        out=np.full(len(combined), np.nan),
        where=weight_sum > 0.0,
    )

    scatter = np.sqrt(
        np.divide(
            (((heat_values - mean_heat[:, None]) ** 2) * weight_values).sum(axis=1),
            weight_sum,
            out=np.zeros(len(combined), dtype=float),
            where=weight_sum > 0.0,
        )
    )
    sem = scatter / np.sqrt(n_runs.where(n_runs > 0))
    run_to_run_deviation = np.where(
        finite_heat_mask,
        np.abs(raw_heat_values - mean_heat[:, None]),
        np.nan,
    )
    run_to_run_spread = np.full(len(combined), np.nan, dtype=float)
    deviation_mask = np.isfinite(run_to_run_deviation).any(axis=1)
    run_to_run_spread[deviation_mask] = np.nanmax(
        run_to_run_deviation[deviation_mask],
        axis=1,
    )
    run_to_run_spread = np.where(n_runs > 1, run_to_run_spread, np.nan)

    sigma_array = combined[sigma_cols].to_numpy(dtype=float)
    sigma_weight_values = np.column_stack(
        [
            combined.get(f"weight_{sigma_col.removeprefix('sigma_')}", pd.Series(1.0, index=combined.index)).to_numpy(dtype=float)
            for sigma_col in sigma_cols
        ]
    )
    sigma_weight_values = np.where(np.isfinite(sigma_array), sigma_weight_values, 0.0)
    sigma_array = np.where(np.isfinite(sigma_array), sigma_array, 0.0)
    sigma_weight_sum = sigma_weight_values.sum(axis=1)
    propagated_noise = np.divide(
        np.sqrt(np.sum((sigma_array * sigma_weight_values) ** 2, axis=1)),
        sigma_weight_sum,
        out=np.full(len(combined), np.nan),
        where=sigma_weight_sum > 0.0,
    )

    total_sigma = np.sqrt(sem**2 + propagated_noise**2)
    spread_component = np.where(np.isfinite(run_to_run_spread), run_to_run_spread, 0.0)
    noise_component = np.where(np.isfinite(propagated_noise), propagated_noise, 0.0)
    combined_uncertainty = np.sqrt(spread_component**2 + noise_component**2)
    combined_uncertainty = np.where(
        np.isfinite(run_to_run_spread) | np.isfinite(propagated_noise),
        combined_uncertainty,
        np.nan,
    )

    combined["n_runs"] = n_runs
    combined["mean_heat_flow_w_g"] = mean_heat
    combined["run_to_run_spread_w_g"] = run_to_run_spread
    combined["propagated_noise_w_g"] = propagated_noise
    combined["combined_uncertainty_w_g"] = combined_uncertainty
    combined["sigma_heat_flow_w_g"] = total_sigma
    combined["lower_heat_flow_w_g"] = mean_heat - total_sigma
    combined["upper_heat_flow_w_g"] = mean_heat + total_sigma
    combined["lower_run_to_run_heat_flow_w_g"] = mean_heat - run_to_run_spread
    combined["upper_run_to_run_heat_flow_w_g"] = mean_heat + run_to_run_spread
    combined["lower_combined_uncertainty_heat_flow_w_g"] = mean_heat - combined_uncertainty
    combined["upper_combined_uncertainty_heat_flow_w_g"] = mean_heat + combined_uncertainty

    if pre_cols:
        pre_values = combined[pre_cols].to_numpy(dtype=float)
        pre_weight_values = np.column_stack(
            [
                combined.get(f"weight_{pre_col.removeprefix('pre_')}", pd.Series(1.0, index=combined.index)).to_numpy(dtype=float)
                for pre_col in pre_cols
            ]
        )
        pre_weight_values = np.where(np.isfinite(pre_values), pre_weight_values, 0.0)
        pre_values = np.where(np.isfinite(pre_values), pre_values, 0.0)
        pre_weight_sum = pre_weight_values.sum(axis=1)
        combined["mean_pre_background_heat_flow_w_g"] = np.divide(
            (pre_values * pre_weight_values).sum(axis=1),
            pre_weight_sum,
            out=np.full(len(combined), np.nan),
            where=pre_weight_sum > 0.0,
        )

    combined = combined.loc[combined["n_runs"] > 0].copy()
    combined = combined.sort_values("temperature_c").reset_index(drop=True)
    return combined


def _temperature_window_extreme_point(
    temperature_c: pd.Series | np.ndarray,
    heat_flow_w_g: pd.Series | np.ndarray,
    *,
    temperature_range_c: tuple[float, float],
    feature: str,
) -> tuple[float, float]:
    temperature_values = np.asarray(temperature_c, dtype=float)
    heat_flow_values = np.asarray(heat_flow_w_g, dtype=float)
    low_c, high_c = temperature_range_c
    finite_mask = (
        np.isfinite(temperature_values)
        & np.isfinite(heat_flow_values)
        & (temperature_values >= float(low_c))
        & (temperature_values <= float(high_c))
    )
    if not finite_mask.any():
        return float("nan"), float("nan")

    window_temperatures = temperature_values[finite_mask]
    window_heat_flow = heat_flow_values[finite_mask]
    if feature == "minimum":
        selected_index = int(np.argmin(window_heat_flow))
    elif feature == "maximum":
        selected_index = int(np.argmax(window_heat_flow))
    else:
        raise ValueError(f"Unsupported phase-transition feature: {feature!r}")
    return float(window_temperatures[selected_index]), float(window_heat_flow[selected_index])


def _phase_transition_candidate_rows(
    *,
    source_name: str,
    source_type: str,
    temperature_c: pd.Series | np.ndarray,
    heat_flow_w_g: pd.Series | np.ndarray,
) -> list[dict[str, str | float]]:
    temperature_values = np.asarray(temperature_c, dtype=float)
    heat_flow_values = np.asarray(heat_flow_w_g, dtype=float)
    finite_curve_mask = np.isfinite(temperature_values) & np.isfinite(heat_flow_values)
    if finite_curve_mask.any():
        curve_min_c = float(np.nanmin(temperature_values[finite_curve_mask]))
        curve_max_c = float(np.nanmax(temperature_values[finite_curve_mask]))
    else:
        curve_min_c = float("nan")
        curve_max_c = float("nan")

    rows: list[dict[str, str | float]] = []
    for transition in HFE7200_PHASE_TRANSITION_CANDIDATES:
        low_c, high_c = tuple(float(value) for value in transition["temperature_range_c"])
        feature = str(transition["feature"])
        candidate_temperature_c, candidate_heat_flow_w_g = _temperature_window_extreme_point(
            temperature_values,
            heat_flow_values,
            temperature_range_c=(low_c, high_c),
            feature=feature,
        )

        if np.isfinite(candidate_temperature_c):
            fully_covered = (
                np.isfinite(curve_min_c)
                and np.isfinite(curve_max_c)
                and curve_min_c <= low_c
                and curve_max_c >= high_c
            )
            status = "candidate" if fully_covered else "candidate (partial window)"
        else:
            status = "not covered"

        rows.append(
            {
                "Source": source_name,
                "Source type": source_type,
                "Transition": str(transition["label"]),
                "Candidate temperature [°C]": candidate_temperature_c,
                "Heat flow [W/g]": candidate_heat_flow_w_g,
                "Search window [°C]": f"{low_c:.0f} to {high_c:.0f}",
                "Feature": feature,
                "Status": status,
            }
        )
    return rows


def _build_hfe7200_phase_transition_summary(
    runs: Mapping[str, Hfe7200Ln2DipRun],
    run_names: Sequence[str],
    *,
    combined_dsc_curve: pd.DataFrame,
    combined_source_name: str,
    three_m_curve: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, str | float]] = []
    for name in run_names:
        curve = _raw_dsc_plot_curve(runs[name])
        rows.extend(
            _phase_transition_candidate_rows(
                source_name=name,
                source_type="run",
                temperature_c=curve.get("temperature_c", pd.Series(dtype=float)),
                heat_flow_w_g=curve.get("smoothed_heat_flow_w_g", pd.Series(dtype=float)),
            )
        )

    if not combined_dsc_curve.empty:
        rows.extend(
            _phase_transition_candidate_rows(
                source_name=combined_source_name,
                source_type="measured mean",
                temperature_c=combined_dsc_curve["temperature_c"],
                heat_flow_w_g=combined_dsc_curve["mean_heat_flow_w_g"],
            )
        )

    if (
        not three_m_curve.empty
        and "linear_baseline_referenced_heat_flow_w_g" in three_m_curve
    ):
        rows.extend(
            _phase_transition_candidate_rows(
                source_name="3M reference",
                source_type="reference",
                temperature_c=three_m_curve["temperature_C"],
                heat_flow_w_g=three_m_curve["linear_baseline_referenced_heat_flow_w_g"],
            )
        )

    return pd.DataFrame(rows)


def _parse_log_timestamp(log_path: Path) -> pd.Timestamp:
    return pd.to_datetime(log_path.stem.removeprefix("log_"), format="%Y%m%d_%H%M%S")


def _format_tc_offsets(offsets: dict[str, float]) -> str:
    if not offsets:
        return ""
    return "; ".join(
        f"{column.removesuffix('_C')} {offset_c:+.2f}"
        for column, offset_c in offsets.items()
    )


def prepare_hfe7200_ln2_dip_review(
    *,
    repo_root: str | Path | None = None,
    tc_calibration_path: str | Path | None = None,
    ln2_reference_c: float = -196.0,
    smoothing_window_s: float = 10.0,
    room_reference_f: float = 68.5,
    tube_mass_g: float = 15.3759,
    glass_cp_j_kgk: float = 800.0,
    comparison_start_date: str | pd.Timestamp = DEFAULT_HFE7200_COMPARISON_START_DATE,
    comparison_baseline_range_c: tuple[float, float] = DEFAULT_DSC_BASELINE_RANGE_C,
    dsc_bin_width_c: float = DEFAULT_DSC_BIN_WIDTH_C,
    dsc_min_count: int = DEFAULT_DSC_MIN_COUNT,
) -> Hfe7200Ln2DipReview:
    """Prepare the HFE-7200 LN2 dip notebook dataset with shared plotting inputs."""

    resolved_repo_root = _find_repo_root(Path(repo_root) if repo_root is not None else None)
    room_reference_c = (room_reference_f - 32.0) * 5.0 / 9.0
    comparison_start = pd.Timestamp(comparison_start_date)
    comparison_baseline_low_c, comparison_baseline_high_c = comparison_baseline_range_c
    glass_capacity_j_k = (tube_mass_g / 1000.0) * glass_cp_j_kgk
    hfe_density_kg_m3 = hfe_liquid_density_kg_m3(room_reference_c)

    log_dir = resolved_repo_root / "data" / "raw" / "LN_dip"
    three_m_curve_path = resolved_repo_root / "data" / "processed" / "HX_performance" / "blue_curve_digitized.csv"

    raw_runs: dict[str, pd.DataFrame] = {}
    run_paths: dict[str, Path] = {}
    run_configs: dict[str, dict[str, Any]] = {}
    for config in DEFAULT_HFE7200_LN2_DIP_RUNS:
        log_path = log_dir / str(config["filename"])
        raw_runs[str(config["name"])] = _load_hfe_run(
            log_path,
            room_reference_c=room_reference_c,
            smoothing_window_s=smoothing_window_s,
            tc_calibration_path=tc_calibration_path,
        )
        run_paths[str(config["name"])] = log_path
        run_configs[str(config["name"])] = dict(config)
    run_order = tuple(str(config["name"]) for config in DEFAULT_HFE7200_LN2_DIP_RUNS)

    apr8_study = prepare_cryogenic_dip_study(run_paths["Apr 8 HFE run"], smoothing_window_s=smoothing_window_s)
    apr10_study = prepare_cryogenic_dip_study(run_paths["Apr 10 HFE run"], smoothing_window_s=smoothing_window_s)
    apr14_pm_study = prepare_cryogenic_dip_study(run_paths["Apr 14 PM HFE run"], smoothing_window_s=smoothing_window_s)

    prepared_frames: dict[str, pd.DataFrame] = {}
    fill_volumes_ml: dict[str, float] = {}
    hfe_masses_g: dict[str, float] = {}

    for name, config in run_configs.items():
        if "measured_hfe_mass_g" in config:
            hfe_mass_g = float(config["measured_hfe_mass_g"])
            hfe_mass_kg = hfe_mass_g / 1000.0
            fill_volume_ml = (hfe_mass_kg / hfe_density_kg_m3) * 1.0e6
        else:
            fill_volume_ml = float(config["fill_volume_ml"])
            hfe_mass_kg = hfe_density_kg_m3 * (fill_volume_ml * 1.0e-6)
            hfe_mass_g = hfe_mass_kg * 1000.0

        prepared_frames[name] = _apply_hfe_capacity_model(
            raw_runs[name],
            hfe_mass_g=hfe_mass_g,
            hfe_mass_kg=hfe_mass_kg,
            glass_capacity_j_k=glass_capacity_j_k,
        )
        fill_volumes_ml[name] = fill_volume_ml
        hfe_masses_g[name] = hfe_mass_g

    apr9_post_peak_index = int(raw_runs["Apr 9 HFE run"]["probe_smooth_c"].idxmax())
    apr9_post_peak = raw_runs["Apr 9 HFE run"].loc[apr9_post_peak_index:, "probe_rate_c_s"]
    apr9_sustained_plunge = (
        apr9_post_peak.lt(-0.15)
        .rolling(5, min_periods=5)
        .sum()
        .eq(5)
    )
    apr9_decline_end_index = int(apr9_sustained_plunge[apr9_sustained_plunge].index[0])
    apr9_cooldown_start_index = apr9_decline_end_index - 5 + 1

    cooldown_start_index_8 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 8 HFE run"],
        start_index=apr8_study.plunge_start_index,
        rate_threshold_c_s=-0.15,
        confirm_samples=5,
        direction="negative",
    )
    turnaround_index_8 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 8 HFE run"],
        start_index=int(prepared_frames["Apr 8 HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )
    turnaround_index_10 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 10 HFE run"],
        start_index=int(prepared_frames["Apr 10 HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )
    cooldown_start_index_14 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 14 HFE run"],
        start_index=0,
        rate_threshold_c_s=-0.15,
        confirm_samples=5,
        direction="negative",
    )
    turnaround_index_14 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 14 HFE run"],
        start_index=int(prepared_frames["Apr 14 HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )
    cooldown_start_index_14_pm = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 14 PM HFE run"],
        start_index=apr14_pm_study.plunge_start_index,
        rate_threshold_c_s=-0.15,
        confirm_samples=5,
        direction="negative",
    )
    turnaround_index_14_pm = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 14 PM HFE run"],
        start_index=int(prepared_frames["Apr 14 PM HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )
    turnaround_index_21 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 21 HFE run"],
        start_index=int(prepared_frames["Apr 21 HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )
    turnaround_index_23 = _first_sustained_rate_run_start_index(
        prepared_frames["Apr 23 HFE run"],
        start_index=int(prepared_frames["Apr 23 HFE run"]["probe_calibrated_smooth_c"].idxmin()),
        rate_threshold_c_s=0.01,
        confirm_samples=5,
        direction="positive",
    )

    phase_metadata: dict[str, dict[str, float | int | None]] = {
        "Apr 8 HFE run": {
            "cooldown_start_index": cooldown_start_index_8,
            "turnaround_index": turnaround_index_8,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 9 HFE run": {
            "cooldown_start_index": apr9_cooldown_start_index,
            "turnaround_index": int(prepared_frames["Apr 9 HFE run"].loc[apr9_cooldown_start_index:, "probe_calibrated_smooth_c"].idxmin()),
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 10 HFE run": {
            "cooldown_start_index": apr10_study.plunge_start_index,
            "turnaround_index": turnaround_index_10,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 14 HFE run": {
            "cooldown_start_index": cooldown_start_index_14,
            "turnaround_index": turnaround_index_14,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 14 PM HFE run": {
            "cooldown_start_index": cooldown_start_index_14_pm,
            "turnaround_index": turnaround_index_14_pm,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 21 HFE run": {
            "cooldown_start_index": 0,
            "turnaround_index": turnaround_index_21,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
        "Apr 23 HFE run": {
            "cooldown_start_index": 0,
            "turnaround_index": turnaround_index_23,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        },
    }
    generic_phase_names: list[str] = []
    for name in run_order:
        if name in phase_metadata:
            continue
        frame = prepared_frames[name]
        cooldown_start_index = _first_sustained_rate_run_start_index(
            frame,
            start_index=0,
            rate_threshold_c_s=-0.15,
            confirm_samples=5,
            direction="negative",
        )
        turnaround_index = _first_sustained_rate_run_start_index(
            frame,
            start_index=int(frame["probe_calibrated_smooth_c"].idxmin()),
            rate_threshold_c_s=0.01,
            confirm_samples=5,
            direction="positive",
        )
        phase_metadata[name] = {
            "cooldown_start_index": cooldown_start_index,
            "turnaround_index": turnaround_index,
            "air_band_end_time_min": None,
            "insulation_band_start_time_min": None,
        }
        generic_phase_names.append(name)

    prepared_frames["Apr 8 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 8 HFE run"],
        pre_label="pre-plunge",
        cooldown_start_index=cooldown_start_index_8,
        turnaround_index=turnaround_index_8,
    )
    prepared_frames["Apr 9 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 9 HFE run"],
        pre_label="pre-cycle",
        cooldown_start_index=apr9_cooldown_start_index,
        turnaround_index=int(phase_metadata["Apr 9 HFE run"]["turnaround_index"]),
    )
    prepared_frames["Apr 10 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 10 HFE run"],
        pre_label="pre-plunge",
        cooldown_start_index=apr10_study.plunge_start_index,
        turnaround_index=turnaround_index_10,
    )
    prepared_frames["Apr 14 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 14 HFE run"],
        pre_label="pre-plunge",
        cooldown_start_index=cooldown_start_index_14,
        turnaround_index=turnaround_index_14,
    )
    prepared_frames["Apr 14 PM HFE run"] = _add_phase_labels(
        prepared_frames["Apr 14 PM HFE run"],
        pre_label="pre-plunge",
        cooldown_start_index=cooldown_start_index_14_pm,
        turnaround_index=turnaround_index_14_pm,
    )
    prepared_frames["Apr 21 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 21 HFE run"],
        pre_label="log start",
        cooldown_start_index=0,
        turnaround_index=turnaround_index_21,
    )
    prepared_frames["Apr 23 HFE run"] = _add_phase_labels(
        prepared_frames["Apr 23 HFE run"],
        pre_label="log start",
        cooldown_start_index=0,
        turnaround_index=turnaround_index_23,
    )
    for name in generic_phase_names:
        prepared_frames[name] = _add_phase_labels(
            prepared_frames[name],
            pre_label=str(run_configs[name]["pre_label"]),
            cooldown_start_index=int(phase_metadata[name]["cooldown_start_index"]),
            turnaround_index=int(phase_metadata[name]["turnaround_index"]),
        )

    apr9_warmup_after_min = prepared_frames["Apr 9 HFE run"].loc[int(phase_metadata["Apr 9 HFE run"]["turnaround_index"]) :].copy()
    apr9_air_band_end_index = int(
        apr9_warmup_after_min.loc[
            apr9_warmup_after_min["probe_calibrated_raw_c"] >= -168.0
        ].index[0]
    )
    apr9_air_to_insulation_band_end_index = int(
        apr9_warmup_after_min.loc[
            apr9_warmup_after_min["probe_raw_c"] >= -145.0
        ].index[0]
    )
    phase_metadata["Apr 9 HFE run"]["air_band_end_time_min"] = float(
        prepared_frames["Apr 9 HFE run"].loc[apr9_air_band_end_index, "t_rel_min"]
    )
    phase_metadata["Apr 9 HFE run"]["insulation_band_start_time_min"] = float(
        prepared_frames["Apr 9 HFE run"].loc[apr9_air_to_insulation_band_end_index, "t_rel_min"]
    )

    apr14_air_marker = _interpolate_phase_marker(
        prepared_frames["Apr 14 HFE run"],
        phase="warmup",
        target_temp_c=-164.0,
    )
    if apr14_air_marker is not None:
        phase_metadata["Apr 14 HFE run"]["air_band_end_time_min"] = float(apr14_air_marker["time_min"])
        phase_metadata["Apr 14 HFE run"]["insulation_band_start_time_min"] = float(apr14_air_marker["time_min"])

    phase_metadata["Apr 14 PM HFE run"]["insulation_band_start_time_min"] = float(
        prepared_frames["Apr 14 PM HFE run"].loc[turnaround_index_14_pm, "t_rel_min"]
    )
    phase_metadata["Apr 21 HFE run"]["insulation_band_start_time_min"] = float(
        prepared_frames["Apr 21 HFE run"].loc[turnaround_index_21, "t_rel_min"]
    )
    phase_metadata["Apr 23 HFE run"]["insulation_band_start_time_min"] = float(
        prepared_frames["Apr 23 HFE run"].loc[turnaround_index_23, "t_rel_min"]
    )
    for name in generic_phase_names:
        if str(run_configs[name]["warmup_environment"]) == "insulation":
            phase_metadata[name]["insulation_band_start_time_min"] = float(
                prepared_frames[name].loc[int(phase_metadata[name]["turnaround_index"]), "t_rel_min"]
            )

    runs: dict[str, Hfe7200Ln2DipRun] = {}
    for name in run_order:
        config = run_configs[name]
        frame = prepared_frames[name].copy()
        cooldown_start_index = int(phase_metadata[name]["cooldown_start_index"])
        turnaround_index = int(phase_metadata[name]["turnaround_index"])
        cooldown_time_min = float(frame.loc[cooldown_start_index, "t_rel_min"])
        frame["t_from_cooldown_min"] = frame["t_rel_min"] - cooldown_time_min
        frame["phase_code"] = _phase_code_series(
            frame,
            warmup_environment=str(config["warmup_environment"]),
            insulation_band_start_time_min=phase_metadata[name]["insulation_band_start_time_min"],
        )
        frame["phase_detail"] = frame["phase_code"].map(PHASE_LABELS)

        timestamp = _parse_log_timestamp(run_paths[name])
        run_stub = Hfe7200Ln2DipRun(
            name=name,
            log_path=run_paths[name],
            timestamp=timestamp,
            data=frame,
            pre_label=str(config["pre_label"]),
            color=str(config["color"]),
            warmup_environment=str(config["warmup_environment"]),
            hfe_fill_volume_ml=float(fill_volumes_ml[name]),
            hfe_mass_g=float(hfe_masses_g[name]),
            room_temperature_c=room_reference_c,
            cooldown_start_index=cooldown_start_index,
            turnaround_index=turnaround_index,
            air_band_end_time_min=(
                None if phase_metadata[name]["air_band_end_time_min"] is None else float(phase_metadata[name]["air_band_end_time_min"])
            ),
            insulation_band_start_time_min=(
                None
                if phase_metadata[name]["insulation_band_start_time_min"] is None
                else float(phase_metadata[name]["insulation_band_start_time_min"])
            ),
            tc_correction_method=str(frame.attrs.get("tc_correction_method", "")),
            tc_correction_note=str(frame.attrs.get("tc_correction_note", "")),
            tc_room_anchor_samples=float(frame.attrs.get("legacy_tc_room_anchor_samples", np.nan)),
            tc_room_anchor_offsets_c=dict(frame.attrs.get("legacy_tc_room_anchor_offsets_c", {})),
            selected_warmup=pd.DataFrame(),
            dsc_like_summary=pd.DataFrame(),
            room_coupling_w_gk=np.nan,
            probe_random_noise_c=float(frame.attrs.get("probe_random_noise_c", np.nan)),
        )

        selected_warmup = _selected_warmup_frame(run_stub)
        selected_warmup, dsc_like_summary, room_coupling_w_gk = _compute_dsc_like_warmup(
            selected_warmup,
            room_temperature_c=room_reference_c,
            baseline_low_c=comparison_baseline_low_c,
            baseline_high_c=comparison_baseline_high_c,
            bin_width_c=dsc_bin_width_c,
            min_count=dsc_min_count,
        )

        runs[name] = Hfe7200Ln2DipRun(
            name=name,
            log_path=run_paths[name],
            timestamp=timestamp,
            data=frame,
            pre_label=str(config["pre_label"]),
            color=str(config["color"]),
            warmup_environment=str(config["warmup_environment"]),
            hfe_fill_volume_ml=float(fill_volumes_ml[name]),
            hfe_mass_g=float(hfe_masses_g[name]),
            room_temperature_c=room_reference_c,
            cooldown_start_index=cooldown_start_index,
            turnaround_index=turnaround_index,
            air_band_end_time_min=(
                None if phase_metadata[name]["air_band_end_time_min"] is None else float(phase_metadata[name]["air_band_end_time_min"])
            ),
            insulation_band_start_time_min=(
                None
                if phase_metadata[name]["insulation_band_start_time_min"] is None
                else float(phase_metadata[name]["insulation_band_start_time_min"])
            ),
            tc_correction_method=str(frame.attrs.get("tc_correction_method", "")),
            tc_correction_note=str(frame.attrs.get("tc_correction_note", "")),
            tc_room_anchor_samples=float(frame.attrs.get("legacy_tc_room_anchor_samples", np.nan)),
            tc_room_anchor_offsets_c=dict(frame.attrs.get("legacy_tc_room_anchor_offsets_c", {})),
            selected_warmup=selected_warmup,
            dsc_like_summary=dsc_like_summary,
            room_coupling_w_gk=room_coupling_w_gk,
            probe_random_noise_c=float(frame.attrs.get("probe_random_noise_c", np.nan)),
        )

    comparison_run_names = tuple(
        name for name in run_order
        if runs[name].timestamp >= comparison_start
    )

    three_m_curve = (
        pd.read_csv(three_m_curve_path)
        .dropna(subset=["temperature_C", "heat_flow_W_per_g"])
        .sort_values("temperature_C")
        .reset_index(drop=True)
        .copy()
    )
    three_m_baseline_mask = three_m_curve["temperature_C"].between(comparison_baseline_low_c, comparison_baseline_high_c)
    three_m_reference_level_w_g = float(three_m_curve.loc[three_m_baseline_mask, "heat_flow_W_per_g"].mean())
    three_m_curve["baseline_referenced_heat_flow_w_g"] = (
        three_m_curve["heat_flow_W_per_g"] - three_m_reference_level_w_g
    )
    three_m_curve["linear_baseline_referenced_heat_flow_w_g"] = (
        three_m_curve["heat_flow_W_per_g"].to_numpy(dtype=float)
        - _linear_fit_values(
            three_m_curve.loc[three_m_baseline_mask, "temperature_C"],
            three_m_curve.loc[three_m_baseline_mask, "heat_flow_W_per_g"],
            three_m_curve["temperature_C"],
        )
    )

    combined_dsc_curve = _combine_dsc_summaries(
        runs,
        comparison_run_names,
        rate_sigma_window_s=smoothing_window_s,
        bin_width_c=dsc_bin_width_c,
    )
    phase_transition_summary = _build_hfe7200_phase_transition_summary(
        runs,
        run_order,
        combined_dsc_curve=combined_dsc_curve,
        combined_source_name=f"{comparison_start:%b} {comparison_start.day}+ mean",
        three_m_curve=three_m_curve,
    )

    setup_table = pd.DataFrame(
        [
            {
                "Run": name,
                "Log file": run.log_path.name,
                "HFE fill volume [mL]": run.hfe_fill_volume_ml,
                "HFE mass [g]": run.hfe_mass_g,
                "Room temperature [°C]": run.room_temperature_c,
                "Warmup branch used": run.selected_warmup_label,
                "TC correction": run.tc_correction_method,
                "Included in Apr 14+ DSC comparison": run.timestamp >= comparison_start,
            }
            for name, run in runs.items()
        ]
    )
    calibration_summary = pd.DataFrame(
        [
            {
                "Run": name,
                "Log file": run.log_path.name,
                "TC correction": run.tc_correction_method,
                "Room anchor samples": run.tc_room_anchor_samples,
                "Room anchor offsets [°C]": _format_tc_offsets(run.tc_room_anchor_offsets_c),
                "Correction note": run.tc_correction_note,
            }
            for name, run in runs.items()
        ]
    )
    phase_summary = pd.DataFrame(
        [
            {
                "Run": name,
                "Cooldown start [min]": float(run.data.loc[run.cooldown_start_index, "t_rel_min"]),
                "Turnaround [min]": float(run.data.loc[run.turnaround_index, "t_rel_min"]),
                "Warmup in insulation start [min]": run.insulation_band_start_time_min,
                "Final time [min]": float(run.data["t_rel_min"].iloc[-1]),
                "Minimum temperature [°C]": float(run.data["probe_calibrated_smooth_c"].min()),
                "Maximum temperature [°C]": float(run.data["probe_calibrated_smooth_c"].max()),
                "k_run [mW/g/K]": run.room_coupling_w_gk * 1000.0,
            }
            for name, run in runs.items()
        ]
    )

    return Hfe7200Ln2DipReview(
        repo_root=resolved_repo_root,
        runs=runs,
        run_order=run_order,
        rate_run_names=tuple(name for name in DEFAULT_HFE7200_RATE_RUN_NAMES if name in runs),
        comparison_run_names=comparison_run_names,
        three_m_coherent_run_names=tuple(
            name for name in DEFAULT_HFE7200_THREE_M_COHERENT_RUN_NAMES if name in runs
        ),
        setup_table=setup_table,
        calibration_summary=calibration_summary,
        phase_summary=phase_summary,
        phase_transition_summary=phase_transition_summary,
        three_m_curve=three_m_curve,
        combined_dsc_curve=combined_dsc_curve,
        smoothing_window_s=float(smoothing_window_s),
        ln2_reference_c=ln2_reference_c,
        room_reference_c=room_reference_c,
        comparison_baseline_low_c=comparison_baseline_low_c,
        comparison_baseline_high_c=comparison_baseline_high_c,
        three_m_reference_points=DEFAULT_THREE_M_REFERENCE_POINTS,
    )


def _normalize_run_names(review: Hfe7200Ln2DipReview, run_names: Sequence[str] | None, *, default: Sequence[str]) -> tuple[str, ...]:
    resolved = tuple(default if run_names is None else run_names)
    unknown = [name for name in resolved if name not in review.runs]
    if unknown:
        raise KeyError(f"Unknown run name(s): {', '.join(unknown)}")
    return resolved


def _style_axis(ax: plt.Axes) -> None:
    ax.set_axisbelow(True)
    ax.minorticks_on()
    ax.grid(which="major", alpha=0.32)
    ax.grid(which="minor", alpha=0.14, linewidth=0.6)


def _apply_zero_centered_split_yscale(
    ax: plt.Axes,
    *,
    y_max_pos: float,
    y_abs_min_neg: float,
    tick_count_per_side: int = 4,
) -> None:
    """Zero-centred piecewise linear y-scale with independent positive/negative spans."""

    y_max_pos = float(y_max_pos)
    y_abs_min_neg = float(y_abs_min_neg)

    def forward(values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        return np.where(arr >= 0.0, arr / y_max_pos, arr / y_abs_min_neg)

    def inverse(values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        return np.where(arr >= 0.0, arr * y_max_pos, arr * y_abs_min_neg)

    ax.set_yscale("function", functions=(forward, inverse))
    ax.set_ylim(-y_abs_min_neg, y_max_pos)

    from matplotlib.ticker import FixedLocator

    positive_ticks = np.linspace(0.0, y_max_pos, tick_count_per_side + 1)[1:]
    negative_ticks = -np.linspace(0.0, y_abs_min_neg, tick_count_per_side + 1)[1:][::-1]
    ax.yaxis.set_major_locator(FixedLocator([*negative_ticks, 0.0, *positive_ticks]))


def _add_reference_temperature_lines(ax: plt.Axes, review: Hfe7200Ln2DipReview) -> None:
    for reference in review.three_m_reference_points:
        ax.axhline(float(reference["temperature_c"]), color="0.2", lw=0.9, ls="--", alpha=0.35)


def _add_reference_temperature_markers(ax: plt.Axes, review: Hfe7200Ln2DipReview) -> None:
    for reference in review.three_m_reference_points:
        ax.axvline(float(reference["temperature_c"]), color="0.2", lw=0.9, ls="--", alpha=0.35)


def _finite_interp(x_target: float, x_values: pd.Series | np.ndarray, y_values: pd.Series | np.ndarray) -> float:
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    finite_mask = np.isfinite(x_array) & np.isfinite(y_array)
    if finite_mask.sum() < 2:
        return float("nan")

    x_finite = x_array[finite_mask]
    y_finite = y_array[finite_mask]
    order = np.argsort(x_finite)
    x_sorted = x_finite[order]
    y_sorted = y_finite[order]
    if x_target < float(x_sorted[0]) or x_target > float(x_sorted[-1]):
        return float("nan")
    return float(np.interp(x_target, x_sorted, y_sorted))


def _linear_fit_values(
    x_fit_values: pd.Series | np.ndarray,
    y_fit_values: pd.Series | np.ndarray,
    x_eval_values: pd.Series | np.ndarray,
) -> np.ndarray:
    x_fit = np.asarray(x_fit_values, dtype=float)
    y_fit = np.asarray(y_fit_values, dtype=float)
    x_eval = np.asarray(x_eval_values, dtype=float)
    finite_mask = np.isfinite(x_fit) & np.isfinite(y_fit)
    if finite_mask.sum() >= 2:
        slope, intercept = np.polyfit(x_fit[finite_mask], y_fit[finite_mask], deg=1)
        return slope * x_eval + intercept
    if finite_mask.sum() == 1:
        return np.full_like(x_eval, float(y_fit[finite_mask][0]), dtype=float)
    return np.full_like(x_eval, np.nan, dtype=float)


def _global_minimum_point(
    x_values: pd.Series | np.ndarray,
    y_values: pd.Series | np.ndarray,
    *,
    x_range: tuple[float, float] | None = None,
) -> tuple[float, float]:
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    finite_mask = np.isfinite(x_array) & np.isfinite(y_array)
    if x_range is not None:
        low_x, high_x = x_range
        finite_mask &= (x_array >= float(low_x)) & (x_array <= float(high_x))
    if not finite_mask.any():
        return float("nan"), float("nan")

    x_finite = x_array[finite_mask]
    y_finite = y_array[finite_mask]
    minimum_index = int(np.argmin(y_finite))
    return float(x_finite[minimum_index]), float(y_finite[minimum_index])


def _global_maximum_point(
    x_values: pd.Series | np.ndarray,
    y_values: pd.Series | np.ndarray,
    *,
    minimum_x: float | None = None,
) -> tuple[float, float]:
    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    finite_mask = np.isfinite(x_array) & np.isfinite(y_array)
    if minimum_x is not None:
        finite_mask &= x_array >= float(minimum_x)
    if not finite_mask.any():
        return float("nan"), float("nan")

    x_finite = x_array[finite_mask]
    y_finite = y_array[finite_mask]
    maximum_index = int(np.argmax(y_finite))
    return float(x_finite[maximum_index]), float(y_finite[maximum_index])


def _plot_curve_with_gap_breaks(
    ax: plt.Axes,
    x: pd.Series | np.ndarray,
    y: pd.Series | np.ndarray,
    *,
    gap_threshold_c: float = 3.0,
    **plot_kwargs: Any,
) -> None:
    x_values = np.asarray(x, dtype=float)
    y_values = np.asarray(y, dtype=float)
    if x_values.size == 0:
        return

    order = np.argsort(x_values)
    x_values = x_values[order]
    y_values = y_values[order]
    finite_mask = np.isfinite(x_values) & np.isfinite(y_values)
    x_values = x_values[finite_mask]
    y_values = y_values[finite_mask]
    if x_values.size == 0:
        return

    split_indices = np.where(np.diff(x_values) > gap_threshold_c)[0] + 1
    for x_segment, y_segment in zip(np.split(x_values, split_indices), np.split(y_values, split_indices)):
        if x_segment.size == 0:
            continue
        ax.plot(x_segment, y_segment, **plot_kwargs)


def plot_hfe7200_ln2_dip_temperature_overview(
    review: Hfe7200Ln2DipReview,
    *,
    run_names: Sequence[str] | None = None,
    time_max_min: float = 90.0,
    ax: plt.Axes | None = None,
) -> plt.Axes:
    """Plot all selected HFE runs on one temperature-vs-time axis."""

    selected_run_names = _normalize_run_names(review, run_names, default=review.run_order)
    if ax is None:
        _, ax = plt.subplots(figsize=(12.2, 6.6), constrained_layout=True)

    for name in selected_run_names:
        run = review.runs[name]
        frame = run.data
        band_frame = frame.dropna(subset=["t_from_cooldown_min", "probe_calibrated_smooth_c"])
        if not band_frame.empty:
            band_temp = band_frame["probe_calibrated_smooth_c"].to_numpy(dtype=float)
            band_sigma = _ttest_tc_tolerance_c(band_temp)
            ax.fill_between(
                band_frame["t_from_cooldown_min"].to_numpy(dtype=float),
                band_temp - band_sigma,
                band_temp + band_sigma,
                color=run.color,
                alpha=0.15,
                linewidth=0,
            )
        for phase_code in PHASE_ORDER:
            phase_frame = frame.loc[frame["phase_code"] == phase_code]
            if phase_frame.empty:
                continue
            ax.plot(
                phase_frame["t_from_cooldown_min"],
                phase_frame["probe_calibrated_smooth_c"],
                color=run.color,
                ls=PHASE_LINESTYLES[phase_code],
                lw=PHASE_LINEWIDTHS[phase_code],
                alpha=PHASE_ALPHAS[phase_code],
            )

        turnaround = frame.loc[run.turnaround_index]
        ax.scatter(
            turnaround["t_from_cooldown_min"],
            turnaround["probe_calibrated_smooth_c"],
            s=34,
            facecolors="white",
            edgecolors=run.color,
            linewidths=1.2,
            marker="o",
            zorder=4,
        )
        if run.insulation_band_start_time_min is not None:
            insulation_candidates = frame.index[frame["phase_code"] == "warmup_insulation"]
            if len(insulation_candidates) > 0:
                insulation_start = frame.loc[int(insulation_candidates[0])]
                ax.scatter(
                    insulation_start["t_from_cooldown_min"],
                    insulation_start["probe_calibrated_smooth_c"],
                    s=34,
                    facecolors="white",
                    edgecolors=run.color,
                    linewidths=1.2,
                    marker="s",
                    zorder=4,
                )

    ax.axhline(review.room_reference_c, color="0.45", lw=1.1, ls="--", alpha=0.7)
    ax.axhline(review.ln2_reference_c, color="0.4", lw=1.0, ls=":", alpha=0.8)
    _style_axis(ax)

    x_min = min(
        float(review.runs[name].data["t_from_cooldown_min"].min())
        for name in selected_run_names
    )
    ax.set_xlim(x_min, float(time_max_min))

    run_handles = [
        Line2D([], [], color=review.runs[name].color, lw=2.0, label=name)
        for name in selected_run_names
    ]
    phase_handles = [
        Line2D([], [], color="0.2", lw=PHASE_LINEWIDTHS[phase], ls=PHASE_LINESTYLES[phase], label=PHASE_LABELS[phase])
        for phase in PHASE_ORDER
    ]
    phase_handles.extend(
        [
            Line2D([], [], color="0.2", marker="o", markersize=5, markerfacecolor="white", lw=0, label="turnaround"),
            Line2D([], [], color="0.2", marker="s", markersize=5, markerfacecolor="white", lw=0, label="insulation starts"),
            Line2D([], [], color="0.45", lw=1.1, ls="--", label=f"room ({review.room_reference_c:.1f} °C)"),
            Line2D([], [], color="0.4", lw=1.0, ls=":", label=f"LN2 ({review.ln2_reference_c:.1f} °C)"),
        ]
    )

    ax.legend(handles=[*run_handles, *phase_handles], loc="best", fontsize=8, ncol=2)

    ax.set_xlabel("Time from cooldown start [min]")
    ax.set_ylabel("HFE temperature [°C]")
    ax.set_title("HFE-7200 LN2 dip temperature histories")
    return ax


def plot_hfe7200_ln2_dip_rate_overview(
    review: Hfe7200Ln2DipReview,
    *,
    run_names: Sequence[str] | None = None,
    ax: plt.Axes | None = None,
    rate_sigma_window_s: float | None = None,
) -> plt.Axes:
    """Plot dT/dt vs temperature for the selected HFE runs."""

    if rate_sigma_window_s is None:
        rate_sigma_window_s = review.smoothing_window_s
    selected_run_names = _normalize_run_names(review, run_names, default=review.rate_run_names)
    if ax is None:
        _, ax = plt.subplots(figsize=(9.8, 6.0), constrained_layout=True)

    positive_rate_values: list[np.ndarray] = []
    negative_rate_values: list[np.ndarray] = []
    for name in selected_run_names:
        run = review.runs[name]
        frame = run.data
        rate_sigma_scalar = float(run.probe_random_noise_c) / float(rate_sigma_window_s)
        for phase_code in ("cooldown", "warmup_air", "warmup_insulation"):
            phase_frame = frame.loc[
                frame["phase_code"] == phase_code,
                ["probe_calibrated_smooth_c", "probe_calibrated_rate_c_s"],
            ].dropna()
            if phase_frame.empty:
                continue
            temp_values = phase_frame["probe_calibrated_smooth_c"].to_numpy(dtype=float)
            rate_values = phase_frame["probe_calibrated_rate_c_s"].to_numpy(dtype=float)
            positive_rate_values.append(rate_values[rate_values > 0.0])
            negative_rate_values.append(rate_values[rate_values < 0.0])
            ax.fill_between(
                temp_values,
                rate_values - rate_sigma_scalar,
                rate_values + rate_sigma_scalar,
                color=run.color,
                alpha=0.15,
                linewidth=0,
            )
            ax.plot(
                temp_values,
                rate_values,
                color=run.color,
                ls=PHASE_LINESTYLES[phase_code],
                lw=PHASE_LINEWIDTHS[phase_code],
                alpha=0.9,
            )

    ax.axhline(0.0, color="0.35", lw=1.0)
    _style_axis(ax)

    y_max_pos = 0.0
    if positive_rate_values:
        stacked_positive = np.concatenate(positive_rate_values)
        finite_positive = stacked_positive[np.isfinite(stacked_positive)]
        if finite_positive.size > 0:
            y_max_pos = 1.05 * float(np.max(finite_positive))

    y_abs_min_neg = 0.0
    if negative_rate_values:
        stacked_negative = np.concatenate(negative_rate_values)
        finite_negative = stacked_negative[np.isfinite(stacked_negative)]
        if finite_negative.size > 0:
            y_abs_min_neg = 1.05 * float(np.max(np.abs(finite_negative)))

    if y_max_pos > 0.0 and y_abs_min_neg > 0.0:
        _apply_zero_centered_split_yscale(ax, y_max_pos=y_max_pos, y_abs_min_neg=y_abs_min_neg)

    run_handles = [
        Line2D([], [], color=review.runs[name].color, lw=2.0, label=name)
        for name in selected_run_names
    ]
    phase_handles = [
        Line2D([], [], color="0.2", lw=PHASE_LINEWIDTHS[phase], ls=PHASE_LINESTYLES[phase], label=PHASE_LABELS[phase])
        for phase in ("cooldown", "warmup_air", "warmup_insulation")
    ]

    ax.legend(handles=[*run_handles, *phase_handles], loc="best", fontsize=8)

    ax.set_xlabel("HFE temperature [°C]")
    ax.set_ylabel("Temperature rate dT/dt [°C/s]")
    ax.set_title("Calibrated HFE temperature-rate histories")
    return ax


def plot_hfe7200_ln2_dip_dsc_like_overview(
    review: Hfe7200Ln2DipReview,
    *,
    run_names: Sequence[str] | None = None,
    include_mean_band: bool = True,
    show_pre_background: bool = True,
    rate_sigma_window_s: float | None = None,
    ax: plt.Axes | None = None,
) -> plt.Axes:
    """Plot DSC-like warmup curves for the selected HFE runs."""

    if rate_sigma_window_s is None:
        rate_sigma_window_s = review.smoothing_window_s
    selected_run_names = _normalize_run_names(review, run_names, default=review.comparison_run_names)
    if ax is None:
        _, ax = plt.subplots(figsize=(10.0, 6.0), constrained_layout=True)

    ax.axvspan(review.comparison_baseline_low_c, review.comparison_baseline_high_c, color="0.55", alpha=0.08)
    baseline_handle = Patch(
        facecolor="0.55",
        alpha=0.08,
        label=(
            f"background-fit interval "
            f"({review.comparison_baseline_low_c:.0f} to {review.comparison_baseline_high_c:.0f} °C)"
        ),
    )
    band_handle: Patch | None = None
    mean_handle: Line2D | None = None

    if include_mean_band:
        combined = _combine_dsc_summaries(
            review.runs,
            selected_run_names,
            rate_sigma_window_s=rate_sigma_window_s,
        )
        if not combined.empty:
            ax.fill_between(
                combined["temperature_c"],
                combined["lower_heat_flow_w_g"],
                combined["upper_heat_flow_w_g"],
                color="C3",
                alpha=0.16,
                zorder=1,
            )
            ax.plot(
                combined["temperature_c"],
                combined["mean_heat_flow_w_g"],
                color="C3",
                lw=2.4,
                alpha=0.95,
                zorder=3,
            )
            band_handle = Patch(facecolor="C3", alpha=0.16, label="equal-weight mean ±1σ")
            mean_handle = Line2D([], [], color="C3", lw=2.4, label="equal-weight mean")

    run_handles: list[Line2D] = []
    for name in selected_run_names:
        run = review.runs[name]
        curve = _raw_dsc_plot_curve(
            run,
            extra_columns=("estimated_heat_flow_w_g", "apparent_capacity_j_k"),
        )
        if curve.empty:
            continue
        _overlay_run_dsc_curve(
            ax,
            curve=curve,
            run=run,
            rate_sigma_window_s=rate_sigma_window_s,
            show_pre_background=show_pre_background,
        )
        run_handles.append(Line2D([], [], color=run.color, lw=1.8, label=name))

    ax.axhline(0.0, color="0.35", lw=1.0)
    _style_axis(ax)
    legend_handles: list[Any] = [baseline_handle, *run_handles]
    if show_pre_background:
        legend_handles.extend(
            Line2D(
                [],
                [],
                color=review.runs[name].color,
                lw=1.1,
                ls=":",
                label=f"{name} pre-background-removal",
            )
            for name in selected_run_names
        )
    legend_handles.append(
        Patch(facecolor="0.2", alpha=0.18, label="per-run reading-error band")
    )
    if mean_handle is not None:
        legend_handles.append(mean_handle)
    if band_handle is not None:
        legend_handles.append(band_handle)
    ax.legend(handles=legend_handles, loc="best", fontsize=8)

    ax.set_xlabel("HFE temperature [°C]")
    ax.set_ylabel("DSC-like event heat flow (Exo Up) [W/g]")
    ax.set_title("HFE-7200 DSC-like warmup curves")
    return ax


def _overlay_run_dsc_curve(
    ax: plt.Axes,
    *,
    curve: pd.DataFrame,
    run: Hfe7200Ln2DipRun,
    rate_sigma_window_s: float,
    show_pre_background: bool,
    event_linewidth: float = 1.45,
    event_alpha: float = 0.88,
    pre_background_alpha: float = 0.7,
) -> None:
    temperature_c = curve["temperature_c"].to_numpy(dtype=float)
    event_w_g = curve["smoothed_heat_flow_w_g"].to_numpy(dtype=float)
    capacity_j_k = curve["smoothed_apparent_capacity_j_k"].to_numpy(dtype=float)
    sigma_rate_c_s = float(run.probe_random_noise_c) / float(rate_sigma_window_s)
    sigma_w_g = (capacity_j_k / float(run.hfe_mass_g)) * sigma_rate_c_s

    ax.fill_between(
        temperature_c,
        event_w_g - sigma_w_g,
        event_w_g + sigma_w_g,
        color=run.color,
        alpha=0.18,
        linewidth=0,
        zorder=1,
    )
    if show_pre_background:
        pre_background_w_g = -curve["smoothed_estimated_heat_flow_w_g"].to_numpy(dtype=float)
        ax.plot(
            temperature_c,
            pre_background_w_g,
            color=run.color,
            lw=1.1,
            ls=":",
            alpha=pre_background_alpha,
            zorder=2,
        )
    ax.plot(
        temperature_c,
        event_w_g,
        color=run.color,
        lw=event_linewidth,
        alpha=event_alpha,
        zorder=3,
    )


def plot_hfe7200_ln2_dip_vs_three_m(
    review: Hfe7200Ln2DipReview,
    *,
    run_names: Sequence[str] | None = None,
    include_mean_band: bool = True,
    show_pre_background: bool = True,
    rate_sigma_window_s: float | None = None,
    ax: plt.Axes | None = None,
) -> plt.Axes:
    """Plot DSC-like HFE warmup curves against the digitized 3M reference.

    Defaults to the selected HFE subset whose DSC-like event curves are most
    coherent in the transition region. Both the digitized 3M reference and the
    measured event curve are shown relative to the background-temperature range.
    """

    if rate_sigma_window_s is None:
        rate_sigma_window_s = review.smoothing_window_s
    selected_run_names = _normalize_run_names(
        review, run_names, default=review.three_m_coherent_run_names
    )
    axis_label_fontsize = 24
    tick_label_fontsize = 20
    legend_fontsize = 17
    reference_linewidth = 3.0
    mean_linewidth = 3.8
    pre_background_linewidth = 1.8
    transition_marker_size = 115
    transition_marker_linewidth = 1.6
    if ax is None:
        _, ax = plt.subplots(figsize=(12.0, 9.0), constrained_layout=True)

    three_m_baseline_mask = review.three_m_curve["temperature_C"].between(
        review.comparison_baseline_low_c,
        review.comparison_baseline_high_c,
    )
    three_m_baseline_temperature_c = review.three_m_curve.loc[
        three_m_baseline_mask,
        "temperature_C",
    ]
    three_m_baseline_heat_flow_w_g = review.three_m_curve.loc[
        three_m_baseline_mask,
        "heat_flow_W_per_g",
    ]
    three_m_plot_curve = review.three_m_curve.copy()
    three_m_plot_curve["baseline_referenced_heat_flow_w_g"] = (
        three_m_plot_curve["heat_flow_W_per_g"].to_numpy(dtype=float)
        - _linear_fit_values(
            three_m_baseline_temperature_c,
            three_m_baseline_heat_flow_w_g,
            three_m_plot_curve["temperature_C"],
        )
    )

    ax.axvspan(
        review.comparison_baseline_low_c,
        review.comparison_baseline_high_c,
        color="0.55",
        alpha=0.08,
    )
    baseline_handle = Patch(
        facecolor="0.55",
        alpha=0.08,
        label=f"Background fit ({review.comparison_baseline_low_c:.0f}→{review.comparison_baseline_high_c:.0f}°C)",
    )

    ax.plot(
        three_m_plot_curve["temperature_C"],
        three_m_plot_curve["baseline_referenced_heat_flow_w_g"],
        color="black",
        lw=reference_linewidth,
        alpha=0.95,
        zorder=4,
        label="3M reference",
    )

    band_handle: Patch | None = None
    mean_handle: Line2D | None = None
    combined_mean_curve = pd.DataFrame()
    pre_background_handle: Line2D | None = None
    transition_handles: list[Line2D] = []
    if show_pre_background:
        plotted_pre_background = False
        for name in selected_run_names:
            run = review.runs[name]
            curve = _raw_dsc_plot_curve(
                run,
                extra_columns=("estimated_heat_flow_w_g",),
            )
            if curve.empty or "smoothed_estimated_heat_flow_w_g" not in curve:
                continue
            pre_background_w_g = -curve["smoothed_estimated_heat_flow_w_g"].to_numpy(dtype=float)
            ax.plot(
                curve["temperature_c"],
                pre_background_w_g,
                color=run.color,
                lw=pre_background_linewidth,
                ls=":",
                alpha=0.78,
                zorder=4.5,
            )
            plotted_pre_background = True
        if plotted_pre_background:
            pre_background_handle = Line2D(
                [],
                [],
                color="0.35",
                lw=pre_background_linewidth,
                ls=":",
                label="Raw data (no background removal)",
            )

    if include_mean_band:
        combined = _combine_dsc_summaries(
            review.runs,
            selected_run_names,
            rate_sigma_window_s=rate_sigma_window_s,
            extend_to_union=True,
        )
        if not combined.empty:
            combined_mean_curve = combined.copy()
            combined_mean_curve["plot_mean_heat_flow_w_g"] = (
                combined_mean_curve["mean_heat_flow_w_g"]
            )
            combined_mean_curve["plot_lower_combined_uncertainty_heat_flow_w_g"] = (
                combined_mean_curve["lower_combined_uncertainty_heat_flow_w_g"]
            )
            combined_mean_curve["plot_upper_combined_uncertainty_heat_flow_w_g"] = (
                combined_mean_curve["upper_combined_uncertainty_heat_flow_w_g"]
            )
            mean_label = "Measured mean"
            band_label = "Combined uncertainty"
            ax.fill_between(
                combined_mean_curve["temperature_c"],
                combined_mean_curve["plot_lower_combined_uncertainty_heat_flow_w_g"],
                combined_mean_curve["plot_upper_combined_uncertainty_heat_flow_w_g"],
                color="C3",
                alpha=0.28,
                zorder=5,
            )
            ax.plot(
                combined_mean_curve["temperature_c"],
                combined_mean_curve["plot_mean_heat_flow_w_g"],
                color="C3",
                lw=mean_linewidth,
                alpha=0.98,
                zorder=6,
            )
            band_handle = Patch(facecolor="C3", alpha=0.28, label=band_label)
            mean_handle = Line2D([], [], color="C3", lw=mean_linewidth, label=mean_label)

    if not combined_mean_curve.empty:
        transition_points = sorted(
            review.three_m_reference_points,
            key=lambda item: (
                ["glass_transition", "melt_temperature", "cold_crystallization"].index(str(item["key"]))
                if str(item["key"]) in {"glass_transition", "melt_temperature", "cold_crystallization"}
                else 99
            ),
        )
        for reference in transition_points:
            reference_key = str(reference["key"])
            marker = THREE_M_TRANSITION_MARKERS.get(reference_key, "o")
            if reference_key == "melt_temperature":
                three_m_temp_c, three_m_y = _global_minimum_point(
                    three_m_plot_curve["temperature_C"],
                    three_m_plot_curve["baseline_referenced_heat_flow_w_g"],
                )
                measured_temp_c, measured_y = _global_minimum_point(
                    combined_mean_curve["temperature_c"],
                    combined_mean_curve["plot_mean_heat_flow_w_g"],
                )
            elif reference_key == "glass_transition":
                three_m_temp_c, three_m_y = _global_minimum_point(
                    three_m_plot_curve["temperature_C"],
                    three_m_plot_curve["baseline_referenced_heat_flow_w_g"],
                    x_range=GLASS_TRANSITION_SEARCH_RANGE_C,
                )
                measured_temp_c = MEASURED_GLASS_TRANSITION_TEMPERATURE_C
                measured_y = _finite_interp(
                    measured_temp_c,
                    combined_mean_curve["temperature_c"],
                    combined_mean_curve["plot_mean_heat_flow_w_g"],
                )
            elif reference_key == "cold_crystallization":
                three_m_temp_c, three_m_y = _global_maximum_point(
                    three_m_plot_curve["temperature_C"],
                    three_m_plot_curve["baseline_referenced_heat_flow_w_g"],
                    minimum_x=COLD_CRYSTALLIZATION_MIN_TEMPERATURE_C,
                )
                measured_temp_c, measured_y = _global_maximum_point(
                    combined_mean_curve["temperature_c"],
                    combined_mean_curve["plot_mean_heat_flow_w_g"],
                    minimum_x=COLD_CRYSTALLIZATION_MIN_TEMPERATURE_C,
                )
            else:
                transition_temp_c = float(reference["temperature_c"])
                three_m_temp_c = transition_temp_c
                measured_temp_c = transition_temp_c
                three_m_y = _finite_interp(
                    transition_temp_c,
                    three_m_plot_curve["temperature_C"],
                    three_m_plot_curve["baseline_referenced_heat_flow_w_g"],
                )
                measured_y = _finite_interp(
                    transition_temp_c,
                    combined_mean_curve["temperature_c"],
                    combined_mean_curve["plot_mean_heat_flow_w_g"],
                )

            if np.isfinite(three_m_y):
                ax.scatter(
                    three_m_temp_c,
                    three_m_y,
                    s=transition_marker_size,
                    marker=marker,
                    facecolors="white",
                    edgecolors="black",
                    linewidths=transition_marker_linewidth,
                    zorder=8,
                )
                if reference_key == "melt_temperature":
                    ax.annotate(
                        f"{three_m_temp_c:.0f}°C",
                        (three_m_temp_c, three_m_y),
                        xytext=(18, 2),
                        textcoords="offset points",
                        ha="left",
                        va="bottom",
                        fontsize=14,
                        color="black",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )
                elif reference_key == "cold_crystallization":
                    ax.annotate(
                        f"{three_m_temp_c:.0f}°C",
                        (three_m_temp_c, three_m_y),
                        xytext=(18, 4),
                        textcoords="offset points",
                        ha="left",
                        va="bottom",
                        fontsize=14,
                        color="black",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )
                elif reference_key == "glass_transition":
                    ax.annotate(
                        f"{three_m_temp_c:.0f}°C",
                        (three_m_temp_c, three_m_y),
                        xytext=(8, -10),
                        textcoords="offset points",
                        ha="left",
                        va="top",
                        fontsize=14,
                        color="black",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )
            if np.isfinite(measured_y):
                ax.scatter(
                    measured_temp_c,
                    measured_y,
                    s=transition_marker_size,
                    marker=marker,
                    facecolors="C3",
                    edgecolors="white",
                    linewidths=transition_marker_linewidth,
                    zorder=9,
                )
                if reference_key == "melt_temperature":
                    ax.annotate(
                        f"{measured_temp_c:.0f}°C",
                        (measured_temp_c, measured_y),
                        xytext=(-12, 4),
                        textcoords="offset points",
                        ha="right",
                        va="bottom",
                        fontsize=14,
                        color="C3",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )
                elif reference_key == "cold_crystallization":
                    ax.annotate(
                        f"{measured_temp_c:.0f}°C",
                        (measured_temp_c, measured_y),
                        xytext=(-8, 20),
                        textcoords="offset points",
                        ha="right",
                        va="bottom",
                        fontsize=14,
                        color="C3",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )
                elif reference_key == "glass_transition":
                    ax.annotate(
                        f"{measured_temp_c:.0f}°C",
                        (measured_temp_c, measured_y),
                        xytext=(10, 8),
                        textcoords="offset points",
                        ha="left",
                        va="bottom",
                        fontsize=14,
                        color="C3",
                        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.82},
                        zorder=10,
                    )

            transition_handles.append(
                Line2D(
                    [],
                    [],
                    color="0.2",
                    marker=marker,
                    markersize=9,
                    markerfacecolor="white",
                    markeredgecolor="0.2",
                    markeredgewidth=transition_marker_linewidth,
                    lw=0,
                    label=THREE_M_TRANSITION_LABELS.get(reference_key, str(reference["label"])),
                )
            )

    ax.axhline(0.0, color="0.35", lw=1.4)
    _style_axis(ax)
    ax.tick_params(
        axis="both",
        which="major",
        labelsize=tick_label_fontsize,
        length=7,
        width=1.2,
    )
    ax.tick_params(axis="both", which="minor", length=4, width=0.9)

    legend_handles: list[Any] = [
        Line2D(
            [],
            [],
            color="black",
            lw=reference_linewidth,
            label="3M reference",
        ),
    ]
    if mean_handle is not None:
        legend_handles.append(mean_handle)
    if band_handle is not None:
        legend_handles.append(band_handle)
    if pre_background_handle is not None:
        legend_handles.append(pre_background_handle)
    legend_handles.append(baseline_handle)
    legend_handles.extend(transition_handles)

    ax.legend(
        handles=legend_handles,
        loc="best",
        fontsize=legend_fontsize,
        ncol=2,
        frameon=True,
        framealpha=0.92,
        borderpad=0.6,
        labelspacing=0.55,
        handlelength=2.4,
        columnspacing=1.0,
    )

    if not combined_mean_curve.empty:
        x_min = min(
            float(review.three_m_curve["temperature_C"].min()),
            float(combined_mean_curve["temperature_c"].min()),
        )
        x_max = max(
            float(review.three_m_curve["temperature_C"].max()),
            float(combined_mean_curve["temperature_c"].max()),
        )
        x_padding = max(3.0, 0.03 * (x_max - x_min))
        ax.set_xlim(x_min - x_padding, x_max + x_padding)

    ax.set_xlabel("HFE temperature [°C]", fontsize=axis_label_fontsize, labelpad=10)
    ax.set_ylabel(
        "Baseline-referenced heat flow (Exo Up) [W/g]",
        fontsize=axis_label_fontsize,
        labelpad=12,
    )
    ax.set_title("")
    return ax
