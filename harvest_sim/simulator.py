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
        self.fields = [Field.create(spec, config) for spec in config.field_specs]
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
        return (self.config.rectangular_opening_capacity_acph
                if field.geometry == "rectangular"
                else self.config.irregular_opening_capacity_acph)

    def run(self) -> SimulationResult:
        c, d = self.config, self.downstream
        productive_h = blocked_h = switching_h = 0.0
        harvested_ac = generated_bu = 0.0
        switches = 0
        current: TaskRef | None = None
        combine_field: int | None = None
        field_order: list[str] = []
        switch_remaining_h = 0.0
        pending: TaskRef | None = None
        harvest_completion_h: float | None = None
        harvest_end_inventory: dict[str, Any] = {}
        series: list[dict[str, Any]] = []

        def begin_field(field_index: int) -> None:
            field = self.fields[field_index]
            if field.moisture:
                return
            field.moisture = c.moisture_for_harvest_position(len(field_order))
            d.set_field_context(field.field_id, field.one_way_travel_min)
            field_order.append(field.field_id)

        def record(state: str, active: TaskRef | None, duration_h: float = 0.0,
                   event: str = "") -> None:
            mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
            if abs(mass_error) > 1e-6:
                raise AssertionError(f"Mass balance error: {mass_error}")
            if not -c.numerical_tolerance <= d.combine_tank_bu <= c.combine_tank_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Combine grain tank outside physical bounds")
            for semi in d.semis:
                if not -c.numerical_tolerance <= semi.load_bu <= c.semi_capacity_bu + c.numerical_tolerance:
                    raise AssertionError("Semi load outside physical bounds")
            if not -c.numerical_tolerance <= d.receiving_pit_inventory_bu <= c.receiving_pit_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Receiving pit outside physical bounds")
            if not -c.numerical_tolerance <= d.wet_bin_inventory_bu <= c.wet_bin_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Wet bin outside physical bounds")
            series.append({
                "time_h": d.time_h,
                "duration_h": duration_h,
                "current_field": self.fields[active[0]].field_id if active else "",
                "current_task": active[1] if active else "",
                "current_moisture": self.fields[active[0]].moisture if active else "",
                "combine_state": state,
                "event": event,
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

            event_start = len(d.events)
            d.handle_events()
            if harvest_finished:
                d.dispatch_final_loads()
            else:
                d.unload_combine()
            if len(d.events) > event_start:
                names = ";".join(f"{event['semi_id']}:{event['event']}"
                                 for event in d.events[event_start:])
                record("EVENT", current, event=names)

            if harvest_finished and d.unprocessed_grain_bu <= c.numerical_tolerance:
                record("DONE", None)
                break
            if d.time_h >= c.max_simulation_h:
                raise RuntimeError("Simulation exceeded maximum duration")

            step = min(c.dt_h, d.next_event_h - d.time_h,
                       c.max_simulation_h - d.time_h)
            if harvest_finished:
                d.advance_time(step)
                record("POST_HARVEST", None, step)
                continue

            if switch_remaining_h > c.numerical_tolerance:
                step = min(step, switch_remaining_h)
                d.advance_time(step)
                switch_remaining_h -= step
                switching_h += step
                record("SWITCHING", pending, step)
                if switch_remaining_h <= c.numerical_tolerance:
                    current, pending = pending, None
                    if current is None:
                        raise AssertionError("Field switch has no destination")
                    combine_field = current[0]
                    begin_field(combine_field)
                continue

            # A selected field must finish both phases before another field can start.
            if combine_field is not None and not self.fields[combine_field].field_complete:
                choice = (combine_field, self.fields[combine_field].available_tasks()[0])
            else:
                field_mobile_free = c.combine_tank_capacity_bu - d.combine_tank_bu
                field_mobile_free += sum(
                    c.semi_capacity_bu - semi.load_bu
                    for semi in d.semis if semi.state == AT_FIELD)
                system_capacity = (c.combine_tank_capacity_bu
                                   + c.semi_count * c.semi_capacity_bu
                                   + c.receiving_pit_capacity_bu
                                   + c.wet_bin_capacity_bu)
                choice = self.controller.choose(
                    self.fields,
                    current,
                    receiving_pressure=d.receiving_pressure,
                    downstream_inventory_bu=(d.receiving_pit_inventory_bu
                                             + d.wet_bin_inventory_bu),
                    field_mobile_free_bu=field_mobile_free,
                    system_buffer_free_bu=system_capacity - d.unprocessed_grain_bu,
                    current_dryer_rate_buph=d.current_dryer_rate_buph,
                    candidate_moisture=c.moisture_for_harvest_position(len(field_order)),
                )
            if choice is None:
                raise AssertionError("Unfinished fields have no available task")
            if combine_field is not None and choice[0] != combine_field:
                switches += 1
                if c.switch_time_h > c.numerical_tolerance:
                    pending = choice
                    switch_remaining_h = c.switch_time_h
                    continue

            current = choice
            if combine_field != choice[0]:
                combine_field = choice[0]
                begin_field(combine_field)

            field = self.fields[current[0]]
            capacity = self.capacity(field, current[1])
            free_tank_bu = c.combine_tank_capacity_bu - d.combine_tank_bu
            if free_tank_bu <= c.numerical_tolerance:
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
            d.add_harvested_grain(grain, field.moisture, field.field_id)
            field.harvest(current[1], acres)
            harvested_ac += acres
            generated_bu += grain
            productive_h += step
            record("HARVESTING", current, step)
            if current[1] not in field.available_tasks():
                current = None

        if harvest_completion_h is None:
            raise AssertionError("Missing harvest completion time")
        mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
        field_by_id = {field.field_id: field for field in self.fields}
        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "field_order": " -> ".join(field_order),
            "dryer_rate_order": " -> ".join(
                f"{field_id}:{c.dryer_high_moisture_rate_buph:g}"
                if field_by_id[field_id].moisture == "High"
                else f"{field_id}:{c.dryer_low_moisture_rate_buph:g}"
                for field_id in field_order
            ),
            "grain_leg_rate_buph": c.grain_leg_rate_buph,
            "wet_bin_capacity_bu": c.wet_bin_capacity_bu,
            "dryer_high_moisture_rate_buph": c.dryer_high_moisture_rate_buph,
            "dryer_low_moisture_rate_buph": c.dryer_low_moisture_rate_buph,
            "semi_unload_time_min": c.semi_unload_time_h * 60.0,
            "combine_tank_capacity_bu": c.combine_tank_capacity_bu,
            "semi_count": c.semi_count,
            "semi_capacity_bu": c.semi_capacity_bu,
            "total_completion_time_h": harvest_completion_h,
            "harvest_completion_time_h": harvest_completion_h,
            "system_completion_time_h": d.time_h,
            "post_harvest_time_h": d.time_h - harvest_completion_h,
            "productive_harvest_time_h": productive_h,
            "blocked_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "switching_time_h": switching_h,
            "number_of_field_switches": switches,
            "combine_utilization": (productive_h / harvest_completion_h
                                    if harvest_completion_h else 0.0),
            "total_harvested_ac": harvested_ac,
            "total_harvested_grain_bu": generated_bu,
            "total_unloaded_grain_bu": d.total_unloaded_bu,
            "total_grain_to_wet_bin_bu": d.total_leg_transferred_bu,
            "total_dried_grain_bu": d.total_dried_bu,
            "total_dried_high_moisture_bu": d.dried_by_moisture_bu["High"],
            "total_dried_low_moisture_bu": d.dried_by_moisture_bu["Low"],
            "total_dried_first_two_fields_bu": d.dried_by_moisture_bu["High"],
            "total_dried_remaining_fields_bu": d.dried_by_moisture_bu["Low"],
            "dried_at_harvest_completion_bu": harvest_end_inventory["cumulative_grain_dried_bu"],
            "combine_tank_at_harvest_completion_bu": harvest_end_inventory["combine_tank_bu"],
            "semi_at_field_grain_at_harvest_completion_bu": harvest_end_inventory["semi_at_field_grain_bu"],
            "in_transit_grain_at_harvest_completion_bu": harvest_end_inventory["in_transit_grain_bu"],
            "semi_at_gc_grain_at_harvest_completion_bu": harvest_end_inventory["semi_at_gc_grain_bu"],
            "receiving_pit_at_harvest_completion_bu": harvest_end_inventory["receiving_pit_inventory_bu"],
            "wet_bin_at_harvest_completion_bu": harvest_end_inventory["wet_bin_inventory_bu"],
            "maximum_combine_tank_bu": max(row["combine_tank_bu"] for row in series),
            "maximum_receiving_pit_inventory_bu": max(row["receiving_pit_inventory_bu"] for row in series),
            "maximum_wet_bin_inventory_bu": max(row["wet_bin_inventory_bu"] for row in series),
            "number_of_semi_trips": sum(semi.trips for semi in d.semis),
            "final_unprocessed_grain_bu": d.unprocessed_grain_bu,
            "mass_balance_error_bu": (0.0 if abs(mass_error) <= c.numerical_tolerance
                                      else mass_error),
        }
        return SimulationResult(self.controller.name, metrics, series,
                                self.fields, d.events)
