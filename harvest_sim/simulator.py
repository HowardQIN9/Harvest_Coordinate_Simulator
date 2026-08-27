from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import SimulationConfig
from .controller import AdaptiveController, SequentialController, TaskRef
from .downstream import Downstream
from .field import INTERIOR, OPENING, Field


@dataclass
class SimulationResult:
    policy: str
    metrics: dict[str, float | int | str]
    time_series: list[dict[str, Any]]
    fields: list[Field]


class Simulator:
    def __init__(self, config: SimulationConfig, policy: str = "sequential") -> None:
        self.config = config
        self.fields = [Field.create(i + 1, geometry, config) for i, geometry in enumerate(config.field_geometries)]
        self.downstream = Downstream(config.buffer_capacity_bu, config.downstream_rate_buph)
        if policy == "sequential":
            self.controller = SequentialController()
        elif policy == "adaptive":
            self.controller = AdaptiveController(
                config.high_buffer_fraction * config.buffer_capacity_bu,
                config.low_buffer_fraction * config.buffer_capacity_bu,
                config.adaptive_min_task_h,
            )
        else:
            raise ValueError(f"Unknown policy: {policy}")

    def capacity(self, field: Field, task: str) -> float:
        if task == INTERIOR:
            return self.config.interior_capacity_acph
        return (self.config.rectangular_opening_capacity_acph if field.geometry == "rectangular"
                else self.config.irregular_opening_capacity_acph)

    def run(self) -> SimulationResult:
        c = self.config
        time_h = productive_h = blocked_h = switching_h = 0.0
        harvested_ac = generated_bu = 0.0
        switches = 0
        current: TaskRef | None = None
        combine_field: int | None = None
        switch_remaining_h = 0.0
        pending: TaskRef | None = None
        task_elapsed_h = 0.0
        series: list[dict[str, Any]] = []
        max_buffer = buffer_integral = 0.0

        def record(state: str, active: TaskRef | None, elapsed_h: float = 0.0) -> None:
            nonlocal max_buffer, buffer_integral
            field_name = self.fields[active[0]].field_id if active else ""
            task_name = active[1] if active else ""
            max_buffer = max(max_buffer, self.downstream.buffer_bu)
            buffer_integral += self.downstream.buffer_bu * elapsed_h
            series.append({
                "time_h": time_h, "buffer_bu": self.downstream.buffer_bu,
                "current_field": field_name, "current_task": task_name,
                "combine_state": state, "cumulative_harvested_ac": harvested_ac,
                "cumulative_grain_generated_bu": generated_bu,
                "cumulative_grain_processed_bu": self.downstream.total_processed_bu,
            })

        record("IDLE", None)
        while harvested_ac < c.total_area_ac - c.numerical_tolerance:
            if time_h >= c.max_simulation_h:
                raise RuntimeError("Simulation exceeded maximum duration")

            if switch_remaining_h > c.numerical_tolerance:
                step = min(c.dt_h, switch_remaining_h)
                self.downstream.process(step)
                switch_remaining_h -= step
                switching_h += step
                time_h += step
                if switch_remaining_h <= c.numerical_tolerance:
                    current, pending, task_elapsed_h = pending, None, 0.0
                    combine_field = current[0] if current else combine_field
                record("SWITCHING", pending or current, step)
                continue

            choice = self.controller.choose(
                self.fields, current, buffer_bu=self.downstream.buffer_bu,
                task_elapsed_h=task_elapsed_h,
            )
            if choice is None:
                break
            if combine_field is not None and choice[0] != combine_field:
                switches += 1
                pending = choice
                switch_remaining_h = c.switch_time_h
                continue
            if choice != current:
                current, task_elapsed_h = choice, 0.0
                combine_field = choice[0]

            index, task = current
            field = self.fields[index]
            capacity = self.capacity(field, task)
            # End a step exactly at a task boundary instead of padding it to a
            # complete minute. A blocked step still consumes its full duration.
            step = min(c.dt_h, field.remaining(task) / capacity)
            potential_ac = capacity * step
            potential_grain = potential_ac * c.yield_bu_per_ac
            accepted_grain, _ = self.downstream.step_with_inflow(potential_grain, step)
            actual_ac = accepted_grain / c.yield_bu_per_ac
            actual_productive_h = actual_ac / capacity
            step_blocked_h = max(0.0, step - actual_productive_h)
            field.harvest(task, actual_ac)
            harvested_ac += actual_ac
            generated_bu += accepted_grain
            productive_h += actual_productive_h
            blocked_h += step_blocked_h
            task_elapsed_h += step
            time_h += step

            state = "BLOCKED" if step_blocked_h > c.numerical_tolerance else "HARVESTING"
            completed = task not in field.available_tasks()
            record(state, current, step)
            if completed:
                # Interior in the same field becomes available without a switch.
                current = None
                task_elapsed_h = 0.0

        mass_error = generated_bu - self.downstream.total_processed_bu - self.downstream.buffer_bu
        if abs(mass_error) > 1e-6:
            raise AssertionError(f"Mass balance error: {mass_error}")
        if not (-c.numerical_tolerance <= self.downstream.buffer_bu <= c.buffer_capacity_bu + c.numerical_tolerance):
            raise AssertionError("Buffer outside physical bounds")

        reported_blocked_h = 0.0 if abs(blocked_h) <= c.numerical_tolerance else blocked_h
        reported_mass_error = 0.0 if abs(mass_error) <= c.numerical_tolerance else mass_error

        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "downstream_rate_bu_per_h": c.downstream_rate_buph,
            "total_completion_time_h": time_h,
            "productive_harvest_time_h": productive_h,
            "blocked_time_h": reported_blocked_h,
            "switching_time_h": switching_h,
            "number_of_field_switches": switches,
            "combine_utilization": productive_h / time_h,
            "total_harvested_ac": harvested_ac,
            "total_harvested_grain_bu": generated_bu,
            "total_processed_grain_bu": self.downstream.total_processed_bu,
            "final_buffer_bu": self.downstream.buffer_bu,
            "maximum_buffer_bu": max_buffer,
            "average_buffer_bu": buffer_integral / time_h,
            "mass_balance_error_bu": reported_mass_error,
        }
        return SimulationResult(self.controller.name, metrics, series, self.fields)
