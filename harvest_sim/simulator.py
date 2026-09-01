from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import SimulationConfig
from .controller import AdaptiveController, SequentialController, TaskRef
from .downstream import AT_FIELD, Downstream
from .field import INTERIOR, Field


@dataclass
class SimulationResult:
    policy: str
    metrics: dict[str, float | int | str]
    time_series: list[dict[str, Any]]
    fields: list[Field]
    transport_events: list[dict[str, Any]]


class Simulator:
    def __init__(self, config: SimulationConfig, policy: str = "sequential") -> None:
        self.config = config
        self.fields = [Field.create(i + 1, geometry, config) for i, geometry in enumerate(config.field_geometries)]
        self.downstream = Downstream(config)
        if policy == "sequential":
            self.controller = SequentialController()
        elif policy == "adaptive":
            self.controller = AdaptiveController(config)
        else:
            raise ValueError(f"Unknown policy: {policy}")

    def capacity(self, field: Field, task: str) -> float:
        if task == INTERIOR:
            return self.config.interior_capacity_acph
        return (self.config.rectangular_opening_capacity_acph if field.geometry == "rectangular"
                else self.config.irregular_opening_capacity_acph)

    def run(self) -> SimulationResult:
        c, d = self.config, self.downstream
        productive_h = blocked_h = switching_h = 0.0
        harvested_ac = generated_bu = 0.0
        switches = 0
        current: TaskRef | None = None
        combine_field: int | None = None
        switch_remaining_h = 0.0
        pending: TaskRef | None = None
        harvest_completion_h: float | None = None
        harvest_end_inventory: dict[str, Any] = {}
        series: list[dict[str, Any]] = []

        def record(state: str, active: TaskRef | None, duration_h: float = 0.0, event: str = "") -> None:
            # Check grain conservation and all finite mobile capacities at every
            # interval boundary and after instantaneous transfers/deliveries.
            mass_error = generated_bu - d.total_processed_bu - d.unprocessed_grain_bu
            if abs(mass_error) > 1e-6:
                raise AssertionError(f"Mass balance error: {mass_error}")
            if not -c.numerical_tolerance <= d.combine_tank_bu <= c.combine_tank_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Combine grain tank outside physical bounds")
            for semi in d.semis:
                if not -c.numerical_tolerance <= semi.load_bu <= c.semi_capacity_bu + c.numerical_tolerance:
                    raise AssertionError("Semi load outside physical bounds")
            if d.grain_center_inventory_bu < -c.numerical_tolerance:
                raise AssertionError("Negative grain center inventory")
            series.append({
                "time_h": d.time_h, "duration_h": duration_h,
                "current_field": self.fields[active[0]].field_id if active else "",
                "current_task": active[1] if active else "",
                "combine_state": state, "event": event,
                "cumulative_harvested_ac": harvested_ac,
                "cumulative_grain_generated_bu": generated_bu,
                **d.snapshot(),
            })

        record("IDLE", None)
        while True:
            harvest_finished = all(field.field_complete for field in self.fields)
            if harvest_finished and harvest_completion_h is None:
                harvest_completion_h = d.time_h
                harvest_end_inventory = d.snapshot()
                record("HARVEST_COMPLETE", None)

            # Events take no time. Save a second row at the same timestamp so
            # inventory plots show actual jumps, not sloping truck deliveries.
            event_start = len(d.events)
            d.handle_events()
            if harvest_finished:
                d.dispatch_final_loads()
            else:
                d.unload_combine()
            if len(d.events) > event_start:
                names = ";".join(f"{e['semi_id']}:{e['event']}" for e in d.events[event_start:])
                record("EVENT", current, event=names)

            if harvest_finished and d.unprocessed_grain_bu <= c.numerical_tolerance:
                record("DONE", None)
                break
            if d.time_h >= c.max_simulation_h:
                raise RuntimeError("Simulation exceeded maximum duration")

            # Split at arrivals, returns and GC emptying, independently of dt_h.
            step = min(c.dt_h, d.next_event_h - d.time_h, c.max_simulation_h - d.time_h)
            if harvest_finished:
                d.advance_time(step)
                record("POST_HARVEST", None, step)
                continue

            # While changing fields, transport and GC processing continue.
            if switch_remaining_h > c.numerical_tolerance:
                step = min(step, switch_remaining_h)
                d.advance_time(step)
                switch_remaining_h -= step
                switching_h += step
                record("SWITCHING", pending, step)
                if switch_remaining_h <= c.numerical_tolerance:
                    current, pending = pending, None
                    combine_field = current[0]
                continue

            # Both phases must finish before a controller may choose a new field.
            if combine_field is not None and not self.fields[combine_field].field_complete:
                choice = (combine_field, self.fields[combine_field].available_tasks()[0])
            else:
                choice = self.controller.choose(self.fields, current, receiving_pressure=d.receiving_pressure)
            if choice is None:
                raise AssertionError("Unfinished fields have no available task")
            if combine_field is not None and choice[0] != combine_field:
                switches += 1
                if c.switch_time_h > c.numerical_tolerance:
                    pending = choice
                    switch_remaining_h = c.switch_time_h
                    continue
            current = choice
            combine_field = choice[0]

            field = self.fields[current[0]]
            capacity = self.capacity(field, current[1])
            free_tank_bu = c.combine_tank_capacity_bu - d.combine_tank_bu
            if free_tank_bu <= c.numerical_tolerance:
                # A full tank was already offered to every on-site semi above.
                # Therefore only lack of a receiving semi causes blocking.
                if any(semi.state == AT_FIELD for semi in d.semis):
                    raise AssertionError("Combine blocked while a semi can receive grain")
                d.advance_time(step)
                blocked_h += step
                record("BLOCKED", current, step)
                continue

            step = min(step, field.remaining(current[1]) / capacity,
                       free_tank_bu / (capacity * c.yield_bu_per_ac))
            acres = capacity * step
            grain = acres * c.yield_bu_per_ac
            d.advance_time(step)
            d.combine_tank_bu += grain
            field.harvest(current[1], acres)
            harvested_ac += acres
            generated_bu += grain
            productive_h += step
            record("HARVESTING", current, step)
            if current[1] not in field.available_tasks():
                current = None

        # Harvest completion retains the previous metric's meaning. The series
        # also includes final shipments and GC processing, but not unused returns.
        mass_error = generated_bu - d.total_processed_bu - d.unprocessed_grain_bu
        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "grain_center_processing_rate_buph": c.grain_center_processing_rate_buph,
            "combine_tank_capacity_bu": c.combine_tank_capacity_bu,
            "semi_count": c.semi_count, "semi_capacity_bu": c.semi_capacity_bu,
            "semi_gc_arrival_h": c.semi_gc_arrival_h, "semi_round_trip_h": c.semi_round_trip_h,
            "total_completion_time_h": harvest_completion_h,
            "harvest_completion_time_h": harvest_completion_h,
            "system_completion_time_h": d.time_h,
            "post_harvest_time_h": d.time_h - harvest_completion_h,
            "productive_harvest_time_h": productive_h,
            "blocked_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "switching_time_h": switching_h, "number_of_field_switches": switches,
            "combine_utilization": productive_h / harvest_completion_h if harvest_completion_h else 0.0,
            "total_harvested_ac": harvested_ac, "total_harvested_grain_bu": generated_bu,
            "total_received_grain_bu": d.total_received_bu,
            "total_processed_grain_bu": d.total_processed_bu,
            "processed_grain_at_harvest_completion_bu": harvest_end_inventory["cumulative_grain_processed_bu"],
            "combine_tank_at_harvest_completion_bu": harvest_end_inventory["combine_tank_bu"],
            "semi_at_field_grain_at_harvest_completion_bu": harvest_end_inventory["semi_at_field_grain_bu"],
            "in_transit_grain_at_harvest_completion_bu": harvest_end_inventory["in_transit_grain_bu"],
            "grain_center_inventory_at_harvest_completion_bu": harvest_end_inventory["grain_center_inventory_bu"],
            "maximum_combine_tank_bu": max(row["combine_tank_bu"] for row in series),
            "maximum_grain_center_inventory_bu": max(row["grain_center_inventory_bu"] for row in series),
            "number_of_semi_trips": sum(semi.trips for semi in d.semis),
            "final_unprocessed_grain_bu": d.unprocessed_grain_bu,
            "mass_balance_error_bu": 0.0 if abs(mass_error) <= c.numerical_tolerance else mass_error,
        }
        return SimulationResult(self.controller.name, metrics, series, self.fields, d.events)
