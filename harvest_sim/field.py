from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import FieldSpec, SimulationConfig


OPENING = "Opening"
INTERIOR = "InteriorHarvest"


@dataclass
class Field:
    field_id: str
    area_ac: float
    geometry: str
    yield_bu_per_ac: float
    moisture_pct: float
    one_way_travel_min: float
    opening_area_fraction: float
    opening_capacity_factor: float
    opening_remaining_ac: float
    interior_remaining_ac: float

    @classmethod
    def create(
            cls, spec: FieldSpec, config: SimulationConfig,
            moisture_pct: float) -> "Field":
        opening_fraction = (
            config.opening_area_fraction
            if spec.opening_area_fraction is None
            else spec.opening_area_fraction
        )
        opening_factor = (
            config.opening_capacity_factor(spec.geometry)
            if spec.opening_capacity_factor is None
            else spec.opening_capacity_factor
        )
        field_yield = (
            config.yield_bu_per_ac
            if spec.yield_bu_per_ac is None else spec.yield_bu_per_ac
        )
        opening_area = spec.area_ac * opening_fraction
        return cls(
            field_id=spec.field_id,
            area_ac=spec.area_ac,
            geometry=spec.geometry,
            yield_bu_per_ac=field_yield,
            moisture_pct=moisture_pct,
            one_way_travel_min=spec.one_way_travel_min,
            opening_area_fraction=opening_fraction,
            opening_capacity_factor=opening_factor,
            opening_remaining_ac=opening_area,
            interior_remaining_ac=spec.area_ac - opening_area,
        )

    @property
    def total_area_ac(self) -> float:
        """Compatibility alias for earlier result code."""
        return self.area_ac

    @property
    def opening_area_ac(self) -> float:
        return self.area_ac * self.opening_area_fraction

    @property
    def interior_area_ac(self) -> float:
        return self.area_ac - self.opening_area_ac

    @property
    def remaining_area_ac(self) -> float:
        return self.opening_remaining_ac + self.interior_remaining_ac

    @property
    def remaining_grain_bu(self) -> float:
        return self.remaining_area_ac * self.yield_bu_per_ac

    @property
    def opening_complete(self) -> bool:
        return self.opening_remaining_ac <= 1e-9

    @property
    def interior_complete(self) -> bool:
        return self.interior_remaining_ac <= 1e-9

    @property
    def field_complete(self) -> bool:
        return self.opening_complete and self.interior_complete

    @property
    def current_phase(self) -> str | None:
        if not self.opening_complete:
            return OPENING
        if not self.interior_complete:
            return INTERIOR
        return None

    def available_tasks(self) -> list[str]:
        phase = self.current_phase
        return [] if phase is None else [phase]

    def remaining(self, task: str) -> float:
        if task == OPENING:
            return self.opening_remaining_ac
        if task == INTERIOR:
            return self.interior_remaining_ac
        raise ValueError(f"Unknown field phase: {task}")

    def remaining_grain(self, task: str) -> float:
        return self.remaining(task) * self.yield_bu_per_ac

    def harvest(self, task: str, acres: float) -> None:
        if acres < 0:
            raise ValueError("Harvested area must be nonnegative")
        if task == OPENING:
            self.opening_remaining_ac = max(
                0.0, self.opening_remaining_ac - acres)
        elif task == INTERIOR:
            self.interior_remaining_ac = max(
                0.0, self.interior_remaining_ac - acres)
        else:
            raise ValueError(f"Unknown field phase: {task}")


class FieldGenerator:
    """Construct fully populated, fresh Field objects for one scenario."""

    @staticmethod
    def generate(
            config: SimulationConfig,
            moistures_pct: tuple[float, ...] | None = None) -> list[Field]:
        moistures = (
            config.sample_field_moistures()
            if moistures_pct is None else tuple(moistures_pct)
        )
        if len(moistures) != len(config.field_specs):
            raise ValueError(
                "moistures_pct must contain one value per field")
        return [
            Field.create(spec, config, moisture)
            for spec, moisture in zip(config.field_specs, moistures)
        ]


class FieldSelector(Protocol):
    def choose_next(
            self, fields: list[Field], current_index: int | None) -> int | None:
        ...


@dataclass(frozen=True)
class FixedOrderFieldSelector:
    order_indices: tuple[int, ...]

    def choose_next(
            self, fields: list[Field], current_index: int | None) -> int | None:
        if (current_index is not None
                and not fields[current_index].field_complete):
            return current_index
        for index in self.order_indices:
            if not fields[index].field_complete:
                return index
        return None
