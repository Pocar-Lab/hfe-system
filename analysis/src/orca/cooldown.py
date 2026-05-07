"""Simple cooldown and heat-exchanger sizing model for the HFE system.

The dissertation-facing model in this module intentionally keeps one clear
physics picture:

* the HFE-side tube area sets the heat-exchanger length required to reject a
  steady heat leak without dropping the tube wall below the HFE freezing limit;
* the cooldown transient is a one-node energy balance with an analytic
  exponential solution.

Older multi-scenario LN2 wetting and gas-side cooldown machinery was removed
from the public ORCA API so this file has one model to audit and cite.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Iterable

import numpy as np
import pandas as pd

from .leaks import hfe_liquid_density_kg_m3

SECONDS_PER_MINUTE = 60.0
SECONDS_PER_HOUR = 3600.0
KELVIN_OFFSET = 273.15
HFE7000_CP_INTERCEPT_J_KG_K = 1_223.2
HFE7000_CP_SLOPE_J_KG_K_PER_C = 3.0803
HFE7200_CP_REFERENCE_TEMP_C = 25.0
HFE7200_CP_REFERENCE_J_KG_K = 1_220.0
# Low-temperature HFE-family HTC basis from HowLowCanYouGo.pdf:
# HFE-7000 at -90 C has Pr ~= 46 and k = 0.0974 W/m/K. The document reports
# Re ~= 3900 in a coiled exchanger; Re = 50 is used here as a deliberately
# weak-mixing design basis for a conservative nominal HFE-side coefficient.
HFE_HTC_BASIS_REYNOLDS = 50.0
HFE_HTC_BASIS_PRANDTL = 46.0
HFE_HTC_BASIS_THERMAL_CONDUCTIVITY_W_M_K = 0.0974
HFE_HTC_NOMINAL_W_M2_K = 250.0
HFE_TANK_VOLUME_L = 2.8
HFE_ADDITIONAL_VOLUME_L = 0.260
HFE_INVENTORY_VOLUME_L = HFE_TANK_VOLUME_L + HFE_ADDITIONAL_VOLUME_L


@dataclass(frozen=True)
class StraightTube:
    """Simple straight-tube geometry."""

    length_m: float
    outer_diameter_m: float
    wall_thickness_m: float

    @property
    def inner_diameter_m(self) -> float:
        return max(self.outer_diameter_m - 2.0 * self.wall_thickness_m, 1e-9)

    @property
    def inner_radius_m(self) -> float:
        return 0.5 * self.inner_diameter_m

    @property
    def outer_radius_m(self) -> float:
        return 0.5 * self.outer_diameter_m

    @property
    def internal_area_m2(self) -> float:
        return math.pi * self.inner_diameter_m * self.length_m

    @property
    def external_area_m2(self) -> float:
        return math.pi * self.outer_diameter_m * self.length_m

    @property
    def internal_volume_m3(self) -> float:
        return self.length_m * math.pi * self.inner_radius_m**2

    @property
    def steel_volume_m3(self) -> float:
        return (
            math.pi * (self.outer_radius_m**2 - self.inner_radius_m**2) * self.length_m
        )


@dataclass(frozen=True)
class CylindricalTank:
    """Tank geometry represented by a simple cylindrical shell."""

    height_m: float
    inner_diameter_m: float
    wall_thickness_m: float

    @property
    def inner_radius_m(self) -> float:
        return 0.5 * self.inner_diameter_m

    @property
    def outer_radius_m(self) -> float:
        return self.inner_radius_m + self.wall_thickness_m

    @property
    def internal_volume_m3(self) -> float:
        return math.pi * self.inner_radius_m**2 * self.height_m

    @property
    def steel_volume_m3(self) -> float:
        return (
            math.pi * (self.outer_radius_m**2 - self.inner_radius_m**2) * self.height_m
        )

    @property
    def cross_section_area_m2(self) -> float:
        return math.pi * self.inner_radius_m**2


@dataclass(frozen=True)
class SystemModel:
    """System geometry and fixed material properties used by ORCA notebooks."""

    process_loop: StraightTube
    tank: CylindricalTank
    heat_exchanger: StraightTube
    tank_liquid_volume_m3: float | None = 0.003
    piping_liquid_volume_m3: float | None = 0.001
    initial_temp_k: float = 298.15
    target_temp_k: float = 163.15
    ambient_temp_k: float = 293.0
    steel_conductivity_w_mk: float = 16.0
    steel_cp_j_kgk: float = 500.0
    steel_density_kg_m3: float = 7_850.0
    inner_wall_hfe_htc_w_m2k: float = 150.0
    ambient_air_htc_w_m2k: float = 8.0
    insulation_thickness_m: float = 0.05
    insulation_conductivity_w_mk: float = 0.03
    insulated_heat_leak_reference_w: float = 15.5846
    insulated_heat_leak_reference_temp_k: float = 278.2561715481171

    @property
    def fluid_volume_m3(self) -> float:
        tank_volume_m3 = (
            self.tank.internal_volume_m3
            if self.tank_liquid_volume_m3 is None
            else self.tank_liquid_volume_m3
        )
        piping_volume_m3 = (
            self.process_loop.internal_volume_m3
            if self.piping_liquid_volume_m3 is None
            else self.piping_liquid_volume_m3
        )
        return piping_volume_m3 + tank_volume_m3

    @property
    def steel_mass_kg(self) -> float:
        return (
            self.process_loop.steel_volume_m3
            + self.tank.steel_volume_m3
            + self.heat_exchanger.steel_volume_m3
        ) * self.steel_density_kg_m3

    def loop_turnover_time_s(self, flow_lpm: float) -> float:
        flow_m3_s = lpm_to_m3_s(flow_lpm)
        if flow_m3_s <= 0.0:
            return math.inf
        return self.fluid_volume_m3 / flow_m3_s


@dataclass(frozen=True)
class CooldownDesignInputs:
    """Inputs for the single HFE cooldown and HX sizing model."""

    room_temp_c: float = 25.0
    ambient_temp_c: float = 25.0
    target_temp_c: float = -110.0
    freeze_temp_c: float = -121.0
    heat_leak_target_w: float = 200.0
    hfe_volume_l: float = HFE_INVENTORY_VOLUME_L
    hfe_cp_25c_j_kg_k: float = HFE7200_CP_REFERENCE_J_KG_K
    hfe_htc_w_m2_k: float = HFE_HTC_NOMINAL_W_M2_K
    installed_length_m: float = 6.0
    tube_outer_diameter_m: float = 0.25 * 0.0254
    tube_wall_thickness_m: float = 0.05 * 0.0254
    ln2_latent_heat_j_kg: float = 165.0e3
    ln2_density_kg_m3: float = 808.0
    time_point_count: int = 1_200
    minimum_plot_time_min: float = 60.0


@dataclass(frozen=True)
class HfeHtcBasis:
    """Traceable basis for the nominal HFE-side heat-transfer coefficient."""

    reynolds: float
    prandtl: float
    thermal_conductivity_w_m_k: float
    diameter_m: float
    nusselt: float
    h_basis_w_m2_k: float
    nominal_h_w_m2_k: float


@dataclass(frozen=True)
class CooldownDesignResult:
    """Result and time history from the analytic cooldown model."""

    inputs: CooldownDesignInputs
    hfe_volume_l: float
    hfe_mass_room_kg: float
    hfe_mass_target_kg: float
    hfe_cp_room_j_kg_k: float
    hfe_cp_target_j_kg_k: float
    hfe_cp_effective_j_kg_k: float
    hfe_volumetric_heat_capacity_effective_j_m3_k: float
    installed_area_m2: float
    required_length_m: float
    critical_hfe_htc_w_m2_k: float
    required_hfe_ua_w_per_k: float
    installed_hfe_ua_w_per_k: float
    installed_margin: float
    wall_temp_at_200w_c: float
    ambient_ua_w_per_k: float
    equilibrium_temp_c: float
    thermal_time_constant_s: float
    time_to_target_s: float
    energy_to_target_j: float
    ln2_to_target_kg: float
    ln2_hold_mass_flow_kg_s: float
    ln2_hold_volume_flow_lpm: float
    time_s: np.ndarray
    bulk_temp_c: np.ndarray
    heat_leak_w: np.ndarray
    hx_power_w: np.ndarray
    absorbed_energy_j: np.ndarray
    ln2_consumed_kg: np.ndarray

    @property
    def time_to_target_min(self) -> float:
        return self.time_to_target_s / SECONDS_PER_MINUTE

    @property
    def thermal_time_constant_min(self) -> float:
        return self.thermal_time_constant_s / SECONDS_PER_MINUTE

    @property
    def energy_to_target_mj(self) -> float:
        return self.energy_to_target_j / 1e6

    @property
    def hfe_mass_cold_equivalent_kg(self) -> float:
        """Backward-compatible name for the target-temperature volume mass."""

        return self.hfe_mass_target_kg

    @property
    def room_reference_volume_l(self) -> float:
        """Backward-compatible name for the fixed inventory volume."""

        return self.hfe_volume_l

    def history_frame(self) -> pd.DataFrame:
        """Return the simulated time history as a dataframe for plotting."""

        return pd.DataFrame(
            {
                "time_s": self.time_s,
                "time_min": self.time_s / SECONDS_PER_MINUTE,
                "bulk_temp_c": self.bulk_temp_c,
                "heat_leak_w": self.heat_leak_w,
                "hx_power_w": self.hx_power_w,
                "absorbed_energy_j": self.absorbed_energy_j,
                "ln2_consumed_kg": self.ln2_consumed_kg,
            }
        )


def default_system_model() -> SystemModel:
    """Return the current repository-default HFE geometry."""

    return SystemModel(
        process_loop=StraightTube(
            length_m=4.5,
            outer_diameter_m=3.0 / 8.0 * 0.0254,
            wall_thickness_m=0.035 * 0.0254,
        ),
        tank=CylindricalTank(
            height_m=0.489,
            inner_diameter_m=0.096,
            wall_thickness_m=0.0055,
        ),
        heat_exchanger=StraightTube(
            length_m=6.0,
            outer_diameter_m=0.25 * 0.0254,
            wall_thickness_m=0.05 * 0.0254,
        ),
        tank_liquid_volume_m3=HFE_TANK_VOLUME_L * 1e-3,
        piping_liquid_volume_m3=HFE_ADDITIONAL_VOLUME_L * 1e-3,
    )


def default_cooldown_design_inputs() -> CooldownDesignInputs:
    """Return the nominal dissertation cooldown-design inputs."""

    return CooldownDesignInputs()


def lpm_to_m3_s(flow_lpm: float) -> float:
    """Convert volumetric flow from L/min to m^3/s."""

    return max(flow_lpm, 0.0) / 60_000.0


def celsius_to_kelvin(temp_c: float) -> float:
    """Convert C to K."""

    return float(temp_c + KELVIN_OFFSET)


def kelvin_to_celsius(temp_k: float) -> float:
    """Convert K to C."""

    return float(temp_k - KELVIN_OFFSET)


def hfe_density_kg_m3(temp_k: float) -> float:
    """Return the HFE-7200 liquid density fit in kg/m^3."""

    return hfe_liquid_density_kg_m3(kelvin_to_celsius(temp_k))


def hfe7000_specific_heat_j_kg_k(temp_c: float | np.ndarray) -> float | np.ndarray:
    """Return the HFE-7000 liquid heat-capacity fit used as a temperature shape."""

    cp = HFE7000_CP_INTERCEPT_J_KG_K + HFE7000_CP_SLOPE_J_KG_K_PER_C * np.asarray(
        temp_c,
        dtype=float,
    )
    if np.ndim(cp) == 0:
        return float(cp)
    return cp


def hfe7200_specific_heat_j_kg_k(
    temp_c: float | np.ndarray,
    *,
    reference_cp_25c_j_kg_k: float = HFE7200_CP_REFERENCE_J_KG_K,
) -> float | np.ndarray:
    """Return HFE-7200 heat capacity scaled from the HFE-7000 temperature trend."""

    scale = reference_cp_25c_j_kg_k / hfe7000_specific_heat_j_kg_k(
        HFE7200_CP_REFERENCE_TEMP_C
    )
    cp = scale * np.asarray(hfe7000_specific_heat_j_kg_k(temp_c), dtype=float)
    if np.ndim(cp) == 0:
        return float(cp)
    return cp


def hfe7200_average_specific_heat_j_kg_k(
    start_temp_c: float,
    end_temp_c: float,
    *,
    reference_cp_25c_j_kg_k: float = HFE7200_CP_REFERENCE_J_KG_K,
) -> float:
    """Return the path-average HFE-7200 heat capacity for a linear cp(T) fit."""

    cp_start = hfe7200_specific_heat_j_kg_k(
        start_temp_c,
        reference_cp_25c_j_kg_k=reference_cp_25c_j_kg_k,
    )
    cp_end = hfe7200_specific_heat_j_kg_k(
        end_temp_c,
        reference_cp_25c_j_kg_k=reference_cp_25c_j_kg_k,
    )
    return float(0.5 * (cp_start + cp_end))


def hfe_mass_from_volume_kg(volume_l: float, temp_c: float) -> float:
    """Return HFE-7200 mass for a liquid volume at the requested temperature."""

    if volume_l <= 0.0:
        raise ValueError("volume_l must be positive.")

    return float(volume_l * 1e-3 * hfe_liquid_density_kg_m3(temp_c))


def hfe7200_average_volumetric_heat_capacity_j_m3_k(
    start_temp_c: float,
    end_temp_c: float,
    *,
    reference_cp_25c_j_kg_k: float = HFE7200_CP_REFERENCE_J_KG_K,
    samples: int = 512,
) -> float:
    """Return path-average rho*cp for fixed-volume HFE-7200 inventory."""

    if samples < 2:
        raise ValueError("samples must be at least 2.")

    temps_c = np.linspace(end_temp_c, start_temp_c, samples)
    density_kg_m3 = np.asarray(
        [hfe_liquid_density_kg_m3(temp_c) for temp_c in temps_c],
        dtype=float,
    )
    cp_j_kg_k = np.asarray(
        hfe7200_specific_heat_j_kg_k(
            temps_c,
            reference_cp_25c_j_kg_k=reference_cp_25c_j_kg_k,
        ),
        dtype=float,
    )
    return float(
        np.trapz(density_kg_m3 * cp_j_kg_k, temps_c)
        / (start_temp_c - end_temp_c)
    )


def hfe_specific_heat_j_kgk(temp_k: float) -> float:
    """Return scaled HFE-7200 liquid heat capacity for a temperature in K."""

    return float(hfe7200_specific_heat_j_kg_k(kelvin_to_celsius(temp_k)))


def churchill_bernstein_cylinder_nusselt(
    reynolds: float,
    prandtl: float,
) -> float:
    """Return the Churchill-Bernstein average Nu for cylinder cross-flow."""

    if reynolds <= 0.0:
        raise ValueError("reynolds must be positive.")
    if prandtl <= 0.0:
        raise ValueError("prandtl must be positive.")

    return float(
        0.3
        + (
            0.62
            * reynolds**0.5
            * prandtl ** (1.0 / 3.0)
            / (1.0 + (0.4 / prandtl) ** (2.0 / 3.0)) ** 0.25
            * (1.0 + (reynolds / 282_000.0) ** (5.0 / 8.0)) ** (4.0 / 5.0)
        )
    )


def cylinder_crossflow_htc_w_m2_k(
    *,
    reynolds: float,
    prandtl: float,
    thermal_conductivity_w_m_k: float,
    diameter_m: float,
) -> float:
    """Return h from Nu = hD/k for cross-flow over a circular cylinder."""

    if thermal_conductivity_w_m_k <= 0.0:
        raise ValueError("thermal_conductivity_w_m_k must be positive.")
    if diameter_m <= 0.0:
        raise ValueError("diameter_m must be positive.")

    nusselt = churchill_bernstein_cylinder_nusselt(reynolds, prandtl)
    return float(nusselt * thermal_conductivity_w_m_k / diameter_m)


def nominal_hfe_htc_basis_w_m2_k(
    tube_outer_diameter_m: float,
    *,
    reynolds: float = HFE_HTC_BASIS_REYNOLDS,
    prandtl: float = HFE_HTC_BASIS_PRANDTL,
    thermal_conductivity_w_m_k: float = HFE_HTC_BASIS_THERMAL_CONDUCTIVITY_W_M_K,
) -> float:
    """Return the low-Re cylinder cross-flow basis for the nominal HFE HTC."""

    return cylinder_crossflow_htc_w_m2_k(
        reynolds=reynolds,
        prandtl=prandtl,
        thermal_conductivity_w_m_k=thermal_conductivity_w_m_k,
        diameter_m=tube_outer_diameter_m,
    )


def nominal_hfe_htc_basis(
    tube_outer_diameter_m: float,
    *,
    reynolds: float = HFE_HTC_BASIS_REYNOLDS,
    prandtl: float = HFE_HTC_BASIS_PRANDTL,
    thermal_conductivity_w_m_k: float = HFE_HTC_BASIS_THERMAL_CONDUCTIVITY_W_M_K,
    nominal_h_w_m2_k: float = HFE_HTC_NOMINAL_W_M2_K,
) -> HfeHtcBasis:
    """Return the complete low-Re basis for the nominal HFE HTC."""

    nusselt = churchill_bernstein_cylinder_nusselt(reynolds, prandtl)
    h_basis = cylinder_crossflow_htc_w_m2_k(
        reynolds=reynolds,
        prandtl=prandtl,
        thermal_conductivity_w_m_k=thermal_conductivity_w_m_k,
        diameter_m=tube_outer_diameter_m,
    )
    return HfeHtcBasis(
        reynolds=float(reynolds),
        prandtl=float(prandtl),
        thermal_conductivity_w_m_k=float(thermal_conductivity_w_m_k),
        diameter_m=float(tube_outer_diameter_m),
        nusselt=nusselt,
        h_basis_w_m2_k=h_basis,
        nominal_h_w_m2_k=float(nominal_h_w_m2_k),
    )


def thermal_capacity_j_per_k(model: SystemModel, temp_k: float) -> float:
    """Return total lumped heat capacity of HFE plus steel hardware."""

    fluid_mass_kg = model.fluid_volume_m3 * hfe_density_kg_m3(temp_k)
    return fluid_mass_kg * hfe_specific_heat_j_kgk(temp_k) + (
        model.steel_mass_kg * model.steel_cp_j_kgk
    )


def cooldown_energy_j(
    model: SystemModel,
    *,
    start_temp_k: float | None = None,
    end_temp_k: float | None = None,
    samples: int = 512,
) -> float:
    """Return the sensible energy removed to cool from start to end."""

    start = model.initial_temp_k if start_temp_k is None else float(start_temp_k)
    end = model.target_temp_k if end_temp_k is None else float(end_temp_k)
    if end > start:
        raise ValueError("end_temp_k must be less than or equal to start_temp_k.")
    temps = np.linspace(end, start, samples)
    capacities = np.asarray([thermal_capacity_j_per_k(model, t) for t in temps], dtype=float)
    return float(np.trapz(capacities, temps))


def theoretical_min_ln2_kg(
    model: SystemModel,
    *,
    start_temp_k: float | None = None,
    end_temp_k: float | None = None,
    ln2_latent_heat_j_kg: float = 165.0e3,
) -> float:
    """Latent-only lower bound on LN2 mass for a sensible cooldown."""

    return cooldown_energy_j(
        model,
        start_temp_k=start_temp_k,
        end_temp_k=end_temp_k,
    ) / ln2_latent_heat_j_kg


def cylinder_ambient_ua_geometry_w_per_k(
    *,
    length_m: float,
    inner_radius_m: float,
    wall_thickness_m: float,
    inner_htc_w_m2k: float,
    wall_conductivity_w_mk: float,
    air_htc_w_m2k: float,
    use_insulation: bool,
    insulation_thickness_m: float,
    insulation_conductivity_w_mk: float,
) -> float:
    """Ambient UA for a cylindrical shell using a side-wall resistance model."""

    if length_m <= 0.0 or inner_radius_m <= 0.0:
        return 0.0

    steel_outer_radius_m = inner_radius_m + wall_thickness_m
    area_inner_m2 = 2.0 * math.pi * inner_radius_m * length_m
    resistance_inner = 1.0 / max(inner_htc_w_m2k * area_inner_m2, 1e-12)
    resistance_wall = math.log(steel_outer_radius_m / inner_radius_m) / (
        2.0 * math.pi * wall_conductivity_w_mk * length_m
    )

    if use_insulation:
        insulation_outer_radius_m = steel_outer_radius_m + insulation_thickness_m
        resistance_insulation = math.log(
            insulation_outer_radius_m / steel_outer_radius_m
        ) / (2.0 * math.pi * insulation_conductivity_w_mk * length_m)
        resistance_outer = 1.0 / max(
            air_htc_w_m2k * 2.0 * math.pi * insulation_outer_radius_m * length_m,
            1e-12,
        )
        resistance_total = (
            resistance_inner + resistance_wall + resistance_insulation + resistance_outer
        )
    else:
        resistance_outer = 1.0 / max(
            air_htc_w_m2k * 2.0 * math.pi * steel_outer_radius_m * length_m,
            1e-12,
        )
        resistance_total = resistance_inner + resistance_wall + resistance_outer

    return 1.0 / resistance_total


def ambient_leak_ua_geometry_w_per_k(model: SystemModel, *, use_insulation: bool) -> float:
    """Return the uncalibrated geometry-only ambient leak coefficient."""

    tank_ua = cylinder_ambient_ua_geometry_w_per_k(
        length_m=model.tank.height_m,
        inner_radius_m=model.tank.inner_radius_m,
        wall_thickness_m=model.tank.wall_thickness_m,
        inner_htc_w_m2k=model.inner_wall_hfe_htc_w_m2k,
        wall_conductivity_w_mk=model.steel_conductivity_w_mk,
        air_htc_w_m2k=model.ambient_air_htc_w_m2k,
        use_insulation=use_insulation,
        insulation_thickness_m=model.insulation_thickness_m,
        insulation_conductivity_w_mk=model.insulation_conductivity_w_mk,
    )
    loop_ua = cylinder_ambient_ua_geometry_w_per_k(
        length_m=model.process_loop.length_m,
        inner_radius_m=model.process_loop.inner_radius_m,
        wall_thickness_m=model.process_loop.wall_thickness_m,
        inner_htc_w_m2k=model.inner_wall_hfe_htc_w_m2k,
        wall_conductivity_w_mk=model.steel_conductivity_w_mk,
        air_htc_w_m2k=model.ambient_air_htc_w_m2k,
        use_insulation=use_insulation,
        insulation_thickness_m=model.insulation_thickness_m,
        insulation_conductivity_w_mk=model.insulation_conductivity_w_mk,
    )
    return tank_ua + loop_ua


def ambient_leak_ua_w_per_k(model: SystemModel, *, use_insulation: bool) -> float:
    """Return ambient leak UA calibrated to the measured insulated warm-up."""

    geometry_insulated = ambient_leak_ua_geometry_w_per_k(model, use_insulation=True)
    geometry_requested = ambient_leak_ua_geometry_w_per_k(
        model,
        use_insulation=use_insulation,
    )
    delta_t_reference_k = model.ambient_temp_k - model.insulated_heat_leak_reference_temp_k
    if geometry_insulated <= 0.0 or delta_t_reference_k <= 0.0:
        return geometry_requested

    measured_insulated_ua = model.insulated_heat_leak_reference_w / delta_t_reference_k
    scale = measured_insulated_ua / geometry_insulated
    return geometry_requested * scale


def scale_hfe_mass_to_temperature(
    room_reference_mass_kg: float,
    *,
    room_temp_c: float,
    target_temp_c: float,
) -> float:
    """Scale a room-temperature HFE mass by density ratio to a target temperature.

    This is a fixed-volume equivalent mass, not a statement that physical mass is
    created during cooldown. It is useful when the known inventory is tied to a
    room-temperature filled volume.
    """

    if room_reference_mass_kg <= 0.0:
        raise ValueError("room_reference_mass_kg must be positive.")

    rho_room = hfe_liquid_density_kg_m3(room_temp_c)
    rho_target = hfe_liquid_density_kg_m3(target_temp_c)
    return float(room_reference_mass_kg * rho_target / rho_room)


def required_hx_length_m(
    *,
    heat_leak_w: float,
    tube_outer_diameter_m: float,
    hfe_htc_w_m2_k: float,
    target_temp_c: float,
    freeze_temp_c: float,
) -> float:
    """Return tube length required by the HFE-side wall-temperature limit."""

    delta_t_k = target_temp_c - freeze_temp_c
    if heat_leak_w <= 0.0:
        raise ValueError("heat_leak_w must be positive.")
    if tube_outer_diameter_m <= 0.0:
        raise ValueError("tube_outer_diameter_m must be positive.")
    if hfe_htc_w_m2_k <= 0.0:
        raise ValueError("hfe_htc_w_m2_k must be positive.")
    if delta_t_k <= 0.0:
        raise ValueError("target_temp_c must be warmer than freeze_temp_c.")

    return float(heat_leak_w / (math.pi * tube_outer_diameter_m * hfe_htc_w_m2_k * delta_t_k))


def _energy_removed_j(
    *,
    ua_hx_w_per_k: float,
    wall_temp_k: float,
    initial_temp_k: float,
    equilibrium_temp_k: float,
    tau_s: float,
    time_s: np.ndarray | float,
) -> np.ndarray:
    time = np.asarray(time_s, dtype=float)
    return ua_hx_w_per_k * (
        (equilibrium_temp_k - wall_temp_k) * time
        + (initial_temp_k - equilibrium_temp_k)
        * tau_s
        * (1.0 - np.exp(-time / tau_s))
    )


def simulate_simple_cooldown(
    inputs: CooldownDesignInputs | None = None,
    *,
    hfe_htc_w_m2_k: float | None = None,
    installed_length_m: float | None = None,
    time_s: Iterable[float] | None = None,
) -> CooldownDesignResult:
    """Run the analytic one-node cooldown model."""

    base = default_cooldown_design_inputs() if inputs is None else inputs
    selected = replace(
        base,
        hfe_htc_w_m2_k=base.hfe_htc_w_m2_k if hfe_htc_w_m2_k is None else hfe_htc_w_m2_k,
        installed_length_m=base.installed_length_m if installed_length_m is None else installed_length_m,
    )

    target_temp_k = celsius_to_kelvin(selected.target_temp_c)
    freeze_temp_k = celsius_to_kelvin(selected.freeze_temp_c)
    ambient_temp_k = celsius_to_kelvin(selected.ambient_temp_c)
    initial_temp_k = celsius_to_kelvin(selected.room_temp_c)

    hfe_mass_room_kg = hfe_mass_from_volume_kg(
        selected.hfe_volume_l,
        selected.room_temp_c,
    )
    hfe_mass_target_kg = hfe_mass_from_volume_kg(
        selected.hfe_volume_l,
        selected.target_temp_c,
    )
    hfe_cp_room = float(
        hfe7200_specific_heat_j_kg_k(
            selected.room_temp_c,
            reference_cp_25c_j_kg_k=selected.hfe_cp_25c_j_kg_k,
        )
    )
    hfe_cp_target = float(
        hfe7200_specific_heat_j_kg_k(
            selected.target_temp_c,
            reference_cp_25c_j_kg_k=selected.hfe_cp_25c_j_kg_k,
        )
    )
    hfe_cp_effective = hfe7200_average_specific_heat_j_kg_k(
        selected.room_temp_c,
        selected.target_temp_c,
        reference_cp_25c_j_kg_k=selected.hfe_cp_25c_j_kg_k,
    )
    hfe_volumetric_heat_capacity_effective = (
        hfe7200_average_volumetric_heat_capacity_j_m3_k(
            selected.room_temp_c,
            selected.target_temp_c,
            reference_cp_25c_j_kg_k=selected.hfe_cp_25c_j_kg_k,
        )
    )
    installed_area_m2 = math.pi * selected.tube_outer_diameter_m * selected.installed_length_m
    required_length = required_hx_length_m(
        heat_leak_w=selected.heat_leak_target_w,
        tube_outer_diameter_m=selected.tube_outer_diameter_m,
        hfe_htc_w_m2_k=selected.hfe_htc_w_m2_k,
        target_temp_c=selected.target_temp_c,
        freeze_temp_c=selected.freeze_temp_c,
    )
    required_hfe_ua = selected.heat_leak_target_w / (
        selected.target_temp_c - selected.freeze_temp_c
    )
    installed_hfe_ua = selected.hfe_htc_w_m2_k * installed_area_m2
    critical_hfe_htc = required_hfe_ua / installed_area_m2
    installed_margin = installed_hfe_ua / required_hfe_ua
    wall_temp_at_200w_c = selected.target_temp_c - (
        selected.heat_leak_target_w / installed_hfe_ua
    )
    ambient_ua = selected.heat_leak_target_w / (
        selected.ambient_temp_c - selected.target_temp_c
    )
    heat_capacity = selected.hfe_volume_l * 1e-3 * hfe_volumetric_heat_capacity_effective
    total_ua = ambient_ua + installed_hfe_ua
    equilibrium_temp_k = (
        ambient_ua * ambient_temp_k + installed_hfe_ua * freeze_temp_k
    ) / total_ua
    tau_s = heat_capacity / total_ua

    if initial_temp_k <= target_temp_k:
        time_to_target_s = 0.0
    elif equilibrium_temp_k < target_temp_k:
        time_to_target_s = float(
            -tau_s
            * math.log(
                (target_temp_k - equilibrium_temp_k)
                / (initial_temp_k - equilibrium_temp_k)
            )
        )
    else:
        time_to_target_s = math.nan

    if math.isfinite(time_to_target_s):
        energy_to_target_j = float(
            _energy_removed_j(
                ua_hx_w_per_k=installed_hfe_ua,
                wall_temp_k=freeze_temp_k,
                initial_temp_k=initial_temp_k,
                equilibrium_temp_k=equilibrium_temp_k,
                tau_s=tau_s,
                time_s=time_to_target_s,
            )
        )
    else:
        energy_to_target_j = math.nan

    if time_s is None:
        plot_end_s = selected.minimum_plot_time_min * SECONDS_PER_MINUTE
        if math.isfinite(time_to_target_s):
            plot_end_s = max(plot_end_s, 1.25 * time_to_target_s)
        time_array = np.linspace(0.0, plot_end_s, selected.time_point_count)
    else:
        time_array = np.asarray(list(time_s), dtype=float)
        if time_array.ndim != 1:
            raise ValueError("time_s must be one-dimensional.")
        if np.any(time_array < 0.0):
            raise ValueError("time_s must be non-negative.")

    temp_k = equilibrium_temp_k + (initial_temp_k - equilibrium_temp_k) * np.exp(
        -time_array / tau_s
    )
    heat_leak_w = ambient_ua * (ambient_temp_k - temp_k)
    hx_power_w = installed_hfe_ua * (temp_k - freeze_temp_k)
    absorbed_energy_j = _energy_removed_j(
        ua_hx_w_per_k=installed_hfe_ua,
        wall_temp_k=freeze_temp_k,
        initial_temp_k=initial_temp_k,
        equilibrium_temp_k=equilibrium_temp_k,
        tau_s=tau_s,
        time_s=time_array,
    )
    ln2_consumed_kg = absorbed_energy_j / selected.ln2_latent_heat_j_kg

    ln2_hold_mass_flow_kg_s = selected.heat_leak_target_w / selected.ln2_latent_heat_j_kg
    ln2_hold_volume_flow_lpm = ln2_hold_mass_flow_kg_s / selected.ln2_density_kg_m3 * 60_000.0

    return CooldownDesignResult(
        inputs=selected,
        hfe_volume_l=selected.hfe_volume_l,
        hfe_mass_room_kg=hfe_mass_room_kg,
        hfe_mass_target_kg=hfe_mass_target_kg,
        hfe_cp_room_j_kg_k=hfe_cp_room,
        hfe_cp_target_j_kg_k=hfe_cp_target,
        hfe_cp_effective_j_kg_k=hfe_cp_effective,
        hfe_volumetric_heat_capacity_effective_j_m3_k=hfe_volumetric_heat_capacity_effective,
        installed_area_m2=installed_area_m2,
        required_length_m=required_length,
        critical_hfe_htc_w_m2_k=critical_hfe_htc,
        required_hfe_ua_w_per_k=required_hfe_ua,
        installed_hfe_ua_w_per_k=installed_hfe_ua,
        installed_margin=installed_margin,
        wall_temp_at_200w_c=wall_temp_at_200w_c,
        ambient_ua_w_per_k=ambient_ua,
        equilibrium_temp_c=kelvin_to_celsius(equilibrium_temp_k),
        thermal_time_constant_s=tau_s,
        time_to_target_s=time_to_target_s,
        energy_to_target_j=energy_to_target_j,
        ln2_to_target_kg=energy_to_target_j / selected.ln2_latent_heat_j_kg,
        ln2_hold_mass_flow_kg_s=ln2_hold_mass_flow_kg_s,
        ln2_hold_volume_flow_lpm=ln2_hold_volume_flow_lpm,
        time_s=time_array,
        bulk_temp_c=temp_k - KELVIN_OFFSET,
        heat_leak_w=heat_leak_w,
        hx_power_w=hx_power_w,
        absorbed_energy_j=absorbed_energy_j,
        ln2_consumed_kg=ln2_consumed_kg,
    )


def cooldown_sensitivity_table(
    hfe_htc_values_w_m2_k: Iterable[float],
    inputs: CooldownDesignInputs | None = None,
) -> pd.DataFrame:
    """Return HX length and cooldown metrics across HFE-side HTC values."""

    base = default_cooldown_design_inputs() if inputs is None else inputs
    rows: list[dict[str, float]] = []
    for hfe_htc in hfe_htc_values_w_m2_k:
        result = simulate_simple_cooldown(base, hfe_htc_w_m2_k=float(hfe_htc))
        rows.append(
            {
                "h_HFE_W_m2_K": float(hfe_htc),
                "required_length_m": result.required_length_m,
                "installed_margin": result.installed_margin,
                "wall_temp_at_200w_C": result.wall_temp_at_200w_c,
                "equilibrium_temp_C": result.equilibrium_temp_c,
                "time_to_target_min": result.time_to_target_min,
                "energy_to_target_MJ": result.energy_to_target_mj,
                "ln2_to_target_kg": result.ln2_to_target_kg,
            }
        )
    return pd.DataFrame(rows)


__all__ = [
    "SECONDS_PER_MINUTE",
    "SECONDS_PER_HOUR",
    "KELVIN_OFFSET",
    "HFE_TANK_VOLUME_L",
    "HFE_ADDITIONAL_VOLUME_L",
    "HFE_INVENTORY_VOLUME_L",
    "StraightTube",
    "CylindricalTank",
    "SystemModel",
    "CooldownDesignInputs",
    "HfeHtcBasis",
    "CooldownDesignResult",
    "default_system_model",
    "default_cooldown_design_inputs",
    "lpm_to_m3_s",
    "celsius_to_kelvin",
    "kelvin_to_celsius",
    "hfe_density_kg_m3",
    "hfe7000_specific_heat_j_kg_k",
    "hfe7200_specific_heat_j_kg_k",
    "hfe7200_average_specific_heat_j_kg_k",
    "hfe_mass_from_volume_kg",
    "hfe7200_average_volumetric_heat_capacity_j_m3_k",
    "hfe_specific_heat_j_kgk",
    "churchill_bernstein_cylinder_nusselt",
    "cylinder_crossflow_htc_w_m2_k",
    "nominal_hfe_htc_basis",
    "nominal_hfe_htc_basis_w_m2_k",
    "thermal_capacity_j_per_k",
    "cooldown_energy_j",
    "theoretical_min_ln2_kg",
    "cylinder_ambient_ua_geometry_w_per_k",
    "ambient_leak_ua_geometry_w_per_k",
    "ambient_leak_ua_w_per_k",
    "scale_hfe_mass_to_temperature",
    "required_hx_length_m",
    "simulate_simple_cooldown",
    "cooldown_sensitivity_table",
]
