from __future__ import annotations

from dataclasses import dataclass

from .config import SimulationConfig
from .field import INTERIOR, Field

TaskRef = tuple[int, str]


class SequentialController:
    name = "sequential"

    def choose(self, fields: list[Field], current: TaskRef | None, **_: float) -> TaskRef | None:
        if current is not None:
            index, task = current
            if task in fields[index].available_tasks():
                return current
        for index, field in enumerate(fields):
            tasks = field.available_tasks()
            if tasks:
                return index, tasks[0]
        return None


@dataclass
class AdaptiveController:
    config: SimulationConfig
    name: str = "adaptive"

    def choose(
        self,
        fields: list[Field],
        current: TaskRef | None,
        receiving_pressure: float,
        **_: float,
    ) -> TaskRef | None:
        feasible = [(i, task) for i, field in enumerate(fields) for task in field.available_tasks()]
        if not feasible:
            return None
        current_feasible = current in feasible
        c = self.config
        if c.low_receiving_pressure_fraction < receiving_pressure < c.high_receiving_pressure_fraction and current_feasible:
            return current

        def capacity(task: TaskRef) -> float:
            if task[1] == INTERIOR:
                return c.interior_capacity_acph
            return (c.rectangular_opening_capacity_acph if fields[task[0]].geometry == "rectangular"
                    else c.irregular_opening_capacity_acph)

        # Use actual rates: editing config can make opening faster than interior.
        if receiving_pressure >= c.high_receiving_pressure_fraction:
            return min(feasible, key=lambda task: (capacity(task), task[0]))
        if receiving_pressure <= c.low_receiving_pressure_fraction:
            return min(feasible, key=lambda task: (-capacity(task), task[0]))
        return current if current_feasible else feasible[0]
