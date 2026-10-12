"""USD economic costs: ISU A3-29 method, A3-10 drying reference.

Equipment inputs are illustrative assumptions, NOT ISU equipment quotations.
Annual ownership is allocated by campaign share, independent of waiting time.
"""
from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True)
class MachineCost:
    purchase_usd: float
    salvage_usd: float
    life_years: float
    horsepower: float
    repair_usd_per_active_h: float

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"Invalid machine cost: {item.name}")
        if self.life_years <= 0 or self.salvage_usd > self.purchase_usd:
            raise ValueError("Invalid economic life or salvage value")

    def annual_ownership(self, interest):
        n = self.life_years
        factor = (interest / (1 - (1 + interest) ** -n)
                  if interest else 1 / n)
        return ((self.purchase_usd - self.salvage_usd) * factor
                + interest * self.salvage_usd
                + 0.01 * (self.purchase_usd + self.salvage_usd) / 2)


@dataclass(frozen=True)
class CostConfig:
    # Combine includes header; cart includes tractor (one engine/operator).
    combine: MachineCost = MachineCost(500000, 150000, 10, 400, 35)
    grain_cart: MachineCost = MachineCost(250000, 75000, 12, 250, 15)
    semi: MachineCost = MachineCost(150000, 45000, 10, 400, 12)
    interest_rate: float = 0.05
    campaign_ownership_share: float = 1.0
    diesel_usd_per_gal: float = 2.89
    labor_usd_per_h: float = 24.45
    idle_fuel_fraction: float = 0.10
    drying_usd_per_bu_point: float = 0.055
    target_moisture_pct: float = 15.0
    # Site-specific infrastructure assumptions; no auger price applied to leg.
    grain_leg_usd_per_bu: float = 0.005
    wet_bin_usd_per_bu_h: float = 0.0
    dryer_annual_ownership_usd: float = 0.0
    grain_leg_annual_ownership_usd: float = 5000.0
    wet_bin_annual_ownership_usd: float = 3000.0
    delay_usd_per_h: float = 0.0

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, MachineCost):
                continue
            if not isfinite(value) or value < 0:
                raise ValueError(f"Invalid cost parameter: {item.name}")
        if self.campaign_ownership_share > 1 or self.idle_fuel_fraction > 1:
            raise ValueError("Cost fractions must be between zero and one")
        if self.target_moisture_pct >= 100:
            raise ValueError("Invalid target moisture")


def calculate_costs(metrics, simulation_config, field_list, costs=None):
    """Cost a completed campaign using exact simulator machine-hours.

    The objective is total_cost_usd, subject to all grain harvested and dried.
    Labor is charged during occupied time, released during planned rest.
    Fuel uses A3-29's diesel approximation; semi fuel needs farm calibration.
    """
    costs = costs or CostConfig()
    result = {"cost_currency": "USD", "cost_model": "isu_method_illustrative_inputs"}
    for kind, count in (("combine", simulation_config.n_combines),
                        ("grain_cart", simulation_config.n_grain_carts),
                        ("semi", simulation_config.n_semis)):
        machine = getattr(costs, kind)
        active = float(metrics[f"{kind}_active_machine_h"])
        occupied = float(metrics[f"{kind}_occupied_machine_h"])
        waiting = max(0.0, occupied - active)
        fuel_rate = 0.044 * machine.horsepower * costs.diesel_usd_per_gal
        result[f"cost_{kind}_ownership_usd"] = (
            count * machine.annual_ownership(costs.interest_rate)
            * costs.campaign_ownership_share)
        result[f"cost_{kind}_active_usd"] = active * (
            machine.repair_usd_per_active_h + 1.15 * fuel_rate
            + 1.1 * costs.labor_usd_per_h)
        result[f"cost_{kind}_waiting_usd"] = waiting * (
            1.15 * fuel_rate * costs.idle_fuel_fraction
            + 1.1 * costs.labor_usd_per_h)
    points = sum(f.area_ac * f.yield_bu_per_ac
                 * max(0.0, f.moisture_pct - costs.target_moisture_pct)
                 for f in field_list)
    result["cost_drying_usd"] = points * costs.drying_usd_per_bu_point
    result["cost_grain_leg_usd"] = (
        float(metrics["total_grain_to_wet_bin_bu"]) * costs.grain_leg_usd_per_bu)
    result["cost_wet_bin_storage_usd"] = (
        float(metrics["average_wet_bin_inventory_bu"])
        * float(metrics["system_completion_time_h"]) * costs.wet_bin_usd_per_bu_h)
    result["cost_infrastructure_ownership_usd"] = costs.campaign_ownership_share * (
        costs.dryer_annual_ownership_usd + costs.grain_leg_annual_ownership_usd
        + costs.wet_bin_annual_ownership_usd)
    result["cost_delay_usd"] = (
        float(metrics["harvest_completion_time_h"]) * costs.delay_usd_per_h)
    result["total_cost_usd"] = sum(v for k, v in result.items()
                                   if k.startswith("cost_") and k.endswith("_usd"))
    for unit, key in (("ac", "total_harvested_ac"),
                      ("bu", "total_dried_grain_bu")):
        quantity = float(metrics[key])
        result[f"cost_usd_per_{unit}"] = result["total_cost_usd"] / quantity if quantity else 0.0
    return result
