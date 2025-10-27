"""Utilities for heat-exchanger analysis and reporting."""

from .filters import rolling_slope
from .hx import (
    HeatLeakResult,
    apply_corrections,
    apparent_power,
    bath_capacity_j_per_k,
    fit_heat_leak_and_UA,
    integrate_energy,
)

from .io import TC_MAP, load_tc_csv
from .notebook import (
    HeatLeakFit,
    fit_heat_leak_linear,
    predict_heat_leak_fit,
    fit_ua_from_corrected,
    fit_temperature_window,
    heat_leak_windows,
    heat_leak_subset,
    integrate_corrected_power,
    linear_trend,
    prepare_dataset,
    plot_temperature_window_fit,
    summarize_windows,
    WindowTemperatureFit,
)
from .viz import plot_heat_leak_fit, plot_power_and_flux, plot_temperatures

__all__ = [
    "TC_MAP",
    "load_tc_csv",
    "rolling_slope",
    "HeatLeakResult",
    "apply_corrections",
    "apparent_power",
    "bath_capacity_j_per_k",
    "fit_heat_leak_and_UA",
    "integrate_energy",
    "prepare_dataset",
    "linear_trend",
    "heat_leak_windows",
    "heat_leak_subset",
    "fit_heat_leak_linear",
    "predict_heat_leak_fit",
    "fit_ua_from_corrected",
    "fit_temperature_window",
    "integrate_corrected_power",
    "summarize_windows",
    "HeatLeakFit",
    "WindowTemperatureFit",
    "plot_temperatures",
    "plot_power_and_flux",
    "plot_temperature_window_fit",
    "plot_heat_leak_fit",
]
