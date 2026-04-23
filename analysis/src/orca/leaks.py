"""Leak-test analysis helpers for ORCA."""

# pylint: disable=too-many-lines

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Sequence, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.artist import Artist
from matplotlib.offsetbox import AnchoredOffsetbox, TextArea, VPacker
from matplotlib.transforms import Bbox

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = REPO_ROOT / "data" / "processed"
DEFAULT_LEAK_DATA_DIR = PROCESSED_DATA_DIR / "leak_test"
RAW_LOG_PATTERN = re.compile(r"^log_(\d{8})_(\d{6})(?:.*)?\.csv$")

TIME_COLUMN = "time_s"
PRESSURE_ABS_COLUMN = "pump_pressure_tank_bar_abs"
PRESSURE_GAUGE_COLUMN = "pump_pressure_tank_bar"
PRESSURE_ERR_COLUMN = "pump_pressure_error_bar"
ROOM_TEMP_COLUMN = "fluid_temperature_c"

P_ATM_BAR = 1.01325
TOTAL_SYSTEM_VOLUME_L = 7.0
GAS_TRAP_VOLUME_L = 3.0
FILLED_HFE_VOLUME_L = TOTAL_SYSTEM_VOLUME_L - GAS_TRAP_VOLUME_L
NITROGEN_LEAK_TEST_VOLUME_L = TOTAL_SYSTEM_VOLUME_L
SYSTEM_HEIGHT_M = 2.0
TANK_HEIGHT_M = 0.5
BIN_WIDTH_MIN = 1.0
SECONDS_PER_HOUR = 3600.0
SECONDS_PER_YEAR = 365.25 * 24.0 * SECONDS_PER_HOUR
GAS_CONSTANT_J_PER_MOL_K = 8.314462618
HFE_7200_MOLAR_MASS_KG_PER_MOL = 264.0e-3
HFE_7200_DENSITY_INTERCEPT_G_ML = 1.4811
HFE_7200_DENSITY_SLOPE_G_ML_PER_C = 0.0023026
HFE_7200_VAPOR_PRESSURE_LOG_INTERCEPT = 22.289
HFE_7200_VAPOR_PRESSURE_LOG_SLOPE_K = 3752.1
HFE_REFERENCE_TEMP_C = 25.0
HFE_LIQUID_DENSITY_KG_M3 = 1000.0 * (
    HFE_7200_DENSITY_INTERCEPT_G_ML
    - HFE_7200_DENSITY_SLOPE_G_ML_PER_C * HFE_REFERENCE_TEMP_C
)
HFE_L_PER_YEAR_PER_MBARLS = (
    0.1
    * HFE_7200_MOLAR_MASS_KG_PER_MOL
    * 1000.0
    * SECONDS_PER_YEAR
    / (
        GAS_CONSTANT_J_PER_MOL_K
        * (HFE_REFERENCE_TEMP_C + 273.15)
        * HFE_LIQUID_DENSITY_KG_M3
    )
)
HFE_REFERENCE_VAPOR_PRESSURE_BAR = np.exp(
    HFE_7200_VAPOR_PRESSURE_LOG_INTERCEPT
    - HFE_7200_VAPOR_PRESSURE_LOG_SLOPE_K / (HFE_REFERENCE_TEMP_C + 273.0)
) / 1.0e5
SYSTEM_GAS_TRAP_N2_PARTIAL_PRESSURE_BAR = P_ATM_BAR
HFE_VAPOR_GAUGE_BAR = HFE_REFERENCE_VAPOR_PRESSURE_BAR
DEFAULT_RESERVOIR_XMAX_H = 24.0
DEFAULT_RESERVOIR_YMAX_BAR = 3.0
INHG_TO_BAR = 0.0338638866667
MMHG_TO_BAR = 1.33322368421e-3

GAUGE_RANGE_MIN_BAR = -1.0
GAUGE_RANGE_MAX_BAR = 4.1
GAUGE_FULL_SPAN_BAR = GAUGE_RANGE_MAX_BAR - GAUGE_RANGE_MIN_BAR
GAUGE_END_QUARTER_FRACTION = 0.25
GAUGE_RESOLUTION_BAR = 0.1
VACUUM_GAUGE_FULL_SPAN_INHG = 30.0
VACUUM_GAUGE_END_QUARTER_FRACTION = 0.25
VACUUM_GAUGE_RESOLUTION_INHG = 1.0
ANNOTATION_BOX_CANDIDATES = (
    ("upper right", (0.98, 0.98)),
    ("upper left", (0.02, 0.98)),
    ("lower right", (0.98, 0.02)),
    ("lower left", (0.02, 0.02)),
    ("center right", (0.98, 0.50)),
    ("center left", (0.02, 0.50)),
    ("upper center", (0.50, 0.98)),
    ("lower center", (0.50, 0.02)),
)
ANNOTATION_LINE_SEP = 5
ANNOTATION_MAX_POINTS = 300


@dataclass(frozen=True)
class PressureSeries:
    """Raw pressure measurements loaded from a single CSV file."""

    csv_path: Path
    time_h: np.ndarray
    pressure_abs_bar: np.ndarray
    pressure_error_bar: float
    room_temp_c: float
    initial_gauge_bar: float


@dataclass(frozen=True)
class AveragedTrace:
    """Time-binned pressure trace used for fitting and plotting."""

    time_h: np.ndarray
    pressure_abs_bar: np.ndarray
    pressure_sigma_bar: np.ndarray


@dataclass(frozen=True)
class ExponentialFit:  # pylint: disable=too-many-instance-attributes
    """Exponential pressure-decay fit results."""

    asymptote_bar: float
    asymptote_err_bar: float
    amplitude_bar: float
    amplitude_err_bar: float
    k_per_h: float
    k_err_per_h: float
    tau_h: float
    tau_err_h: float


@dataclass(frozen=True)
class LeakEstimate:
    """Equivalent leak values for the HFE operating case."""

    throughput_mbar_l_per_s: float
    throughput_err_mbar_l_per_s: float
    hfe_loss_l_per_year: float
    hfe_loss_err_l_per_year: float
    upper_limit_throughput_mbar_l_per_s: float | None = None
    upper_limit_hfe_loss_l_per_year: float | None = None
    upper_limit_confidence_level: float | None = None
    is_upper_limit_only: bool = False


@dataclass(frozen=True)
class MeanSystemPressure:
    """Mean hydrostatic pressure assumed for the HFE system."""

    absolute_bar: float
    gauge_bar: float
    hydrostatic_delta_bar: float
    required_tank_fill_gauge_bar: float


@dataclass(frozen=True)
class TopGasTrapOperatingCase:
    """Top gas-trap operating state used for the vapor-leak estimate."""

    total_pressure_abs_bar: float
    gas_trap_volume_l: float
    hfe_vapor_pressure_abs_bar: float
    n2_partial_pressure_abs_bar: float
    hfe_vapor_mole_fraction: float


@dataclass(frozen=True)
class SystemPressureResult:  # pylint: disable=too-many-instance-attributes
    """Complete analysis outputs for a system-pressure leak log."""

    series: PressureSeries
    averaged: AveragedTrace
    fit: ExponentialFit | None
    leak: LeakEstimate | None
    mean_pressure: MeanSystemPressure
    top_gas_trap: TopGasTrapOperatingCase
    fit_curve_time_h: np.ndarray | None
    fit_curve_pressure_bar: np.ndarray | None
    rmse_mbar: float | None
    warning: str | None = None


@dataclass(frozen=True)
class ReservoirLeakCase:  # pylint: disable=too-many-instance-attributes
    """Legacy reservoir leak test inputs for a single O-ring configuration."""

    label: str
    slug: str
    time_h: np.ndarray
    pressure_abs_bar: np.ndarray
    volume_l: float
    operating_gauge_bar: float
    x_max_h: float
    y_max_bar: float
    source_note: str


@dataclass(frozen=True)
class VacuumRateOfRiseCase:  # pylint: disable=too-many-instance-attributes
    """Fixed-volume vacuum rate-of-rise inputs for one isolated loop test."""

    label: str
    slug: str
    time_h: np.ndarray
    pressure_abs_bar: np.ndarray
    pressure_inhg: np.ndarray
    measurement_sigma_bar: np.ndarray
    measurement_sigma_inhg: np.ndarray
    volume_l: float
    system_temp_c: float
    source_note: str


@dataclass(frozen=True)
class FixedTailExponentialFit:
    """Exponential fit with a fixed asymptotic pressure."""

    asymptote_bar: float
    amplitude_bar: float
    amplitude_err_bar: float
    k_per_h: float
    k_err_per_h: float
    cov_log_amplitude_k: np.ndarray


@dataclass(frozen=True)
class ReservoirLeakResult:  # pylint: disable=too-many-instance-attributes
    """Complete analysis outputs for a reservoir leak test case."""

    case: ReservoirLeakCase
    measurement_sigma_bar: np.ndarray
    fit: FixedTailExponentialFit
    leak: LeakEstimate
    start_decay_mbar_per_h: float
    start_decay_err_mbar_per_h: float
    fit_curve_time_h: np.ndarray
    fit_curve_pressure_bar: np.ndarray
    fit_curve_sigma_bar: np.ndarray


@dataclass(frozen=True)
class ReservoirPressureLogResult:  # pylint: disable=too-many-instance-attributes
    """System-style pressure-drop analysis for a logged reservoir O-ring test."""

    label: str
    slug: str
    source_note: str
    pressure_abs_column: str
    volume_l: float
    operating_gauge_bar: float
    series: PressureSeries
    averaged: AveragedTrace
    fit: ExponentialFit | None
    linear_fit: LinearPressureRiseFit | None
    leak: LeakEstimate | None
    fit_curve_time_h: np.ndarray | None
    fit_curve_pressure_bar: np.ndarray | None
    fit_curve_label: str | None
    rmse_mbar: float | None
    warning: str | None = None


@dataclass(frozen=True)
class LinearPressureRiseFit:
    """Weighted linear fit for a fixed-volume vacuum rate-of-rise test."""

    intercept_bar: float
    intercept_err_bar: float
    slope_bar_per_h: float
    slope_err_bar_per_h: float
    covariance: np.ndarray


@dataclass(frozen=True)
class GasLoadEstimate:
    """Gas-load quantities derived from a rate-of-rise slope."""

    throughput_mbar_l_per_s: float
    throughput_err_mbar_l_per_s: float
    hfe_loss_l_per_year: float
    hfe_loss_err_l_per_year: float


@dataclass(frozen=True)
class WaterVaporComparison:
    """Comparison of measured pressure against room-temperature water vapor saturation."""

    saturation_pressure_bar: float
    start_ratio: float
    end_ratio: float
    start_excess_mbar: float
    end_excess_mbar: float


@dataclass(frozen=True)
class VacuumRateOfRiseResult:  # pylint: disable=too-many-instance-attributes
    """Complete analysis outputs for a vacuum rate-of-rise test."""

    case: VacuumRateOfRiseCase
    fit: LinearPressureRiseFit
    gas_load: GasLoadEstimate
    water_vapor: WaterVaporComparison
    fit_curve_time_h: np.ndarray
    fit_curve_pressure_bar: np.ndarray
    fit_curve_sigma_bar: np.ndarray
    rmse_mbar: float


