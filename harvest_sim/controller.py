from __future__ import annotations

from dataclasses import dataclass

from .field import INTERIOR, OPENING, Field

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
    high_buffer_bu: float
    low_buffer_bu: float
    min_task_h: float
    name: str = "adaptive"

    def choose(
        self,
        fields: list[Field],
        current: TaskRef | None,
        buffer_bu: float,
        task_elapsed_h: float = 0.0,
        **_: float,
    ) -> TaskRef | None:
        feasible = [(i, task) for i, field in enumerate(fields) for task in field.available_tasks()]
        if not feasible:
            return None
        current_feasible = current in feasible
        if current_feasible and task_elapsed_h < self.min_task_h:
            return current
        if self.low_buffer_bu < buffer_bu < self.high_buffer_bu and current_feasible:
            return current
        if buffer_bu >= self.high_buffer_bu:
            # Lower inflow first; stay in the current field on a tie.
            rank = {("irregular", OPENING): 0, ("rectangular", OPENING): 1}
            return min(feasible, key=lambda x: (rank.get((fields[x[0]].geometry, x[1]), 2), x[0] != (current or (-1, ""))[0], x[0]))
        interiors = [task for task in feasible if task[1] == INTERIOR]
        if buffer_bu <= self.low_buffer_bu and interiors:
            if current in interiors:
                return current
            return min(interiors, key=lambda x: (x[0] != (current or (-1, ""))[0], x[0]))
        return current if current_feasible else feasible[0]
