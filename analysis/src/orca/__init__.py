"""ORCA: Operational Recirculation and Cryogenic Analysis.

This package is intentionally kept small:
- ``core.py`` contains loading, filtering, and HX calculations
- ``notebook.py`` contains higher-level notebook helpers and plotting
- ``leaks.py`` contains leak-test analysis helpers
- ``cli.py`` contains the command-line entry point
"""

ORCA_MEANING = "Operational Recirculation and Cryogenic Analysis"

from .core import (
    HeatLeakResult,
    TC_MAP,
    apply_corrections,
    apparent_power,
    bath_capacity_j_per_k,
    fit_heat_leak_and_UA,
    integrate_energy,
    load_tc_csv,
    rolling_slope,
)
from .leaks import (
    ReservoirLeakCase,
    analyze_system_pressure_log,
    analyze_reservoir_case,
    default_system_pressure_plot_path,
    default_reservoir_plot_path,
    format_reservoir_leak_report,
    latest_pressure_log,
    plot_system_pressure_result,
    plot_reservoir_leak_result,
    reservoir_summary_table,
    resolve_pressure_log_path,
    system_pressure_summary_table,
)
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
    plot_heat_leak_fit,
    plot_power_and_flux,
    plot_temperatures,
    plot_temperature_window_fit,
    summarize_windows,
    WindowTemperatureFit,
)

__all__ = [
    "ORCA_MEANING",
    "TC_MAP",
    "load_tc_csv",
    "rolling_slope",
    "HeatLeakResult",
    "apply_corrections",
    "apparent_power",
    "bath_capacity_j_per_k",
    "fit_heat_leak_and_UA",
    "integrate_energy",
    "latest_pressure_log",
    "resolve_pressure_log_path",
    "ReservoirLeakCase",
    "analyze_system_pressure_log",
    "analyze_reservoir_case",
    "plot_system_pressure_result",
    "plot_reservoir_leak_result",
    "default_system_pressure_plot_path",
    "default_reservoir_plot_path",
    "reservoir_summary_table",
    "system_pressure_summary_table",
    "format_reservoir_leak_report",
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