def latest_pressure_log(data_dir: Path = RAW_DATA_DIR) -> Path:
    """Return the newest raw pressure log based on its filename timestamp."""

    candidates = sorted(data_dir.glob("log_*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No log CSV files found in {data_dir}")

    def sort_key(path: Path) -> tuple[int, str | int]:
        match = RAW_LOG_PATTERN.match(path.name)
        if match:
            return (1, f"{match.group(1)}{match.group(2)}")
        return (0, path.stat().st_mtime_ns)

    return max(candidates, key=sort_key)


def gauge_accuracy_mpe_bar(reading_gauge_bar: float) -> float:
    """Return the gauge maximum permissible error at the given reading."""

    scale_fraction = (reading_gauge_bar - GAUGE_RANGE_MIN_BAR) / GAUGE_FULL_SPAN_BAR
    in_end_quarter = (
        scale_fraction <= GAUGE_END_QUARTER_FRACTION
        or scale_fraction >= 1.0 - GAUGE_END_QUARTER_FRACTION
    )
    return 0.02 * GAUGE_FULL_SPAN_BAR if in_end_quarter else 0.01 * GAUGE_FULL_SPAN_BAR


def gauge_sigma_bar(reading_gauge_bar: float) -> float:
    """Return the combined pressure uncertainty from gauge accuracy and resolution."""

    accuracy_sigma_bar = gauge_accuracy_mpe_bar(reading_gauge_bar) / np.sqrt(3.0)
    resolution_sigma_bar = GAUGE_RESOLUTION_BAR / np.sqrt(3.0)
    return float(np.sqrt(accuracy_sigma_bar**2 + resolution_sigma_bar**2))


def vacuum_gauge_accuracy_mpe_inhg(reading_inhg: float) -> float:
    """Return the vacuum-gauge maximum permissible error at the given reading."""

    abs_reading_inhg = abs(reading_inhg)
    quarter_span_inhg = VACUUM_GAUGE_FULL_SPAN_INHG * VACUUM_GAUGE_END_QUARTER_FRACTION
    in_end_quarter = (
        abs_reading_inhg <= quarter_span_inhg
        or abs_reading_inhg >= VACUUM_GAUGE_FULL_SPAN_INHG - quarter_span_inhg
    )
    return (
        0.02 * VACUUM_GAUGE_FULL_SPAN_INHG
        if in_end_quarter
        else 0.01 * VACUUM_GAUGE_FULL_SPAN_INHG
    )


def vacuum_gauge_sigma_inhg(reading_inhg: float) -> float:
    """Return the combined vacuum-gauge uncertainty in inHg."""

    accuracy_sigma_inhg = vacuum_gauge_accuracy_mpe_inhg(reading_inhg) / np.sqrt(3.0)
    resolution_sigma_inhg = VACUUM_GAUGE_RESOLUTION_INHG / np.sqrt(3.0)
    return float(np.sqrt(accuracy_sigma_inhg**2 + resolution_sigma_inhg**2))


def water_vapor_pressure_bar(temp_c: float) -> float:
    """Return the saturation pressure of water in bar using an Antoine fit."""

    clipped_temp_c = np.clip(float(temp_c), -10.0, 100.0)
    antoine_a, antoine_b, antoine_c = 8.07131, 1730.63, 233.426
    pressure_mmhg = 10 ** (antoine_a - antoine_b / (antoine_c + clipped_temp_c))
    return float(pressure_mmhg * MMHG_TO_BAR)


def resolve_hfe_temp_c(temp_c: float | None) -> float:
    """Return a finite HFE property temperature, falling back to 25 C."""

    if temp_c is None:
        return HFE_REFERENCE_TEMP_C
    resolved_temp_c = float(temp_c)
    if not np.isfinite(resolved_temp_c):
        return HFE_REFERENCE_TEMP_C
    return resolved_temp_c


def hfe_liquid_density_kg_m3(temp_c: float | None = None) -> float:
    """Return the HFE-7200 liquid density in kg/m^3."""

    resolved_temp_c = resolve_hfe_temp_c(temp_c)
    density_g_ml = (
        HFE_7200_DENSITY_INTERCEPT_G_ML
        - HFE_7200_DENSITY_SLOPE_G_ML_PER_C * resolved_temp_c
    )
    if density_g_ml <= 0.0:
        raise ValueError(f"HFE-7200 density became non-physical at {resolved_temp_c:.1f} C.")
    return float(1000.0 * density_g_ml)


def hfe_vapor_pressure_bar(temp_c: float | None = None) -> float:
    """Return the HFE-7200 vapor pressure in bar abs."""

    resolved_temp_c = resolve_hfe_temp_c(temp_c)
    pressure_pa = np.exp(
        HFE_7200_VAPOR_PRESSURE_LOG_INTERCEPT
        - HFE_7200_VAPOR_PRESSURE_LOG_SLOPE_K / (resolved_temp_c + 273.0)
    )
    return float(pressure_pa / 1.0e5)


def hfe_liquid_loss_l_per_year_per_mbar_l_s(temp_c: float | None = None) -> float:
    """Convert HFE-7200 vapor throughput into liquid-equivalent loss."""

    resolved_temp_c = resolve_hfe_temp_c(temp_c)
    liquid_density_kg_m3 = hfe_liquid_density_kg_m3(resolved_temp_c)
    return float(
        0.1
        * HFE_7200_MOLAR_MASS_KG_PER_MOL
        * 1000.0
        * SECONDS_PER_YEAR
        / (
            GAS_CONSTANT_J_PER_MOL_K
            * (resolved_temp_c + 273.15)
            * liquid_density_kg_m3
        )
    )


def resolve_pressure_log_path(input_path: str | Path | None) -> Path:
    """Resolve an optional input path relative to the repo or raw-data dir."""

    if input_path is None:
        return latest_pressure_log()

    candidate = Path(input_path)
    if not candidate.is_absolute():
        repo_candidate = REPO_ROOT / candidate
        if repo_candidate.exists():
            candidate = repo_candidate
        else:
            raw_candidate = RAW_DATA_DIR / candidate
            if raw_candidate.exists():
                candidate = raw_candidate

    candidate = candidate.resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Input CSV not found: {candidate}")
    return candidate


def default_system_pressure_data_path(
    csv_path: Path,
    output_dir: Path = DEFAULT_LEAK_DATA_DIR,
) -> Path:
    """Return the default processed-data path for a system-pressure analysis."""

    return output_dir / f"{csv_path.stem}_tank_pressure_evolution_n2_gas_trap.csv"


def default_reservoir_data_path(
    case: ReservoirLeakCase,
    output_dir: Path = DEFAULT_LEAK_DATA_DIR,
) -> Path:
    """Return the default processed-data path for a reservoir leak test."""

    return output_dir / f"reservoir_leak_test_{case.slug}.csv"


def default_reservoir_pressure_log_data_path(
    result: ReservoirPressureLogResult,
    output_dir: Path = DEFAULT_LEAK_DATA_DIR,
) -> Path:
    """Return the default processed-data path for a logged reservoir pressure analysis."""

    return output_dir / f"reservoir_pressure_drop_{result.slug}.csv"


def default_vacuum_rate_of_rise_data_path(
    case: VacuumRateOfRiseCase,
    output_dir: Path = DEFAULT_LEAK_DATA_DIR,
) -> Path:
    """Return the default processed-data path for a vacuum rate-of-rise test."""

    return output_dir / f"vacuum_rate_of_rise_{case.slug}.csv"


def _padded_numeric_frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    """Return a DataFrame from unequal-length numeric arrays padded with NaNs."""

    if not columns:
        return pd.DataFrame()

    max_length = max(len(values) for values in columns.values())
    frame_data: dict[str, np.ndarray] = {}
    for name, values in columns.items():
        array = np.asarray(values, dtype=float)
        padded = np.full(max_length, np.nan, dtype=float)
        padded[: len(array)] = array
        frame_data[name] = padded
    return pd.DataFrame(frame_data)


def _write_processed_frame(frame: pd.DataFrame, output_path: Path) -> Path:
    """Write a processed-data frame to disk and return its resolved path."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    return output_path


def system_pressure_plot_data(result: SystemPressureResult) -> pd.DataFrame:
    """Return the processed data needed to recreate a system-pressure plot."""

    columns = {
        "averaged_time_h": result.averaged.time_h,
        "averaged_pressure_bar_abs": result.averaged.pressure_abs_bar,
        "averaged_pressure_sigma_bar": result.averaged.pressure_sigma_bar,
    }
    if result.fit_curve_time_h is not None and result.fit_curve_pressure_bar is not None:
        columns["fit_time_h"] = result.fit_curve_time_h
        columns["fit_pressure_bar_abs"] = result.fit_curve_pressure_bar
    return _padded_numeric_frame(columns)


def reservoir_leak_plot_data(result: ReservoirLeakResult) -> pd.DataFrame:
    """Return the processed data needed to recreate a reservoir leak plot."""

    return _padded_numeric_frame(
        {
            "measurement_time_h": result.case.time_h,
            "measurement_pressure_bar_abs": result.case.pressure_abs_bar,
            "measurement_sigma_bar": result.measurement_sigma_bar,
            "fit_time_h": result.fit_curve_time_h,
            "fit_pressure_bar_abs": result.fit_curve_pressure_bar,
            "fit_sigma_bar": result.fit_curve_sigma_bar,
        }
    )


def reservoir_pressure_log_plot_data(result: ReservoirPressureLogResult) -> pd.DataFrame:
    """Return the processed data needed to recreate a reservoir pressure-log plot."""

    columns = {
        "averaged_time_h": result.averaged.time_h,
        "averaged_pressure_bar_abs": result.averaged.pressure_abs_bar,
        "averaged_pressure_sigma_bar": result.averaged.pressure_sigma_bar,
    }
    if result.fit_curve_time_h is not None and result.fit_curve_pressure_bar is not None:
        columns["fit_time_h"] = result.fit_curve_time_h
        columns["fit_pressure_bar_abs"] = result.fit_curve_pressure_bar
    return _padded_numeric_frame(columns)


def vacuum_rate_of_rise_plot_data(result: VacuumRateOfRiseResult) -> pd.DataFrame:
    """Return the processed data needed to recreate a vacuum rate-of-rise plot."""

    return _padded_numeric_frame(
        {
            "measurement_time_h": result.case.time_h,
            "measurement_pressure_bar_abs": result.case.pressure_abs_bar,
            "measurement_sigma_bar": result.case.measurement_sigma_bar,
            "fit_time_h": result.fit_curve_time_h,
            "fit_pressure_bar_abs": result.fit_curve_pressure_bar,
            "fit_sigma_bar": result.fit_curve_sigma_bar,
            "water_saturation_time_h": result.fit_curve_time_h,
            "water_saturation_bar_abs": np.full_like(
                result.fit_curve_time_h,
                result.water_vapor.saturation_pressure_bar,
                dtype=float,
            ),
        }
    )


def export_system_pressure_plot_data(
    result: SystemPressureResult,
    output_path: Path | None = None,
) -> Path:
    """Export the processed data used by a system-pressure plot."""

    export_path = (
        default_system_pressure_data_path(result.series.csv_path)
        if output_path is None
        else output_path
    )
    return _write_processed_frame(system_pressure_plot_data(result), export_path)


def export_reservoir_leak_plot_data(
    result: ReservoirLeakResult,
    output_path: Path | None = None,
) -> Path:
    """Export the processed data used by a reservoir leak plot."""

    export_path = default_reservoir_data_path(result.case) if output_path is None else output_path
    return _write_processed_frame(reservoir_leak_plot_data(result), export_path)


def export_reservoir_pressure_log_plot_data(
    result: ReservoirPressureLogResult,
    output_path: Path | None = None,
) -> Path:
    """Export the processed data used by a reservoir pressure-log plot."""

    export_path = (
        default_reservoir_pressure_log_data_path(result)
        if output_path is None
        else output_path
    )
    return _write_processed_frame(reservoir_pressure_log_plot_data(result), export_path)


def export_vacuum_rate_of_rise_plot_data(
    result: VacuumRateOfRiseResult,
    output_path: Path | None = None,
) -> Path:
    """Export the processed data used by a vacuum rate-of-rise plot."""

    export_path = (
        default_vacuum_rate_of_rise_data_path(result.case)
        if output_path is None
        else output_path
    )
    return _write_processed_frame(vacuum_rate_of_rise_plot_data(result), export_path)


def load_pressure_series(
    csv_path: Path,
    *,
    pressure_abs_column: str = PRESSURE_ABS_COLUMN,
    pressure_gauge_column: str | None = PRESSURE_GAUGE_COLUMN,
    pressure_error_column: str = PRESSURE_ERR_COLUMN,
    room_temp_column: str = ROOM_TEMP_COLUMN,
) -> PressureSeries:
    """Load the pressure columns needed for the system leak analysis."""

    required_columns = [TIME_COLUMN, pressure_abs_column, pressure_error_column]
    if pressure_gauge_column is not None:
        required_columns.append(pressure_gauge_column)
    if room_temp_column is not None:
        required_columns.append(room_temp_column)

    frame = pd.read_csv(csv_path, comment="#").dropna(subset=required_columns)
    time_h = ((frame[TIME_COLUMN] - frame[TIME_COLUMN].iloc[0]) / 3600.0).to_numpy(dtype=float)
    pressure_abs_bar = frame[pressure_abs_column].to_numpy(dtype=float)
    if pressure_gauge_column is None:
        pressure_gauge_bar = pressure_abs_bar - P_ATM_BAR
    else:
        pressure_gauge_bar = frame[pressure_gauge_column].to_numpy(dtype=float)
    if room_temp_column is None:
        room_temp_c = np.nan
    else:
        room_temp_c = float(frame[room_temp_column].mean())

    return PressureSeries(
        csv_path=csv_path,
        time_h=time_h,
        pressure_abs_bar=pressure_abs_bar,
        pressure_error_bar=float(frame[pressure_error_column].iloc[0]),
        room_temp_c=room_temp_c,
        initial_gauge_bar=float(pressure_gauge_bar[0]),
    )


def slugify_case_label(label: str) -> str:
    """Return a filesystem-friendly slug derived from a case label."""

    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def build_vacuum_rate_of_rise_case(  # pylint: disable=too-many-arguments
    label: str,
    data_inhg: Sequence[tuple[str, float]],
    volume_l: float,
    system_temp_c: float,
    source_note: str = "",
    *,
    slug: str | None = None,
) -> VacuumRateOfRiseCase:
    """Build a vacuum rate-of-rise case from timestamped gauge readings in inHg."""

    frame = pd.DataFrame(data_inhg, columns=["timestamp", "pressure_inhg"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if len(frame) < 2:
        raise ValueError("Vacuum rate-of-rise analysis requires at least two measurements.")

    timestamp_ns = frame["timestamp"].astype("int64").to_numpy(dtype=np.int64)
    time_h = (timestamp_ns - timestamp_ns[0]) / (1.0e9 * SECONDS_PER_HOUR)
    pressure_inhg = frame["pressure_inhg"].to_numpy(dtype=float)
    pressure_abs_bar = P_ATM_BAR + pressure_inhg * INHG_TO_BAR
    measurement_sigma_inhg = np.array(
        [vacuum_gauge_sigma_inhg(reading) for reading in pressure_inhg],
        dtype=float,
    )
    measurement_sigma_bar = measurement_sigma_inhg * INHG_TO_BAR

    return VacuumRateOfRiseCase(
        label=label,
        slug=slug if slug is not None else slugify_case_label(label),
        time_h=time_h,
        pressure_abs_bar=pressure_abs_bar,
        pressure_inhg=pressure_inhg,
        measurement_sigma_bar=measurement_sigma_bar,
        measurement_sigma_inhg=measurement_sigma_inhg,
        volume_l=float(volume_l),
        system_temp_c=float(system_temp_c),
        source_note=source_note,
    )


def build_reservoir_case_from_log(  # pylint: disable=too-many-arguments
    csv_path: str | Path,
    label: str,
    volume_l: float,
    operating_gauge_bar: float,
    *,
    pressure_abs_column: str = "pump_pressure_after_bar_abs",
    source_note: str = "",
    slug: str | None = None,
    x_max_h: float | None = None,
    y_max_bar: float | None = None,
    bin_width_min: float = BIN_WIDTH_MIN,
    asymptote_bar: float = P_ATM_BAR,
    tail_margin_bar: float | None = None,
) -> ReservoirLeakCase:
    """Build a reservoir leak case from a logged absolute-pressure channel."""

    resolved_path = resolve_pressure_log_path(csv_path)
    frame = pd.read_csv(resolved_path, comment="#").dropna(
        subset=[TIME_COLUMN, pressure_abs_column, PRESSURE_ERR_COLUMN]
    )
    if len(frame) < 2:
        raise ValueError("Reservoir log analysis requires at least two pressure samples.")

    time_h = (
        (frame[TIME_COLUMN] - frame[TIME_COLUMN].iloc[0]) / SECONDS_PER_HOUR
    ).to_numpy(dtype=float)
    pressure_abs_bar = frame[pressure_abs_column].to_numpy(dtype=float)
    pressure_err_bar = float(frame[PRESSURE_ERR_COLUMN].iloc[0])
    resolved_tail_margin_bar = pressure_err_bar if tail_margin_bar is None else float(tail_margin_bar)

    averaged = make_weighted_average_trace(
        time_h,
        pressure_abs_bar,
        pressure_err_bar,
        bin_width_min=bin_width_min,
    )
    case_time_h = averaged.time_h
    case_pressure_abs_bar = averaged.pressure_abs_bar
    if case_time_h.size < 2:
        raise ValueError("Reservoir log averaging produced fewer than two fit points.")

    cutoff_indices = np.flatnonzero(
        case_pressure_abs_bar <= asymptote_bar + resolved_tail_margin_bar
    )
    if cutoff_indices.size > 0:
        cutoff_index = int(cutoff_indices[0])
        case_time_h = case_time_h[:cutoff_index]
        case_pressure_abs_bar = case_pressure_abs_bar[:cutoff_index]
    if case_time_h.size < 2:
        raise ValueError(
            "Reservoir log must stay above the fixed asymptote long enough to fit a decay."
        )

    resolved_slug = slug if slug is not None else slugify_case_label(label)
    resolved_x_max_h = (
        max(DEFAULT_RESERVOIR_XMAX_H, float(time_h[-1]) * 1.02)
        if x_max_h is None
        else float(x_max_h)
    )
    resolved_y_max_bar = (
        max(DEFAULT_RESERVOIR_YMAX_BAR, float(np.max(case_pressure_abs_bar)) * 1.05)
        if y_max_bar is None
        else float(y_max_bar)
    )
    default_source_note = (
        f"Raw log: {resolved_path.name}; column: {pressure_abs_column}; "
        f"{bin_width_min:.1f} min averages"
    )
    if cutoff_indices.size > 0:
        default_source_note += (
            f"; fit stops before first <= {asymptote_bar + resolved_tail_margin_bar:.3f} bar abs"
        )

    return ReservoirLeakCase(
        label=label,
        slug=resolved_slug,
        time_h=case_time_h,
        pressure_abs_bar=case_pressure_abs_bar,
        volume_l=float(volume_l),
        operating_gauge_bar=float(operating_gauge_bar),
        x_max_h=resolved_x_max_h,
        y_max_bar=resolved_y_max_bar,
        source_note=source_note or default_source_note,
    )


def weighted_sample_std(values: np.ndarray, weights: np.ndarray) -> float:
    """Return the weighted sample standard deviation."""

    if values.size <= 1:
        return 0.0

    mean = np.sum(weights * values) / np.sum(weights)
    numerator = np.sum(weights * (values - mean) ** 2)
    denominator = np.sum(weights) - np.sum(weights**2) / np.sum(weights)
    if denominator <= 0.0:
        return 0.0
    return float(np.sqrt(max(numerator / denominator, 0.0)))

# pylint: disable=too-many-locals
def make_weighted_average_trace(
    time_h: np.ndarray,
    pressure_abs_bar: np.ndarray,
    pressure_err_bar: float,
    bin_width_min: float = BIN_WIDTH_MIN,
) -> AveragedTrace:
    """Bin the raw pressure trace in time and compute weighted means."""

    bin_width_h = bin_width_min / 60.0
    bin_edges_h = np.arange(0.0, float(time_h[-1]) + bin_width_h, bin_width_h)
    if bin_edges_h[-1] < time_h[-1]:
        bin_edges_h = np.append(bin_edges_h, time_h[-1])

    bin_index = np.digitize(time_h, bin_edges_h, right=False) - 1
    time_mean_h: list[float] = []
    pressure_mean_bar: list[float] = []
    pressure_sigma_bar: list[float] = []

    for index in range(len(bin_edges_h) - 1):
        mask = bin_index == index
        if not np.any(mask):
            continue

        time_bin_h = time_h[mask]
        pressure_bin_bar = pressure_abs_bar[mask]
        weights = np.full_like(pressure_bin_bar, 1.0 / (pressure_err_bar**2), dtype=float)

        time_mean_h.append(float(np.sum(weights * time_bin_h) / np.sum(weights)))
        pressure_mean = float(np.sum(weights * pressure_bin_bar) / np.sum(weights))
        pressure_mean_bar.append(pressure_mean)

        sigma_bar = weighted_sample_std(pressure_bin_bar, weights)
        pressure_sigma_bar.append(pressure_err_bar if sigma_bar == 0.0 else sigma_bar)

    return AveragedTrace(
        time_h=np.array(time_mean_h, dtype=float),
        pressure_abs_bar=np.array(pressure_mean_bar, dtype=float),
        pressure_sigma_bar=np.array(pressure_sigma_bar, dtype=float),
    )


def fixed_tail_exponential_model(
    time_h: np.ndarray,
    amplitude_bar: float,
    k_per_h: float,
    *,
    asymptote_bar: float = P_ATM_BAR,
) -> np.ndarray:
    """Exponential decay toward a fixed asymptotic pressure."""

    return asymptote_bar + amplitude_bar * np.exp(k_per_h * time_h)


def fit_fixed_tail_exponential_with_band(
    time_h: np.ndarray,
    pressure_abs_bar: np.ndarray,
    pressure_err_bar: np.ndarray,
    x_eval_h: np.ndarray,
    *,
    asymptote_bar: float = P_ATM_BAR,
) -> tuple[FixedTailExponentialFit, np.ndarray, np.ndarray]:
    """Fit ``P_abs(t) = P_inf + A exp(k t)`` with a fixed asymptote."""

    pressure_gauge_bar = pressure_abs_bar - asymptote_bar
    if np.any(pressure_gauge_bar <= 0.0):
        raise ValueError("Fixed-tail exponential fit requires pressure above the asymptote.")

    design = np.column_stack((np.ones_like(time_h), time_h))
    log_pressure = np.log(pressure_gauge_bar)
    sigma_log = np.maximum(np.asarray(pressure_err_bar, dtype=float) / pressure_gauge_bar, 1e-12)
    weights = 1.0 / np.square(sigma_log)
    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_log_pressure = log_pressure * np.sqrt(weights)
    normal_matrix = weighted_design.T @ weighted_design
    cov_log_amplitude_k = np.linalg.inv(normal_matrix)
    params = np.linalg.solve(normal_matrix, weighted_design.T @ weighted_log_pressure)
    log_amplitude_bar = float(params[0])
    k_per_h = float(params[1])

    amplitude_bar = float(np.exp(log_amplitude_bar))
    sigma_log_amplitude = float(np.sqrt(max(cov_log_amplitude_k[0, 0], 0.0)))
    amplitude_err_bar = float(amplitude_bar * sigma_log_amplitude)
    k_err_per_h = float(np.sqrt(max(cov_log_amplitude_k[1, 1], 0.0)))

    log_fit = log_amplitude_bar + k_per_h * x_eval_h
    pressure_gauge_fit_bar = np.exp(log_fit)
    pressure_abs_fit_bar = fixed_tail_exponential_model(
        x_eval_h,
        amplitude_bar,
        k_per_h,
        asymptote_bar=asymptote_bar,
    )

    design_eval = np.column_stack((np.ones_like(x_eval_h), x_eval_h))
    log_variance = np.einsum("ij,jk,ik->i", design_eval, cov_log_amplitude_k, design_eval)
    pressure_abs_sigma_bar = pressure_gauge_fit_bar * np.sqrt(np.clip(log_variance, 0.0, None))

    fit = FixedTailExponentialFit(
        asymptote_bar=asymptote_bar,
        amplitude_bar=amplitude_bar,
        amplitude_err_bar=amplitude_err_bar,
        k_per_h=float(k_per_h),
        k_err_per_h=k_err_per_h,
        cov_log_amplitude_k=cov_log_amplitude_k,
    )
    return fit, pressure_abs_fit_bar, pressure_abs_sigma_bar


def fixed_tail_fit_to_exponential_fit(fit: FixedTailExponentialFit) -> ExponentialFit:
    """Convert a fixed-tail exponential fit into the shared exponential-fit container."""

    tau_h = -1.0 / fit.k_per_h
    tau_err_h = fit.k_err_per_h / (fit.k_per_h**2)
    return ExponentialFit(
        asymptote_bar=fit.asymptote_bar,
        asymptote_err_bar=0.0,
        amplitude_bar=fit.amplitude_bar,
        amplitude_err_bar=fit.amplitude_err_bar,
        k_per_h=fit.k_per_h,
        k_err_per_h=fit.k_err_per_h,
        tau_h=float(tau_h),
        tau_err_h=float(tau_err_h),
    )


def fit_linear_pressure_rise_with_band(
    time_h: np.ndarray,
    pressure_abs_bar: np.ndarray,
    pressure_err_bar: np.ndarray,
    x_eval_h: np.ndarray,
) -> tuple[LinearPressureRiseFit, np.ndarray, np.ndarray]:
    """Fit ``P_abs(t) = intercept + slope * t`` and return the prediction band."""

    sigma_bar = np.maximum(np.asarray(pressure_err_bar, dtype=float), 1e-12)
    design = np.column_stack((np.ones_like(time_h), time_h))
    weights = 1.0 / np.square(sigma_bar)
    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_pressure = pressure_abs_bar * np.sqrt(weights)
    normal_matrix = weighted_design.T @ weighted_design
    covariance = np.linalg.inv(normal_matrix)
    params = np.linalg.solve(normal_matrix, weighted_design.T @ weighted_pressure)

    fit = LinearPressureRiseFit(
        intercept_bar=float(params[0]),
        intercept_err_bar=float(np.sqrt(covariance[0, 0])),
        slope_bar_per_h=float(params[1]),
        slope_err_bar_per_h=float(np.sqrt(covariance[1, 1])),
        covariance=covariance,
    )

    design_eval = np.column_stack((np.ones_like(x_eval_h), x_eval_h))
    pressure_fit_bar = fit.intercept_bar + fit.slope_bar_per_h * x_eval_h
    pressure_fit_var = np.einsum("ij,jk,ik->i", design_eval, covariance, design_eval)
    pressure_fit_sigma_bar = np.sqrt(np.clip(pressure_fit_var, 0.0, None))
    return fit, pressure_fit_bar, pressure_fit_sigma_bar


def compute_mean_system_pressure(hfe_temp_c: float | None = None) -> MeanSystemPressure:
    """Return the mean hydrostatic pressure assumed for the HFE system."""

    liquid_density_kg_m3 = hfe_liquid_density_kg_m3(hfe_temp_c)
    hydrostatic_delta_bar = liquid_density_kg_m3 * 9.81 * SYSTEM_HEIGHT_M / 1.0e5
    required_fill_bar = (
        liquid_density_kg_m3
        * 9.81
        * max(SYSTEM_HEIGHT_M - TANK_HEIGHT_M, 0.0)
        / 1.0e5
    )
    return MeanSystemPressure(
        absolute_bar=P_ATM_BAR + hydrostatic_delta_bar / 2.0,
        gauge_bar=hydrostatic_delta_bar / 2.0,
        hydrostatic_delta_bar=hydrostatic_delta_bar,
        required_tank_fill_gauge_bar=required_fill_bar,
    )


def compute_system_top_gas_trap_operating_case(
    hfe_temp_c: float | None = None,
) -> TopGasTrapOperatingCase:
    """Return the top gas-trap operating state for the system vapor-leak estimate."""

    hfe_vapor_partial_pressure_bar = hfe_vapor_pressure_bar(hfe_temp_c)
    n2_partial_pressure_abs_bar = SYSTEM_GAS_TRAP_N2_PARTIAL_PRESSURE_BAR
    total_pressure_abs_bar = n2_partial_pressure_abs_bar + hfe_vapor_partial_pressure_bar
    hfe_vapor_mole_fraction = hfe_vapor_partial_pressure_bar / max(total_pressure_abs_bar, 1e-12)
    return TopGasTrapOperatingCase(
        total_pressure_abs_bar=total_pressure_abs_bar,
        gas_trap_volume_l=GAS_TRAP_VOLUME_L,
        hfe_vapor_pressure_abs_bar=hfe_vapor_partial_pressure_bar,
        n2_partial_pressure_abs_bar=n2_partial_pressure_abs_bar,
        hfe_vapor_mole_fraction=hfe_vapor_mole_fraction,
    )


def compute_hfe_equivalent_leak(
    k_per_h: float,
    k_err_per_h: float,
    driving_pressure_bar: float,
    leak_test_volume_l: float = NITROGEN_LEAK_TEST_VOLUME_L,
    *,
    hfe_temp_c: float | None = None,
    upper_limit_confidence_level: float | None = None,
    prefer_upper_limit_when_consistent_with_zero: bool = False,
) -> LeakEstimate:
    """Convert the fitted decay constant into HFE-equivalent leak numbers."""

    decay_bar_per_h = -k_per_h * driving_pressure_bar
    decay_err_bar_per_h = k_err_per_h * abs(driving_pressure_bar)
    throughput_mbar_l_per_s = leak_test_volume_l * decay_bar_per_h * 1000.0 / 3600.0
    throughput_err_mbar_l_per_s = leak_test_volume_l * decay_err_bar_per_h * 1000.0 / 3600.0
    hfe_loss_factor = hfe_liquid_loss_l_per_year_per_mbar_l_s(hfe_temp_c)
    upper_limit_throughput_mbar_l_per_s: float | None = None
    upper_limit_hfe_loss_l_per_year: float | None = None
    is_upper_limit_only = False
    if upper_limit_confidence_level is not None:
        z_value = NormalDist().inv_cdf(float(upper_limit_confidence_level))
        upper_limit_decay_bar_per_h = max(
            0.0,
            decay_bar_per_h + z_value * decay_err_bar_per_h,
        )
        upper_limit_throughput_mbar_l_per_s = (
            leak_test_volume_l * upper_limit_decay_bar_per_h * 1000.0 / 3600.0
        )
        upper_limit_hfe_loss_l_per_year = (
            upper_limit_throughput_mbar_l_per_s * hfe_loss_factor
        )
        is_upper_limit_only = prefer_upper_limit_when_consistent_with_zero and (
            decay_bar_per_h <= z_value * decay_err_bar_per_h
        )
    return LeakEstimate(
        throughput_mbar_l_per_s=throughput_mbar_l_per_s,
        throughput_err_mbar_l_per_s=throughput_err_mbar_l_per_s,
        hfe_loss_l_per_year=throughput_mbar_l_per_s * hfe_loss_factor,
        hfe_loss_err_l_per_year=throughput_err_mbar_l_per_s * hfe_loss_factor,
        upper_limit_throughput_mbar_l_per_s=upper_limit_throughput_mbar_l_per_s,
        upper_limit_hfe_loss_l_per_year=upper_limit_hfe_loss_l_per_year,
        upper_limit_confidence_level=upper_limit_confidence_level,
        is_upper_limit_only=is_upper_limit_only,
    )


def compute_vacuum_gas_load(
    slope_bar_per_h: float,
    slope_err_bar_per_h: float,
    volume_l: float,
    *,
    hfe_temp_c: float | None = None,
) -> GasLoadEstimate:
    """Convert a rate-of-rise slope into gas throughput."""

    throughput_mbar_l_per_s = volume_l * slope_bar_per_h * 1000.0 / SECONDS_PER_HOUR
    throughput_err_mbar_l_per_s = volume_l * slope_err_bar_per_h * 1000.0 / SECONDS_PER_HOUR
    hfe_loss_factor = hfe_liquid_loss_l_per_year_per_mbar_l_s(hfe_temp_c)
    return GasLoadEstimate(
        throughput_mbar_l_per_s=float(throughput_mbar_l_per_s),
        throughput_err_mbar_l_per_s=float(throughput_err_mbar_l_per_s),
        hfe_loss_l_per_year=float(throughput_mbar_l_per_s * hfe_loss_factor),
        hfe_loss_err_l_per_year=float(
            throughput_err_mbar_l_per_s * hfe_loss_factor
        ),
    )


def compute_start_decay_metrics(fit: FixedTailExponentialFit) -> tuple[float, float]:
    """Return the initial pressure-decay rate and propagated uncertainty."""

    start_decay_bar_per_h = -fit.k_per_h * fit.amplitude_bar
    start_decay_gradient = np.array(
        [
            start_decay_bar_per_h,
            -fit.amplitude_bar,
        ],
        dtype=float,
    )
    start_decay_err_bar_per_h = float(
        np.sqrt(
            max(
                start_decay_gradient @ fit.cov_log_amplitude_k @ start_decay_gradient,
                0.0,
            )
        )
    )
    return 1000.0 * start_decay_bar_per_h, 1000.0 * start_decay_err_bar_per_h


def linear_pressure_drop_metrics(fit: LinearPressureRiseFit) -> tuple[float, float]:
    """Return the fitted linear pressure-drop rate as positive mbar/h for a decay."""

    return -1000.0 * fit.slope_bar_per_h, 1000.0 * fit.slope_err_bar_per_h


def rmse_bar(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Return the root-mean-square error in bar."""

    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def compare_with_water_vapor(
    pressure_abs_bar: np.ndarray,
    system_temp_c: float,
) -> WaterVaporComparison:
    """Compare measured absolute pressure against room-temperature water saturation."""

    saturation_pressure_bar = water_vapor_pressure_bar(system_temp_c)
    start_pressure_bar = float(pressure_abs_bar[0])
    end_pressure_bar = float(pressure_abs_bar[-1])
    if saturation_pressure_bar <= 0.0:
        start_ratio = np.inf
        end_ratio = np.inf
    else:
        start_ratio = start_pressure_bar / saturation_pressure_bar
        end_ratio = end_pressure_bar / saturation_pressure_bar
    return WaterVaporComparison(
        saturation_pressure_bar=saturation_pressure_bar,
        start_ratio=float(start_ratio),
        end_ratio=float(end_ratio),
        start_excess_mbar=(start_pressure_bar - saturation_pressure_bar) * 1000.0,
        end_excess_mbar=(end_pressure_bar - saturation_pressure_bar) * 1000.0,
    )


def pressure_drop_fit_warning(
    averaged: AveragedTrace,
    fit: ExponentialFit,
    *,
    label: str,
) -> str | None:
    """Return a warning when a pressure-drop fit is not meaningfully constrained."""

    net_drop_bar = float(averaged.pressure_abs_bar[0] - averaged.pressure_abs_bar[-1])
    endpoint_sigma_bar = float(
        np.hypot(averaged.pressure_sigma_bar[0], averaged.pressure_sigma_bar[-1])
    )
    if net_drop_bar <= endpoint_sigma_bar:
        return f"{label}: no clear pressure drop; fit skipped."
    if fit.k_per_h >= 0.0:
        return f"{label}: pressure-drop fit not constrained; fit skipped."
    return None


def analyze_pressure_drop_log(  # pylint: disable=too-many-arguments
    csv_path: str | Path,
    *,
    pressure_abs_column: str,
    pressure_gauge_column: str | None,
    pressure_error_column: str,
    room_temp_column: str | None,
    bin_width_min: float,
    warning_label: str,
) -> tuple[
    PressureSeries,
    AveragedTrace,
    ExponentialFit | None,
    np.ndarray | None,
    np.ndarray | None,
    float | None,
    str | None,
]:
    """Run the shared averaging and fit path for a logged pressure-drop trace."""

    resolved_path = resolve_pressure_log_path(csv_path)
    series = load_pressure_series(
        resolved_path,
        pressure_abs_column=pressure_abs_column,
        pressure_gauge_column=pressure_gauge_column,
        pressure_error_column=pressure_error_column,
        room_temp_column=room_temp_column,
    )
    averaged = make_weighted_average_trace(
        series.time_h,
        series.pressure_abs_bar,
        series.pressure_error_bar,
        bin_width_min=bin_width_min,
    )

    fit: ExponentialFit | None = None
    fit_curve_time_h: np.ndarray | None = None
    fit_curve_pressure_bar: np.ndarray | None = None
    rmse_mbar: float | None = None
    warning_message: str | None = None

    try:
        candidate_curve_time_h = np.linspace(0.0, float(averaged.time_h[-1]), 500)
        fixed_tail_fit, candidate_curve_pressure_bar, _ = fit_fixed_tail_exponential_with_band(
            averaged.time_h,
            averaged.pressure_abs_bar,
            averaged.pressure_sigma_bar,
            candidate_curve_time_h,
            asymptote_bar=P_ATM_BAR,
        )
        candidate_fit = fixed_tail_fit_to_exponential_fit(fixed_tail_fit)
        warning_message = pressure_drop_fit_warning(
            averaged,
            candidate_fit,
            label=warning_label,
        )
        if warning_message is None:
            fit = candidate_fit
            fit_curve_time_h = candidate_curve_time_h
            fit_curve_pressure_bar = candidate_curve_pressure_bar
            averaged_fit_bar = fixed_tail_exponential_model(
                averaged.time_h,
                fit.amplitude_bar,
                fit.k_per_h,
                asymptote_bar=fit.asymptote_bar,
            )
            rmse_mbar = 1000.0 * rmse_bar(averaged.pressure_abs_bar, averaged_fit_bar)
    except (np.linalg.LinAlgError, RuntimeError, ValueError) as exc:
        warning_message = f"{warning_label}: fit failed ({exc}); showing measurements only."

    if warning_message is not None:
        warnings.warn(warning_message, stacklevel=2)

    return (
        series,
        averaged,
        fit,
        fit_curve_time_h,
        fit_curve_pressure_bar,
        rmse_mbar,
        warning_message,
    )


def analyze_system_pressure_log(csv_path: str | Path) -> SystemPressureResult:
    """Run the full system-pressure leak analysis for a CSV log."""

    (
        series,
        averaged,
        fit,
        fit_curve_time_h,
        fit_curve_pressure_bar,
        rmse_mbar,
        warning_message,
    ) = analyze_pressure_drop_log(
        csv_path,
        pressure_abs_column=PRESSURE_ABS_COLUMN,
        pressure_gauge_column=PRESSURE_GAUGE_COLUMN,
        pressure_error_column=PRESSURE_ERR_COLUMN,
        room_temp_column=ROOM_TEMP_COLUMN,
        bin_width_min=BIN_WIDTH_MIN,
        warning_label="System",
    )
    mean_pressure = compute_mean_system_pressure(series.room_temp_c)
    top_gas_trap = compute_system_top_gas_trap_operating_case(series.room_temp_c)
    leak = None
    if fit is not None:
        leak = compute_hfe_equivalent_leak(
            fit.k_per_h,
            fit.k_err_per_h,
            top_gas_trap.hfe_vapor_pressure_abs_bar,
            hfe_temp_c=series.room_temp_c,
        )
    return SystemPressureResult(
        series=series,
        averaged=averaged,
        fit=fit,
        leak=leak,
        mean_pressure=mean_pressure,
        top_gas_trap=top_gas_trap,
        fit_curve_time_h=fit_curve_time_h,
        fit_curve_pressure_bar=fit_curve_pressure_bar,
        rmse_mbar=rmse_mbar,
        warning=warning_message,
    )


def analyze_reservoir_pressure_log(  # pylint: disable=too-many-arguments
    csv_path: str | Path,
    label: str,
    volume_l: float,
    operating_gauge_bar: float,
    *,
    pressure_abs_column: str = "pump_pressure_after_bar_abs",
    pressure_gauge_column: str | None = "pump_pressure_after_bar",
    pressure_error_column: str = PRESSURE_ERR_COLUMN,
    room_temp_column: str = ROOM_TEMP_COLUMN,
    source_note: str = "",
    slug: str | None = None,
    bin_width_min: float = BIN_WIDTH_MIN,
) -> ReservoirPressureLogResult:
    """Run a system-style pressure-drop analysis for a logged reservoir test."""

    resolved_slug = slug if slug is not None else slugify_case_label(label)
    (
        series,
        averaged,
        fit,
        fit_curve_time_h,
        fit_curve_pressure_bar,
        rmse_mbar,
        warning_message,
    ) = analyze_pressure_drop_log(
        csv_path,
        pressure_abs_column=pressure_abs_column,
        pressure_gauge_column=pressure_gauge_column,
        pressure_error_column=pressure_error_column,
        room_temp_column=room_temp_column,
        bin_width_min=bin_width_min,
        warning_label=label,
    )
    default_source_note = f"Source log: {series.csv_path.name}"

    linear_fit: LinearPressureRiseFit | None = None
    linear_fit_curve_time_h: np.ndarray | None = None
    linear_fit_curve_pressure_bar: np.ndarray | None = None
    try:
        linear_fit_curve_time_h = np.linspace(0.0, float(averaged.time_h[-1]), 500)
        linear_fit, linear_fit_curve_pressure_bar, _ = fit_linear_pressure_rise_with_band(
            averaged.time_h,
            averaged.pressure_abs_bar,
            averaged.pressure_sigma_bar,
            linear_fit_curve_time_h,
        )
    except np.linalg.LinAlgError:
        linear_fit = None
        linear_fit_curve_time_h = None
        linear_fit_curve_pressure_bar = None

    leak = None
    if fit is not None:
        leak = compute_hfe_equivalent_leak(
            fit.k_per_h,
            fit.k_err_per_h,
            operating_gauge_bar,
            leak_test_volume_l=volume_l,
            hfe_temp_c=series.room_temp_c,
        )

    display_fit_curve_time_h = fit_curve_time_h
    display_fit_curve_pressure_bar = fit_curve_pressure_bar
    display_fit_curve_label = "Exponential fit" if fit_curve_time_h is not None else None
    if fit is None and linear_fit_curve_time_h is not None and linear_fit_curve_pressure_bar is not None:
        display_fit_curve_time_h = linear_fit_curve_time_h
        display_fit_curve_pressure_bar = linear_fit_curve_pressure_bar
        display_fit_curve_label = "Linear fit"
        fitted_pressure_bar = linear_fit.intercept_bar + linear_fit.slope_bar_per_h * averaged.time_h
        rmse_mbar = 1000.0 * rmse_bar(averaged.pressure_abs_bar, fitted_pressure_bar)
        if warning_message is not None:
            warning_message = f"{warning_message} Linear fit shown for comparison."

    return ReservoirPressureLogResult(
        label=label,
        slug=resolved_slug,
        source_note=source_note or default_source_note,
        pressure_abs_column=pressure_abs_column,
        volume_l=float(volume_l),
        operating_gauge_bar=float(operating_gauge_bar),
        series=series,
        averaged=averaged,
        fit=fit,
        linear_fit=linear_fit,
        leak=leak,
        fit_curve_time_h=display_fit_curve_time_h,
        fit_curve_pressure_bar=display_fit_curve_pressure_bar,
        fit_curve_label=display_fit_curve_label,
        rmse_mbar=rmse_mbar,
        warning=warning_message,
    )


def analyze_reservoir_case(case: ReservoirLeakCase) -> ReservoirLeakResult:
    """Run the legacy reservoir leak analysis for one O-ring case."""

    fit_curve_time_h = np.linspace(0.0, case.x_max_h, 400)
    pressure_gauge_bar = case.pressure_abs_bar - P_ATM_BAR
    measurement_sigma_bar = np.array(
        [gauge_sigma_bar(reading) for reading in pressure_gauge_bar],
        dtype=float,
    )
    fit, fit_curve_pressure_bar, fit_curve_sigma_bar = fit_fixed_tail_exponential_with_band(
        case.time_h,
        case.pressure_abs_bar,
        measurement_sigma_bar,
        fit_curve_time_h,
        asymptote_bar=P_ATM_BAR,
    )
    leak = compute_hfe_equivalent_leak(
        fit.k_per_h,
        fit.k_err_per_h,
        case.operating_gauge_bar,
        leak_test_volume_l=case.volume_l,
    )
    start_decay_mbar_per_h, start_decay_err_mbar_per_h = compute_start_decay_metrics(fit)
    return ReservoirLeakResult(
        case=case,
        measurement_sigma_bar=measurement_sigma_bar,
        fit=fit,
        leak=leak,
        start_decay_mbar_per_h=start_decay_mbar_per_h,
        start_decay_err_mbar_per_h=start_decay_err_mbar_per_h,
        fit_curve_time_h=fit_curve_time_h,
        fit_curve_pressure_bar=fit_curve_pressure_bar,
        fit_curve_sigma_bar=fit_curve_sigma_bar,
    )


def analyze_vacuum_rate_of_rise_case(case: VacuumRateOfRiseCase) -> VacuumRateOfRiseResult:
    """Run the fixed-volume vacuum rate-of-rise analysis for one case."""

    fit_curve_time_h = np.linspace(0.0, float(case.time_h[-1]) * 1.05, 400)
    fit, fit_curve_pressure_bar, fit_curve_sigma_bar = fit_linear_pressure_rise_with_band(
        case.time_h,
        case.pressure_abs_bar,
        case.measurement_sigma_bar,
        fit_curve_time_h,
    )
    fitted_pressure_bar = fit.intercept_bar + fit.slope_bar_per_h * case.time_h
    gas_load = compute_vacuum_gas_load(
        fit.slope_bar_per_h,
        fit.slope_err_bar_per_h,
        case.volume_l,
        hfe_temp_c=case.system_temp_c,
    )
    water_vapor = compare_with_water_vapor(case.pressure_abs_bar, case.system_temp_c)
    return VacuumRateOfRiseResult(
        case=case,
        fit=fit,
        gas_load=gas_load,
        water_vapor=water_vapor,
        fit_curve_time_h=fit_curve_time_h,
        fit_curve_pressure_bar=fit_curve_pressure_bar,
        fit_curve_sigma_bar=fit_curve_sigma_bar,
        rmse_mbar=1000.0 * rmse_bar(case.pressure_abs_bar, fitted_pressure_bar),
    )


def format_scientific_number(value: float, precision: int = 2) -> str:
    """Format a scalar in scientific notation without appending a unit."""

    if value == 0.0:
        return "0"
    exponent = int(np.floor(np.log10(abs(value))))
    mantissa = value / (10.0**exponent)
    if exponent == 0:
        return rf"${value:.{precision}f}$"
    return rf"${mantissa:.{precision}f} \times 10^{{{exponent}}}$"


def format_scientific_value(value: float, unit: str, precision: int = 2) -> str:
    """Format a value in scientific notation for plot annotations."""

    return f"{format_scientific_number(value, precision)} {unit}"


def format_scientific_value_with_uncertainty(
    value: float,
    uncertainty: float,
    unit: str,
    precision: int = 2,
) -> str:
    """Format a value and uncertainty with a shared power of ten and unit."""

    scale_value = max(abs(value), abs(uncertainty))
    if scale_value == 0.0:
        return rf"$0 \pm 0$ {unit}"
    exponent = int(np.floor(np.log10(scale_value)))
    scale = 10.0**exponent
    value_mantissa = value / scale
    uncertainty_mantissa = uncertainty / scale
    if exponent == 0:
        return rf"$({value:.{precision}f} \pm {uncertainty:.{precision}f})$ {unit}"
    return (
        rf"$({value_mantissa:.{precision}f} \pm {uncertainty_mantissa:.{precision}f}) "
        rf"\times 10^{{{exponent}}}$ {unit}"
    )


def format_leak_annotation_line(
    leak: LeakEstimate,
    *,
    unit: str,
    use_hfe_loss: bool = False,
) -> str:
    """Format either a best-fit leak value or an upper limit for annotations."""

    if use_hfe_loss:
        value = leak.hfe_loss_l_per_year
        uncertainty = leak.hfe_loss_err_l_per_year
        upper_limit = leak.upper_limit_hfe_loss_l_per_year
    else:
        value = leak.throughput_mbar_l_per_s
        uncertainty = leak.throughput_err_mbar_l_per_s
        upper_limit = leak.upper_limit_throughput_mbar_l_per_s

    if leak.is_upper_limit_only and upper_limit is not None:
        confidence_label = int(round(100.0 * float(leak.upper_limit_confidence_level or 0.0)))
        return f"<= {format_scientific_value(upper_limit, unit)} ({confidence_label}% CL)"

    return format_scientific_value_with_uncertainty(value, uncertainty, unit)


def annotation_sample_cloud(
    axis: plt.Axes,
    x_values: np.ndarray,
    y_values: np.ndarray,
) -> np.ndarray:
    """Return a display-coordinate point cloud for overlap scoring."""

    x_array = np.asarray(x_values, dtype=float)
    y_array = np.asarray(y_values, dtype=float)
    if x_array.size == 0 or y_array.size == 0:
        return np.empty((0, 2), dtype=float)
    if x_array.size != y_array.size:
        x_array, y_array = np.broadcast_arrays(x_array, y_array)
    if x_array.size > ANNOTATION_MAX_POINTS:
        indices = np.linspace(0, x_array.size - 1, ANNOTATION_MAX_POINTS, dtype=int)
        x_array = x_array[indices]
        y_array = y_array[indices]
    return axis.transData.transform(np.column_stack((x_array, y_array)))


def annotation_overlap_area(first: Bbox, second: Bbox | None) -> float:
    """Return the overlap area of two bounding boxes in display coordinates."""

    if second is None:
        return 0.0
    overlap_x0 = max(first.x0, second.x0)
    overlap_y0 = max(first.y0, second.y0)
    overlap_x1 = min(first.x1, second.x1)
    overlap_y1 = min(first.y1, second.y1)
    if overlap_x1 <= overlap_x0 or overlap_y1 <= overlap_y0:
        return 0.0
    return float((overlap_x1 - overlap_x0) * (overlap_y1 - overlap_y0))


def build_value_box(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    axis: plt.Axes,
    title: str,
    lines: Sequence[str],
    fontsize: float,
    loc: str,
    anchor: tuple[float, float],
) -> AnchoredOffsetbox:
    """Build a single title-plus-lines annotation box."""

    text_boxes: list[Artist] = [
        TextArea(
            title,
            textprops={"fontsize": fontsize, "fontweight": "bold"},
        ),
    ]
    text_boxes.extend(
        TextArea(line, textprops={"fontsize": fontsize})
        for line in lines
    )
    packed_box = VPacker(
        children=text_boxes,
        align="left",
        pad=0,
        sep=ANNOTATION_LINE_SEP,
    )
    annotation_box = AnchoredOffsetbox(
        loc=loc,
        child=packed_box,
        pad=0.35,
        borderpad=0.55,
        frameon=True,
        bbox_to_anchor=anchor,
        bbox_transform=axis.transAxes,
    )
    annotation_box.patch.set_boxstyle("round,pad=0.4")
    annotation_box.patch.set_facecolor("white")
    annotation_box.patch.set_alpha(0.88)
    return annotation_box


def add_best_value_box(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    axis: plt.Axes,
    title: str,
    lines: Sequence[str],
    fontsize: float,
    point_clouds: Sequence[np.ndarray],
    legend: Artist | None,
) -> AnchoredOffsetbox:
    """Add an annotation box at the least-overlapping candidate location."""

    figure = axis.get_figure()
    if figure is None:
        raise RuntimeError("Annotation placement requires an attached figure.")
    canvas = cast(Any, figure.canvas)
    canvas.draw()
    renderer = canvas.get_renderer()
    legend_bbox = legend.get_window_extent(renderer) if legend is not None else None

    best_score = np.inf
    best_loc: str | None = None
    best_anchor: tuple[float, float] | None = None
    for loc, anchor in ANNOTATION_BOX_CANDIDATES:
        candidate_box = build_value_box(axis, title, lines, fontsize, loc, anchor)
        axis.add_artist(candidate_box)
        canvas.draw()
        candidate_bbox = candidate_box.get_window_extent(renderer)
        overlap_points = 0
        for point_cloud in point_clouds:
            if point_cloud.size == 0:
                continue
            inside_x = (
                (point_cloud[:, 0] >= candidate_bbox.x0)
                & (point_cloud[:, 0] <= candidate_bbox.x1)
            )
            inside_y = (
                (point_cloud[:, 1] >= candidate_bbox.y0)
                & (point_cloud[:, 1] <= candidate_bbox.y1)
            )
            overlap_points += int(np.count_nonzero(inside_x & inside_y))

        legend_overlap = annotation_overlap_area(candidate_bbox, legend_bbox)
        score = float(overlap_points) + 1.0e6 * legend_overlap
        candidate_box.remove()
        if score < best_score:
            best_score = score
            best_loc = loc
            best_anchor = anchor

    if best_loc is None or best_anchor is None:
        raise RuntimeError("Failed to place annotation box.")
    best_box = build_value_box(axis, title, lines, fontsize, best_loc, best_anchor)
    axis.add_artist(best_box)
    return best_box


def plot_pressure_drop_trace(  # pylint: disable=too-many-arguments,too-many-locals
    averaged: AveragedTrace,
    *,
    title: str,
    source_note: str,
    fit_curve_time_h: np.ndarray | None = None,
    fit_curve_pressure_bar: np.ndarray | None = None,
    fit_curve_label: str = "Pressure drop fit",
    value_box_title: str | None = None,
    value_box_lines: Sequence[str] | None = None,
    warning: str | None = None,
    output_path: Path | None = None,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot a pressure-drop trace with the shared system-style layout."""

    fig, axis = plt.subplots(figsize=(12, 8))
    axis.fill_between(
        averaged.time_h,
        averaged.pressure_abs_bar - averaged.pressure_sigma_bar,
        averaged.pressure_abs_bar + averaged.pressure_sigma_bar,
        color="tab:blue",
        alpha=0.18,
        label=r"Mean $\pm\sigma$",
    )
    axis.plot(
        averaged.time_h,
        averaged.pressure_abs_bar,
        color="tab:blue",
        linewidth=3.0,
        label="Mean",
    )
    if fit_curve_time_h is not None and fit_curve_pressure_bar is not None:
        axis.plot(
            fit_curve_time_h,
            fit_curve_pressure_bar,
            color="tab:green",
            linewidth=3.0,
            label=fit_curve_label,
        )

    y_samples = [
        averaged.pressure_abs_bar - averaged.pressure_sigma_bar,
        averaged.pressure_abs_bar + averaged.pressure_sigma_bar,
    ]
    if fit_curve_pressure_bar is not None:
        y_samples.append(fit_curve_pressure_bar)
    y_all = np.concatenate(y_samples)
    y_min = float(np.min(y_all))
    y_max = float(np.max(y_all))
    y_pad = max(0.04 * (y_max - y_min), 0.03)

    axis.set_xlim(0.0, float(averaged.time_h[-1]))
    axis.set_ylim(P_ATM_BAR, y_max + y_pad)
    axis.set_xlabel("Time (hours)", fontsize=20)
    axis.set_ylabel("Pressure (bar abs)", fontsize=20)
    axis.set_title(title, fontsize=24)
    axis.tick_params(axis="both", labelsize=16)
    axis.grid(True, alpha=0.3)
    legend = axis.legend(loc="best", fontsize=16, framealpha=0.95)
    legend_fontsize = float(legend.get_texts()[0].get_fontsize()) if legend.get_texts() else 16.0

    fig.tight_layout(rect=(0.0, 0.06, 1.0, 1.0))
    if value_box_title is not None and value_box_lines:
        point_clouds = [
            annotation_sample_cloud(
                axis,
                averaged.time_h,
                averaged.pressure_abs_bar - averaged.pressure_sigma_bar,
            ),
            annotation_sample_cloud(axis, averaged.time_h, averaged.pressure_abs_bar),
            annotation_sample_cloud(
                axis,
                averaged.time_h,
                averaged.pressure_abs_bar + averaged.pressure_sigma_bar,
            ),
        ]
        if fit_curve_time_h is not None and fit_curve_pressure_bar is not None:
            point_clouds.append(
                annotation_sample_cloud(axis, fit_curve_time_h, fit_curve_pressure_bar)
            )
        add_best_value_box(
            axis,
            value_box_title,
            value_box_lines,
            legend_fontsize,
            point_clouds,
            legend,
        )

    fig.text(
        0.99,
        0.032,
        source_note,
        ha="right",
        va="bottom",
        fontsize=9,
        color="0.45",
    )
    if warning is not None:
        fig.text(
            0.99,
            0.012,
            warning,
            ha="right",
            va="bottom",
            fontsize=9,
            color="tab:red",
        )

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    if close:
        plt.close(fig)
    return fig, axis


def plot_system_pressure_result(
    result: SystemPressureResult,
    output_path: Path | None = None,
    data_path: Path | None = None,
    *,
    save_plot_data: bool = True,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot the system-pressure analysis and export plot-ready processed data."""

    if save_plot_data:
        export_system_pressure_plot_data(result, data_path)

    value_box_lines: list[str] | None = None
    if result.leak is not None:
        value_box_lines = [
            f"Volume: {NITROGEN_LEAK_TEST_VOLUME_L:.0f} L N$_2$ leak-test ({result.top_gas_trap.gas_trap_volume_l:.0f} L gas trap)",
            f"Pressure: {result.top_gas_trap.total_pressure_abs_bar:.2f} bar ({result.top_gas_trap.hfe_vapor_pressure_abs_bar:.3f} bar of HFE vapor)",
            (
                "HFE loss (top vapor leaks): "
                f"{format_leak_annotation_line(result.leak, unit='L/year', use_hfe_loss=True)}"
            ),
        ]
    return plot_pressure_drop_trace(
        result.averaged,
        title=r"HFE System $N_2$ Leak Test",
        source_note=f"Source log: {result.series.csv_path.name}",
        fit_curve_time_h=result.fit_curve_time_h,
        fit_curve_pressure_bar=result.fit_curve_pressure_bar,
        value_box_title="HFE-equivalent values" if value_box_lines is not None else None,
        value_box_lines=value_box_lines,
        warning=result.warning,
        output_path=output_path,
        close=close,
    )


def plot_reservoir_leak_result(
    result: ReservoirLeakResult,
    output_path: Path | None = None,
    data_path: Path | None = None,
    *,
    save_plot_data: bool = True,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot a reservoir leak test and export plot-ready processed data."""

    if save_plot_data:
        export_reservoir_leak_plot_data(result, data_path)

    fig, axis = plt.subplots(figsize=(10, 6))
    axis.fill_between(
        result.fit_curve_time_h,
        result.fit_curve_pressure_bar - result.fit_curve_sigma_bar,
        result.fit_curve_pressure_bar + result.fit_curve_sigma_bar,
        color="tab:blue",
        alpha=0.20,
        label=r"Mean $\pm\sigma$",
    )
    axis.plot(
        result.fit_curve_time_h,
        result.fit_curve_pressure_bar,
        color="tab:blue",
        linewidth=2.5,
        label="Mean",
    )
    axis.errorbar(
        result.case.time_h,
        result.case.pressure_abs_bar,
        yerr=result.measurement_sigma_bar,
        fmt="o",
        capsize=4,
        color="tab:orange",
        label="Measurements",
    )
    axis.set_xlim(0.0, result.case.x_max_h)
    axis.set_ylim(P_ATM_BAR, result.case.y_max_bar)
    axis.set_xlabel("Time (hours)", fontsize=18)
    axis.set_ylabel("Reservoir pressure (bar abs)", fontsize=18)
    axis.set_title(f"Reservoir Leak Test - {result.case.label}", fontsize=22)
    axis.tick_params(axis="both", labelsize=14)
    axis.grid(True, alpha=0.3)
    legend = axis.legend(loc="upper right", fontsize=14, framealpha=0.95)
    legend_fontsize = float(legend.get_texts()[0].get_fontsize()) if legend.get_texts() else 14.0

    operating_abs_bar = P_ATM_BAR + result.case.operating_gauge_bar
    fig.tight_layout()
    add_best_value_box(
        axis,
        "HFE-equivalent values",
        [
            f"Volume: {result.case.volume_l:.0f} L",
            f"Pressure: {operating_abs_bar:.2f} bar abs (HFE vapor)",
            (
                "HFE-7200 liquid-equivalent loss (from vapor leak): "
                f"{result.leak.hfe_loss_l_per_year:.2f} L/year"
            ),
        ],
        legend_fontsize,
        [
            annotation_sample_cloud(
                axis,
                result.fit_curve_time_h,
                result.fit_curve_pressure_bar - result.fit_curve_sigma_bar,
            ),
            annotation_sample_cloud(axis, result.fit_curve_time_h, result.fit_curve_pressure_bar),
            annotation_sample_cloud(
                axis,
                result.fit_curve_time_h,
                result.fit_curve_pressure_bar + result.fit_curve_sigma_bar,
            ),
            annotation_sample_cloud(
                axis,
                result.case.time_h,
                result.case.pressure_abs_bar,
            ),
        ],
        legend,
    )

    fig.text(
        0.99,
        0.012,
        result.case.source_note,
        ha="right",
        va="bottom",
        fontsize=9,
        color="0.45",
    )

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    if close:
        plt.close(fig)
    return fig, axis


def plot_reservoir_pressure_log_result(
    result: ReservoirPressureLogResult,
    output_path: Path | None = None,
    data_path: Path | None = None,
    *,
    save_plot_data: bool = True,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot a logged reservoir pressure-drop analysis and export plot-ready data."""

    if save_plot_data:
        export_reservoir_pressure_log_plot_data(result, data_path)

    value_box_lines: list[str] | None = None
    if result.linear_fit is not None:
        operating_abs_bar = P_ATM_BAR + result.operating_gauge_bar
        linear_drop_mbar_h, linear_drop_err_mbar_h = linear_pressure_drop_metrics(
            result.linear_fit
        )
        value_box_lines = [
            f"Volume: {result.volume_l:.0f} L",
            f"Pressure: {operating_abs_bar:.2f} bar abs (HFE vapor)",
            f"Pressure drop (linear fit): {linear_drop_mbar_h:.2f} ± {linear_drop_err_mbar_h:.2f} mbar/h",
        ]
        if result.leak is not None:
            value_box_lines.append(
                "HFE-7200 liquid-equivalent loss (from vapor leak): "
                f"{format_leak_annotation_line(result.leak, unit='L/year', use_hfe_loss=True)}"
            )
    return plot_pressure_drop_trace(
        result.averaged,
        title=f"Reservoir Pressure Drop - {result.label}",
        source_note=result.source_note,
        fit_curve_time_h=result.fit_curve_time_h,
        fit_curve_pressure_bar=result.fit_curve_pressure_bar,
        fit_curve_label=result.fit_curve_label or "Pressure drop fit",
        value_box_title="Pressure-drop values" if value_box_lines is not None else None,
        value_box_lines=value_box_lines,
        warning=result.warning,
        output_path=output_path,
        close=close,
    )


def plot_vacuum_rate_of_rise_result(
    result: VacuumRateOfRiseResult,
    output_path: Path | None = None,
    data_path: Path | None = None,
    *,
    save_plot_data: bool = True,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot a vacuum rate-of-rise result and export plot-ready processed data."""

    if save_plot_data:
        export_vacuum_rate_of_rise_plot_data(result, data_path)

    fig, axis = plt.subplots(figsize=(10, 6))
    gas_load_text = format_scientific_value(
        result.gas_load.throughput_mbar_l_per_s,
        "mbar·L/s",
    )
    axis.fill_between(
        result.fit_curve_time_h,
        result.fit_curve_pressure_bar - result.fit_curve_sigma_bar,
        result.fit_curve_pressure_bar + result.fit_curve_sigma_bar,
        color="tab:blue",
        alpha=0.20,
        label=r"Fit $\pm\sigma$",
    )
    axis.plot(
        result.fit_curve_time_h,
        result.fit_curve_pressure_bar,
        color="tab:blue",
        linewidth=2.5,
        label="Rate-of-rise fit",
    )
    axis.errorbar(
        result.case.time_h,
        result.case.pressure_abs_bar,
        yerr=result.case.measurement_sigma_bar,
        fmt="o",
        capsize=4,
        color="tab:orange",
        label="Measurements",
    )
    axis.axhline(
        result.water_vapor.saturation_pressure_bar,
        color="tab:green",
        linestyle="--",
        linewidth=2.0,
        label="Water vapor saturation",
    )
    axis.set_xlim(0.0, float(result.fit_curve_time_h[-1]))
    axis.set_ylim(
        0.0,
        max(
            float(np.max(result.fit_curve_pressure_bar + result.fit_curve_sigma_bar)),
            float(np.max(result.case.pressure_abs_bar)),
            result.water_vapor.saturation_pressure_bar,
        )
        * 1.08,
    )
    axis.set_xlabel("Time (hours)", fontsize=18)
    axis.set_ylabel("Loop pressure (bar abs)", fontsize=18)
    axis.set_title(result.case.label, fontsize=22)
    axis.tick_params(axis="both", labelsize=14)
    axis.grid(True, alpha=0.3)
    legend = axis.legend(loc="upper left", fontsize=14, framealpha=0.95)
    legend_fontsize = float(legend.get_texts()[0].get_fontsize()) if legend.get_texts() else 14.0

    fig.tight_layout()
    add_best_value_box(
        axis,
        "Rate-of-rise values",
        [
            f"Volume: {result.case.volume_l:.1f} L",
            (
                f"Rise rate: {result.fit.slope_bar_per_h * 1000.0:.2f} "
                f"± {result.fit.slope_err_bar_per_h * 1000.0:.2f} mbar/h"
            ),
            f"Gas load: {gas_load_text}",
            (
                f"HFE loss: {result.gas_load.hfe_loss_l_per_year:.3f} "
                f"± {result.gas_load.hfe_loss_err_l_per_year:.3f} L/year"
            ),
        ],
        legend_fontsize,
        [
            annotation_sample_cloud(
                axis,
                result.fit_curve_time_h,
                result.fit_curve_pressure_bar - result.fit_curve_sigma_bar,
            ),
            annotation_sample_cloud(axis, result.fit_curve_time_h, result.fit_curve_pressure_bar),
            annotation_sample_cloud(
                axis,
                result.fit_curve_time_h,
                result.fit_curve_pressure_bar + result.fit_curve_sigma_bar,
            ),
            annotation_sample_cloud(axis, result.case.time_h, result.case.pressure_abs_bar),
            annotation_sample_cloud(
                axis,
                result.fit_curve_time_h,
                np.full_like(
                    result.fit_curve_time_h,
                    result.water_vapor.saturation_pressure_bar,
                ),
            ),
        ],
        legend,
    )

    fig.text(
        0.99,
        0.012,
        result.case.source_note,
        ha="right",
        va="bottom",
        fontsize=9,
        color="0.45",
    )

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    if close:
        plt.close(fig)
    return fig, axis


def format_system_pressure_report(
    result: SystemPressureResult,
    output_path: Path | None = None,
) -> str:
    """Return a console-style text report for a system-pressure analysis."""

    fit_points = len(result.averaged.time_h)
    lines = [
        "=== Tank pressure evolution exponential fit (top gas trap: 1 atm N2 + HFE vapor) ===",
        f"CSV file                   : {result.series.csv_path}",
    ]
    if output_path is not None:
        lines.append(f"Saved figure               : {output_path}")
    if result.fit is None or result.leak is None or result.rmse_mbar is None:
        lines.extend(
            [
                f"Elapsed time               : {result.series.time_h[-1]:.3f} h",
                (
                    f"Pressure start/end         : {result.series.pressure_abs_bar[0]:.3f} -> "
                    f"{result.series.pressure_abs_bar[-1]:.3f} bar abs"
                ),
                f"Warning                    : {result.warning or 'Pressure-drop fit not available.'}",
            ]
        )
        return "\n".join(lines)
    lines.extend(
        [
            f"Elapsed time               : {result.series.time_h[-1]:.3f} h",
            f"Fit bins used              : {fit_points} / {fit_points}",
            (
                f"Fit asymptote             : {result.fit.asymptote_bar:.4f} +/- "
                f"{result.fit.asymptote_err_bar:.4f} bar abs"
            ),
            (
                f"Asymptote offset vs 1 atm : "
                f"{(result.fit.asymptote_bar - P_ATM_BAR) * 1000.0:.1f} mbar"
            ),
            (
                f"Fit time span              : {result.averaged.time_h[0]:.3f} -> "
                f"{result.averaged.time_h[-1]:.3f} h"
            ),
            (
                f"Tank pressure start/end    : {result.series.pressure_abs_bar[0]:.3f} -> "
                f"{result.series.pressure_abs_bar[-1]:.3f} bar abs"
            ),
            f"Initial test overpressure  : {result.series.initial_gauge_bar:.3f} bar",
            f"Average fluid temperature  : {result.series.room_temp_c:.3f} C",
            f"N2 leak-test gas volume    : {NITROGEN_LEAK_TEST_VOLUME_L:.3f} L",
            f"System volume              : {TOTAL_SYSTEM_VOLUME_L:.3f} L",
            f"Filled HFE volume          : {FILLED_HFE_VOLUME_L:.3f} L",
            f"Top gas trap volume        : {result.top_gas_trap.gas_trap_volume_l:.3f} L",
            (
                f"Top gas total pressure     : "
                f"{result.top_gas_trap.total_pressure_abs_bar:.3f} bar abs"
            ),
            (
                f"HFE vapor partial pressure : "
                f"{result.top_gas_trap.hfe_vapor_pressure_abs_bar:.3f} bar abs"
            ),
            (
                f"N2 partial pressure        : "
                f"{result.top_gas_trap.n2_partial_pressure_abs_bar:.3f} bar abs"
            ),
            (
                f"HFE vapor mole fraction    : "
                f"{100.0 * result.top_gas_trap.hfe_vapor_mole_fraction:.2f} %"
            ),
            (
                f"  HFE vapor throughput     : {result.leak.throughput_mbar_l_per_s:.4f} +/- "
                f"{result.leak.throughput_err_mbar_l_per_s:.4f} mbar·L/s"
            ),
            (
                f"  HFE-7200 liq.-eq. loss   : {result.leak.hfe_loss_l_per_year:.2f} +/- "
                f"{result.leak.hfe_loss_err_l_per_year:.2f} L/year"
            ),
            f"Pressure error used        : +/-{result.series.pressure_error_bar:.3f} bar",
            "",
            "Exponential fit:",
            (
                "  P_abs(bar) = "
                f"({result.fit.asymptote_bar:.5f} +/- {result.fit.asymptote_err_bar:.5f}) + "
                f"({result.fit.amplitude_bar:.4f} +/- {result.fit.amplitude_err_bar:.4f}) * "
                f"exp(({result.fit.k_per_h:.5f} +/- {result.fit.k_err_per_h:.5f}) t_h)"
            ),
            (
                "  P_excess_over_fit_tail(bar) = "
                f"({result.fit.amplitude_bar:.4f} +/- {result.fit.amplitude_err_bar:.4f}) * "
                f"exp(({result.fit.k_per_h:.5f} +/- {result.fit.k_err_per_h:.5f}) t_h)"
            ),
            f"  RMSE                                : {result.rmse_mbar:.1f} mbar",
        ]
    )
    return "\n".join(lines)


def format_reservoir_leak_report(
    result: ReservoirLeakResult,
    output_path: Path | None = None,
) -> str:
    """Return a console-style text report for a reservoir leak test."""

    lines = [
        f"=== Reservoir leak test: {result.case.label} ===",
    ]
    if output_path is not None:
        lines.append(f"Saved figure               : {output_path}")
    lines.extend(
        [
            f"Elapsed time               : {result.case.time_h[-1]:.3f} h",
            (
                "P_gauge(bar) = "
                f"({result.fit.amplitude_bar:.4f} +/- {result.fit.amplitude_err_bar:.4f}) * "
                f"exp(({result.fit.k_per_h:.5f} +/- {result.fit.k_err_per_h:.5f}) t_h)"
            ),
            (
                f"Pressure start/end         : {result.case.pressure_abs_bar[0]:.3f} -> "
                f"{result.case.pressure_abs_bar[-1]:.3f} bar abs"
            ),
            (
                f"dP/dt (at test start)      : {result.start_decay_mbar_per_h:.1f} +/- "
                f"{result.start_decay_err_mbar_per_h:.1f} mbar/h"
            ),
            (
                f"Q (at HFE vapor pressure)  : {result.leak.throughput_mbar_l_per_s:.4f} +/- "
                f"{result.leak.throughput_err_mbar_l_per_s:.4f} mbar·L/s"
            ),
            (
                f"HFE loss                   : {result.leak.hfe_loss_l_per_year:.2f} +/- "
                f"{result.leak.hfe_loss_err_l_per_year:.2f} L/year"
            ),
        ]
    )
    return "\n".join(lines)


def format_vacuum_rate_of_rise_report(
    result: VacuumRateOfRiseResult,
    output_path: Path | None = None,
) -> str:
    """Return a console-style text report for a vacuum rate-of-rise test."""

    h2o_saturation_line = (
        "H2O saturation pressure    : "
        f"{result.water_vapor.saturation_pressure_bar:.4f} bar abs "
        f"at {result.case.system_temp_c:.1f} C"
    )
    lines = [
        f"=== Vacuum rate-of-rise test: {result.case.label} ===",
    ]
    if output_path is not None:
        lines.append(f"Saved figure               : {output_path}")
    lines.extend(
        [
            f"Elapsed time               : {result.case.time_h[-1]:.3f} h",
            (
                "P_abs(bar) = "
                f"({result.fit.intercept_bar:.5f} ± {result.fit.intercept_err_bar:.5f}) + "
                f"({result.fit.slope_bar_per_h:.6f} ± {result.fit.slope_err_bar_per_h:.6f}) t_h"
            ),
            (
                f"Pressure start/end         : {result.case.pressure_abs_bar[0]:.3f} -> "
                f"{result.case.pressure_abs_bar[-1]:.3f} bar abs"
            ),
            (
                f"dP/dt                      : {result.fit.slope_bar_per_h * 1000.0:.2f} ± "
                f"{result.fit.slope_err_bar_per_h * 1000.0:.2f} mbar/h"
            ),
            (
                f"Gas load Q                 : {result.gas_load.throughput_mbar_l_per_s:.4f} ± "
                f"{result.gas_load.throughput_err_mbar_l_per_s:.4f} mbar·L/s"
            ),
            (
                f"HFE loss                   : {result.gas_load.hfe_loss_l_per_year:.3f} ± "
                f"{result.gas_load.hfe_loss_err_l_per_year:.3f} L/year"
            ),
            h2o_saturation_line,
            (
                f"Measured / H2O saturation  : {result.water_vapor.start_ratio:.2f} -> "
                f"{result.water_vapor.end_ratio:.2f}"
            ),
            (
                f"Excess above H2O sat       : {result.water_vapor.start_excess_mbar:.1f} -> "
                f"{result.water_vapor.end_excess_mbar:.1f} mbar"
            ),
            f"RMSE                       : {result.rmse_mbar:.1f} mbar",
        ]
    )
    return "\n".join(lines)


def system_pressure_summary_table(results: Sequence[SystemPressureResult]) -> pd.DataFrame:
    """Return a compact summary table for a set of system-pressure analyses."""

    summary = pd.DataFrame(
        [
            {
                "log_file": result.series.csv_path.name,
                "elapsed_h": float(result.series.time_h[-1]),
                "fit_asymptote_bar_abs": (
                    np.nan if result.fit is None else result.fit.asymptote_bar
                ),
                "fit_k_per_h": np.nan if result.fit is None else result.fit.k_per_h,
                "mean_pressure_bar_abs": result.mean_pressure.absolute_bar,
                "top_gas_total_pressure_bar_abs": result.top_gas_trap.total_pressure_abs_bar,
                "hfe_vapor_partial_pressure_bar_abs": (
                    result.top_gas_trap.hfe_vapor_pressure_abs_bar
                ),
                "n2_partial_pressure_bar_abs": result.top_gas_trap.n2_partial_pressure_abs_bar,
                "top_gas_hfe_mole_fraction": result.top_gas_trap.hfe_vapor_mole_fraction,
                "leak_mbar_l_s": (
                    np.nan if result.leak is None else result.leak.throughput_mbar_l_per_s
                ),
                "hfe_loss_l_year": (
                    np.nan if result.leak is None else result.leak.hfe_loss_l_per_year
                ),
                "rmse_mbar": result.rmse_mbar,
                "warning": result.warning,
            }
            for result in results
        ]
    )
    if summary["warning"].isna().all():
        summary = summary.drop(columns=["warning"])
    return summary


def vacuum_rate_of_rise_summary_table(
    results: Sequence[VacuumRateOfRiseResult],
) -> pd.DataFrame:
    """Return a compact summary table for a set of vacuum rate-of-rise tests."""

    return pd.DataFrame(
        [
            {
                "case": result.case.label,
                "elapsed_h": float(result.case.time_h[-1]),
                "start_pressure_bar_abs": float(result.case.pressure_abs_bar[0]),
                "end_pressure_bar_abs": float(result.case.pressure_abs_bar[-1]),
                "rise_rate_mbar_h": result.fit.slope_bar_per_h * 1000.0,
                "gas_load_mbar_l_s": result.gas_load.throughput_mbar_l_per_s,
                "hfe_loss_l_year": result.gas_load.hfe_loss_l_per_year,
                "water_saturation_bar_abs": result.water_vapor.saturation_pressure_bar,
                "start_over_water_sat": result.water_vapor.start_ratio,
                "end_over_water_sat": result.water_vapor.end_ratio,
                "rmse_mbar": result.rmse_mbar,
            }
            for result in results
        ]
    )


def reservoir_pressure_log_summary_table(
    results: Sequence[ReservoirPressureLogResult],
) -> pd.DataFrame:
    """Return a compact summary table for logged reservoir pressure-drop tests."""

    summary = pd.DataFrame(
        [
            {
                "case": result.label,
                "elapsed_h": float(result.series.time_h[-1]),
                "fit_asymptote_bar_abs": (
                    np.nan if result.fit is None else result.fit.asymptote_bar
                ),
                "fit_k_per_h": np.nan if result.fit is None else result.fit.k_per_h,
                "start_pressure_bar_abs": float(result.series.pressure_abs_bar[0]),
                "end_pressure_bar_abs": float(result.series.pressure_abs_bar[-1]),
                "volume_l": result.volume_l,
                "operating_pressure_bar_abs": P_ATM_BAR + result.operating_gauge_bar,
                "linear_pressure_drop_mbar_h": (
                    np.nan
                    if result.linear_fit is None
                    else -1000.0 * result.linear_fit.slope_bar_per_h
                ),
                "linear_pressure_drop_err_mbar_h": (
                    np.nan
                    if result.linear_fit is None
                    else 1000.0 * result.linear_fit.slope_err_bar_per_h
                ),
                "leak_mbar_l_s": (
                    np.nan if result.leak is None else result.leak.throughput_mbar_l_per_s
                ),
                "leak_err_mbar_l_s": (
                    np.nan if result.leak is None else result.leak.throughput_err_mbar_l_per_s
                ),
                "hfe_loss_l_year": (
                    np.nan if result.leak is None else result.leak.hfe_loss_l_per_year
                ),
                "hfe_loss_err_l_year": (
                    np.nan if result.leak is None else result.leak.hfe_loss_err_l_per_year
                ),
                "rmse_mbar": result.rmse_mbar,
                "warning": result.warning,
            }
            for result in results
        ]
    )
    if summary["warning"].isna().all():
        summary = summary.drop(columns=["warning"])
    return summary


def reservoir_summary_table(results: Sequence[ReservoirLeakResult]) -> pd.DataFrame:
    """Return a compact summary table for a set of reservoir leak tests."""

    return pd.DataFrame(
        [
            {
                "case": result.case.label,
                "elapsed_h": float(result.case.time_h[-1]),
                "fit_k_per_h": result.fit.k_per_h,
                "start_pressure_bar_abs": float(result.case.pressure_abs_bar[0]),
                "end_pressure_bar_abs": float(result.case.pressure_abs_bar[-1]),
                "volume_l": result.case.volume_l,
                "operating_pressure_bar_abs": P_ATM_BAR + result.case.operating_gauge_bar,
                "start_decay_mbar_h": result.start_decay_mbar_per_h,
                "leak_mbar_l_s": result.leak.throughput_mbar_l_per_s,
                "hfe_loss_l_year": result.leak.hfe_loss_l_per_year,
            }
            for result in results
        ]
    )
