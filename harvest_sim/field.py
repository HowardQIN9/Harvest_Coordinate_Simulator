from dataclasses import dataclass

from .config import FieldSpec, SimulationConfig


OPENING = "Opening"
INTERIOR = "InteriorHarvest"


@dataclass
class Field:
    field_id: str
    geometry: str
    one_way_travel_min: float
    total_area_ac: float
    opening_remaining_ac: float
    interior_remaining_ac: float
    moisture_pct: float

    @classmethod
    def create(cls, spec: FieldSpec, config: SimulationConfig,
               moisture_pct: float) -> "Field":
        opening_area_ac = config.opening_area_ac(spec.area_ac)
        return cls(spec.field_id, spec.geometry, spec.one_way_travel_min,
                   spec.area_ac, opening_area_ac,
                   spec.area_ac - opening_area_ac, moisture_pct)

    @property
    def opening_complete(self) -> bool:
        return self.opening_remaining_ac <= 1e-9

    @property
    def field_complete(self) -> bool:
        return self.opening_complete and self.interior_remaining_ac <= 1e-9

    def available_tasks(self) -> list[str]:
        if self.opening_remaining_ac > 1e-9:
            return [OPENING]
        if self.interior_remaining_ac > 1e-9:
            return [INTERIOR]
        return []

    def remaining(self, task: str) -> float:
        return self.opening_remaining_ac if task == OPENING else self.interior_remaining_ac

    def harvest(self, task: str, acres: float) -> None:
        if task == OPENING:
            self.opening_remaining_ac = max(0.0, self.opening_remaining_ac - acres)
        else:
            self.interior_remaining_ac = max(0.0, self.interior_remaining_ac - acres)
