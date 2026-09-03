from dataclasses import dataclass

from .config import FieldSpec, SimulationConfig


OPENING = "Opening"
INTERIOR = "InteriorHarvest"


@dataclass
class Field:
    field_id: str
    geometry: str
    moisture: str
    one_way_travel_min: float
    total_area_ac: float
    opening_remaining_ac: float
    interior_remaining_ac: float

    @classmethod
    def create(cls, spec: FieldSpec, config: SimulationConfig) -> "Field":
        return cls(spec.field_id, spec.geometry, spec.moisture, spec.one_way_travel_min,
                   spec.area_ac, config.opening_area_ac,
                   spec.area_ac - config.opening_area_ac)

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
