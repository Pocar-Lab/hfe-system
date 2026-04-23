"""Shared ORCA helpers for flow-log review notebooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import re
from typing import Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import linregress

from .leaks import HFE_7200_DENSITY_INTERCEPT_G_ML, HFE_7200_DENSITY_SLOPE_G_ML_PER_C

LB_TO_KG = 0.45359237
US_GAL_TO_M3 = 0.003785411784
FT_TO_M = 0.3048
LB_PER_GAL_TO_KG_PER_M3 = LB_TO_KG / US_GAL_TO_M3
BAR_LMIN_TO_W = 1e5 * 1e-3 / 60.0

DEFAULT_GEAR_PUMP_POWER_TREND_S = 150.0

DEFAULT_HFE_LIQUID_DENSITY_BOUNDS = (1200.0, 1600.0)

MAIN_OVERVIEW_PUMP_CMD_YLIM = (0.0, 100.0)
MAIN_OVERVIEW_PUMP_FREQ_YLIM = (0.0, 72.0)
MAIN_OVERVIEW_TEMPERATURE_YLIM = (-120.0, 30.0)
MAIN_OVERVIEW_MASS_FLOW_YLIM = (-1.5, 7.0)
MAIN_OVERVIEW_VOLUME_FLOW_YLIM = (-2.0, 6.5)
MAIN_OVERVIEW_PRESSURE_YLIM = (0.9, 3.4)

SEGMENT_CLASS_COLORS: Mapping[str, str] = {
    "gas-rich / empty": "C3",
    "priming / flow-direction flips": "C1",
    "draining tail / mixed phase": "C4",
    "short usable window": "C2",
    "usable liquid circulation": "C0",
}

DEFAULT_INTERESTING_TEMPERATURE_SIGNALS = (
    "density_kg_m3_si",
    "mass_flow_kgmin_si",
    "volume_flow_lmin_si",
    "pump_input_power_w",
    "pump_output_current_a",
    "delta_p_bar_recomputed",
)

SIGNAL_LABELS: Mapping[str, str] = {
    "temperature_c_si": "Flow-meter temperature [°C]",
    "temp_mean_C": "TC mean [°C]",
    "temp_span_C": "TC span [°C]",
    "density_kg_m3_si": "Density [kg/m$^3$]",
    "mass_flow_kgmin_si": "Mass flow [kg/min]",
    "volume_flow_lmin_si": "Volume flow [L/min]",
    "cum_mass_kg": "Cumulative mass [kg]",
    "cum_volume_l": "Cumulative volume [L]",
    "pump_freq_hz": "Pump frequency [Hz]",
    "pump_input_power_w": "Pump input power [W]",
    "pump_input_power_trend_w": "Smoothed pump input power [W]",
    "pump_output_current_a": "Pump output current [A]",
    "pump_output_voltage_v": "Pump output voltage [V]",
    "pump_pressure_before_bar_abs": "Before pump [bar abs]",
    "pump_pressure_after_bar_abs": "After pump [bar abs]",
    "pump_pressure_tank_bar_abs": "Tank [bar abs]",
    "delta_p_bar_recomputed": "Pressure rise [bar]",
    "hydraulic_power_w": "Hydraulic power [W]",
    "nu_ref_local_cSt": "Local smooth law [cSt]",
    "nu_density_power_cSt": "Density-power fit [cSt]",
    "nu_combined_law_cSt": "Combined pump+flow law [cSt]",
    "nu_combined_law_trend_cSt": "Combined-law mean trend [cSt]",
    "nu_gear_power_law_cSt": "Gear-pump power law [cSt]",
    "nu_flow_only_proxy_cSt": "Flow-only proxy [cSt]",
}

THERMOCOUPLE_TAG_BY_INDEX: Mapping[int, str] = {
    0: "U0",
    1: "U1",
    2: "U2",
    3: "TFO",
    4: "TTI",
    5: "U5",
    6: "TTO",
    7: "TMI",
    8: "THI",
    9: "THM",
}

CONNECTED_THERMOCOUPLE_TAGS: tuple[str, ...] = ("TFO", "TTI", "TTO", "TMI", "THI", "THM")

THERMOCOUPLE_COLUMN_MAP: Mapping[str, str] = {
    f"temp{index}_C": f"{tag}_C"
    for index, tag in THERMOCOUPLE_TAG_BY_INDEX.items()
    if tag in CONNECTED_THERMOCOUPLE_TAGS
}

THERMOCOUPLE_LABELS: Mapping[str, str] = {
    "TFO_C": "TFO",
    "TTI_C": "TTI",
    "TTO_C": "TTO",
    "TMI_C": "TMI",
    "THM_C": "THM",
    "THI_C": "THI",
}

LEGACY_TC_FIX_TIMESTAMP = datetime(2026, 4, 20, 11, 15, 45)
LEGACY_WRONG_TYPE_TC_COLUMNS: tuple[str, ...] = ("TTEST_C", "TFO_C", "TTI_C", "TTO_C", "TMI_C")
LEGACY_FLOW_WRONG_TYPE_TC_COLUMNS: tuple[str, ...] = ("TFO_C", "TTI_C", "TTO_C", "TMI_C")
LEGACY_EFFECTIVE_COLD_JUNCTION_C = 21.44563390332121
LEGACY_ROOM_ONLY_TC_CALIBRATION: Mapping[str, tuple[float, float]] = {
    "THM_C": (1.0, -0.6422222222222214),
    "THI_C": (1.0, 0.5877777777777773),
}

# NIST ITS-90 absolute emf reference functions for the two thermocouple types we
# need for legacy back-conversion. The logged legacy temperatures were produced
# by a Type-K linearisation of mostly Type-T probes.
_NIST_TYPE_K_ABS_EMF_TABLE = (
    (
        -270.0,
        0.0,
        np.array(
            [
                -0.163226974860e-22,
                -0.198892668780e-19,
                -0.104516093650e-16,
                -0.310888728940e-14,
                -0.574103274280e-12,
                -0.675090591730e-10,
                -0.499048287770e-08,
                -0.328589067840e-06,
                0.236223735980e-04,
                0.394501280250e-01,
                0.000000000000e00,
            ]
        ),
        None,
    ),
    (
        0.0,
        1372.0,
        np.array(
            [
                -0.121047212750e-25,
                0.971511471520e-22,
                -0.320207200030e-18,
                0.560750590590e-15,
                -0.560728448890e-12,
                0.318409457190e-09,
                -0.994575928740e-07,
                0.185587700320e-04,
                0.389212049750e-01,
                -0.176004136860e-01,
            ]
        ),
        (0.118597600000e00, -0.118343200000e-03, 0.126968600000e03),
    ),
)
_NIST_TYPE_T_ABS_EMF_TABLE = (
    (
        -270.0,
        0.0,
        np.array(
            [
                0.797951539270e-30,
                0.139450270620e-26,
                0.107955392700e-23,
                0.487686622860e-21,
                0.142515947790e-18,
                0.282135219250e-16,
                0.384939398830e-14,
                0.360711542050e-12,
                0.226511565930e-10,
                0.901380195590e-09,
                0.200329735540e-07,
                0.118443231050e-06,
                0.441944343470e-04,
                0.387481063640e-01,
                0.000000000000e00,
            ]
        ),
        None,
    ),
    (
        0.0,
        400.0,
        np.array(
            [
                -0.275129016730e-19,
                0.454791352900e-16,
                -0.308157587720e-13,
                0.109968809280e-10,
                -0.218822568460e-08,
                0.206182434040e-06,
                0.332922278800e-04,
                0.387481063640e-01,
                0.000000000000e00,
            ]
        ),
        None,
    ),
)

SIGNAL_TITLES: Mapping[str, str] = {
    "density_kg_m3_si": "Density follows temperature closely",
    "mass_flow_kgmin_si": "Mass flow drifts with cooldown",
    "volume_flow_lmin_si": "Volume flow changes only modestly",
    "pump_input_power_w": "Pump input power carries a weak trend",
    "pump_output_current_a": "Pump current carries a weak trend",
    "delta_p_bar_recomputed": "Pressure rise changes during cooldown",
}

REFERENCE_3M_N7200 = pd.DataFrame(
    {
        "temperature_c": [25.0, 0.0, -10.0, -20.0, -30.0, -40.0, -50.0, -60.0, -70.0, -100.0, -120.0],
        "kinematic_viscosity_cSt": [0.41, 0.67, 0.78, 0.93, 1.14, 1.42, 1.84, 2.48, 3.72, 12.47, 64.47],
    }
).sort_values("temperature_c")


@dataclass(frozen=True)
class RegressionSummary:
    """Small wrapper around a linear fit result."""

    slope: float
    intercept: float
    rvalue: float
    pvalue: float
    stderr: float
    intercept_stderr: float
    n_samples: int

    @property
    def r_squared(self) -> float:
        return float(self.rvalue**2)


@dataclass(frozen=True)
class FlowLogReview:
    """Prepared view of a flow log with the stable cooldown hold extracted."""

    log_path: Path
    data: pd.DataFrame
    flow_note: str
    valid_temp_cols: tuple[str, ...]
    segment_summary: pd.DataFrame
    run_segment_id: int
    dominant_cmd_pct: float
    sweep_windows: pd.DataFrame
    sweep_step_summary: pd.DataFrame
    control_phase_summary: pd.DataFrame
    cooldown: pd.DataFrame
    cooldown_phase_summary: pd.DataFrame

    @property
    def stable_start_s(self) -> float:
        return float(self.cooldown["time_s"].iloc[0])

    @property
    def stable_end_s(self) -> float:
        return float(self.cooldown["time_s"].iloc[-1])

    @property
    def stable_start_min(self) -> float:
        return self.stable_start_s / 60.0

    @property
    def stable_end_min(self) -> float:
        return self.stable_end_s / 60.0


@dataclass(frozen=True)
class SignalTemperatureStudy:
    """Summary of cooldown signals regressed against temperature."""

    signals: tuple[str, ...]
    summary: pd.DataFrame


@dataclass(frozen=True)
class DensityStudy:
    """Density-centric cooldown study, including viscosity proxies."""

    cooldown: pd.DataFrame
    fits: Mapping[str, RegressionSummary]
    regression_summary: pd.DataFrame
    reference_summary: pd.DataFrame
    single_parameter_law_summary: pd.DataFrame
    combined_law_summary: pd.DataFrame
    law_phase_summary: pd.DataFrame
    comparison_summary: pd.DataFrame
    dynamic_summary: pd.DataFrame
    combined_trend_window: int


def _numeric_column(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    value = frame[column] if column in frame.columns else pd.Series(default, index=frame.index)
    return pd.to_numeric(value, errors="coerce")


def _evaluate_nist_abs_emf_c(values_c: np.ndarray | pd.Series | float, table: Sequence[tuple]) -> np.ndarray:
    temperatures_c = np.asarray(values_c, dtype=float)
    emf_mv = np.full(temperatures_c.shape, np.nan, dtype=float)
    last_index = len(table) - 1

    for index, (t_min_c, t_max_c, coefficients, gaussian) in enumerate(table):
        upper_ok = temperatures_c <= t_max_c if index == last_index else temperatures_c < t_max_c
        mask = np.isfinite(temperatures_c) & (temperatures_c >= t_min_c) & upper_ok
        if not np.any(mask):
            continue

        segment_temp_c = temperatures_c[mask]
        segment_emf_mv = np.polyval(coefficients, segment_temp_c)
        if gaussian is not None:
            amplitude, exponent_scale, center_c = gaussian
            segment_emf_mv = segment_emf_mv + amplitude * np.exp(exponent_scale * (segment_temp_c - center_c) ** 2)
        emf_mv[mask] = segment_emf_mv

    return emf_mv


@lru_cache(maxsize=1)
def _type_t_abs_emf_lookup() -> tuple[np.ndarray, np.ndarray]:
    # A dense monotonic lookup table is fast and stable enough for notebook use.
    temperature_grid_c = np.arange(-270.0, 400.0 + 0.02, 0.02, dtype=float)
    emf_grid_mv = _evaluate_nist_abs_emf_c(temperature_grid_c, _NIST_TYPE_T_ABS_EMF_TABLE)
    return emf_grid_mv, temperature_grid_c


def _inverse_type_t_abs_emf_to_c(emf_mv: np.ndarray | pd.Series | float) -> np.ndarray:
    emf_values_mv = np.asarray(emf_mv, dtype=float)
    emf_grid_mv, temperature_grid_c = _type_t_abs_emf_lookup()
    temperatures_c = np.full(emf_values_mv.shape, np.nan, dtype=float)
    in_range = (
        np.isfinite(emf_values_mv)
        & (emf_values_mv >= float(emf_grid_mv[0]))
        & (emf_values_mv <= float(emf_grid_mv[-1]))
    )
    if np.any(in_range):
        temperatures_c[in_range] = np.interp(emf_values_mv[in_range], emf_grid_mv, temperature_grid_c)
    return temperatures_c


def _legacy_wrong_k_to_true_t_c(
    values_c: np.ndarray | pd.Series | float,
    *,
    cold_junction_c: float = LEGACY_EFFECTIVE_COLD_JUNCTION_C,
) -> np.ndarray:
    wrong_values_c = np.asarray(values_c, dtype=float)
    measured_abs_emf_mv = _evaluate_nist_abs_emf_c(wrong_values_c, _NIST_TYPE_K_ABS_EMF_TABLE)

    cj_abs_emf_k_mv = float(_evaluate_nist_abs_emf_c(np.array([cold_junction_c]), _NIST_TYPE_K_ABS_EMF_TABLE)[0])
    cj_abs_emf_t_mv = float(_evaluate_nist_abs_emf_c(np.array([cold_junction_c]), _NIST_TYPE_T_ABS_EMF_TABLE)[0])

    measured_relative_emf_mv = measured_abs_emf_mv - cj_abs_emf_k_mv
    corrected_abs_emf_t_mv = measured_relative_emf_mv + cj_abs_emf_t_mv
    return _inverse_type_t_abs_emf_to_c(corrected_abs_emf_t_mv)


def _parse_log_timestamp(log_path: str | Path | None) -> datetime | None:
    if log_path is None:
        return None
    match = re.search(r"log_(\d{8})_(\d{6})", Path(log_path).stem)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")


def is_legacy_wrong_type_log(log_path: str | Path | None) -> bool:
    timestamp = _parse_log_timestamp(log_path)
    return bool(timestamp is not None and timestamp < LEGACY_TC_FIX_TIMESTAMP)


def _legacy_room_anchor_mask(
    data: pd.DataFrame,
    *,
    tc_columns: Sequence[str],
    flow_reference_column: str,
) -> pd.Series:
    if flow_reference_column not in data.columns or not tc_columns:
        return pd.Series(False, index=data.index)

    flow_temp_c = _numeric_column(data, flow_reference_column)
    tc_frame = data.loc[:, list(tc_columns)].apply(pd.to_numeric, errors="coerce")
    tc_span_c = tc_frame.max(axis=1) - tc_frame.min(axis=1)
    finite = flow_temp_c.notna() & tc_span_c.notna()
    if int(finite.sum()) == 0:
        return pd.Series(False, index=data.index)

    high_quantile_c = float(np.nanpercentile(flow_temp_c[finite], 97))
    room_mask = finite & flow_temp_c.ge(high_quantile_c - 0.5) & tc_span_c.le(2.0)
    if int(room_mask.sum()) >= 20:
        return room_mask

    warm_quantile_c = float(np.nanpercentile(flow_temp_c[finite], 95))
    room_mask = finite & flow_temp_c.ge(warm_quantile_c - 1.0) & tc_span_c.le(3.0)
    return room_mask


def _warm_tc_anchor_mask(data: pd.DataFrame, *, tc_columns: Sequence[str]) -> pd.Series:
    if not tc_columns:
        return pd.Series(False, index=data.index)

    tc_frame = data.loc[:, list(tc_columns)].apply(pd.to_numeric, errors="coerce")
    tc_mean_c = tc_frame.mean(axis=1)
    tc_span_c = tc_frame.max(axis=1) - tc_frame.min(axis=1)
    finite = tc_mean_c.notna() & tc_span_c.notna()
    if int(finite.sum()) == 0:
        return pd.Series(False, index=data.index)

    high_quantile_c = float(np.nanpercentile(tc_mean_c[finite], 97))
    room_mask = finite & tc_mean_c.ge(high_quantile_c - 0.5) & tc_span_c.le(2.0)
    if int(room_mask.sum()) >= 20:
        return room_mask

    warm_quantile_c = float(np.nanpercentile(tc_mean_c[finite], 95))
    return finite & tc_mean_c.ge(warm_quantile_c - 1.0) & tc_span_c.le(3.0)


def apply_legacy_tc_correction(
    frame: pd.DataFrame,
    *,
    log_path: str | Path | None = None,
    flow_reference_column: str = "temperature_c_si",
    room_reference_c: float | None = None,
) -> tuple[pd.DataFrame, str]:
    """Back-convert pre-fix flow logs whose T-type probes were decoded as K-type."""

    data = canonicalize_tc_columns(frame)
    legacy_tc_columns = tuple(
        column
        for column in LEGACY_WRONG_TYPE_TC_COLUMNS
        if column in data.columns and _numeric_column(data, column).notna().mean() > 0.05
    )
    if not legacy_tc_columns:
        return data, ""

    for column in legacy_tc_columns:
        raw_values_c = _numeric_column(data, column)
        data[column] = _legacy_wrong_k_to_true_t_c(raw_values_c.to_numpy())

    note_parts = [
        (
            "Legacy wrong-type TC reconstruction applied to "
            f"{', '.join(column.removesuffix('_C') for column in legacy_tc_columns)} "
            f"using an effective cold-junction of {LEGACY_EFFECTIVE_COLD_JUNCTION_C:.2f} °C."
        )
    ]

    room_anchor_offsets_c: dict[str, float] = {}
    room_anchor_mask = pd.Series(False, index=data.index)

    if flow_reference_column in data.columns:
        room_anchor_mask = _legacy_room_anchor_mask(
            data,
            tc_columns=tuple(column for column in LEGACY_FLOW_WRONG_TYPE_TC_COLUMNS if column in data.columns),
            flow_reference_column=flow_reference_column,
        )
        if int(room_anchor_mask.sum()) > 0:
            flow_reference_c = _numeric_column(data, flow_reference_column)
            for column in legacy_tc_columns:
                delta_c = flow_reference_c[room_anchor_mask] - _numeric_column(data, column)[room_anchor_mask]
                if not delta_c.notna().any():
                    continue
                offset_c = float(np.nanmedian(delta_c))
                if np.isfinite(offset_c):
                    data[column] = _numeric_column(data, column) + offset_c
                    room_anchor_offsets_c[column] = offset_c
            note_parts.append(
                "Per-channel room-anchor offsets were fitted from the warmest stable flow-meter segment "
                f"(n={int(room_anchor_mask.sum())})."
            )
    elif room_reference_c is not None:
        room_anchor_mask = _warm_tc_anchor_mask(data, tc_columns=legacy_tc_columns)
        if int(room_anchor_mask.sum()) > 0:
            for column in legacy_tc_columns:
                room_values_c = _numeric_column(data, column)[room_anchor_mask]
                if not room_values_c.notna().any():
                    continue
                offset_c = float(room_reference_c - np.nanmedian(room_values_c))
                if np.isfinite(offset_c):
                    data[column] = _numeric_column(data, column) + offset_c
                    room_anchor_offsets_c[column] = offset_c
            note_parts.append(
                "Per-channel room-anchor offsets were fitted from the warmest stable TC segment "
                f"using {room_reference_c:.3f} °C as the room reference (n={int(room_anchor_mask.sum())})."
            )

    applied_room_only = []
    for column, (gain, offset_c) in LEGACY_ROOM_ONLY_TC_CALIBRATION.items():
        if column not in data.columns:
            continue
        data[column] = gain * _numeric_column(data, column) + offset_c
        applied_room_only.append(column.removesuffix("_C"))
    if applied_room_only:
        note_parts.append(f"April 20 room-only offsets were also applied to {', '.join(applied_room_only)}.")

    data.attrs["legacy_tc_correction_applied"] = True
    data.attrs["legacy_tc_correction_log_path"] = str(log_path) if log_path is not None else ""
    data.attrs["legacy_tc_effective_cold_junction_c"] = LEGACY_EFFECTIVE_COLD_JUNCTION_C
    data.attrs["legacy_tc_room_anchor_samples"] = int(room_anchor_mask.sum())
    data.attrs["legacy_tc_room_anchor_offsets_c"] = room_anchor_offsets_c
    return data, " ".join(note_parts)


def canonicalize_tc_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename logger thermocouple columns to the canonical TC tags used in notebooks."""

    data = frame.copy()
    rename_map: dict[str, str] = {}
    for raw_column, canonical_column in THERMOCOUPLE_COLUMN_MAP.items():
        if raw_column in data.columns and canonical_column not in data.columns:
            rename_map[raw_column] = canonical_column
    if rename_map:
        data = data.rename(columns=rename_map)
    return data


