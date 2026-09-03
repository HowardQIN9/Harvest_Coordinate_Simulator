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
        downstream_inventory_bu: float = 0.0,
        field_mobile_free_bu: float = 0.0,
        system_buffer_free_bu: float = 0.0,
        current_dryer_rate_buph: float = 0.0,
        **_: float,
    ) -> TaskRef | None:
        feasible = [(i, task) for i, field in enumerate(fields) for task in field.available_tasks()]
        if not feasible:
            return None
        c = self.config
        if current in feasible:
            return current

        def opening_capacity(field: Field) -> float:
            return (c.rectangular_opening_capacity_acph
                    if field.geometry == "rectangular"
                    else c.irregular_opening_capacity_acph)

        def forecast(task: TaskRef) -> tuple[float, float, int]:
            """Estimate the whole candidate field, including future backpressure.

            This intentionally evaluates through the end of the field because the
            combine is not allowed to switch again during that field.
            """
            field = fields[task[0]]
            productive_h = (field.opening_remaining_ac / opening_capacity(field)
                            + field.interior_remaining_ac / c.interior_capacity_acph)
            grain_bu = (field.opening_remaining_ac + field.interior_remaining_ac) * c.yield_bu_per_ac
            travel_h = field.one_way_travel_min / 60.0
            truck_cycle_h = 2.0 * travel_h + c.semi_unload_time_h
            truck_rate = c.semi_count * c.semi_capacity_bu / truck_cycle_h
            unload_dock_rate = c.semi_capacity_bu / c.semi_unload_time_h
            transport_rate = min(truck_rate, unload_dock_rate, c.grain_leg_rate_buph)
            dryer_rate = c.dryer_rate_for_moisture(field.moisture_pct)
            active_dryer_rate = current_dryer_rate_buph or dryer_rate

            transport_shortfall = max(
                0.0, grain_bu - field_mobile_free_bu - transport_rate * productive_h)
            transport_delay_h = transport_shortfall / transport_rate
            dryer_shortfall = max(
                0.0, grain_bu - system_buffer_free_bu - active_dryer_rate * productive_h)
            dryer_delay_h = dryer_shortfall / active_dryer_rate
            elapsed_h = productive_h + max(transport_delay_h, dryer_delay_h)

            projected_inventory = max(
                0.0, downstream_inventory_bu + grain_bu - active_dryer_rate * elapsed_h)
            # Retaining too much grain after this field reduces the capacity
            # available to every later choice. Increase that look-ahead weight as
            # the field receiving system becomes pressured.
            look_ahead_weight = 0.10 + 0.40 * receiving_pressure
            future_clearance_h = projected_inventory / dryer_rate
            score = elapsed_h + look_ahead_weight * future_clearance_h
            return score, elapsed_h, task[0]

        return min(feasible, key=forecast)
