"""Cooldown modeling helpers for the HFE system.

This module packages the reusable solver logic that previously lived in the
legacy standalone cooldown scripts.

Geometry and leak calibration sources inside this repository:

* ``analysis/notebooks/HFE_cooldown.ipynb`` carries the legacy geometry and
  notes that those dimensions were validated on hardware.
* That same notebook anchors the insulated ambient heat leak to a measured
  warm-up value of 15.5846 W at 278.256 K.
* ``clients/web/index.html`` and ``firmware/src/main.cpp`` both note an HFE
  pump ceiling of about 4.0 L/min, which is used here as a sanity bound for
  the default flow scenarios.

Important remaining modeling uncertainties:

* The liquid inventory now uses 3 L in the tank and about 1 L in the piping,
  per the latest confirmed system values. The exposed piping length that
  should go with that 1 L inventory is still not fully pinned down in the repo.
* The stagnant HFE film thickness around the coil remains a tunable
  assumption. The legacy notebook used 15 mm; the packaged default here is
  2 mm because the larger film suppressed HX UA enough that the calibrated
  model could not reproduce the cooldown behavior implied elsewhere in the repo.
* The external HFE-side coil coupling is intentionally modeled with a simple
  flow-only HTC surrogate anchored near the nominal operating point. The repo
  no longer carries a viscosity-driven transport correlation in this model.
* The model now uses 6 m of 1/4 in OD stainless tubing for the heat
  exchanger, which gives about 0.12 m^2 of external area and matches the
  latest confirmed hardware description.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Sequence

import numpy as np

from .leaks import hfe_liquid_density_kg_m3

SECONDS_PER_MINUTE = 60.0
SECONDS_PER_HOUR = 3600.0
KELVIN_OFFSET = 273.15
DEFAULT_PUMP_MAX_FLOW_LPM = 4.0


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
    """System geometry and fixed material properties."""

    process_loop: StraightTube
    tank: CylindricalTank
    heat_exchanger: StraightTube
    tank_liquid_volume_m3: float | None = 0.003
    piping_liquid_volume_m3: float | None = 0.001
    initial_temp_k: float = 298.0
    target_temp_k: float = 170.0
    ambient_temp_k: float = 293.0
    ln2_saturation_temp_k: float = 77.0
    steel_conductivity_w_mk: float = 16.0
    steel_cp_j_kgk: float = 500.0
    steel_density_kg_m3: float = 7_850.0
    inner_wall_hfe_htc_w_m2k: float = 150.0
    ambient_air_htc_w_m2k: float = 8.0
    insulation_thickness_m: float = 0.05
    insulation_conductivity_w_mk: float = 0.03
    stagnant_hfe_film_thickness_m: float = 0.002
    hfe_liquid_conductivity_w_mk: float = 0.065
    outer_hfe_htc_reference_w_m2k: float = 120.0
    outer_hfe_htc_reference_flow_lpm: float = 2.0
    outer_hfe_htc_flow_exponent: float = 0.5
    ln2_density_kg_m3: float = 808.0
    ln2_latent_heat_j_kg: float = 1.99e5
    n2_gas_cp_j_kgk: float = 1_040.0
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
class CooldownScenario:
    """Cooldown-case definition."""

    name: str
    h_boil_w_m2k: float
    h_gas_w_m2k: float
    ln2_flow_lpm: float
    hfe_flow_lpm: float
    use_insulation: bool = True
    notes: str = ""


@dataclass(frozen=True)
class PowerBreakdown:
    """Instantaneous heat-transfer terms at one bulk temperature."""

    ambient_heat_w: float
    removed_heat_w: float
    liquid_ua_w_per_k: float
    gas_ua_w_per_k: float
    outer_hfe_htc_w_m2k: float
    liquid_coil_fraction: float
    latent_capacity_w: float
    gas_sensible_capacity_w: float


@dataclass(frozen=True)
class CooldownResult:
    """Time history from one cooldown simulation."""

    scenario: CooldownScenario
    time_s: np.ndarray
    bulk_temp_k: np.ndarray
    ambient_heat_w: np.ndarray
    removed_heat_w: np.ndarray
    liquid_ua_w_per_k: np.ndarray
    gas_ua_w_per_k: np.ndarray
    outer_hfe_htc_w_m2k: np.ndarray
    liquid_coil_fraction: np.ndarray
    delivered_ln2_kg: np.ndarray
    absorbed_energy_j: np.ndarray
    ambient_ua_w_per_k: float
    turnover_time_s: float
    ln2_latent_heat_j_kg: float
    theoretical_min_ln2_kg: float
    time_to_target_min: float


def default_system_model() -> SystemModel:
    """Return the current repository-default cooldown geometry."""

    return SystemModel(
        process_loop=StraightTube(
            length_m=4.5,
            outer_diameter_m=3.0 / 8.0 * 0.0254,
            wall_thickness_m=0.035 * 0.0254,
        ),
        tank=CylindricalTank(
            height_m=0.5,
            inner_diameter_m=0.096,
            wall_thickness_m=0.0055,
        ),
        heat_exchanger=StraightTube(
            length_m=6.0,
            outer_diameter_m=0.25 * 0.0254,
            wall_thickness_m=0.05 * 0.0254,
        ),
    )


def default_scenarios() -> tuple[CooldownScenario, ...]:
    """Return a small uncertainty sweep bounded by the current pump range."""

    scenarios = (
        CooldownScenario(
            name="Optimistic",
            h_boil_w_m2k=1_500.0,
            h_gas_w_m2k=15.0,
            ln2_flow_lpm=0.08,
            hfe_flow_lpm=3.0,
            use_insulation=True,
            notes="Higher HFE mixing and higher continuous LN2 feed.",
        ),
        CooldownScenario(
            name="Nominal",
            h_boil_w_m2k=1_000.0,
            h_gas_w_m2k=10.0,
            ln2_flow_lpm=0.05,
            hfe_flow_lpm=2.0,
            use_insulation=True,
            notes="Legacy nominal recirculation case.",
        ),
        CooldownScenario(
            name="Pessimistic",
            h_boil_w_m2k=500.0,
            h_gas_w_m2k=5.0,
            ln2_flow_lpm=0.03,
            hfe_flow_lpm=0.8,
            use_insulation=True,
            notes="Lower mixing, weaker boiling, and lower LN2 feed.",
        ),
    )
    for scenario in scenarios:
        if scenario.hfe_flow_lpm > DEFAULT_PUMP_MAX_FLOW_LPM:
            raise ValueError(
                f"Scenario {scenario.name} exceeds the configured pump ceiling of "
                f"{DEFAULT_PUMP_MAX_FLOW_LPM:.1f} L/min."
            )
    return scenarios


def lpm_to_m3_s(flow_lpm: float) -> float:
    """Convert volumetric flow from L/min to m^3/s."""

    return max(flow_lpm, 0.0) / 60_000.0


def kelvin_to_celsius(temp_k: float) -> float:
    """Convert K to C."""

    return float(temp_k - KELVIN_OFFSET)


def hfe_density_kg_m3(temp_k: float) -> float:
    """Return the ORCA HFE density fit in kg/m^3."""

    return hfe_liquid_density_kg_m3(kelvin_to_celsius(temp_k))


def hfe_specific_heat_j_kgk(temp_k: float) -> float:
    """Approximate HFE-7200 liquid heat capacity."""

    return max(900.0, 1_220.0 + 1.5 * (temp_k - 298.0))


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
    """Return the energy that must be removed to cool from start to end."""

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
) -> float:
    """Latent-only lower bound on LN2 mass required for the cooldown."""

    return cooldown_energy_j(
        model,
        start_temp_k=start_temp_k,
        end_temp_k=end_temp_k,
    ) / model.ln2_latent_heat_j_kg


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
    """Ambient UA for a cylindrical shell using the side-wall resistance model."""

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
    """Return the ambient leak UA calibrated to the measured insulated warm-up."""

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


def coil_outer_hfe_htc_w_m2k(
    model: SystemModel,
    *,
    temp_k: float,
    hfe_flow_lpm: float,
) -> float:
    """Estimate the HFE-side external HTC around the coil.

    The simplified cooldown model keeps only a flow-rate dependence here.
    ``temp_k`` is retained for API compatibility with existing callers but is
    not used in the surrogate.
    """

    if hfe_flow_lpm <= 0.0:
        return 0.0

    reference_flow_lpm = max(model.outer_hfe_htc_reference_flow_lpm, 1e-12)
    flow_ratio = max(hfe_flow_lpm, 0.0) / reference_flow_lpm
    return model.outer_hfe_htc_reference_w_m2k * flow_ratio**model.outer_hfe_htc_flow_exponent


def hx_ua_w_per_k(
    model: SystemModel,
    *,
    temp_k: float,
    hfe_flow_lpm: float,
    ln2_side_htc_w_m2k: float,
) -> tuple[float, float]:
    """Return the full-coil UA and outer HFE HTC."""

    if ln2_side_htc_w_m2k <= 0.0:
        return 0.0, 0.0

    hx = model.heat_exchanger
    outer_htc = coil_outer_hfe_htc_w_m2k(
        model,
        temp_k=temp_k,
        hfe_flow_lpm=hfe_flow_lpm,
    )
    if outer_htc <= 0.0:
        return 0.0, outer_htc

    resistance_inner = 1.0 / max(ln2_side_htc_w_m2k * hx.internal_area_m2, 1e-12)
    resistance_wall = math.log(hx.outer_diameter_m / hx.inner_diameter_m) / (
        2.0 * math.pi * model.steel_conductivity_w_mk * hx.length_m
    )
    stagnant_outer_radius_m = hx.outer_radius_m + model.stagnant_hfe_film_thickness_m
    resistance_stagnant_hfe = math.log(stagnant_outer_radius_m / hx.outer_radius_m) / (
        2.0 * math.pi * model.hfe_liquid_conductivity_w_mk * hx.length_m
    )
    resistance_outer = 1.0 / max(outer_htc * hx.external_area_m2, 1e-12)

    resistance_total = (
        resistance_inner + resistance_wall + resistance_stagnant_hfe + resistance_outer
    )
    return 1.0 / resistance_total, outer_htc


def power_breakdown(
    model: SystemModel,
    scenario: CooldownScenario,
    *,
    temp_k: float,
) -> PowerBreakdown:
    """Return instantaneous heat-flow terms for the current bulk temperature."""

    ambient_ua = ambient_leak_ua_w_per_k(model, use_insulation=scenario.use_insulation)
    liquid_ua, outer_htc = hx_ua_w_per_k(
        model,
        temp_k=temp_k,
        hfe_flow_lpm=scenario.hfe_flow_lpm,
        ln2_side_htc_w_m2k=scenario.h_boil_w_m2k,
    )
    gas_ua, _ = hx_ua_w_per_k(
        model,
        temp_k=temp_k,
        hfe_flow_lpm=scenario.hfe_flow_lpm,
        ln2_side_htc_w_m2k=scenario.h_gas_w_m2k,
    )

    delta_t_hx_k = max(temp_k - model.ln2_saturation_temp_k, 0.0)
    latent_capacity_w = (
        lpm_to_m3_s(scenario.ln2_flow_lpm)
        * model.ln2_density_kg_m3
        * model.ln2_latent_heat_j_kg
    )
    gas_sensible_capacity_w = (
        lpm_to_m3_s(scenario.ln2_flow_lpm)
        * model.ln2_density_kg_m3
        * model.n2_gas_cp_j_kgk
        * delta_t_hx_k
    )
    liquid_heat_limit_w = liquid_ua * delta_t_hx_k

    if liquid_heat_limit_w <= latent_capacity_w + 1e-12:
        liquid_fraction = 1.0 if delta_t_hx_k > 0.0 else 0.0
        removed_heat_w = liquid_heat_limit_w
    else:
        liquid_fraction = max(
            0.0,
            min(1.0, latent_capacity_w / max(liquid_heat_limit_w, 1e-12)),
        )
        gas_heat_limit_w = (1.0 - liquid_fraction) * gas_ua * delta_t_hx_k
        removed_heat_w = latent_capacity_w + min(gas_heat_limit_w, gas_sensible_capacity_w)

    ambient_heat_w = ambient_ua * (model.ambient_temp_k - temp_k)
    return PowerBreakdown(
        ambient_heat_w=ambient_heat_w,
        removed_heat_w=removed_heat_w,
        liquid_ua_w_per_k=liquid_ua,
        gas_ua_w_per_k=gas_ua,
        outer_hfe_htc_w_m2k=outer_htc,
        liquid_coil_fraction=liquid_fraction,
        latent_capacity_w=latent_capacity_w,
        gas_sensible_capacity_w=gas_sensible_capacity_w,
    )


def simulate_cooldown(
    scenario: CooldownScenario,
    *,
    model: SystemModel | None = None,
    dt_s: float = 1.0,
    max_time_h: float = 6.0,
    stop_on_target: bool = False,
) -> CooldownResult:
    """Run the lumped cooldown simulation for one scenario."""

    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive.")
    system = default_system_model() if model is None else model
    total_steps = int(math.ceil(max_time_h * SECONDS_PER_HOUR / dt_s))
    ln2_mass_flow_kg_s = lpm_to_m3_s(scenario.ln2_flow_lpm) * system.ln2_density_kg_m3
    ambient_ua = ambient_leak_ua_w_per_k(system, use_insulation=scenario.use_insulation)
    turnover_time_s = system.loop_turnover_time_s(scenario.hfe_flow_lpm)

    time_s = np.zeros(total_steps + 1, dtype=float)
    bulk_temp_k = np.zeros_like(time_s)
    ambient_heat_w = np.zeros_like(time_s)
    removed_heat_w = np.zeros_like(time_s)
    liquid_ua_w_per_k = np.zeros_like(time_s)
    gas_ua_w_per_k = np.zeros_like(time_s)
    outer_hfe_htc_w_m2k = np.zeros_like(time_s)
    liquid_coil_fraction = np.zeros_like(time_s)
    delivered_ln2_kg = np.zeros_like(time_s)
    absorbed_energy_j = np.zeros_like(time_s)

    temp_k = system.initial_temp_k
    bulk_temp_k[0] = temp_k
    initial_power = power_breakdown(system, scenario, temp_k=temp_k)
    ambient_heat_w[0] = initial_power.ambient_heat_w
    removed_heat_w[0] = initial_power.removed_heat_w
    liquid_ua_w_per_k[0] = initial_power.liquid_ua_w_per_k
    gas_ua_w_per_k[0] = initial_power.gas_ua_w_per_k
    outer_hfe_htc_w_m2k[0] = initial_power.outer_hfe_htc_w_m2k
    liquid_coil_fraction[0] = initial_power.liquid_coil_fraction

    last_index = total_steps
    time_to_target_min = float("nan")

    for step in range(1, total_steps + 1):
        current_power = power_breakdown(system, scenario, temp_k=temp_k)
        total_capacity_j_per_k = thermal_capacity_j_per_k(system, temp_k)
        next_temp_k = temp_k + (
            (current_power.ambient_heat_w - current_power.removed_heat_w) * dt_s
            / total_capacity_j_per_k
        )
        next_temp_k = max(system.ln2_saturation_temp_k, next_temp_k)

        previous_temp_k = temp_k
        temp_k = next_temp_k

        time_s[step] = step * dt_s
        bulk_temp_k[step] = temp_k
        ambient_heat_w[step] = current_power.ambient_heat_w
        removed_heat_w[step] = current_power.removed_heat_w
        liquid_ua_w_per_k[step] = current_power.liquid_ua_w_per_k
        gas_ua_w_per_k[step] = current_power.gas_ua_w_per_k
        outer_hfe_htc_w_m2k[step] = current_power.outer_hfe_htc_w_m2k
        liquid_coil_fraction[step] = current_power.liquid_coil_fraction
        delivered_ln2_kg[step] = delivered_ln2_kg[step - 1] + ln2_mass_flow_kg_s * dt_s
        absorbed_energy_j[step] = (
            absorbed_energy_j[step - 1] + current_power.removed_heat_w * dt_s
        )

        crossed_target = (
            math.isnan(time_to_target_min)
            and previous_temp_k > system.target_temp_k
            and temp_k <= system.target_temp_k
        )
        if crossed_target:
            fraction = 1.0
            if previous_temp_k != temp_k:
                fraction = (previous_temp_k - system.target_temp_k) / (
                    previous_temp_k - temp_k
                )
            time_to_target_min = ((step - 1) + fraction) * dt_s / SECONDS_PER_MINUTE
            if stop_on_target:
                last_index = step
                break

    end = last_index + 1
    return CooldownResult(
        scenario=scenario,
        time_s=time_s[:end].copy(),
        bulk_temp_k=bulk_temp_k[:end].copy(),
        ambient_heat_w=ambient_heat_w[:end].copy(),
        removed_heat_w=removed_heat_w[:end].copy(),
        liquid_ua_w_per_k=liquid_ua_w_per_k[:end].copy(),
        gas_ua_w_per_k=gas_ua_w_per_k[:end].copy(),
        outer_hfe_htc_w_m2k=outer_hfe_htc_w_m2k[:end].copy(),
        liquid_coil_fraction=liquid_coil_fraction[:end].copy(),
        delivered_ln2_kg=delivered_ln2_kg[:end].copy(),
        absorbed_energy_j=absorbed_energy_j[:end].copy(),
        ambient_ua_w_per_k=ambient_ua,
        turnover_time_s=turnover_time_s,
        ln2_latent_heat_j_kg=system.ln2_latent_heat_j_kg,
        theoretical_min_ln2_kg=theoretical_min_ln2_kg(system),
        time_to_target_min=time_to_target_min,
    )


def simulate_suite(
    scenarios: Sequence[CooldownScenario] | None = None,
    *,
    model: SystemModel | None = None,
    dt_s: float = 1.0,
    max_time_h: float = 6.0,
    stop_on_target: bool = False,
) -> dict[str, CooldownResult]:
    """Run a group of cooldown scenarios."""

    selected = default_scenarios() if scenarios is None else tuple(scenarios)
    return {
        scenario.name: simulate_cooldown(
            scenario,
            model=model,
            dt_s=dt_s,
            max_time_h=max_time_h,
            stop_on_target=stop_on_target,
        )
        for scenario in selected
    }


def summarize_results(
    results: Mapping[str, CooldownResult] | Iterable[CooldownResult],
) -> list[dict[str, float | str | bool]]:
    """Return a compact tabular summary for notebook use."""

    if isinstance(results, Mapping):
        result_iterable = results.values()
    else:
        result_iterable = results

    rows: list[dict[str, float | str | bool]] = []
    for result in result_iterable:
        rows.append(
            {
                "scenario": result.scenario.name,
                "insulated": result.scenario.use_insulation,
                "hfe_flow_lpm": result.scenario.hfe_flow_lpm,
                "ln2_flow_lpm": result.scenario.ln2_flow_lpm,
                "ambient_ua_w_per_k": result.ambient_ua_w_per_k,
                "initial_liquid_ua_w_per_k": result.liquid_ua_w_per_k[0],
                "turnover_time_min": result.turnover_time_s / SECONDS_PER_MINUTE,
                "time_to_target_min": result.time_to_target_min,
                "reached_target": math.isfinite(result.time_to_target_min),
                "final_temp_k": float(result.bulk_temp_k[-1]),
                "delivered_ln2_kg": float(result.delivered_ln2_kg[-1]),
                "absorbed_energy_kj": float(result.absorbed_energy_j[-1] / 1e3),
                "absorbed_energy_latent_equiv_kg": float(
                    result.absorbed_energy_j[-1] / result.ln2_latent_heat_j_kg
                ),
                "theoretical_min_ln2_kg": result.theoretical_min_ln2_kg,
                "max_removed_heat_w": float(np.max(result.removed_heat_w)),
                "max_ambient_heat_w": float(np.max(result.ambient_heat_w)),
            }
        )
    return rows