def connected_tc_columns(frame: pd.DataFrame, *, min_valid_fraction: float = 0.05) -> tuple[str, ...]:
    """Return the connected TC columns that are present and meaningfully populated."""

    columns: list[str] = []
    for tag in CONNECTED_THERMOCOUPLE_TAGS:
        column = f"{tag}_C"
        if column in frame.columns and pd.to_numeric(frame[column], errors="coerce").notna().mean() > min_valid_fraction:
            columns.append(column)
    return tuple(columns)


def tc_display_name(column: str) -> str:
    """Return a short notebook-friendly thermocouple label for a dataframe column."""

    return THERMOCOUPLE_LABELS.get(column, column)


def _fit_linear_relation(x: pd.Series | np.ndarray, y: pd.Series | np.ndarray) -> RegressionSummary:
    x_values = np.asarray(x, dtype=float)
    y_values = np.asarray(y, dtype=float)
    mask = np.isfinite(x_values) & np.isfinite(y_values)
    x_values = x_values[mask]
    y_values = y_values[mask]
    if x_values.size < 2 or np.allclose(x_values, x_values[0]):
        return RegressionSummary(
            slope=float("nan"),
            intercept=float("nan"),
            rvalue=float("nan"),
            pvalue=float("nan"),
            stderr=float("nan"),
            intercept_stderr=float("nan"),
            n_samples=int(x_values.size),
        )

    fit = linregress(x_values, y_values)
    intercept_stderr = getattr(fit, "intercept_stderr", float("nan"))
    return RegressionSummary(
        slope=float(fit.slope),
        intercept=float(fit.intercept),
        rvalue=float(fit.rvalue),
        pvalue=float(fit.pvalue),
        stderr=float(fit.stderr),
        intercept_stderr=float(intercept_stderr),
        n_samples=int(x_values.size),
    )


