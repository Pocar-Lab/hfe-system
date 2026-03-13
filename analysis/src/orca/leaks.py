"""Leak-test analysis helpers for ORCA."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.artist import Artist
from matplotlib.offsetbox import AnchoredOffsetbox, TextArea, VPacker
from scipy.optimize import curve_fit

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_PLOT_DIR = REPO_ROOT / "data" / "reports" / "leaks"
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
HFE_LIQUID_DENSITY_KG_M3 = 1420.0
BIN_WIDTH_MIN = 1.0
HFE_L_PER_YEAR_PER_MBARLS = 22.38
HFE_VAPOR_GAUGE_BAR = (109.0 / 760.0) * P_ATM_BAR
DEFAULT_RESERVOIR_XMAX_H = 24.0
DEFAULT_RESERVOIR_YMAX_BAR = 3.0

GAUGE_RANGE_MIN_BAR = -1.0
GAUGE_RANGE_MAX_BAR = 4.1
GAUGE_FULL_SPAN_BAR = GAUGE_RANGE_MAX_BAR - GAUGE_RANGE_MIN_BAR
GAUGE_END_QUARTER_FRACTION = 0.25
GAUGE_RESOLUTION_BAR = 0.1


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
    """Free-asymptote exponential fit results."""

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


@dataclass(frozen=True)
class MeanSystemPressure:
    """Mean hydrostatic pressure assumed for the HFE system."""

    absolute_bar: float
    gauge_bar: float
    hydrostatic_delta_bar: float
    required_tank_fill_gauge_bar: float


@dataclass(frozen=True)
class SystemPressureResult:  # pylint: disable=too-many-instance-attributes
    """Complete analysis outputs for a system-pressure leak log."""

    series: PressureSeries
    averaged: AveragedTrace
    fit: ExponentialFit
    leak: LeakEstimate
    mean_pressure: MeanSystemPressure
    fit_curve_time_h: np.ndarray
    fit_curve_pressure_bar: np.ndarray
    rmse_mbar: float


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


def default_system_pressure_plot_path(
    csv_path: Path,
    output_dir: Path = DEFAULT_PLOT_DIR,
) -> Path:
    """Return the default saved-plot path for a system-pressure analysis."""

    return output_dir / f"{csv_path.stem}_tank_pressure_evolution_n2_gas_trap.png"


def load_pressure_series(csv_path: Path) -> PressureSeries:
    """Load the pressure columns needed for the system leak analysis."""

    frame = pd.read_csv(csv_path).dropna(
        subset=[
            TIME_COLUMN,
            PRESSURE_ABS_COLUMN,
            PRESSURE_GAUGE_COLUMN,
            PRESSURE_ERR_COLUMN,
        ]
    )
    time_h = ((frame[TIME_COLUMN] - frame[TIME_COLUMN].iloc[0]) / 3600.0).to_numpy(dtype=float)
    pressure_abs_bar = frame[PRESSURE_ABS_COLUMN].to_numpy(dtype=float)
    pressure_gauge_bar = frame[PRESSURE_GAUGE_COLUMN].to_numpy(dtype=float)

    return PressureSeries(
        csv_path=csv_path,
        time_h=time_h,
        pressure_abs_bar=pressure_abs_bar,
        pressure_error_bar=float(frame[PRESSURE_ERR_COLUMN].iloc[0]),
        room_temp_c=float(frame[ROOM_TEMP_COLUMN].mean()),
        initial_gauge_bar=float(pressure_gauge_bar[0]),
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


def free_asymptote_exponential_model(
    time_h: np.ndarray,
    asymptote_bar: float,
    amplitude_bar: float,
    k_per_h: float,
) -> np.ndarray:
    """Exponential decay toward a fitted asymptotic pressure."""

    return asymptote_bar + amplitude_bar * np.exp(k_per_h * time_h)


def initial_fit_guess(pressure_abs_bar: np.ndarray) -> tuple[float, float, float]:
    """Build a stable initial guess for the free-asymptote exponential fit."""

    tail_guess_bar = float(np.mean(pressure_abs_bar[max(len(pressure_abs_bar) - 60, 0) :]))
    amplitude_guess_bar = max(float(pressure_abs_bar[0] - tail_guess_bar), 1e-6)
    return tail_guess_bar, amplitude_guess_bar, -0.4


def fit_system_pressure_decay(
    time_h: np.ndarray,
    pressure_abs_bar: np.ndarray,
    pressure_sigma_bar: float | np.ndarray,
) -> ExponentialFit:
    """Fit the averaged absolute pressure trace with a free asymptote."""

    sigma_bar = np.maximum(np.asarray(pressure_sigma_bar, dtype=float), 1e-12)
    bounds = ([0.0, 0.0, -np.inf], [np.inf, np.inf, 0.0])
    params, covariance = curve_fit(
        free_asymptote_exponential_model,
        time_h,
        pressure_abs_bar,
        p0=initial_fit_guess(pressure_abs_bar),
        sigma=sigma_bar,
        absolute_sigma=True,
        bounds=bounds,
        maxfev=20000,
    )
    asymptote_bar = float(params[0])
    amplitude_bar = float(params[1])
    k_per_h = float(params[2])
    asymptote_err_bar = float(np.sqrt(covariance[0, 0]))
    amplitude_err_bar = float(np.sqrt(covariance[1, 1]))
    k_err_per_h = float(np.sqrt(covariance[2, 2]))
    tau_h = -1.0 / k_per_h
    tau_err_h = k_err_per_h / (k_per_h**2)
    return ExponentialFit(
        asymptote_bar=asymptote_bar,
        asymptote_err_bar=asymptote_err_bar,
        amplitude_bar=amplitude_bar,
        amplitude_err_bar=amplitude_err_bar,
        k_per_h=k_per_h,
        k_err_per_h=k_err_per_h,
        tau_h=float(tau_h),
        tau_err_h=float(tau_err_h),
    )


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
    log_amplitude_bar, k_per_h = np.linalg.solve(design, log_pressure)

    sigma_log = pressure_err_bar / pressure_gauge_bar
    cov_log_measurements = np.diag(sigma_log**2)
    design_inv = np.linalg.inv(design)
    cov_log_amplitude_k = design_inv @ cov_log_measurements @ design_inv.T

    amplitude_bar = float(np.exp(log_amplitude_bar))
    sigma_log_amplitude = float(np.sqrt(max(cov_log_amplitude_k[0, 0], 0.0)))
    amplitude_err_bar = float(amplitude_bar * sigma_log_amplitude)
    k_err_per_h = float(np.sqrt(max(cov_log_amplitude_k[1, 1], 0.0)))

    log_fit = log_amplitude_bar + k_per_h * x_eval_h
    pressure_gauge_fit_bar = np.exp(log_fit)
    pressure_abs_fit_bar = asymptote_bar + pressure_gauge_fit_bar

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


def compute_mean_system_pressure() -> MeanSystemPressure:
    """Return the mean hydrostatic pressure assumed for the HFE system."""

    hydrostatic_delta_bar = HFE_LIQUID_DENSITY_KG_M3 * 9.81 * SYSTEM_HEIGHT_M / 1.0e5
    required_fill_bar = (
        HFE_LIQUID_DENSITY_KG_M3
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


def compute_hfe_equivalent_leak(
    k_per_h: float,
    k_err_per_h: float,
    target_gauge_bar: float,
    leak_test_volume_l: float = NITROGEN_LEAK_TEST_VOLUME_L,
) -> LeakEstimate:
    """Convert the fitted decay constant into HFE-equivalent leak numbers."""

    decay_bar_per_h = -k_per_h * target_gauge_bar
    decay_err_bar_per_h = k_err_per_h * abs(target_gauge_bar)
    throughput_mbar_l_per_s = leak_test_volume_l * decay_bar_per_h * 1000.0 / 3600.0
    throughput_err_mbar_l_per_s = leak_test_volume_l * decay_err_bar_per_h * 1000.0 / 3600.0
    return LeakEstimate(
        throughput_mbar_l_per_s=throughput_mbar_l_per_s,
        throughput_err_mbar_l_per_s=throughput_err_mbar_l_per_s,
        hfe_loss_l_per_year=throughput_mbar_l_per_s * HFE_L_PER_YEAR_PER_MBARLS,
        hfe_loss_err_l_per_year=throughput_err_mbar_l_per_s * HFE_L_PER_YEAR_PER_MBARLS,
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


def rmse_bar(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Return the root-mean-square error in bar."""

    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def analyze_system_pressure_log(csv_path: str | Path) -> SystemPressureResult:
    """Run the full system-pressure leak analysis for a CSV log."""

    resolved_path = resolve_pressure_log_path(csv_path)
    series = load_pressure_series(resolved_path)
    averaged = make_weighted_average_trace(
        series.time_h,
        series.pressure_abs_bar,
        series.pressure_error_bar,
    )
    fit = fit_system_pressure_decay(
        averaged.time_h,
        averaged.pressure_abs_bar,
        averaged.pressure_sigma_bar,
    )
    fit_curve_time_h = np.linspace(float(averaged.time_h[0]), float(averaged.time_h[-1]), 500)
    fit_curve_pressure_bar = free_asymptote_exponential_model(
        fit_curve_time_h,
        fit.asymptote_bar,
        fit.amplitude_bar,
        fit.k_per_h,
    )
    averaged_fit_bar = free_asymptote_exponential_model(
        averaged.time_h,
        fit.asymptote_bar,
        fit.amplitude_bar,
        fit.k_per_h,
    )
    mean_pressure = compute_mean_system_pressure()
    leak = compute_hfe_equivalent_leak(
        fit.k_per_h,
        fit.k_err_per_h,
        mean_pressure.gauge_bar,
    )
    return SystemPressureResult(
        series=series,
        averaged=averaged,
        fit=fit,
        leak=leak,
        mean_pressure=mean_pressure,
        fit_curve_time_h=fit_curve_time_h,
        fit_curve_pressure_bar=fit_curve_pressure_bar,
        rmse_mbar=1000.0 * rmse_bar(averaged.pressure_abs_bar, averaged_fit_bar),
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


def format_scientific_value(value: float, unit: str, precision: int = 2) -> str:
    """Format a value in scientific notation for plot annotations."""

    if value == 0.0:
        return f"0 {unit}"
    exponent = int(np.floor(np.log10(abs(value))))
    mantissa = value / (10.0**exponent)
    return rf"${mantissa:.{precision}f} \times 10^{{{exponent}}}$ {unit}"


def plot_system_pressure_result(
    result: SystemPressureResult,
    output_path: Path | None = None,
    *,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot the system-pressure analysis, optionally saving the figure."""

    fig, axis = plt.subplots(figsize=(12, 8))
    axis.fill_between(
        result.averaged.time_h,
        result.averaged.pressure_abs_bar - result.averaged.pressure_sigma_bar,
        result.averaged.pressure_abs_bar + result.averaged.pressure_sigma_bar,
        color="tab:blue",
        alpha=0.18,
        label=r"Mean $\pm\sigma$",
    )
    axis.plot(
        result.averaged.time_h,
        result.averaged.pressure_abs_bar,
        color="tab:blue",
        linewidth=3.0,
        label="Mean",
    )
    axis.plot(
        result.fit_curve_time_h,
        result.fit_curve_pressure_bar,
        color="tab:green",
        linewidth=3.0,
        label="Pressure drop fit",
    )
    axis.set_xlim(0.0, float(result.averaged.time_h[-1]))
    axis.set_xlabel("Time (hours)", fontsize=20)
    axis.set_ylabel("Tank pressure (bar abs)", fontsize=20)
    axis.set_title(r"HFE System $N_2$ Leak Test", fontsize=24)
    axis.tick_params(axis="both", labelsize=16)
    axis.grid(True, alpha=0.3)
    axis.legend(loc="upper right", fontsize=16, framealpha=0.95)

    text_boxes: list[Artist] = [
        TextArea(
            "HFE-equivalent values",
            textprops={"fontsize": 17, "fontweight": "bold"},
        ),
        TextArea(
            "\n".join(
                [
                    f"Volume: {TOTAL_SYSTEM_VOLUME_L:.0f} L (HFE: {FILLED_HFE_VOLUME_L:.0f} L)",
                    f"Pressure: {result.mean_pressure.absolute_bar:.2f} bar abs (1 atm N2 trap)",
                    (
                        "Leaks: "
                        f"{format_scientific_value(
                            result.leak.throughput_mbar_l_per_s,
                            'mbar·L/s',
                        )}"
                    ),
                    f"HFE loss: {result.leak.hfe_loss_l_per_year:.2f} L/year",
                ]
            ),
            textprops={"fontsize": 16},
        ),
    ]
    packed_box = VPacker(children=text_boxes, align="left", pad=0, sep=8)
    annotation_box = AnchoredOffsetbox(
        loc="center right",
        child=packed_box,
        pad=0.4,
        borderpad=0.6,
        frameon=True,
        bbox_to_anchor=(0.97, 0.50),
        bbox_transform=axis.transAxes,
    )
    annotation_box.patch.set_boxstyle("round,pad=0.4")
    annotation_box.patch.set_facecolor("white")
    annotation_box.patch.set_alpha(0.88)
    axis.add_artist(annotation_box)

    fig.text(
        0.99,
        0.012,
        f"Source log: {result.series.csv_path.name}",
        ha="right",
        va="bottom",
        fontsize=9,
        color="0.45",
    )

    fig.tight_layout()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
    if close:
        plt.close(fig)
    return fig, axis


def default_reservoir_plot_path(
    case: ReservoirLeakCase,
    output_dir: Path = DEFAULT_PLOT_DIR,
) -> Path:
    """Return the default saved-plot path for a reservoir leak test."""

    return output_dir / f"reservoir_leak_test_{case.slug}.png"


def plot_reservoir_leak_result(
    result: ReservoirLeakResult,
    output_path: Path | None = None,
    *,
    close: bool = False,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot a reservoir leak test result, optionally saving the figure."""

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
    axis.set_ylim(0.0, result.case.y_max_bar)
    axis.set_xlabel("Time (hours)", fontsize=18)
    axis.set_ylabel("Reservoir pressure (bar abs)", fontsize=18)
    axis.set_title(f"Reservoir Leak Test - {result.case.label}", fontsize=22)
    axis.tick_params(axis="both", labelsize=14)
    axis.grid(True, alpha=0.3)
    axis.legend(loc="upper right", fontsize=14, framealpha=0.95)

    operating_abs_bar = P_ATM_BAR + result.case.operating_gauge_bar
    text_boxes: list[Artist] = [
        TextArea(
            "HFE-equivalent values",
            textprops={"fontsize": 15, "fontweight": "bold"},
        ),
        TextArea(
            "\n".join(
                [
                    f"Volume: {result.case.volume_l:.0f} L",
                    f"Pressure: {operating_abs_bar:.2f} bar abs (HFE vapor)",
                    (
                        "Leaks: "
                        f"{format_scientific_value(
                            result.leak.throughput_mbar_l_per_s,
                            'mbar·L/s',
                        )}"
                    ),
                    f"HFE loss: {result.leak.hfe_loss_l_per_year:.2f} L/year",
                ]
            ),
            textprops={"fontsize": 14},
        ),
    ]
    packed_box = VPacker(children=text_boxes, align="left", pad=0, sep=8)
    annotation_box = AnchoredOffsetbox(
        loc="center right",
        child=packed_box,
        pad=0.35,
        borderpad=0.55,
        frameon=True,
        bbox_to_anchor=(0.97, 0.52),
        bbox_transform=axis.transAxes,
    )
    annotation_box.patch.set_boxstyle("round,pad=0.4")
    annotation_box.patch.set_facecolor("white")
    annotation_box.patch.set_alpha(0.88)
    axis.add_artist(annotation_box)

    fig.text(
        0.99,
        0.012,
        result.case.source_note,
        ha="right",
        va="bottom",
        fontsize=9,
        color="0.45",
    )

    fig.tight_layout()
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
        "=== Tank pressure evolution exponential fit (1 atm abs N2 gas trap) ===",
        f"CSV file                   : {result.series.csv_path}",
    ]
    if output_path is not None:
        lines.append(f"Saved figure               : {output_path}")
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
            f"Gas trap volume            : {GAS_TRAP_VOLUME_L:.3f} L",
            f"Hydrostatic model height   : {SYSTEM_HEIGHT_M:.3f} m (full system height)",
            f"Effective liquid height    : {SYSTEM_HEIGHT_M:.3f} m",
            f"Tank height                : {TANK_HEIGHT_M:.3f} m",
            (
                f"Required tank fill pressure: "
                f"{result.mean_pressure.required_tank_fill_gauge_bar:.3f} bar gauge"
            ),
            (
                f"Hydrostatic delta over {SYSTEM_HEIGHT_M:.3f} m : "
                f"{result.mean_pressure.hydrostatic_delta_bar:.3f} bar"
            ),
            (
                f"Mean system pressure       : {result.mean_pressure.absolute_bar:.3f} bar abs "
                f"(1 atm abs N2 gas trap)"
            ),
            (
                f"  Leak throughput          : {result.leak.throughput_mbar_l_per_s:.4f} +/- "
                f"{result.leak.throughput_err_mbar_l_per_s:.4f} mbar·L/s"
            ),
            (
                f"  HFE loss                 : {result.leak.hfe_loss_l_per_year:.2f} +/- "
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


def system_pressure_summary_table(results: Sequence[SystemPressureResult]) -> pd.DataFrame:
    """Return a compact summary table for a set of system-pressure analyses."""

    return pd.DataFrame(
        [
            {
                "log_file": result.series.csv_path.name,
                "elapsed_h": float(result.series.time_h[-1]),
                "fit_asymptote_bar_abs": result.fit.asymptote_bar,
                "fit_k_per_h": result.fit.k_per_h,
                "mean_pressure_bar_abs": result.mean_pressure.absolute_bar,
                "leak_mbar_l_s": result.leak.throughput_mbar_l_per_s,
                "hfe_loss_l_year": result.leak.hfe_loss_l_per_year,
                "rmse_mbar": result.rmse_mbar,
            }
            for result in results
        ]
    )


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
