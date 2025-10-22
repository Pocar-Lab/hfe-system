"""Utilities for heat-exchanger analysis and reporting."""

from .io import TC_MAP, load_tc_csv
from .filters import rolling_slope
from .hx import (
    HeatLeakResult,
    apply_corrections,
    apparent_power,
    bath_capacity_j_per_k,
    fit_heat_leak_and_UA,
    integrate_energy,
)

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
]