def _fit_standardized_log_viscosity_model(
    data: pd.DataFrame,
    feature_columns: Sequence[str],
    target_log_nu: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    feature_matrix = data[list(feature_columns)].to_numpy(dtype=float)
    feature_mean = feature_matrix.mean(axis=0)
    feature_std = feature_matrix.std(axis=0)
    feature_std = np.where(feature_std > 0.0, feature_std, 1.0)
    feature_z = (feature_matrix - feature_mean) / feature_std
    fit = np.linalg.lstsq(
        np.column_stack([np.ones(len(data)), feature_z]),
        target_log_nu,
        rcond=None,
    )[0]
    prediction = np.exp(np.column_stack([np.ones(len(data)), feature_z]) @ fit)
    return fit, feature_mean, feature_std, prediction


def _rolling_window_from_elapsed(
    elapsed_s: pd.Series,
    *,
    duration_s: float,
    minimum: int = 11,
) -> tuple[int, float]:
    elapsed_values = np.asarray(elapsed_s, dtype=float)
    finite = np.isfinite(elapsed_values)
    elapsed_values = np.sort(elapsed_values[finite])
    deltas = np.diff(elapsed_values)
    deltas = deltas[deltas > 0.0]
    median_step_s = float(np.median(deltas)) if deltas.size else max(duration_s, 1.0)

    window = int(round(duration_s / median_step_s))
    window = max(window, minimum)
    if window % 2 == 0:
        window += 1

    max_window = len(finite) if len(finite) % 2 == 1 else len(finite) - 1
    max_window = max(max_window, 3)
    window = min(window, max_window)
    actual_duration_s = window * median_step_s
    return window, actual_duration_s


def detect_legacy_flow_export(frame: pd.DataFrame) -> bool:
    """Return whether the flow-meter columns look like the legacy mislabeled export."""

    required = {"fluid_temperature_raw", "fluid_density_kg_m3"}
    if not required.issubset(frame.columns):
        return False

    temp_raw = pd.to_numeric(frame["fluid_temperature_raw"], errors="coerce").dropna()
    density_raw = pd.to_numeric(frame["fluid_density_kg_m3"], errors="coerce").dropna()
    if temp_raw.empty or density_raw.empty:
        return False

    return bool(temp_raw.median() > 40.0 and density_raw.quantile(0.95) < 20.0)


def add_canonical_flow_columns(
    frame: pd.DataFrame,
    *,
    density_bounds: tuple[float, float] = DEFAULT_HFE_LIQUID_DENSITY_BOUNDS,
    log_path: str | Path | None = None,
) -> tuple[pd.DataFrame, str]:
    """Normalize one logger CSV into the canonical SI columns used in review notebooks."""

    data = canonicalize_tc_columns(frame)
    legacy = detect_legacy_flow_export(data)

    if legacy:
        note = (
            "This CSV looks like a legacy export: the flow-meter columns behave like ft/s, "
            "US gal/min, lb/min, degF and lb/gal even though the headers look SI. "
            "The notebook converts them to canonical SI before plotting."
        )
        data["flow_velocity_mps_si"] = _numeric_column(data, "fluid_flow_velocity_mps") * FT_TO_M
        data["volume_flow_m3s_si"] = _numeric_column(data, "fluid_volume_flow_m3s") * US_GAL_TO_M3 / 60.0
        data["volume_flow_lmin_si"] = _numeric_column(data, "fluid_volume_flow_m3s") * US_GAL_TO_M3 * 1000.0
        data["mass_flow_kgs_si"] = _numeric_column(data, "fluid_mass_flow_kgs") * LB_TO_KG / 60.0
        data["mass_flow_kgmin_si"] = _numeric_column(data, "fluid_mass_flow_kgs") * LB_TO_KG
        data["temperature_c_si"] = (_numeric_column(data, "fluid_temperature_raw") - 32.0) * 5.0 / 9.0
        data["density_kg_m3_si"] = _numeric_column(data, "fluid_density_kg_m3") * LB_PER_GAL_TO_KG_PER_M3
    else:
        note = "This CSV already looks SI-like, so the notebook uses the logged flow-meter columns directly."
        data["flow_velocity_mps_si"] = _numeric_column(data, "fluid_flow_velocity_mps")
        data["volume_flow_m3s_si"] = _numeric_column(data, "fluid_volume_flow_m3s")
        data["volume_flow_lmin_si"] = data["volume_flow_m3s_si"] * 60.0 * 1000.0
        data["mass_flow_kgs_si"] = _numeric_column(data, "fluid_mass_flow_kgs")
        data["mass_flow_kgmin_si"] = data["mass_flow_kgs_si"] * 60.0
        if "fluid_temperature_c" in data.columns:
            data["temperature_c_si"] = _numeric_column(data, "fluid_temperature_c")
        elif "fluid_temperature_raw" in data.columns:
            data["temperature_c_si"] = _numeric_column(data, "fluid_temperature_raw") - 273.15
        else:
            data["temperature_c_si"] = pd.Series(np.nan, index=data.index)
        data["density_kg_m3_si"] = _numeric_column(data, "fluid_density_kg_m3")

    for column in (
        "pump_cmd_pct",
        "pump_freq_hz",
        "pump_input_power_w",
        "pump_output_current_a",
        "pump_output_voltage_v",
        "pump_pressure_before_bar_abs",
        "pump_pressure_after_bar_abs",
        "pump_pressure_tank_bar_abs",
        "valve",
    ):
        data[column] = _numeric_column(data, column)

    if "mode" not in data.columns:
        data["mode"] = ""
    else:
        data["mode"] = data["mode"].fillna("").astype(str)

    data["pump_running"] = data["pump_freq_hz"].fillna(0.0) > 0.5
    lo, hi = density_bounds
    data["liquid_like_density"] = data["density_kg_m3_si"].between(lo, hi)
    data["positive_mass_flow"] = data["mass_flow_kgmin_si"] > 0.0
    data["usable_sample"] = data["pump_running"] & data["liquid_like_density"]
    data["delta_p_bar_recomputed"] = data["pump_pressure_after_bar_abs"] - data["pump_pressure_before_bar_abs"]
    data["t_min"] = _numeric_column(data, "time_s") / 60.0
    data["cmd_bucket_pct"] = data["pump_cmd_pct"].round(0)

    if is_legacy_wrong_type_log(log_path):
        data, tc_note = apply_legacy_tc_correction(data, log_path=log_path)
        if tc_note:
            note = f"{note} {tc_note}"

    return data, note


def _contiguous_true_segments(mask: pd.Series) -> list[tuple[int, int]]:
    values = mask.fillna(False).to_numpy()
    segments: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(values):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            segments.append((start, index - 1))
            start = None
    if start is not None:
        segments.append((start, len(values) - 1))
    return segments


def _classify_segment(row: pd.Series) -> str:
    if row["liquid_fraction"] < 0.50:
        return "gas-rich / empty"
    if row["positive_mass_flow_fraction"] < 0.75:
        return "priming / flow-direction flips"
    if row["liquid_fraction"] < 0.90:
        return "draining tail / mixed phase"
    if row["duration_s"] < 20.0:
        return "short usable window"
    return "usable liquid circulation"


def segment_slice(data: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    """Return one contiguous segment from a dataframe and a segment-summary row."""

    return data[data["time_s"].between(row["start_s"], row["end_s"])].copy()


def build_segment_summary(data: pd.DataFrame) -> pd.DataFrame:
    """Summarize all contiguous pump-running regions in a log."""

    rows: list[dict[str, float | int | str | bool]] = []
    for segment_id, (start, end) in enumerate(_contiguous_true_segments(data["pump_running"]), start=1):
        segment = data.iloc[start : end + 1].copy()
        cmd_levels = sorted({int(value) for value in segment["cmd_bucket_pct"].dropna() if abs(value) > 1e-9})
        liquid = segment[segment["liquid_like_density"] & (segment["pump_cmd_pct"] > 0.0)].copy()
        reference = liquid if not liquid.empty else segment
        row = {
            "segment_id": segment_id,
            "start_s": float(segment["time_s"].iloc[0]),
            "end_s": float(segment["time_s"].iloc[-1]),
            "duration_s": float(segment["time_s"].iloc[-1] - segment["time_s"].iloc[0]),
            "cmd_level_count": int(len(cmd_levels)),
            "pump_cmd_levels_pct": ", ".join(str(value) for value in cmd_levels) if cmd_levels else "0",
            "median_freq_hz": float(segment["pump_freq_hz"].median()),
            "max_freq_hz": float(segment["pump_freq_hz"].max()),
            "median_mass_flow_kgmin": float(segment["mass_flow_kgmin_si"].median()),
            "median_volume_flow_lmin": float(segment["volume_flow_lmin_si"].median()),
            "median_density_kg_m3": float(segment["density_kg_m3_si"].median()),
            "median_flow_temp_C": float(segment["temperature_c_si"].median()),
            "liquid_fraction": float(segment["liquid_like_density"].mean()),
            "positive_mass_flow_fraction": float(segment["positive_mass_flow"].mean()),
            "flow_temp_change_C": float(reference["temperature_c_si"].iloc[-1] - reference["temperature_c_si"].iloc[0]),
            "density_change_kg_m3": float(reference["density_kg_m3_si"].iloc[-1] - reference["density_kg_m3_si"].iloc[0]),
        }
        row["classification"] = _classify_segment(pd.Series(row))
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["start_s", "end_s", "duration_s"])

    summary = pd.DataFrame(rows).set_index("segment_id")
    summary["use_for_quantitative_flow"] = summary["classification"].eq("usable liquid circulation")
    return summary


def command_step_summary(
    segment: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    """Break one liquid-like segment into pump-command steps."""

    work = segment[segment["liquid_like_density"] & (segment["pump_cmd_pct"] > 0.0)].copy()
    if work.empty:
        raise ValueError("The provided segment does not contain liquid-like positive-flow samples.")

    work["cmd_bucket_pct"] = work["pump_cmd_pct"].round(0)
    work["dt_s"] = work["time_s"].diff().fillna(0.0).clip(lower=0.0)
    first_cmd = work["cmd_bucket_pct"].iloc[0]
    command_change = work["cmd_bucket_pct"].ne(work["cmd_bucket_pct"].shift(fill_value=first_cmd))
    work["step_id"] = command_change.cumsum()
    work["time_from_step_s"] = work.groupby("step_id")["time_s"].transform(lambda series: series - series.iloc[0])
    settle_cutoff_s = max(float(work["dt_s"].median()), 2.0)
    settled = work[work["time_from_step_s"] >= settle_cutoff_s].copy()
    if settled.empty:
        settled = work.copy()

    step_windows = (
        work.groupby("step_id")
        .agg(
            cmd_pct=("cmd_bucket_pct", "first"),
            start_s=("time_s", "min"),
            end_s=("time_s", "max"),
            duration_s=("time_s", lambda series: series.iloc[-1] - series.iloc[0]),
        )
        .sort_index()
    )
    step_summary = (
        settled.groupby("cmd_bucket_pct")
        .agg(
            median_freq_hz=("pump_freq_hz", "median"),
            median_mass_flow_kgmin=("mass_flow_kgmin_si", "median"),
            median_volume_flow_lmin=("volume_flow_lmin_si", "median"),
            median_density_kg_m3=("density_kg_m3_si", "median"),
            median_delta_p_bar=("delta_p_bar_recomputed", "median"),
            median_power_w=("pump_input_power_w", "median"),
            dwell_time_s=("dt_s", "sum"),
            sample_count=("pump_freq_hz", "size"),
        )
        .sort_index()
    )
    step_summary["flow_per_w_kgmin_per_w"] = step_summary["median_mass_flow_kgmin"] / step_summary["median_power_w"]
    return work, step_windows, step_summary, settle_cutoff_s


def _build_control_phase_summary(hold_candidates: pd.DataFrame, stable_hold: pd.DataFrame) -> pd.DataFrame:
    shutdown_tail = hold_candidates[hold_candidates["time_s"] > stable_hold["time_s"].iloc[-1]].copy()
    rows: list[dict[str, float | str | int]] = []
    phase_specs = [
        ("20% entry / valve closed", hold_candidates[hold_candidates["time_s"] < stable_hold["time_s"].iloc[0]].copy()),
        ("20% stable hold (mode A, valve=1)", stable_hold.copy()),
        ("20% shutdown tail", shutdown_tail),
    ]
    for label, subset in phase_specs:
        if subset.empty:
            continue
        rows.append(
            {
                "phase": label,
                "start_s": float(subset["time_s"].iloc[0]),
                "end_s": float(subset["time_s"].iloc[-1]),
                "duration_s": float(subset["time_s"].iloc[-1] - subset["time_s"].iloc[0]),
                "samples": int(len(subset)),
                "mode_set": ", ".join(sorted({str(value) for value in subset["mode"].dropna().unique()})),
                "valve_set": ", ".join(str(int(value)) for value in sorted({float(value) for value in subset["valve"].dropna().unique()})),
                "median_freq_hz": float(subset["pump_freq_hz"].median()),
                "median_flow_temp_C": float(subset["temperature_c_si"].median()),
                "median_density_kg_m3": float(subset["density_kg_m3_si"].median()),
                "median_mass_flow_kgmin": float(subset["mass_flow_kgmin_si"].median()),
                "median_volume_flow_lmin": float(subset["volume_flow_lmin_si"].median()),
                "median_delta_p_bar": float(subset["delta_p_bar_recomputed"].median()),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("phase")


def _build_cooldown_frame(stable_hold: pd.DataFrame, valid_temp_cols: Sequence[str]) -> pd.DataFrame:
    cooldown = stable_hold.copy().sort_values("time_s")
    cooldown["elapsed_s"] = cooldown["time_s"] - cooldown["time_s"].iloc[0]
    cooldown["dt_s"] = cooldown["time_s"].diff().fillna(0.0).clip(lower=0.0)
    cooldown["mass_increment_kg"] = cooldown["mass_flow_kgmin_si"] * cooldown["dt_s"] / 60.0
    cooldown["volume_increment_l"] = cooldown["volume_flow_lmin_si"] * cooldown["dt_s"] / 60.0
    cooldown["cum_mass_kg"] = cooldown["mass_increment_kg"].cumsum()
    cooldown["cum_volume_l"] = cooldown["volume_increment_l"].cumsum()
    if valid_temp_cols:
        cooldown["temp_mean_C"] = cooldown[list(valid_temp_cols)].mean(axis=1)
        cooldown["temp_span_C"] = cooldown[list(valid_temp_cols)].max(axis=1) - cooldown[list(valid_temp_cols)].min(axis=1)
    else:
        cooldown["temp_mean_C"] = np.nan
        cooldown["temp_span_C"] = np.nan

    quantile_count = min(4, len(cooldown))
    if quantile_count <= 1:
        cooldown["phase"] = "Q1"
    else:
        labels = [f"Q{i + 1}" for i in range(quantile_count)]
        cooldown["phase"] = pd.qcut(np.arange(len(cooldown)), q=quantile_count, labels=labels)
    return cooldown


def _summarize_cooldown_phases(cooldown: pd.DataFrame) -> pd.DataFrame:
    return (
        cooldown.groupby("phase", observed=False)
        .agg(
            median_flow_temp_C=("temperature_c_si", "median"),
            median_density_kg_m3=("density_kg_m3_si", "median"),
            median_mass_flow_kgmin=("mass_flow_kgmin_si", "median"),
            median_volume_flow_lmin=("volume_flow_lmin_si", "median"),
            median_pump_input_power_w=("pump_input_power_w", "median"),
            median_pump_output_current_a=("pump_output_current_a", "median"),
            median_delta_p_bar=("delta_p_bar_recomputed", "median"),
            median_tc_mean_C=("temp_mean_C", "median"),
            median_tc_span_C=("temp_span_C", "median"),
        )
    )


def prepare_flow_log_review(
    path: str | Path,
    *,
    density_bounds: tuple[float, float] = DEFAULT_HFE_LIQUID_DENSITY_BOUNDS,
) -> FlowLogReview:
    """Load one flow log and extract the stable automatic cooldown hold."""

    log_path = Path(path)
    data = pd.read_csv(log_path, comment="#")
    data, flow_note = add_canonical_flow_columns(data, density_bounds=density_bounds, log_path=log_path)
    segment_summary = build_segment_summary(data)

    valid_temp_cols = connected_tc_columns(data)

    quantitative_segments = segment_summary[segment_summary["use_for_quantitative_flow"]].copy()
    if quantitative_segments.empty:
        raise RuntimeError("No liquid-like pump-running segments were found in this log.")

    run_segment_id = int(quantitative_segments["duration_s"].idxmax())
    run = segment_slice(data, segment_summary.loc[run_segment_id])
    run_liquid, sweep_windows, sweep_step_summary, _ = command_step_summary(run)

    dominant_cmd_pct = float(sweep_step_summary["dwell_time_s"].idxmax())
    hold_candidates = run_liquid[run_liquid["cmd_bucket_pct"].eq(dominant_cmd_pct)].copy().sort_values("time_s")
    stable_mask = hold_candidates["mode"].eq("A") & hold_candidates["valve"].fillna(0.0).ge(0.5)
    stable_hold = hold_candidates[stable_mask].copy()
    if stable_hold.empty:
        stable_hold = hold_candidates.copy()

    cooldown = _build_cooldown_frame(stable_hold, valid_temp_cols)
    control_phase_summary = _build_control_phase_summary(hold_candidates, stable_hold)
    cooldown_phase_summary = _summarize_cooldown_phases(cooldown)

    return FlowLogReview(
        log_path=log_path,
        data=data,
        flow_note=flow_note,
        valid_temp_cols=valid_temp_cols,
        segment_summary=segment_summary,
        run_segment_id=run_segment_id,
        dominant_cmd_pct=dominant_cmd_pct,
        sweep_windows=sweep_windows,
        sweep_step_summary=sweep_step_summary,
        control_phase_summary=control_phase_summary,
        cooldown=cooldown,
        cooldown_phase_summary=cooldown_phase_summary,
    )


def flow_log_overview_table(review: FlowLogReview) -> pd.Series:
    """Return a short, display-friendly overview for a prepared log review."""

    return pd.Series(
        {
            "flow_note": review.flow_note,
            "rows": int(len(review.data)),
            "time_start_s": float(review.data["time_s"].min()),
            "time_end_s": float(review.data["time_s"].max()),
            "run_segment_id": review.run_segment_id,
            "dominant_command_pct": review.dominant_cmd_pct,
            "stable_hold_start_s": review.stable_start_s,
            "stable_hold_end_s": review.stable_end_s,
            "stable_hold_duration_s": float(review.cooldown["elapsed_s"].iloc[-1]),
            "valid_thermocouples": ", ".join(tc_display_name(column) for column in review.valid_temp_cols),
        },
        name="value",
    )


def cooldown_overview_table(review: FlowLogReview) -> pd.Series:
    """Return the key cooldown-by-time summary values for the stable hold."""

    cooldown = review.cooldown
    return pd.Series(
        {
            "stable_hold_duration_s": float(cooldown["dt_s"].sum()),
            "cumulative_transferred_mass_kg": float(cooldown["cum_mass_kg"].iloc[-1]),
            "cumulative_transferred_volume_l": float(cooldown["cum_volume_l"].iloc[-1]),
            "temperature_drop_C": float(cooldown["temperature_c_si"].iloc[-1] - cooldown["temperature_c_si"].iloc[0]),
            "density_rise_kg_m3": float(cooldown["density_kg_m3_si"].iloc[-1] - cooldown["density_kg_m3_si"].iloc[0]),
            "mass_flow_change_kgmin": float(cooldown["mass_flow_kgmin_si"].iloc[-1] - cooldown["mass_flow_kgmin_si"].iloc[0]),
            "pump_power_change_W": float(cooldown["pump_input_power_w"].iloc[-1] - cooldown["pump_input_power_w"].iloc[0]),
        },
        name="value",
    )


def summarize_signals_vs_temperature(
    cooldown: pd.DataFrame,
    *,
    signals: Sequence[str] = DEFAULT_INTERESTING_TEMPERATURE_SIGNALS,
) -> SignalTemperatureStudy:
    """Regress selected cooldown signals against flow-meter temperature."""

    rows: list[dict[str, float | str | int]] = []
    available_signals: list[str] = []
    for signal in signals:
        if signal not in cooldown.columns:
            continue
        fit = _fit_linear_relation(cooldown["temperature_c_si"], cooldown[signal])
        rows.append(
            {
                "signal": signal,
                "slope_per_C": fit.slope,
                "r": fit.rvalue,
                "r2": fit.r_squared,
                "n_samples": fit.n_samples,
                "segment_min": float(cooldown[signal].min()),
                "segment_median": float(cooldown[signal].median()),
                "segment_max": float(cooldown[signal].max()),
                "end_minus_start": float(cooldown[signal].iloc[-1] - cooldown[signal].iloc[0]),
            }
        )
        available_signals.append(signal)

    summary = pd.DataFrame(rows).sort_values("r2", ascending=False).set_index("signal")
    return SignalTemperatureStudy(signals=tuple(available_signals), summary=summary)


def build_density_study(cooldown: pd.DataFrame) -> DensityStudy:
    """Build the density and viscosity study used in the flow-log review notebook."""

    data = cooldown.copy().sort_values("time_s")
    fits = {
        "density_vs_temperature": _fit_linear_relation(data["temperature_c_si"], data["density_kg_m3_si"]),
        "mass_flow_vs_temperature": _fit_linear_relation(data["temperature_c_si"], data["mass_flow_kgmin_si"]),
        "temperature_vs_time": _fit_linear_relation(data["elapsed_s"], data["temperature_c_si"]),
        "density_vs_time": _fit_linear_relation(data["elapsed_s"], data["density_kg_m3_si"]),
        "temp_span_vs_time": _fit_linear_relation(data["elapsed_s"], data["temp_span_C"]),
    }
    regression_summary = pd.DataFrame(
        {
            "slope": {name: fit.slope for name, fit in fits.items()},
            "intercept": {name: fit.intercept for name, fit in fits.items()},
            "r2": {name: fit.r_squared for name, fit in fits.items()},
            "n_samples": {name: fit.n_samples for name, fit in fits.items()},
        }
    )

    reference_local = REFERENCE_3M_N7200[REFERENCE_3M_N7200["temperature_c"] >= -20.0].copy()
    local_smooth_fit = np.polyfit(
        reference_local["temperature_c"],
        np.log(reference_local["kinematic_viscosity_cSt"]),
        deg=2,
    )

    data["nu_ref_local_cSt"] = np.exp(np.polyval(local_smooth_fit, data["temperature_c_si"]))
    data["mu_ref_local_cP"] = data["nu_ref_local_cSt"] * data["density_kg_m3_si"] / 1000.0

    density_power_fit = np.linalg.lstsq(
        np.column_stack([np.ones(len(data)), np.log(data["density_kg_m3_si"])]),
        np.log(data["nu_ref_local_cSt"]),
        rcond=None,
    )[0]
    data["nu_density_power_cSt"] = np.exp(
        density_power_fit[0] + density_power_fit[1] * np.log(data["density_kg_m3_si"])
    )
    data["mu_density_power_cP"] = data["nu_density_power_cSt"] * data["density_kg_m3_si"] / 1000.0

    nu_ref_25c_3m_cst = float(
        REFERENCE_3M_N7200.loc[
            REFERENCE_3M_N7200["temperature_c"].eq(25.0),
            "kinematic_viscosity_cSt",
        ].iloc[0]
    )
    fit_volume_temp = _fit_linear_relation(data["temperature_c_si"], data["volume_flow_lmin_si"])
    rho_25_fit_kg_m3 = fits["density_vs_temperature"].intercept + fits["density_vs_temperature"].slope * 25.0
    q_25_fit_lmin = fit_volume_temp.intercept + fit_volume_temp.slope * 25.0
    data["nu_flow_only_proxy_cSt"] = nu_ref_25c_3m_cst * q_25_fit_lmin / data["volume_flow_lmin_si"]
    data["mu_flow_only_proxy_cP"] = data["nu_flow_only_proxy_cSt"] * data["density_kg_m3_si"] / 1000.0

    data["hydraulic_power_w"] = data["delta_p_bar_recomputed"] * data["volume_flow_lmin_si"] * BAR_LMIN_TO_W
    data["pump_input_power_per_lmin"] = data["pump_input_power_w"] / data["volume_flow_lmin_si"]
    data["pump_output_current_per_lmin"] = data["pump_output_current_a"] / data["volume_flow_lmin_si"]
    target_log_nu = np.log(data["nu_ref_local_cSt"].to_numpy())

    single_parameter_specs = [
        ("volume_flow_lmin_si", "nu_from_volume_flow_model_cSt"),
        ("delta_p_bar_recomputed", "nu_from_delta_p_model_cSt"),
        ("pump_input_power_w", "nu_from_input_power_w_model_cSt"),
        ("pump_input_power_per_lmin", "nu_from_input_power_per_lmin_model_cSt"),
        ("pump_output_current_per_lmin", "nu_from_output_current_per_lmin_model_cSt"),
    ]
    single_parameter_rows: list[dict[str, float | str]] = []
    for feature_name, column_name in single_parameter_specs:
        single_fit, feature_mean, feature_std, prediction = _fit_standardized_log_viscosity_model(
            data,
            [feature_name],
            target_log_nu,
        )
        data[column_name] = prediction
        single_parameter_rows.append(
            {
                "feature": feature_name,
                "mean": float(feature_mean[0]),
                "std": float(feature_std[0]),
                "intercept_in_ln_nu": float(single_fit[0]),
                "coef_in_ln_nu": float(single_fit[1]),
            }
        )
    single_parameter_law_summary = pd.DataFrame(single_parameter_rows).set_index("feature")

    combined_law_features = [
        "volume_flow_lmin_si",
        "delta_p_bar_recomputed",
        "pump_input_power_per_lmin",
        "pump_output_current_per_lmin",
    ]
    combined_law_fit, combined_feature_mean, combined_feature_std, combined_prediction = (
        _fit_standardized_log_viscosity_model(
            data,
            combined_law_features,
            target_log_nu,
        )
    )
    data["nu_combined_law_cSt"] = combined_prediction
    data["mu_combined_law_cP"] = data["nu_combined_law_cSt"] * data["density_kg_m3_si"] / 1000.0

    combined_trend_window = min(11, len(data) if len(data) % 2 == 1 else len(data) - 1)
    combined_trend_window = max(combined_trend_window, 3)
    ordered_index = data["temperature_c_si"].sort_values().index
    combined_trend_series = (
        data.loc[ordered_index, "nu_combined_law_cSt"]
        .rolling(window=combined_trend_window, center=True, min_periods=3)
        .mean()
        .interpolate(limit_direction="both")
    )
    data["nu_combined_law_trend_cSt"] = combined_trend_series.reindex(data.index)
    data["mu_combined_law_trend_cP"] = data["nu_combined_law_trend_cSt"] * data["density_kg_m3_si"] / 1000.0

    gear_power_window, gear_power_window_s = _rolling_window_from_elapsed(
        data["elapsed_s"],
        duration_s=DEFAULT_GEAR_PUMP_POWER_TREND_S,
        minimum=11,
    )
    data["pump_input_power_trend_w"] = (
        data["pump_input_power_w"]
        .rolling(window=gear_power_window, center=True, min_periods=max(3, gear_power_window // 4))
        .mean()
        .interpolate(limit_direction="both")
    )
    warm_row = data.loc[data["temperature_c_si"].idxmax()]
    warm_mu_ref_cP = float(warm_row["mu_ref_local_cP"])
    warm_power_trend_w = float(warm_row["pump_input_power_trend_w"])
    mu_excess_cP = data["mu_ref_local_cP"] - warm_mu_ref_cP
    power_excess_w = data["pump_input_power_trend_w"] - warm_power_trend_w
    gear_power_gain_w_per_cP = float(
        np.dot(mu_excess_cP.to_numpy(), power_excess_w.to_numpy())
        / np.dot(mu_excess_cP.to_numpy(), mu_excess_cP.to_numpy())
    )
    data["mu_gear_power_law_cP"] = np.maximum(
        warm_mu_ref_cP + power_excess_w / gear_power_gain_w_per_cP,
        0.0,
    )
    data["nu_gear_power_law_cSt"] = data["mu_gear_power_law_cP"] * 1000.0 / data["density_kg_m3_si"]

    reference_summary = pd.DataFrame(
        {
            "value": [
                local_smooth_fit[0],
                local_smooth_fit[1],
                local_smooth_fit[2],
                density_power_fit[0],
                density_power_fit[1],
                combined_law_fit[0],
                warm_mu_ref_cP,
                warm_power_trend_w,
                gear_power_gain_w_per_cP,
                float(gear_power_window),
                gear_power_window_s,
                float(data["hydraulic_power_w"].mean()),
                float(data["hydraulic_power_w"].mean() / data["pump_input_power_w"].mean() * 100.0),
            ]
        },
        index=[
            "local_smooth_ln_nu_coef_T2",
            "local_smooth_ln_nu_coef_T1",
            "local_smooth_ln_nu_coef_T0",
            "density_power_ln_nu_coef_const",
            "density_power_ln_nu_coef_ln_rho",
            "combined_law_ln_nu_intercept",
            "gear_power_law_warm_dynamic_viscosity_cP",
            "gear_power_law_warm_smoothed_power_w",
            "gear_power_law_gain_w_per_cP",
            "gear_power_law_trend_window_samples",
            "gear_power_law_trend_window_s",
            "mean_hydraulic_power_w",
            "mean_hydraulic_power_pct_of_input",
        ],
    )
    combined_law_summary = pd.DataFrame(
        {
            "mean": combined_feature_mean,
            "std": combined_feature_std,
            "coef_in_ln_nu": combined_law_fit[1:],
        },
        index=combined_law_features,
    )
    law_phase_summary = (
        data.groupby("phase", observed=False)
        .agg(
            median_temp_C=("temperature_c_si", "median"),
            median_nu_ref_local_cSt=("nu_ref_local_cSt", "median"),
            median_nu_density_power_cSt=("nu_density_power_cSt", "median"),
            median_nu_gear_power_law_cSt=("nu_gear_power_law_cSt", "median"),
            median_nu_combined_law_cSt=("nu_combined_law_cSt", "median"),
            median_nu_flow_only_proxy_cSt=("nu_flow_only_proxy_cSt", "median"),
            median_mu_gear_power_law_cP=("mu_gear_power_law_cP", "median"),
            median_mu_density_power_cP=("mu_density_power_cP", "median"),
            median_mu_combined_law_cP=("mu_combined_law_cP", "median"),
        )
    )

    comparison_rows: list[dict[str, float | str]] = []
    for label, column in [
        ("Density-power fit", "nu_density_power_cSt"),
        ("Gear-pump power law", "nu_gear_power_law_cSt"),
        ("Combined pump+flow law", "nu_combined_law_cSt"),
        ("Flow-only proxy", "nu_flow_only_proxy_cSt"),
    ]:
        residual = data[column] - data["nu_ref_local_cSt"]
        fit = _fit_linear_relation(data["nu_ref_local_cSt"], data[column])
        comparison_rows.append(
            {
                "method": label,
                "rmse_vs_local_smooth_cSt": float(np.sqrt(np.mean(residual**2))),
                "median_abs_pct_error_vs_local_smooth": float(
                    np.median(np.abs(residual / data["nu_ref_local_cSt"])) * 100.0
                ),
                "fit_slope_vs_local_smooth": fit.slope,
                "fit_intercept_vs_local_smooth": fit.intercept,
                "fit_r2_vs_local_smooth": fit.r_squared,
                "cold_over_warm": float(
                    data.loc[data["temperature_c_si"].idxmin(), column]
                    / data.loc[data["temperature_c_si"].idxmax(), column]
                ),
                "cold_end_cSt": float(data.loc[data["temperature_c_si"].idxmin(), column]),
                "warm_end_cSt": float(data.loc[data["temperature_c_si"].idxmax(), column]),
            }
        )
    comparison_summary = pd.DataFrame(comparison_rows).set_index("method").sort_values("rmse_vs_local_smooth_cSt")

    dynamic_summary = pd.DataFrame(
        {
            "warm_end_cP": [
                float(data.loc[data["temperature_c_si"].idxmax(), "mu_ref_local_cP"]),
                float(data.loc[data["temperature_c_si"].idxmax(), "mu_density_power_cP"]),
                float(data.loc[data["temperature_c_si"].idxmax(), "mu_gear_power_law_cP"]),
                float(data.loc[data["temperature_c_si"].idxmax(), "mu_combined_law_cP"]),
                float(data.loc[data["temperature_c_si"].idxmax(), "mu_flow_only_proxy_cP"]),
            ],
            "cold_end_cP": [
                float(data.loc[data["temperature_c_si"].idxmin(), "mu_ref_local_cP"]),
                float(data.loc[data["temperature_c_si"].idxmin(), "mu_density_power_cP"]),
                float(data.loc[data["temperature_c_si"].idxmin(), "mu_gear_power_law_cP"]),
                float(data.loc[data["temperature_c_si"].idxmin(), "mu_combined_law_cP"]),
                float(data.loc[data["temperature_c_si"].idxmin(), "mu_flow_only_proxy_cP"]),
            ],
        },
        index=[
            "Local smooth law",
            "Density-power fit",
            "Gear-pump power law",
            "Combined pump+flow law",
            "Flow-only proxy",
        ],
    )
    dynamic_summary["cold_over_warm"] = dynamic_summary["cold_end_cP"] / dynamic_summary["warm_end_cP"]

    return DensityStudy(
        cooldown=data,
        fits=fits,
        regression_summary=regression_summary,
        reference_summary=reference_summary,
        single_parameter_law_summary=single_parameter_law_summary,
        combined_law_summary=combined_law_summary,
        law_phase_summary=law_phase_summary,
        comparison_summary=comparison_summary,
        dynamic_summary=dynamic_summary,
        combined_trend_window=combined_trend_window,
    )


def _shade_segments(ax: plt.Axes, summary: pd.DataFrame) -> None:
    for _, row in summary.iterrows():
        color = SEGMENT_CLASS_COLORS.get(str(row["classification"]), "0.85")
        ax.axvspan(float(row["start_s"]) / 60.0, float(row["end_s"]) / 60.0, color=color, alpha=0.12)


def _plot_fit_line(ax: plt.Axes, x_values: pd.Series, fit: RegressionSummary) -> None:
    if not np.isfinite(fit.slope) or not np.isfinite(fit.intercept):
        return
    x_plot = np.linspace(float(np.nanmin(x_values)), float(np.nanmax(x_values)), 200)
    ax.plot(x_plot, fit.intercept + fit.slope * x_plot, color="black")


def plot_log_overview(review: FlowLogReview) -> plt.Figure:
    """Plot the full-log overview with the stable cooldown hold highlighted."""

    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True, constrained_layout=True)
    data = review.data

    ax_cmd = axes[0]
    ax_freq = ax_cmd.twinx()
    ax_cmd.plot(
        data["t_min"],
        data["pump_cmd_pct"],
        color="#111827",
        label="Pump command / frequency",
    )
    _shade_segments(ax_cmd, review.segment_summary)
    ax_cmd.axvspan(review.stable_start_min, review.stable_end_min, color="C2", alpha=0.18)
    ax_cmd.set_ylabel("Pump command [%]")
    ax_cmd.set_ylim(*MAIN_OVERVIEW_PUMP_CMD_YLIM)
    ax_freq.set_ylabel("Pump frequency [Hz]")
    ax_freq.set_ylim(*MAIN_OVERVIEW_PUMP_FREQ_YLIM)
    ax_cmd.set_title("Full log overview with the stable automatic 20% hold highlighted")
    lines = ax_cmd.get_lines()
    ax_cmd.legend(lines, [line.get_label() for line in lines], loc="best")

    axes[1].plot(data["t_min"], data["temperature_c_si"], label="Flow-meter temperature [°C]")
    for column in review.valid_temp_cols:
        axes[1].plot(data["t_min"], data[column], alpha=0.8, label=tc_display_name(column))
    axes[1].axvspan(review.stable_start_min, review.stable_end_min, color="C2", alpha=0.12)
    axes[1].set_ylabel("Temperature [°C]")
    axes[1].set_ylim(*MAIN_OVERVIEW_TEMPERATURE_YLIM)
    axes[1].legend(loc="best", ncols=max(1, min(3, len(review.valid_temp_cols) + 1)))

    ax_mass = axes[2]
    ax_volume = ax_mass.twinx()
    ax_mass.plot(data["t_min"], data["mass_flow_kgmin_si"], color="#2563eb", label="Mass flow [kg/min]")
    ax_volume.plot(data["t_min"], data["volume_flow_lmin_si"], color="#dc2626", label="Volume flow [L/min]")
    ax_mass.axvspan(review.stable_start_min, review.stable_end_min, color="C2", alpha=0.12)
    ax_mass.set_ylabel("Mass flow [kg/min]")
    ax_mass.set_ylim(*MAIN_OVERVIEW_MASS_FLOW_YLIM)
    ax_volume.set_ylabel("Volume flow [L/min]")
    ax_volume.set_ylim(*MAIN_OVERVIEW_VOLUME_FLOW_YLIM)
    lines = ax_mass.get_lines() + ax_volume.get_lines()
    ax_mass.legend(lines, [line.get_label() for line in lines], loc="best")

    axes[3].plot(data["t_min"], data["pump_pressure_before_bar_abs"], label="Before pump [bar abs]")
    axes[3].plot(data["t_min"], data["pump_pressure_after_bar_abs"], label="After pump [bar abs]")
    axes[3].plot(data["t_min"], data["pump_pressure_tank_bar_abs"], label="Tank [bar abs]")
    axes[3].axvspan(review.stable_start_min, review.stable_end_min, color="C2", alpha=0.12)
    axes[3].set_ylabel("Pressure [bar abs]")
    axes[3].set_ylim(*MAIN_OVERVIEW_PRESSURE_YLIM)
    axes[3].set_xlabel("Elapsed time [min]")
    axes[3].legend(loc="best")
    return fig


def plot_cooldown_thermal_overview(review: FlowLogReview) -> plt.Figure:
    """Plot thermal evolution and the thermocouple heatmap during cooldown."""

    cooldown = review.cooldown
    has_heatmap = bool(review.valid_temp_cols)
    rows = 2 if has_heatmap else 1
    fig, axes = plt.subplots(rows, 1, figsize=(14, 8 if has_heatmap else 4.5), constrained_layout=True)
    if rows == 1:
        axes = [axes]

    axes[0].plot(cooldown["elapsed_s"], cooldown["temperature_c_si"], label="Flow-meter temperature [°C]")
    if "temp_mean_C" in cooldown:
        axes[0].plot(cooldown["elapsed_s"], cooldown["temp_mean_C"], label="TC mean [°C]")
    ax_span = axes[0].twinx()
    ax_span.plot(cooldown["elapsed_s"], cooldown["temp_span_C"], color="C3", label="TC span [°C]")
    axes[0].set_xlabel("Elapsed time [s]")
    axes[0].set_ylabel("Temperature [°C]")
    ax_span.set_ylabel("TC span [°C]")
    axes[0].set_title("Thermal evolution during the stable automatic hold")
    lines = axes[0].get_lines() + ax_span.get_lines()
    axes[0].legend(lines, [line.get_label() for line in lines], loc="best")

    if has_heatmap:
        temp_map = cooldown[list(review.valid_temp_cols)].to_numpy().T
        image = axes[1].imshow(
            temp_map,
            aspect="auto",
            interpolation="nearest",
            cmap="coolwarm",
            extent=[
                float(cooldown["elapsed_s"].min()),
                float(cooldown["elapsed_s"].max()),
                len(review.valid_temp_cols) - 0.5,
                -0.5,
            ],
        )
        axes[1].set_yticks(
            range(len(review.valid_temp_cols)),
            labels=[tc_display_name(column) for column in review.valid_temp_cols],
        )
        axes[1].set_xlabel("Elapsed time [s]")
        axes[1].set_title("Thermocouple temperature map during cooldown")
        colorbar = fig.colorbar(image, ax=axes[1])
        colorbar.set_label("Temperature [°C]")

    return fig


def plot_cooldown_flow_meter_history(review: FlowLogReview) -> plt.Figure:
    """Plot the main flow-meter values during the stable cooldown hold."""

    cooldown = review.cooldown
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, constrained_layout=True)

    ax_temp = axes[0]
    ax_density = ax_temp.twinx()
    ax_temp.plot(cooldown["elapsed_s"], cooldown["temperature_c_si"], label="Flow-meter temperature [°C]")
    ax_density.plot(cooldown["elapsed_s"], cooldown["density_kg_m3_si"], color="C2", label="Density [kg/m$^3$]")
    ax_temp.set_ylabel("Temperature [°C]")
    ax_density.set_ylabel("Density [kg/m$^3$]")
    ax_temp.set_title("Flow-meter temperature and density")
    lines = ax_temp.get_lines() + ax_density.get_lines()
    ax_temp.legend(lines, [line.get_label() for line in lines], loc="best")

    ax_mass = axes[1]
    ax_volume = ax_mass.twinx()
    ax_mass.plot(cooldown["elapsed_s"], cooldown["mass_flow_kgmin_si"], label="Mass flow [kg/min]")
    ax_volume.plot(cooldown["elapsed_s"], cooldown["volume_flow_lmin_si"], color="C4", label="Volume flow [L/min]")
    ax_mass.set_ylabel("Mass flow [kg/min]")
    ax_volume.set_ylabel("Volume flow [L/min]")
    ax_mass.set_title("Flow-meter mass and volume flow")
    lines = ax_mass.get_lines() + ax_volume.get_lines()
    ax_mass.legend(lines, [line.get_label() for line in lines], loc="best")

    ax_cum_mass = axes[2]
    ax_cum_volume = ax_cum_mass.twinx()
    ax_cum_mass.plot(cooldown["elapsed_s"], cooldown["cum_mass_kg"], label="Cumulative mass [kg]")
    ax_cum_volume.plot(cooldown["elapsed_s"], cooldown["cum_volume_l"], color="C5", label="Cumulative volume [L]")
    ax_cum_mass.set_xlabel("Elapsed time [s]")
    ax_cum_mass.set_ylabel("Transferred mass [kg]")
    ax_cum_volume.set_ylabel("Transferred volume [L]")
    ax_cum_mass.set_title("Integrated flow-meter transfer")
    lines = ax_cum_mass.get_lines() + ax_cum_volume.get_lines()
    ax_cum_mass.legend(lines, [line.get_label() for line in lines], loc="best")
    return fig


def plot_cooldown_pump_history(review: FlowLogReview) -> plt.Figure:
    """Plot pump telemetry during the stable cooldown hold."""

    cooldown = review.cooldown
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True, constrained_layout=True)

    axes[0].plot(cooldown["elapsed_s"], cooldown["pump_freq_hz"], label="Pump frequency [Hz]")
    axes[0].set_ylabel("Frequency [Hz]")
    axes[0].set_title("Pump frequency stays nearly fixed during the hold")
    axes[0].legend(loc="best")

    axes[1].plot(cooldown["elapsed_s"], cooldown["pump_input_power_w"], label="Pump input power [W]")
    axes[1].set_ylabel("Input power [W]")
    axes[1].set_title("Pump electrical input")
    axes[1].legend(loc="best")

    axes[2].plot(cooldown["elapsed_s"], cooldown["pump_output_current_a"], label="Pump output current [A]")
    if cooldown["pump_output_voltage_v"].notna().any():
        axes[2].plot(cooldown["elapsed_s"], cooldown["pump_output_voltage_v"], label="Pump output voltage [V]")
    axes[2].set_ylabel("Electrical output")
    axes[2].set_title("Pump output current and voltage")
    axes[2].legend(loc="best")

    axes[3].plot(cooldown["elapsed_s"], cooldown["pump_pressure_before_bar_abs"], label="Before pump [bar abs]")
    axes[3].plot(cooldown["elapsed_s"], cooldown["pump_pressure_after_bar_abs"], label="After pump [bar abs]")
    axes[3].plot(cooldown["elapsed_s"], cooldown["pump_pressure_tank_bar_abs"], label="Tank [bar abs]")
    axes[3].plot(cooldown["elapsed_s"], cooldown["delta_p_bar_recomputed"], label="Pressure rise [bar]")
    axes[3].set_xlabel("Elapsed time [s]")
    axes[3].set_ylabel("Pressure [bar]")
    axes[3].set_title("Pump-side pressure signals")
    axes[3].legend(loc="best")
    return fig


def plot_signals_vs_temperature(
    cooldown: pd.DataFrame,
    *,
    signals: Sequence[str] = DEFAULT_INTERESTING_TEMPERATURE_SIGNALS,
) -> plt.Figure:
    """Scatter selected cooldown signals against flow-meter temperature."""

    available_signals = [signal for signal in signals if signal in cooldown.columns]
    n_signals = len(available_signals)
    ncols = 2
    nrows = int(np.ceil(n_signals / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 4.5 * nrows), constrained_layout=True)
    axes = np.atleast_1d(axes).ravel()

    for ax, signal in zip(axes, available_signals):
        ax.plot(cooldown["temperature_c_si"], cooldown[signal], ".", alpha=0.6)
        fit = _fit_linear_relation(cooldown["temperature_c_si"], cooldown[signal])
        _plot_fit_line(ax, cooldown["temperature_c_si"], fit)
        ax.set_xlabel("Flow-meter temperature [°C]")
        ax.set_ylabel(SIGNAL_LABELS.get(signal, signal))
        title = SIGNAL_TITLES.get(signal, signal.replace("_", " "))
        if np.isfinite(fit.r_squared):
            title = f"{title} (R² = {fit.r_squared:.3f})"
        ax.set_title(title)

    for ax in axes[n_signals:]:
        ax.set_visible(False)

    return fig


def plot_pump_performance_vs_temperature(review: FlowLogReview) -> plt.Figure:
    """Plot direct pump performance metrics against the HFE temperature."""

    cooldown = review.cooldown.copy().sort_values("temperature_c_si")
    pump_freq_hz = cooldown["pump_freq_hz"].replace(0.0, np.nan)
    cooldown["delivered_ml_per_rev"] = cooldown["volume_flow_lmin_si"] * 1000.0 / (60.0 * pump_freq_hz)
    cooldown["specific_input_energy_j_per_l"] = 60.0 * cooldown["pump_input_power_w"] / cooldown["volume_flow_lmin_si"]
    cooldown["hydraulic_power_w"] = cooldown["delta_p_bar_recomputed"] * cooldown["volume_flow_lmin_si"] * BAR_LMIN_TO_W

    fig, axes = plt.subplots(3, 2, figsize=(13.0, 11.0), constrained_layout=True)
    panel_specs = [
        (
            "volume_flow_lmin_si",
            "Delivered volume flow [L/min]",
            "Delivered flow at fixed pump speed",
            {"color": "C0"},
        ),
        (
            "delivered_ml_per_rev",
            "Delivered volume per rev [mL/rev]",
            "Gear-pump slip proxy: delivered volume per revolution",
            {"color": "C1"},
        ),
        (
            "pump_input_power_w",
            "Pump input power [W]",
            "Electrical input power",
            {"color": "C2"},
        ),
        (
            "specific_input_energy_j_per_l",
            "Specific input energy [J/L]",
            "Electrical energy required per liter pumped",
            {"color": "C3"},
        ),
        (
            "delta_p_bar_recomputed",
            "Pump pressure rise [bar]",
            "Pressure rise across the pump",
            {"color": "C4"},
        ),
        (
            "hydraulic_power_w",
            "Hydraulic power [W]",
            "Hydraulic output power from Δp and Q",
            {"color": "C5"},
        ),
    ]

    for ax, (column, ylabel, title, style) in zip(axes.ravel(), panel_specs):
        ax.plot(cooldown["temperature_c_si"], cooldown[column], ".", alpha=0.45, markersize=4, **style)
        fit = _fit_linear_relation(cooldown["temperature_c_si"], cooldown[column])
        _plot_fit_line(ax, cooldown["temperature_c_si"], fit)
        ax.set_xlabel("HFE temperature [°C]")
        ax.set_ylabel(ylabel)
        if np.isfinite(fit.r_squared):
            ax.set_title(f"{title} (R² = {fit.r_squared:.3f})")
        else:
            ax.set_title(title)

    return fig


def _format_log_date_label(log_name: str | None) -> tuple[str, str]:
    if not log_name:
        return "Current run", "current run"

    stem = Path(log_name).stem
    parts = stem.split("_")
    if len(parts) >= 3 and parts[0] == "log":
        try:
            stamp = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
            return stamp.strftime("%Y-%m-%d"), stamp.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return log_name, log_name


def plot_density_law_comparison(study: DensityStudy, *, log_name: str | None = None) -> plt.Figure:
    """Plot the last-run density law against the 3M reference and summarize coefficients."""

    cooldown = study.cooldown.sort_values("temperature_c_si")
    density_fit = study.fits["density_vs_temperature"]
    run_label, title_label = _format_log_date_label(log_name)

    our_temperature = cooldown["temperature_c_si"].to_numpy(dtype=float)
    our_density = cooldown["density_kg_m3_si"].to_numpy(dtype=float)
    our_prediction = density_fit.intercept + density_fit.slope * our_temperature

    density_3m_intercept = 1000.0 * HFE_7200_DENSITY_INTERCEPT_G_ML
    density_3m_slope = -1000.0 * HFE_7200_DENSITY_SLOPE_G_ML_PER_C
    density_3m_prediction = density_3m_intercept + density_3m_slope * our_temperature

    optimass_cal_temp_c = 20.0
    optimass_base_accuracy_kg_m3 = 1.0
    optimass_temp_effect_kg_m3_per_c = 0.015
    density_reading_band_kg_m3 = (
        optimass_base_accuracy_kg_m3
        + optimass_temp_effect_kg_m3_per_c * np.abs(our_temperature - optimass_cal_temp_c)
    )
    display_intercept_error = (
        optimass_base_accuracy_kg_m3
        + optimass_temp_effect_kg_m3_per_c * abs(0.0 - optimass_cal_temp_c)
    )
    display_slope_error = np.max(density_reading_band_kg_m3) / max(
        float(np.max(our_temperature) - np.min(our_temperature)),
        1.0,
    )
    display_intercept_value = round(density_fit.intercept, 1)
    display_intercept_error = round(display_intercept_error, 1)
    display_slope_value = round(density_fit.slope, 2)
    display_slope_error = round(display_slope_error, 2)
    display_density_3m_intercept = round(density_3m_intercept, 1)
    display_density_3m_slope = round(density_3m_slope, 2)

    our_residual = our_density - our_prediction
    density_3m_residual = our_density - density_3m_prediction

    fig = plt.figure(figsize=(12.0, 10.6))
    grid = fig.add_gridspec(2, 1, height_ratios=[8.0, 2.4], hspace=0.05)
    ax_plot = fig.add_subplot(grid[0])
    ax_table = fig.add_subplot(grid[1])
    ax_table.axis("off")

    ax_plot.fill_between(
        our_temperature,
        our_density - density_reading_band_kg_m3,
        our_density + density_reading_band_kg_m3,
        color="tab:blue",
        alpha=0.18,
        label=r"Reading $\pm$ Flow-meter band",
    )
    ax_plot.plot(
        our_temperature,
        our_density,
        color="tab:blue",
        linewidth=3.0,
        label="Density reading",
    )
    ax_plot.plot(
        our_temperature,
        our_prediction,
        color="black",
        linewidth=2.8,
        linestyle=(0, (6, 3)),
        alpha=0.95,
        zorder=6,
        label=f"{run_label} fit",
    )
    ax_plot.plot(
        our_temperature,
        density_3m_prediction,
        color="tab:red",
        linewidth=3.0,
        label="3M density law",
    )
    y_samples = [
        our_density - density_reading_band_kg_m3,
        our_density + density_reading_band_kg_m3,
        our_prediction,
        density_3m_prediction,
    ]
    y_all = np.concatenate(y_samples)
    y_min = float(np.min(y_all))
    y_max = float(np.max(y_all))
    y_pad = max(0.04 * (y_max - y_min), 0.03)

    ax_plot.set_xlim(float(np.min(our_temperature)), float(np.max(our_temperature)))
    ax_plot.set_ylim(y_min - y_pad, y_max + y_pad)
    ax_plot.set_xlabel("HFE temperature (°C)", fontsize=20)
    ax_plot.set_ylabel("Density (kg/m³)", fontsize=20)
    ax_plot.set_title(f"Density law comparison for {title_label}", fontsize=24)
    ax_plot.tick_params(axis="both", labelsize=16)
    ax_plot.grid(True, alpha=0.3)
    ax_plot.legend(loc="best", fontsize=16, framealpha=0.95)

    table_rows = [
        [
            f"{run_label} fit",
            rf"${display_intercept_value:.1f}\,\pm\,{display_intercept_error:.1f}$",
            rf"${display_slope_value:.2f}\,\pm\,{display_slope_error:.2f}$",
            rf"$R^2 = {density_fit.r_squared:.6f},\ \mathrm{{RMSE}} = {np.sqrt(np.mean(our_residual**2)):.3f}\ \mathrm{{kg/m^3}}$",
        ],
        [
            "3M law",
            rf"${display_density_3m_intercept:.1f}$",
            rf"${display_density_3m_slope:.2f}$",
            rf"$\mathrm{{RMSE}} = {np.sqrt(np.mean(density_3m_residual**2)):.3f}\ \mathrm{{kg/m^3}}$",
        ],
    ]
    table_columns = [
        "Dataset",
        "Intercept $a$\n[kg/m³]",
        "Slope $b$\n[kg/m³/°C]",
        "Fit quality\n($\\rho = a + bT$)",
    ]

    summary_table = ax_table.table(
        cellText=table_rows,
        colLabels=table_columns,
        cellLoc="left",
        colLoc="left",
        bbox=(0.0, 0.02, 1.0, 0.72),
        colWidths=[0.18, 0.22, 0.22, 0.38],
    )
    summary_table.auto_set_font_size(False)
    summary_table.set_fontsize(9.1)
    summary_table.scale(1.0, 1.55)
    for (row, _), cell in summary_table.get_celld().items():
        cell.set_edgecolor("0.7")
        if row == 0:
            cell.set_facecolor("#f2f2f2")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("white")
            cell.get_text().set_multialignment("left")

    fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.10)
    table_position = ax_table.get_position()
    ax_table.set_position([0.02, table_position.y0, 0.96, table_position.height])
    fig.text(
        0.02,
        0.050,
        (
            rf"Run: {run_label}. Flow-meter band: "
            rf"$\pm\left(1.0 + 0.015\,|T - 20|\right)$ kg/m³."
        ),
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="0.45",
    )
    return fig


def plot_density_studies(study: DensityStudy) -> plt.Figure:
    """Plot the density-focused cooldown relationships."""

    cooldown = study.cooldown.sort_values("temperature_c_si")
    density_fit = study.fits["density_vs_temperature"]
    mass_flow_fit = study.fits["mass_flow_vs_temperature"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)

    ax_density_time = axes[0, 0]
    ax_temp_time = ax_density_time.twinx()
    ax_density_time.plot(study.cooldown["elapsed_s"], study.cooldown["density_kg_m3_si"], label="Density [kg/m$^3$]")
    ax_temp_time.plot(study.cooldown["elapsed_s"], study.cooldown["temperature_c_si"], color="C1", label="Flow-meter temperature [°C]")
    ax_density_time.set_xlabel("Elapsed time [s]")
    ax_density_time.set_ylabel("Density [kg/m$^3$]")
    ax_temp_time.set_ylabel("Temperature [°C]")
    ax_density_time.set_title("Density rises as the liquid cools")
    lines = ax_density_time.get_lines() + ax_temp_time.get_lines()
    ax_density_time.legend(lines, [line.get_label() for line in lines], loc="best")

    axes[0, 1].scatter(study.cooldown["temperature_c_si"], study.cooldown["density_kg_m3_si"], s=18, alpha=0.6)
    _plot_fit_line(axes[0, 1], study.cooldown["temperature_c_si"], density_fit)
    axes[0, 1].set_xlabel("Flow-meter temperature [°C]")
    axes[0, 1].set_ylabel("Density [kg/m$^3$]")
    axes[0, 1].set_title(f"Density vs temperature (R² = {density_fit.r_squared:.4f})")

    axes[1, 0].scatter(study.cooldown["temperature_c_si"], study.cooldown["mass_flow_kgmin_si"], s=18, alpha=0.6)
    _plot_fit_line(axes[1, 0], study.cooldown["temperature_c_si"], mass_flow_fit)
    axes[1, 0].set_xlabel("Flow-meter temperature [°C]")
    axes[1, 0].set_ylabel("Mass flow [kg/min]")
    axes[1, 0].set_title(f"Mass flow vs temperature (R² = {mass_flow_fit.r_squared:.4f})")

    axes[1, 1].plot(cooldown["temperature_c_si"], cooldown["nu_ref_local_cSt"], label="Local smooth law (3M-derived)")
    axes[1, 1].plot(cooldown["temperature_c_si"], cooldown["nu_density_power_cSt"], label="Density-power fit")
    axes[1, 1].plot(cooldown["temperature_c_si"], cooldown["nu_gear_power_law_cSt"], label="Gear-pump power law")
    axes[1, 1].set_xlabel("Temperature [°C]")
    axes[1, 1].set_ylabel("Kinematic viscosity [cSt]")
    axes[1, 1].set_title("Density and power trends both recover the cooldown viscosity")
    axes[1, 1].legend(loc="best")

    return fig


def plot_viscosity_comparison(study: DensityStudy) -> plt.Figure:
    """Plot each single-parameter viscosity model on its own panel."""

    cooldown = study.cooldown.sort_values("temperature_c_si")
    fig, axes = plt.subplots(3, 2, figsize=(12.0, 11.0), sharex=True, sharey=True, constrained_layout=True)

    # (title, data-column, plot-style, feature-key-in-single_parameter_law_summary or None for gear law)
    panel_specs = [
        ("Volume flow only", "nu_from_volume_flow_model_cSt", {"color": "C4", "linestyle": "--", "linewidth": 1.4}, "volume_flow_lmin_si"),
        ("Pressure rise only", "nu_from_delta_p_model_cSt", {"color": "C5", "linestyle": "--", "linewidth": 1.4}, "delta_p_bar_recomputed"),
        (
            "Input power / flow only",
            "nu_from_input_power_per_lmin_model_cSt",
            {"color": "C6", "linestyle": "--", "linewidth": 1.4},
            "pump_input_power_per_lmin",
        ),
        (
            "Output current / flow only",
            "nu_from_output_current_per_lmin_model_cSt",
            {"color": "C7", "linestyle": "--", "linewidth": 1.4},
            "pump_output_current_per_lmin",
        ),
        ("Gear-pump power law", "nu_gear_power_law_cSt", {"color": "C8", "linestyle": "--", "linewidth": 1.6}, None),
    ]

    _feature_short = {
        "volume_flow_lmin_si": "Q",
        "delta_p_bar_recomputed": r"\Delta P",
        "pump_input_power_per_lmin": "P/Q",
        "pump_output_current_per_lmin": "I/Q",
    }

    for ax, (title, column, style, feat_key) in zip(axes.ravel(), panel_specs):
        ax.plot(cooldown["temperature_c_si"], cooldown["nu_ref_local_cSt"], label="Local smooth law", color="C1")
        ax.plot(
            cooldown["temperature_c_si"],
            cooldown["nu_combined_law_trend_cSt"],
            label="Combined-law mean trend",
            color="C2",
            linestyle=":",
            linewidth=2.0,
        )
        ax.plot(cooldown["temperature_c_si"], cooldown[column], label=title, **style)
        ax.set_title(title)
        ax.legend(loc="upper left", fontsize=8)

        # Equation annotation
        if feat_key is not None and feat_key in study.single_parameter_law_summary.index:
            row = study.single_parameter_law_summary.loc[feat_key]
            a0 = float(row["intercept_in_ln_nu"])
            a1 = float(row["coef_in_ln_nu"])
            xbar = float(row["mean"])
            sig = float(row["std"])
            flabel = _feature_short[feat_key]
            sign = "+" if a1 >= 0 else "-"
            eq_text = (
                rf"$\ln(\nu) = {a0:.3f} {sign} {abs(a1):.3f}\,\frac{{{flabel}-\bar{{x}}}}{{\sigma}}$"
                "\n"
                rf"$\bar{{x}}={xbar:.4f},\ \sigma={sig:.4f}$"
            )
            ax.text(
                0.98, 0.04, eq_text,
                transform=ax.transAxes, fontsize=7.5, va="bottom", ha="right",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.6", alpha=0.88),
            )
        else:
            # Gear-pump power law annotation
            gain = float(study.reference_summary.loc["gear_power_law_gain_w_per_cP", "value"])
            mu_warm = float(study.reference_summary.loc["gear_power_law_warm_dynamic_viscosity_cP", "value"])
            eq_text = (
                rf"$\mu = {mu_warm:.3f} + \Delta P_{{\rm elec}}\,/\,{gain:.2f}$ [cP]"
                "\n"
                rf"gain $= {gain:.2f}$ W/cP"
            )
            ax.text(
                0.98, 0.04, eq_text,
                transform=ax.transAxes, fontsize=7.5, va="bottom", ha="right",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.6", alpha=0.88),
            )

    for ax in axes[-1, :]:
        ax.set_xlabel("Temperature [°C]")
    for ax in axes[:, 0]:
        ax.set_ylabel("Kinematic viscosity [cSt]")
    for ax in axes.ravel()[len(panel_specs):]:
        ax.set_visible(False)

    return fig


def plot_viscosity_estimates(study: DensityStudy) -> plt.Figure:
    """Plot each viscosity estimator as: sensor signal (x) vs ν (y).

    Each panel shows the reference ν scatter coloured by temperature and the
    fitted model curve, so the regression relationship and equation form are
    directly visible in the plot geometry.

    Estimators shown:
    - Volume flow Q
    - Pressure rise ΔP
    - Density ρ via the Andrade equation  (3M-fitted)
    - Pump input power per unit flow  P/Q
    - Pump output current per unit flow  I/Q

    The 3M density law is  ρ = ρ₀ − k·T  (k = |dρ/dT| > 0), so the Andrade
    temperature inversion used in panel 3 is  T_ρ = (ρ₀ − ρ)/k.
    """

    cooldown = study.cooldown.sort_values("temperature_c_si").copy()
    temp = cooldown["temperature_c_si"].to_numpy(float)
    nu_ref = cooldown["nu_ref_local_cSt"].to_numpy(float)

    # ── Andrade fit on 3M datasheet: ln ν = A + B/T_K ────────────────────
    ref_T_K = REFERENCE_3M_N7200["temperature_c"].to_numpy(float) + 273.15
    ref_ln_nu = np.log(REFERENCE_3M_N7200["kinematic_viscosity_cSt"].to_numpy(float))
    _X = np.column_stack([np.ones(len(ref_T_K)), 1.0 / ref_T_K])
    A_and, B_and = np.linalg.lstsq(_X, ref_ln_nu, rcond=None)[0]

    # ── 3M density law:  ρ = ρ₀ − k·T_C  (k > 0) ────────────────────────
    rho_0 = 1000.0 * HFE_7200_DENSITY_INTERCEPT_G_ML
    rho_slope_mag = 1000.0 * HFE_7200_DENSITY_SLOPE_G_ML_PER_C

    T_from_rho_K = (rho_0 - cooldown["density_kg_m3_si"].to_numpy(float)) / rho_slope_mag + 273.15
    nu_rho_andrade = np.exp(A_and + B_and / T_from_rho_K)

    # ── 3M trendline: parametric (signal(T), ν_Andrade(T)) ───────────────
    # For each sensor signal fit a linear signal~T model from the data, then
    # evaluate both signal(T) and ν_Andrade(T) over a smooth temperature range
    # to produce the expected scatter trajectory under 3M physics.
    T_trend_C = np.linspace(temp.min() - 1.0, temp.max() + 1.0, 300)
    T_trend_K = T_trend_C + 273.15
    nu_andrade_trend = np.exp(A_and + B_and / T_trend_K)

    def _signal_vs_T_trend(sig_arr: np.ndarray) -> np.ndarray:
        """Linear fit of signal vs temperature evaluated over T_trend_C."""
        ok = np.isfinite(sig_arr) & np.isfinite(temp)
        coeffs = np.polyfit(temp[ok], sig_arr[ok], 1)
        return np.polyval(coeffs, T_trend_C)

    q_arr   = cooldown["volume_flow_lmin_si"].to_numpy(float)
    dp_arr  = cooldown["delta_p_bar_recomputed"].to_numpy(float)
    p_arr   = cooldown["pump_input_power_w"].to_numpy(float)

    # ── Panel definitions ─────────────────────────────────────────────────
    panels = [
        dict(
            title="Volume flow  →  ν",
            x=q_arr,
            x_label="Volume flow  $Q$  [L/min]",
            nu_model=cooldown["nu_from_volume_flow_model_cSt"].to_numpy(float),
            trend_x=_signal_vs_T_trend(q_arr),
            eq=r"$\ln\nu = \alpha + \beta\,\tilde{Q}$"
               "\n"
               r"$\tilde{Q} = (Q - \bar{Q})\,/\,\sigma_Q$",
        ),
        dict(
            title="Pressure rise  →  ν",
            x=dp_arr,
            x_label="Pressure rise  $\\Delta P$  [bar]",
            nu_model=cooldown["nu_from_delta_p_model_cSt"].to_numpy(float),
            trend_x=_signal_vs_T_trend(dp_arr),
            eq=r"$\ln\nu = \alpha + \beta\,\widetilde{\Delta P}$"
               "\n"
               r"$\widetilde{\Delta P} = (\Delta P - \overline{\Delta P})\,/\,\sigma_{\Delta P}$",
        ),
        dict(
            title="Density  →  ν  (Andrade)",
            x=cooldown["density_kg_m3_si"].to_numpy(float),
            x_label="Density  $\\rho$  [kg/m³]",
            nu_model=nu_rho_andrade,
            trend_x=rho_0 - rho_slope_mag * T_trend_C,  # pure 3M density law
            eq=r"$\nu = \exp(A + B\,/\,T_\rho)$"
               "\n"
               r"$T_\rho\!=\!(\rho_0 - \rho)\,/\,k\;+\;273\;\mathrm{K}$",
        ),
        dict(
            title="Pump input power  →  ν",
            x=p_arr,
            x_label="Input power  $P$  [W]",
            nu_model=cooldown["nu_from_input_power_w_model_cSt"].to_numpy(float),
            trend_x=_signal_vs_T_trend(p_arr),
            eq=r"$\ln\nu = \alpha + \beta\,\tilde{P}$"
               "\n"
               r"$\tilde{P} = (P - \bar{P})\,/\,\sigma_P$",
        ),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(12.0, 9.0), sharey=False, constrained_layout=True)
    sc_last = None

    for ax, panel in zip(axes.ravel(), panels):
        x = panel["x"]
        nu_m = panel["nu_model"]
        valid = np.isfinite(x) & np.isfinite(nu_ref) & np.isfinite(nu_m)
        order = np.argsort(x[valid])

        sc = ax.scatter(
            x[valid], nu_ref[valid],
            s=8, alpha=0.45, c=temp[valid], cmap="coolwarm",
            zorder=2, label="Reference ν (local 3M law)",
        )
        sc_last = sc

        ax.plot(
            panel["trend_x"], nu_andrade_trend,
            color="C1", lw=1.5, linestyle="--", zorder=3, label="3M expected trend",
        )

        ax.plot(
            x[valid][order], nu_m[valid][order],
            color="k", lw=2.0, zorder=4, label="Sensor model",
        )

        ax.set_xlabel(panel["x_label"], fontsize=9)
        ax.set_ylabel("Kinematic viscosity  ν  [cSt]", fontsize=9)
        ax.set_title(panel["title"], fontsize=10, fontweight="bold")
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        ax.grid(True, alpha=0.25)

        ax.text(
            0.03, 0.04, panel["eq"],
            transform=ax.transAxes, fontsize=8.5, va="bottom", ha="left",
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="0.5", alpha=0.92),
        )

    for ax in axes.ravel()[len(panels):]:
        ax.set_visible(False)

    if sc_last is not None:
        fig.colorbar(
            sc_last,
            ax=axes.ravel()[:len(panels)].tolist(),
            label="Temperature [°C]",
            shrink=0.55,
            pad=0.02,
        )

    fig.suptitle(
        "Viscosity estimators — sensor signal vs reference ν  (colour = temperature)",
        fontsize=12, fontweight="bold",
    )
    return fig


def plot_viscosity_vs_temperature(study: DensityStudy) -> plt.Figure:
    """Compare each viscosity estimate against the 3M reference on a temperature axis.

    Each panel shows one estimator's predicted ν vs temperature alongside:
    - the 3M local smooth law (the calibration reference)
    - the 3M Andrade fit  ln ν = A + B/T_K  (two-parameter, fit to full datasheet)
    - the 3M tabulated datasheet points near our operating window
    """

    cooldown = study.cooldown.sort_values("temperature_c_si").copy()
    temp = cooldown["temperature_c_si"].to_numpy(float)
    nu_ref = cooldown["nu_ref_local_cSt"].to_numpy(float)

    # ── 3M Andrade fit ────────────────────────────────────────────────────
    ref_T_K_ds = REFERENCE_3M_N7200["temperature_c"].to_numpy(float) + 273.15
    ref_ln_nu_ds = np.log(REFERENCE_3M_N7200["kinematic_viscosity_cSt"].to_numpy(float))
    _X = np.column_stack([np.ones(len(ref_T_K_ds)), 1.0 / ref_T_K_ds])
    A_and, B_and = np.linalg.lstsq(_X, ref_ln_nu_ds, rcond=None)[0]

    # Andrade curve over a slightly wider T range than our data
    T_plot_C = np.linspace(temp.min() - 3.0, temp.max() + 3.0, 300)
    nu_andrade_curve = np.exp(A_and + B_and / (T_plot_C + 273.15))

    # 3M tabulated points within ±15 °C of our operating window
    ref_T_C_all = REFERENCE_3M_N7200["temperature_c"].to_numpy(float)
    ref_nu_all = REFERENCE_3M_N7200["kinematic_viscosity_cSt"].to_numpy(float)
    near = (ref_T_C_all >= temp.min() - 15.0) & (ref_T_C_all <= temp.max() + 15.0)

    # ── Density → Andrade estimate ────────────────────────────────────────
    rho_0 = 1000.0 * HFE_7200_DENSITY_INTERCEPT_G_ML
    rho_slope_mag = 1000.0 * HFE_7200_DENSITY_SLOPE_G_ML_PER_C
    T_from_rho_K = (rho_0 - cooldown["density_kg_m3_si"].to_numpy(float)) / rho_slope_mag + 273.15
    nu_rho_andrade = np.exp(A_and + B_and / T_from_rho_K)

    # ── Panel definitions: (title, model array, line colour) ─────────────
    panels = [
        ("Volume flow  Q",             cooldown["nu_from_volume_flow_model_cSt"].to_numpy(float),             "C4"),
        ("Pressure rise  ΔP",          cooldown["nu_from_delta_p_model_cSt"].to_numpy(float),                 "C5"),
        ("Density  →  Andrade",        nu_rho_andrade,                                                        "C0"),
        ("Pump input power  P",         cooldown["nu_from_input_power_w_model_cSt"].to_numpy(float),           "C6"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(12.0, 9.0), sharex=True, sharey=True, constrained_layout=True)

    for ax, (title, nu_model, color) in zip(axes.ravel(), panels):
        valid = np.isfinite(temp) & np.isfinite(nu_model) & np.isfinite(nu_ref)
        order = np.argsort(temp[valid])
        t_s = temp[valid][order]

        # 3M references
        ax.plot(T_plot_C, nu_andrade_curve,
                color="C1", lw=1.5, linestyle="--", zorder=3, label="3M Andrade fit")
        ax.plot(t_s, nu_ref[valid][order],
                color="0.45", lw=1.5, zorder=3, label="3M local smooth law")
        ax.scatter(ref_T_C_all[near], ref_nu_all[near],
                   marker="D", s=55, color="C1", edgecolors="k", linewidths=0.7,
                   zorder=6, label="3M datasheet")

        # Model estimate
        ax.plot(t_s, nu_model[valid][order],
                color=color, lw=2.2, zorder=4, label=title)

        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.legend(loc="upper right", fontsize=7.5, framealpha=0.9)
        ax.grid(True, alpha=0.25)

    for ax in axes[-1, :]:
        ax.set_xlabel("Temperature [°C]", fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("Kinematic viscosity  ν  [cSt]", fontsize=9)
    for ax in axes.ravel()[len(panels):]:
        ax.set_visible(False)

    fig.suptitle(
        "Viscosity estimates vs temperature — comparison with 3M reference",
        fontsize=12, fontweight="bold",
    )
    return fig
