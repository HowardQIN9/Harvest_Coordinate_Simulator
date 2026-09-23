"""Deterministic generators for complete field scenarios."""

from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random

from .config import FieldSpec, SimulationConfig


@dataclass(frozen=True)
class FieldScenarioGenerator:
    """Generate heterogeneous fields while controlling total workload.

    Area and yield are normalized so scenarios remain comparable: changing a
    seed redistributes the same total acres and bushels across fields instead
    of silently changing the total harvest workload.
    """

    field_count: int = 10
    total_area_ac: float = 650.0
    total_grain_bu: float = 130_000.0
    area_weight_min: float = 0.75
    area_weight_max: float = 1.25
    yield_weight_min: float = 0.85
    yield_weight_max: float = 1.15
    travel_min_min: float = 5.0
    travel_min_max: float = 25.0
    moisture_min_pct: float = 18.0
    moisture_max_pct: float = 22.0

    def __post_init__(self) -> None:
        if self.field_count <= 0:
            raise ValueError("field_count must be positive")
        if self.total_area_ac <= 0 or self.total_grain_bu <= 0:
            raise ValueError("scenario totals must be positive")
        for low, high, label in (
                (self.area_weight_min, self.area_weight_max, "area weights"),
                (self.yield_weight_min, self.yield_weight_max,
                 "yield weights"),
                (self.travel_min_min, self.travel_min_max, "travel bounds"),
                (self.moisture_min_pct, self.moisture_max_pct,
                 "moisture bounds")):
            if low <= 0 or low >= high:
                raise ValueError(f"Invalid {label}")

    @staticmethod
    def _normalized(values: list[float], target: float) -> list[float]:
        scale = target / sum(values)
        normalized = [value * scale for value in values]
        normalized[-1] += target - sum(normalized)
        return normalized

    def generate_specs(self, seed: int) -> tuple[FieldSpec, ...]:
        rng = Random(seed)
        area_weights = [
            rng.uniform(self.area_weight_min, self.area_weight_max)
            for _ in range(self.field_count)
        ]
        areas = self._normalized(area_weights, self.total_area_ac)

        yield_weights = [
            rng.uniform(self.yield_weight_min, self.yield_weight_max)
            for _ in range(self.field_count)
        ]
        yield_scale = self.total_grain_bu / sum(
            area * weight for area, weight in zip(areas, yield_weights))
        yields = [weight * yield_scale for weight in yield_weights]

        rectangular_count = (self.field_count + 1) // 2
        geometries = (
            ["rectangular"] * rectangular_count
            + ["irregular"] * (self.field_count - rectangular_count)
        )
        rng.shuffle(geometries)

        specs = []
        for index in range(self.field_count):
            specs.append(FieldSpec(
                field_id=f"F{index + 1}",
                area_ac=areas[index],
                geometry=geometries[index],
                one_way_travel_min=rng.uniform(
                    self.travel_min_min, self.travel_min_max),
                yield_bu_per_ac=yields[index],
                moisture_pct=rng.uniform(
                    self.moisture_min_pct, self.moisture_max_pct),
            ))
        return tuple(specs)

    def generate_config(
            self, seed: int,
            base: SimulationConfig | None = None) -> SimulationConfig:
        template = SimulationConfig() if base is None else base
        return replace(
            template,
            field_specs=self.generate_specs(seed),
            fixed_field_order_ids=(),
            moisture_min_pct=self.moisture_min_pct,
            moisture_max_pct=self.moisture_max_pct,
            random_seed=seed,
        )

