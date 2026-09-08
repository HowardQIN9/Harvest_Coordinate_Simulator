from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite
from typing import Any

from .accounting import calculate_daily_machine_hours, summarize_machine_hours
from .config import (INTERIOR_MODES, MODE_2C, MODE_2C_1GC,
                     SimulationConfig)
from .controller import (AdaptiveController, ReactiveController,
                         SequentialController, TaskRef)
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
    def __init__(self, config: SimulationConfig, policy: str = "sequential",
                 field_moistures_pct: tuple[float, ...] | None = None) -> None:
        self.config = config
        moistures = (config.sample_field_moistures()
                     if field_moistures_pct is None else tuple(field_moistures_pct))
        if len(moistures) != len(config.field_specs):
            raise ValueError("field_moistures_pct must contain one value per field")
        if any(not isfinite(value)
               or value < config.moisture_min_pct
               or value > config.moisture_max_pct for value in moistures):
            raise ValueError("field moisture must lie within the configured bounds")
        self.field_moistures_pct = moistures
        self.fields = [Field.create(spec, config, moisture)
                       for spec, moisture in zip(config.field_specs, moistures)]
        self.downstream = Downstream(config)
        if policy == "sequential":
            self.controller = SequentialController()
        elif policy == "reactive":
            self.controller = ReactiveController(config)
        elif policy == "adaptive":
            self.controller = AdaptiveController(config)
        else:
            raise ValueError(f"Unknown policy: {policy}")

    def capacity(self, field: Field, task: str,
                 interior_mode: str = MODE_2C) -> float:
        """Effective capacity in ac/h; no further efficiency deduction applies."""
        if task == INTERIOR:
            rate_buph = self.config.interior_rate_buph(interior_mode)
        else:
            rate_buph = self.config.opening_rate_buph(field.geometry)
        return rate_buph / self.config.yield_bu_per_ac

    def run(self) -> SimulationResult:
        c, d = self.config, self.downstream
        productive_h = blocked_h = switching_h = mode_switching_h = 0.0
        scheduled_rest_h = 0.0
        harvested_ac = generated_bu = 0.0
        switches = 0
        interior_mode_switches = 0
        intentional_slowdown_productive_h = 0.0
        intentional_rate_reduction_capacity_bu = 0.0
        productive_h_by_mode = {mode: 0.0 for mode in INTERIOR_MODES}
        current: TaskRef | None = None
        combine_field: int | None = None
        field_order: list[str] = []
        switch_remaining_h = 0.0
        pending: TaskRef | None = None
        current_interior_mode: str | None = None
        pending_interior_mode: str | None = None
        interior_mode_dwell_h = 0.0
        interior_mode_switch_remaining_h = 0.0
        field_interior_mode_switches = 0
        mode_sequences: dict[str, list[str]] = {}
        harvest_completion_h: float | None = None
        harvest_end_inventory: dict[str, Any] = {}
        series: list[dict[str, Any]] = []

        def begin_field(field_index: int) -> None:
            nonlocal current_interior_mode, pending_interior_mode
            nonlocal interior_mode_dwell_h, interior_mode_switch_remaining_h
            nonlocal field_interior_mode_switches
            field = self.fields[field_index]
            d.set_field_context(field.field_id, field.one_way_travel_min)
            field_order.append(field.field_id)
            current_interior_mode = None
            pending_interior_mode = None
            interior_mode_dwell_h = 0.0
            interior_mode_switch_remaining_h = 0.0
            field_interior_mode_switches = 0
            mode_sequences[field.field_id] = []

        def field_mobile_free_bu() -> float:
            free = c.combine_tank_capacity_bu - d.combine_tank_bu
            free += sum(c.semi_capacity_bu - semi.load_bu
                        for semi in d.semis if semi.state == AT_FIELD)
            return free

        def system_buffer_free_bu() -> float:
            capacity = (c.combine_tank_capacity_bu
                        + c.semi_count * c.semi_capacity_bu
                        + c.receiving_pit_capacity_bu
                        + c.wet_bin_capacity_bu)
            return capacity - d.unprocessed_grain_bu

        def transport_rate_buph(field: Field) -> float:
            cycle_h = 2.0 * field.one_way_travel_min / 60.0 + c.semi_unload_time_h
            return min(
                c.semi_count * c.semi_capacity_bu / cycle_h,
                c.semi_capacity_bu / c.semi_unload_time_h,
                c.grain_leg_rate_buph,
            )

        def harvest_window() -> tuple[bool, float]:
            """Return whether harvesting is open and hours to its next boundary."""
            if c.harvest_rest_window_h <= c.numerical_tolerance:
                return True, inf
            cycle_h = c.harvest_work_window_h + c.harvest_rest_window_h
            phase_h = d.time_h % cycle_h
            if phase_h < c.harvest_work_window_h - c.numerical_tolerance:
                return True, c.harvest_work_window_h - phase_h
            return False, cycle_h - phase_h

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
            active_mode = ""
            active_rate_buph = 0.0
            if active:
                active_field = self.fields[active[0]]
                if active[1] == INTERIOR and current_interior_mode:
                    active_mode = current_interior_mode
                    active_rate_buph = c.interior_rate_buph(current_interior_mode)
                elif active[1] != INTERIOR:
                    active_mode = "Opening-2C"
                    active_rate_buph = c.opening_rate_buph(active_field.geometry)
            downstream_snapshot = d.snapshot()
            fallback_moisture_pct = (
                self.fields[active[0]].moisture_pct if active else None)
            rest_inventory_target_bu = d.dryer_inventory_target_bu(
                c.harvest_rest_window_h, fallback_moisture_pct)
            harvesting_open, hours_to_boundary_h = harvest_window()
            hours_to_rest_h = hours_to_boundary_h if harvesting_open else 0.0
            series.append({
                "time_h": d.time_h,
                "duration_h": duration_h,
                "current_field": self.fields[active[0]].field_id if active else "",
                "current_task": active[1] if active else "",
                "operating_mode": active_mode,
                "effective_harvest_rate_buph": active_rate_buph,
                "interior_mode_dwell_h": (interior_mode_dwell_h
                                           if active_mode in INTERIOR_MODES else 0.0),
                "interior_mode_switches_current_field": field_interior_mode_switches,
                "current_moisture_pct": (self.fields[active[0]].moisture_pct
                                           if active else ""),
                "combine_state": state,
                "event": event,
                "hours_to_harvest_rest_h": hours_to_rest_h,
                "rest_dryer_inventory_target_bu": rest_inventory_target_bu,
                "rest_dryer_inventory_target_gap_bu": (
                    rest_inventory_target_bu - d.wet_bin_inventory_bu),
                "cumulative_harvested_ac": harvested_ac,
                "cumulative_grain_generated_bu": generated_bu,
                **downstream_snapshot,
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

            if interior_mode_switch_remaining_h > c.numerical_tolerance:
                step = min(step, interior_mode_switch_remaining_h)
                d.advance_time(step)
                interior_mode_switch_remaining_h -= step
                mode_switching_h += step
                record("MODE_SWITCHING", current, step)
                if interior_mode_switch_remaining_h <= c.numerical_tolerance:
                    if pending_interior_mode is None or combine_field is None:
                        raise AssertionError("Interior mode switch has no destination")
                    current_interior_mode = pending_interior_mode
                    pending_interior_mode = None
                    interior_mode_dwell_h = 0.0
                    mode_sequences[self.fields[combine_field].field_id].append(
                        current_interior_mode)
                    record("MODE_EVENT", current,
                           event=f"MODE_SWITCH_COMPLETE:{current_interior_mode}")
                continue

            harvesting_open, window_remaining_h = harvest_window()
            if not harvesting_open:
                # Harvesting stops, but partial field-side loads are released so
                # transport, the grain leg, and the dryer can keep working.
                d.dispatch_final_loads()
                step = min(step, d.next_event_h - d.time_h, window_remaining_h)
                d.advance_time(step)
                scheduled_rest_h += step
                record("HARVEST_REST", current, step)
                continue

            # Do not let a productive or blocked interval cross into the
            # scheduled rest window.
            step = min(step, window_remaining_h)

            # A selected field must finish both phases before another field can start.
            if combine_field is not None and not self.fields[combine_field].field_complete:
                choice = (combine_field, self.fields[combine_field].available_tasks()[0])
            else:
                choice = self.controller.choose(
                    self.fields,
                    current,
                    receiving_pressure=d.receiving_pressure,
                    downstream_inventory_bu=(d.receiving_pit_inventory_bu
                                             + d.wet_bin_inventory_bu),
                    field_mobile_free_bu=field_mobile_free_bu(),
                    system_buffer_free_bu=system_buffer_free_bu(),
                    current_dryer_rate_buph=d.current_dryer_rate_buph,
                    wet_bin_inventory_bu=d.wet_bin_inventory_bu,
                    hours_to_harvest_rest_h=window_remaining_h,
                    rest_inventory_target_bu=d.dryer_inventory_target_bu(
                        c.harvest_rest_window_h,
                        (self.fields[current[0]].moisture_pct
                         if current else None)),
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
            rest_inventory_target_bu = d.dryer_inventory_target_bu(
                c.harvest_rest_window_h, field.moisture_pct)
            if current[1] == INTERIOR:
                may_choose_mode = (
                    current_interior_mode is None
                    or (self.controller.allows_interior_mode_switching
                        and field_interior_mode_switches < c.max_interior_mode_switches
                        and interior_mode_dwell_h + c.numerical_tolerance
                        >= c.minimum_interior_mode_dwell_h)
                )
                if may_choose_mode:
                    proposed_mode = self.controller.choose_interior_mode(
                        remaining_grain_bu=(field.interior_remaining_ac
                                            * c.yield_bu_per_ac),
                        receiving_pressure=d.receiving_pressure,
                        field_mobile_free_bu=field_mobile_free_bu(),
                        system_buffer_free_bu=system_buffer_free_bu(),
                        transport_rate_buph=transport_rate_buph(field),
                        active_dryer_rate_buph=(d.current_dryer_rate_buph
                                                or c.dryer_rate_for_moisture(
                                                    field.moisture_pct)),
                        wet_bin_inventory_bu=d.wet_bin_inventory_bu,
                        hours_to_harvest_rest_h=window_remaining_h,
                        rest_inventory_target_bu=rest_inventory_target_bu,
                    )
                    if current_interior_mode is None:
                        current_interior_mode = proposed_mode
                        interior_mode_dwell_h = 0.0
                        mode_sequences[field.field_id].append(proposed_mode)
                        record("MODE_EVENT", current,
                               event=f"MODE_START:{proposed_mode}")
                    elif proposed_mode != current_interior_mode:
                        old_mode = current_interior_mode
                        interior_mode_switches += 1
                        field_interior_mode_switches += 1
                        if c.interior_mode_switch_time_h > c.numerical_tolerance:
                            pending_interior_mode = proposed_mode
                            interior_mode_switch_remaining_h = c.interior_mode_switch_time_h
                            record("MODE_EVENT", current,
                                   event=f"MODE_SWITCH_START:{old_mode}->{proposed_mode}")
                            continue
                        current_interior_mode = proposed_mode
                        interior_mode_dwell_h = 0.0
                        mode_sequences[field.field_id].append(proposed_mode)
                        record("MODE_EVENT", current,
                               event=f"MODE_SWITCH:{old_mode}->{proposed_mode}")
                if current_interior_mode is None:
                    raise AssertionError("Interior harvesting requires an operating mode")
                capacity = self.capacity(field, current[1], current_interior_mode)
            else:
                capacity = self.capacity(field, current[1])
            free_tank_bu = c.combine_tank_capacity_bu - d.combine_tank_bu
            if free_tank_bu <= c.numerical_tolerance:
                if any(semi.state == AT_FIELD for semi in d.semis):
                    raise AssertionError("Combine blocked while a semi can receive grain")
                d.advance_time(step)
                blocked_h += step
                if current[1] == INTERIOR:
                    interior_mode_dwell_h += step
                record("BLOCKED", current, step)
                continue

            step = min(step, field.remaining(current[1]) / capacity,
                       free_tank_bu / (capacity * c.yield_bu_per_ac))
            acres = capacity * step
            grain = acres * c.yield_bu_per_ac
            d.advance_time(step)
            d.add_harvested_grain(grain, field.moisture_pct, field.field_id)
            field.harvest(current[1], acres)
            harvested_ac += acres
            generated_bu += grain
            productive_h += step
            if current[1] == INTERIOR:
                if current_interior_mode is None:
                    raise AssertionError("Missing mode during interior harvest")
                interior_mode_dwell_h += step
                productive_h_by_mode[current_interior_mode] += step
                if current_interior_mode != MODE_2C_1GC:
                    intentional_slowdown_productive_h += step
                    intentional_rate_reduction_capacity_bu += (
                        c.interior_2c_1gc_rate_buph
                        - c.interior_rate_buph(current_interior_mode)
                    ) * step
            record("HARVESTING", current, step)
            if current[1] not in field.available_tasks():
                current = None

        if harvest_completion_h is None:
            raise AssertionError("Missing harvest completion time")
        mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
        field_by_id = {field.field_id: field for field in self.fields}
        grain_weighted_moisture = sum(
            field.total_area_ac * c.yield_bu_per_ac * field.moisture_pct
            for field in self.fields
        ) / c.total_grain_bu
        rest_start_samples: list[dict[str, Any]] = []
        rest_end_samples: list[dict[str, Any]] = []
        if c.harvest_rest_window_h > c.numerical_tolerance:
            cycle_h = c.harvest_work_window_h + c.harvest_rest_window_h
            rest_start_h = c.harvest_work_window_h
            while rest_start_h < harvest_completion_h - c.numerical_tolerance:
                row = min(series, key=lambda item: abs(item["time_h"] - rest_start_h))
                if abs(row["time_h"] - rest_start_h) <= c.dt_h + c.numerical_tolerance:
                    rest_start_samples.append(row)
                rest_end_h = rest_start_h + c.harvest_rest_window_h
                end_row = min(series,
                              key=lambda item: abs(item["time_h"] - rest_end_h))
                if abs(end_row["time_h"] - rest_end_h) <= (
                        c.dt_h + c.numerical_tolerance):
                    rest_end_samples.append(end_row)
                rest_start_h += cycle_h
        rest_start_count = len(rest_start_samples)
        mean_rest_start_wet_bin_bu = (
            sum(row["wet_bin_inventory_bu"] for row in rest_start_samples)
            / rest_start_count if rest_start_count else 0.0
        )
        mean_rest_target_bu = (
            sum(row["rest_dryer_inventory_target_bu"] for row in rest_start_samples)
            / rest_start_count if rest_start_count else 0.0
        )
        mean_abs_rest_target_error_bu = (
            sum(abs(row["rest_dryer_inventory_target_gap_bu"])
                for row in rest_start_samples) / rest_start_count
            if rest_start_count else 0.0
        )
        mean_rest_target_shortfall_bu = (
            sum(max(0.0, row["rest_dryer_inventory_target_gap_bu"])
                for row in rest_start_samples) / rest_start_count
            if rest_start_count else 0.0
        )
        mean_rest_target_excess_bu = (
            sum(max(0.0, -row["rest_dryer_inventory_target_gap_bu"])
                for row in rest_start_samples) / rest_start_count
            if rest_start_count else 0.0
        )
        mean_rest_end_wet_bin_bu = (
            sum(row["wet_bin_inventory_bu"] for row in rest_end_samples)
            / len(rest_end_samples) if rest_end_samples else 0.0
        )
        total_rest_dryer_operating_h = sum(
            end["cumulative_dryer_operating_h"]
            - start["cumulative_dryer_operating_h"]
            for start, end in zip(rest_start_samples, rest_end_samples)
        )
        total_sampled_rest_h = len(rest_end_samples) * c.harvest_rest_window_h
        rest_dryer_utilization = (
            total_rest_dryer_operating_h / total_sampled_rest_h
            if total_sampled_rest_h > c.numerical_tolerance else 0.0
        )
        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "field_order": " -> ".join(field_order),
            "field_moistures_pct": " -> ".join(
                f"{field.field_id}:{field.moisture_pct:.4f}"
                for field in self.fields
            ),
            "dryer_rate_order": " -> ".join(
                f"{field_id}:{c.dryer_rate_for_moisture(field_by_id[field_id].moisture_pct):.4f}"
                for field_id in field_order
            ),
            "interior_mode_sequence": "; ".join(
                f"{field_id}:{'->'.join(mode_sequences[field_id])}"
                for field_id in field_order
            ),
            "random_seed": c.random_seed,
            "moisture_min_pct": c.moisture_min_pct,
            "moisture_max_pct": c.moisture_max_pct,
            "grain_weighted_average_moisture_pct": grain_weighted_moisture,
            "grain_leg_rate_buph": c.grain_leg_rate_buph,
            "wet_bin_capacity_bu": c.wet_bin_capacity_bu,
            "dryer_intercept_buph": c.dryer_intercept_buph,
            "dryer_moisture_slope_buph_per_pct": c.dryer_moisture_slope_buph_per_pct,
            "rectangular_opening_2c_rate_buph": c.rectangular_opening_2c_rate_buph,
            "irregular_opening_2c_rate_buph": c.irregular_opening_2c_rate_buph,
            "interior_1c_rate_buph": c.interior_1c_rate_buph,
            "interior_2c_rate_buph": c.interior_2c_rate_buph,
            "interior_2c_1gc_rate_buph": c.interior_2c_1gc_rate_buph,
            "legacy_interior_rate_buph": c.legacy_interior_rate_buph,
            "minimum_interior_mode_dwell_min": c.minimum_interior_mode_dwell_h * 60.0,
            "maximum_interior_mode_switches_per_field": c.max_interior_mode_switches,
            "interior_mode_switch_time_min": c.interior_mode_switch_time_h * 60.0,
            "interior_prediction_horizon_h": c.interior_prediction_horizon_h,
            "rest_inventory_shortage_weight": c.rest_inventory_shortage_weight,
            "rest_coverage_transport_fraction": c.rest_coverage_transport_fraction,
            "harvest_work_window_h": c.harvest_work_window_h,
            "harvest_rest_window_h": c.harvest_rest_window_h,
            "number_of_rest_starts_before_harvest_completion": rest_start_count,
            "mean_wet_bin_inventory_at_rest_start_bu": mean_rest_start_wet_bin_bu,
            "mean_rest_dryer_inventory_target_bu": mean_rest_target_bu,
            "mean_absolute_rest_inventory_target_error_bu": mean_abs_rest_target_error_bu,
            "mean_rest_inventory_target_shortfall_bu": mean_rest_target_shortfall_bu,
            "mean_rest_inventory_target_excess_bu": mean_rest_target_excess_bu,
            "mean_wet_bin_inventory_at_rest_end_bu": mean_rest_end_wet_bin_bu,
            "dryer_operating_time_during_sampled_rests_h": total_rest_dryer_operating_h,
            "dryer_idle_time_during_sampled_rests_h": (
                total_sampled_rest_h - total_rest_dryer_operating_h),
            "dryer_utilization_during_sampled_rests": rest_dryer_utilization,
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
            "forced_blocking_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "switching_time_h": switching_h,
            "interior_mode_switching_time_h": mode_switching_h,
            "scheduled_harvest_rest_time_h": scheduled_rest_h,
            "number_of_field_switches": switches,
            "number_of_interior_mode_switches": interior_mode_switches,
            "intentional_slowdown_productive_h": intentional_slowdown_productive_h,
            "intentional_rate_reduction_capacity_bu": intentional_rate_reduction_capacity_bu,
            "interior_1c_productive_h": productive_h_by_mode["1C"],
            "interior_2c_productive_h": productive_h_by_mode["2C"],
            "interior_2c_1gc_productive_h": productive_h_by_mode["2C+1GC"],
            "combine_utilization": (productive_h / harvest_completion_h
                                    if harvest_completion_h else 0.0),
            "total_harvested_ac": harvested_ac,
            "total_harvested_grain_bu": generated_bu,
            "total_unloaded_grain_bu": d.total_unloaded_bu,
            "total_grain_to_wet_bin_bu": d.total_leg_transferred_bu,
            "total_dried_grain_bu": d.total_dried_bu,
            "total_dried_average_moisture_pct": (
                d.total_dried_moisture_bushel_pct / d.total_dried_bu
                if d.total_dried_bu > c.numerical_tolerance else 0.0),
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
            "mass_balance_error_bu": (0.0 if abs(mass_error) <= 1e-6
                                      else mass_error),
        }
        daily_machine_hours = calculate_daily_machine_hours(series, c.semi_count)
        metrics.update(summarize_machine_hours(daily_machine_hours))
        return SimulationResult(self.controller.name, metrics, series,
                                self.fields, d.events)
