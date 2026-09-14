from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from .accounting import calculate_daily_machine_hours, summarize_machine_hours
from .config import (
    INTERIOR_MODES,
    MODE_1C,
    MODE_2C,
    MODE_2C_1GC,
    OPENING_MODES,
    SimulationConfig,
)
from .controller import (
    HARVEST,
    STOP,
    FixedController,
    ForecastSegment,
    PredictiveController,
    ReactiveController,
    TaskRef,
    ThresholdController,
)
from .downstream import Downstream
from .field import INTERIOR, OPENING, Field


@dataclass
class SimulationResult:
    policy: str
    metrics: dict[str, float | int | str]
    time_series: list[dict[str, Any]]
    fields: list[Field]
    transport_events: list[dict[str, Any]]


class Simulator:
    def __init__(
            self, config: SimulationConfig, policy: str = "fixed",
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
        normalized = {"sequential": "fixed", "adaptive": "predictive"}.get(
            policy, policy)
        controllers = {
            "fixed": FixedController,
            "threshold": ThresholdController,
            "reactive": ReactiveController,
            "predictive": PredictiveController,
        }
        try:
            self.controller = controllers[normalized](config)
        except KeyError as error:
            raise ValueError(f"Unknown policy: {policy}") from error

    def capacity(self, field: Field, task: str, mode: str = MODE_2C) -> float:
        """Effective capacity in ac/h; no further efficiency deduction applies."""
        rate_buph = (self.config.opening_rate_buph(field.geometry, mode)
                     if task == OPENING
                     else self.config.interior_rate_buph(mode))
        return rate_buph / self.config.yield_bu_per_ac

    def run(self) -> SimulationResult:
        c, d = self.config, self.downstream
        productive_h = blocked_h = switching_h = mode_switching_h = 0.0
        planned_stop_h = 0.0
        dryer_operating_during_stop_h = 0.0
        harvested_ac = generated_bu = 0.0
        field_switches = interior_mode_switches = 0
        intentional_slowdown_h = intentional_rate_reduction_bu = 0.0
        productive_h_by_mode = {mode: 0.0 for mode in INTERIOR_MODES}
        opening_productive_h_by_mode = {mode: 0.0 for mode in OPENING_MODES}

        current: TaskRef | None = None
        combine_field: int | None = None
        field_order: list[str] = []
        pending_field: TaskRef | None = None
        field_switch_remaining_h = 0.0

        current_opening_mode: str | None = None
        current_interior_mode: str | None = None
        pending_interior_mode: str | None = None
        interior_mode_dwell_h = 0.0
        interior_mode_switch_remaining_h = 0.0
        field_interior_mode_switches = 0
        opening_modes: dict[str, str] = {}
        interior_mode_sequences: dict[str, list[str]] = {}

        in_stop = False
        stop_elapsed_h = 0.0
        productive_since_stop_h = 0.0
        stop_count = 0
        stop_durations: list[float] = []
        current_stop_duration_h = 0.0
        stop_start_inventories: list[float] = []
        stop_end_inventories: list[float] = []

        # All state-dependent policies reconsider their actions on the same
        # clock.  This is both an experimental fairness condition and avoids
        # pretending that farm decisions are revised every simulation minute.
        next_control_decision_h = 0.0
        cached_schedule_action = HARVEST

        harvest_completion_h: float | None = None
        harvest_end_inventory: dict[str, Any] = {}
        series: list[dict[str, Any]] = []

        def begin_field(field_index: int) -> None:
            nonlocal current_opening_mode, current_interior_mode
            nonlocal pending_interior_mode, interior_mode_dwell_h
            nonlocal interior_mode_switch_remaining_h
            nonlocal field_interior_mode_switches
            field = self.fields[field_index]
            d.set_field_context(field.field_id, field.one_way_travel_min)
            field_order.append(field.field_id)
            current_opening_mode = None
            current_interior_mode = None
            pending_interior_mode = None
            interior_mode_dwell_h = 0.0
            interior_mode_switch_remaining_h = 0.0
            field_interior_mode_switches = 0
            interior_mode_sequences[field.field_id] = []

        def active_mode_for(task: str | None) -> str:
            if task == OPENING:
                return current_opening_mode or MODE_2C
            return current_interior_mode or MODE_2C_1GC

        def active_storage_context(task: str | None) -> tuple[float, bool]:
            mode = active_mode_for(task)
            combine_capacity = (
                c.combine_count_for_mode(mode)
                * c.combine_tank_capacity_per_combine_bu)
            return combine_capacity, c.grain_cart_enabled_for_mode(mode)

        def transport_rate_buph(field: Field) -> float:
            cycle_h = (2.0 * field.one_way_travel_min / 60.0
                       + c.semi_unload_time_h)
            return min(
                c.semi_count * c.semi_capacity_bu / cycle_h,
                c.semi_capacity_bu / c.semi_unload_time_h,
                c.grain_leg_rate_buph,
            )

        def forecast_segments() -> tuple[ForecastSegment, ...]:
            segments: list[ForecastSegment] = []
            for index in c.field_order_indices():
                field = self.fields[index]
                rate = transport_rate_buph(field)
                if field.opening_remaining_ac > c.numerical_tolerance:
                    segments.append(ForecastSegment(
                        field.field_id, OPENING, field.geometry,
                        field.opening_remaining_ac * c.yield_bu_per_ac,
                        field.moisture_pct, rate,
                    ))
                if field.interior_remaining_ac > c.numerical_tolerance:
                    segments.append(ForecastSegment(
                        field.field_id, INTERIOR, field.geometry,
                        field.interior_remaining_ac * c.yield_bu_per_ac,
                        field.moisture_pct, rate,
                    ))
            return tuple(segments)

        def control_state(field: Field, task: str) -> dict[str, Any]:
            available_modes = OPENING_MODES if task == OPENING else INTERIOR_MODES
            combine_capacity, grain_cart_enabled = active_storage_context(task)
            system_free_bu = max(0.0, c.total_system_storage_bu - d.unprocessed_grain_bu)
            response_h = max(
                c.dt_h,
                field.one_way_travel_min / 60.0
                + c.semi_unload_time_h
                + c.interior_mode_switch_time_h,
            )
            switch_available = (
                task == INTERIOR
                and field_interior_mode_switches < c.max_interior_mode_switches)
            stop_eligible = (
                productive_since_stop_h + c.numerical_tolerance
                >= c.stop_work_requirement_h)
            hours_until_stop = max(
                0.0, c.stop_work_requirement_h - productive_since_stop_h)
            protection_h = response_h
            if not switch_available and not stop_eligible:
                protection_h = max(response_h, hours_until_stop)
            return {
                "phase": task,
                "geometry": field.geometry,
                "remaining_grain_bu": field.remaining(task) * c.yield_bu_per_ac,
                "receiving_pressure": d.receiving_pressure,
                "field_mobile_free_bu": d.field_mobile_free_bu(
                    combine_capacity, grain_cart_enabled),
                "system_buffer_free_bu": system_free_bu,
                "wet_bin_inventory_bu": d.wet_bin_inventory_bu,
                "wet_bin_free_bu": c.wet_bin_capacity_bu - d.wet_bin_inventory_bu,
                "committed_grain_bu": d.committed_grain_bu,
                "transport_rate_buph": transport_rate_buph(field),
                "active_dryer_rate_buph": (
                    d.current_dryer_rate_buph
                    or c.dryer_rate_for_moisture(field.moisture_pct)),
                "response_time_h": response_h,
                "protection_h": protection_h,
                "stop_eligible": stop_eligible,
                "hours_until_stop_eligible_h": hours_until_stop,
                "in_stop": in_stop,
                "stop_elapsed_h": stop_elapsed_h,
                "productive_since_stop_h": productive_since_stop_h,
                "available_modes": available_modes,
                "mode_switches_used": field_interior_mode_switches,
                "forecast_segments": forecast_segments(),
                "forecast_lots": d.forecast_lots(),
            }

        def record(
                state: str, active: TaskRef | None,
                duration_h: float = 0.0, event: str = "") -> None:
            mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
            if abs(mass_error) > 1e-6:
                raise AssertionError(f"Mass balance error: {mass_error}")
            if not -c.numerical_tolerance <= d.combine_tank_bu <= c.maximum_combine_storage_bu + c.numerical_tolerance:
                raise AssertionError("Combine grain tank outside physical bounds")
            if not -c.numerical_tolerance <= d.grain_cart_inventory_bu <= c.grain_cart_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Grain cart outside physical bounds")
            for semi in d.semis:
                if not -c.numerical_tolerance <= semi.load_bu <= c.semi_capacity_bu + c.numerical_tolerance:
                    raise AssertionError("Semi load outside physical bounds")
            if not -c.numerical_tolerance <= d.wet_bin_inventory_bu <= c.wet_bin_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Wet bin outside physical bounds")
            task = active[1] if active else None
            active_mode = active_mode_for(task) if task else ""
            active_rate_buph = 0.0
            if task:
                field = self.fields[active[0]]
                active_rate_buph = (
                    c.opening_rate_buph(field.geometry, active_mode)
                    if task == OPENING else c.interior_rate_buph(active_mode))
            row = {
                "time_h": d.time_h,
                "duration_h": duration_h,
                "current_field": self.fields[active[0]].field_id if active else "",
                "current_task": task or "",
                "operating_mode": active_mode,
                "effective_harvest_rate_buph": active_rate_buph,
                "interior_mode_dwell_h": (interior_mode_dwell_h
                                           if task == INTERIOR else 0.0),
                "interior_mode_switches_current_field": field_interior_mode_switches,
                "current_moisture_pct": (
                    self.fields[active[0]].moisture_pct if active else ""),
                "combine_state": state,
                "event": event,
                "productive_since_stop_h": productive_since_stop_h,
                "stop_eligible": int(
                    productive_since_stop_h + c.numerical_tolerance
                    >= c.stop_work_requirement_h),
                "current_stop_elapsed_h": stop_elapsed_h if in_stop else 0.0,
                "cumulative_planned_stop_h": planned_stop_h,
                "cumulative_harvested_ac": harvested_ac,
                "cumulative_grain_generated_bu": generated_bu,
                **d.snapshot(),
            }
            series.append(row)

        record("IDLE", None)
        while True:
            harvest_finished = all(field.field_complete for field in self.fields)
            if harvest_finished and harvest_completion_h is None:
                harvest_completion_h = d.time_h
                harvest_end_inventory = d.snapshot()
                record("HARVEST_COMPLETE", None)

            event_start = len(d.events)
            d.handle_events()
            task = current[1] if current else None
            combine_capacity, cart_enabled = active_storage_context(task)
            if harvest_finished:
                d.dispatch_partial_loads(combine_capacity, cart_enabled)
            else:
                d.unload_field_storage(combine_capacity, cart_enabled,
                                       force=in_stop)
                if in_stop:
                    d.dispatch_partial_loads(combine_capacity, cart_enabled)
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
            if step <= c.numerical_tolerance:
                d.handle_events()
                continue
            if harvest_finished:
                d.advance_time(step)
                record("POST_HARVEST", None, step)
                continue

            if field_switch_remaining_h > c.numerical_tolerance:
                step = min(step, field_switch_remaining_h)
                d.advance_time(step)
                field_switch_remaining_h -= step
                switching_h += step
                record("SWITCHING", pending_field, step)
                if field_switch_remaining_h <= c.numerical_tolerance:
                    current, pending_field = pending_field, None
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
                    interior_mode_sequences[
                        self.fields[combine_field].field_id].append(
                            current_interior_mode)
                    record("MODE_EVENT", current,
                           event=f"MODE_SWITCH_COMPLETE:{current_interior_mode}")
                continue

            if combine_field is not None and not self.fields[combine_field].field_complete:
                choice = (combine_field,
                          self.fields[combine_field].available_tasks()[0])
            else:
                choice = self.controller.choose(self.fields, current)
            if choice is None:
                raise AssertionError("Unfinished fields have no available task")
            if combine_field is not None and choice[0] != combine_field:
                field_switches += 1
                if c.field_switch_time_h > c.numerical_tolerance:
                    pending_field = choice
                    field_switch_remaining_h = c.field_switch_time_h
                    continue
            current = choice
            if combine_field != choice[0]:
                combine_field = choice[0]
                begin_field(combine_field)

            field = self.fields[current[0]]
            task = current[1]
            state = control_state(field, task)
            control_due = (
                d.time_h + c.numerical_tolerance >= next_control_decision_h)
            if control_due:
                next_control_decision_h = (
                    d.time_h + c.control_decision_interval_h)

            if in_stop:
                if stop_elapsed_h + c.numerical_tolerance < c.minimum_stop_h:
                    action = STOP
                elif control_due:
                    cached_schedule_action = (
                        self.controller.choose_schedule_action(**state))
                    action = cached_schedule_action
                else:
                    action = cached_schedule_action
                if (stop_elapsed_h + c.numerical_tolerance >= c.minimum_stop_h
                        and action == HARVEST):
                    in_stop = False
                    stop_durations.append(current_stop_duration_h)
                    current_stop_duration_h = 0.0
                    stop_end_inventories.append(d.wet_bin_inventory_bu)
                    productive_since_stop_h = 0.0
                    cached_schedule_action = HARVEST
                    record("STOP_END", current, event="VOLUNTARY_STOP_END")
                    continue
                step = min(step, max(
                    c.dt_h, c.minimum_stop_h - stop_elapsed_h))
                dryer_before_h = d.total_dryer_operating_h
                d.advance_time(step)
                dryer_operating_during_stop_h += (
                    d.total_dryer_operating_h - dryer_before_h)
                stop_elapsed_h += step
                current_stop_duration_h += step
                planned_stop_h += step
                record("PLANNED_STOP", current, step)
                continue

            if not state["stop_eligible"]:
                cached_schedule_action = HARVEST
            elif control_due:
                cached_schedule_action = (
                    self.controller.choose_schedule_action(**state))
            stop_action = cached_schedule_action
            if stop_action == STOP:
                in_stop = True
                stop_elapsed_h = 0.0
                current_stop_duration_h = 0.0
                cached_schedule_action = STOP
                stop_count += 1
                stop_start_inventories.append(d.wet_bin_inventory_bu)
                d.dispatch_partial_loads(*active_storage_context(task))
                record("STOP_START", current, event="VOLUNTARY_STOP_START")
                continue

            if task == OPENING:
                if current_opening_mode is None:
                    current_opening_mode = self.controller.choose_opening_mode(**state)
                    opening_modes[field.field_id] = current_opening_mode
                    record("MODE_EVENT", current,
                           event=f"OPENING_MODE_START:{current_opening_mode}")
                mode = current_opening_mode
            else:
                may_choose_mode = (
                    current_interior_mode is None
                    or (control_due
                        and self.controller.allows_interior_mode_switching
                        and field_interior_mode_switches < c.max_interior_mode_switches
                        and interior_mode_dwell_h + c.numerical_tolerance
                        >= c.minimum_interior_mode_dwell_h))
                if may_choose_mode:
                    proposed_mode = self.controller.choose_interior_mode(**state)
                    if current_interior_mode is None:
                        current_interior_mode = proposed_mode
                        interior_mode_dwell_h = 0.0
                        interior_mode_sequences[field.field_id].append(proposed_mode)
                        record("MODE_EVENT", current,
                               event=f"MODE_START:{proposed_mode}")
                    elif proposed_mode != current_interior_mode:
                        old_mode = current_interior_mode
                        interior_mode_switches += 1
                        field_interior_mode_switches += 1
                        pending_interior_mode = proposed_mode
                        interior_mode_switch_remaining_h = c.interior_mode_switch_time_h
                        record("MODE_EVENT", current,
                               event=f"MODE_SWITCH_START:{old_mode}->{proposed_mode}")
                        if interior_mode_switch_remaining_h <= c.numerical_tolerance:
                            current_interior_mode = proposed_mode
                            pending_interior_mode = None
                            interior_mode_dwell_h = 0.0
                            interior_mode_sequences[field.field_id].append(
                                proposed_mode)
                        continue
                if current_interior_mode is None:
                    raise AssertionError("Interior harvesting requires an operating mode")
                mode = current_interior_mode

            combine_capacity = (c.combine_count_for_mode(mode)
                                * c.combine_tank_capacity_per_combine_bu)
            cart_enabled = c.grain_cart_enabled_for_mode(mode)
            d.unload_field_storage(combine_capacity, cart_enabled)
            free_tank_bu = combine_capacity - d.combine_tank_bu
            if free_tank_bu <= c.numerical_tolerance:
                d.advance_time(step)
                blocked_h += step
                if task == INTERIOR:
                    interior_mode_dwell_h += step
                record("BLOCKED", current, step)
                continue

            capacity_acph = self.capacity(field, task, mode)
            step = min(step, field.remaining(task) / capacity_acph,
                       free_tank_bu / (capacity_acph * c.yield_bu_per_ac))
            acres = capacity_acph * step
            grain = acres * c.yield_bu_per_ac
            d.advance_time(step)
            d.add_harvested_grain(grain, field.moisture_pct, field.field_id)
            field.harvest(task, acres)
            harvested_ac += acres
            generated_bu += grain
            productive_h += step
            productive_since_stop_h += step
            if task == OPENING:
                opening_productive_h_by_mode[mode] += step
                maximum_rate = c.opening_rate_buph(field.geometry, MODE_2C)
                if mode != MODE_2C:
                    intentional_slowdown_h += step
                    intentional_rate_reduction_bu += (
                        maximum_rate - c.opening_rate_buph(field.geometry, mode)) * step
            else:
                interior_mode_dwell_h += step
                productive_h_by_mode[mode] += step
                if mode != MODE_2C_1GC:
                    intentional_slowdown_h += step
                    intentional_rate_reduction_bu += (
                        c.interior_2c_1gc_rate_buph
                        - c.interior_rate_buph(mode)) * step
            record("HARVESTING", current, step)
            if task not in field.available_tasks():
                current = None

        if harvest_completion_h is None:
            raise AssertionError("Missing harvest completion time")
        if in_stop and current_stop_duration_h > 0:
            stop_durations.append(current_stop_duration_h)
        mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
        field_by_id = {field.field_id: field for field in self.fields}
        grain_weighted_moisture = sum(
            field.total_area_ac * c.yield_bu_per_ac * field.moisture_pct
            for field in self.fields) / c.total_grain_bu
        stop_hours = sum(stop_durations)
        mean_stop_h = stop_hours / len(stop_durations) if stop_durations else 0.0
        mean_stop_start_inventory = (
            sum(stop_start_inventories) / len(stop_start_inventories)
            if stop_start_inventories else 0.0)
        mean_stop_end_inventory = (
            sum(stop_end_inventories) / len(stop_end_inventories)
            if stop_end_inventories else 0.0)

        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "research_question": (
                "When does predictive coordination provide additional value over "
                "current-state reactive coordination under downstream constraints?"),
            "field_order": " -> ".join(field_order),
            "field_order_rule": c.default_field_order_rule,
            "field_moistures_pct": " -> ".join(
                f"{field.field_id}:{field.moisture_pct:.4f}" for field in self.fields),
            "dryer_rate_order": " -> ".join(
                f"{field_id}:{c.dryer_rate_for_moisture(field_by_id[field_id].moisture_pct):.4f}"
                for field_id in field_order),
            "opening_mode_sequence": "; ".join(
                f"{field_id}:{opening_modes.get(field_id, '')}" for field_id in field_order),
            "interior_mode_sequence": "; ".join(
                f"{field_id}:{'->'.join(interior_mode_sequences[field_id])}"
                for field_id in field_order),
            "random_seed": c.random_seed,
            "moisture_min_pct": c.moisture_min_pct,
            "moisture_max_pct": c.moisture_max_pct,
            "grain_weighted_average_moisture_pct": grain_weighted_moisture,
            "opening_area_fraction": c.opening_area_fraction,
            "grain_leg_rate_buph": c.grain_leg_rate_buph,
            "wet_bin_capacity_bu": c.wet_bin_capacity_bu,
            "dryer_intercept_buph": c.dryer_intercept_buph,
            "dryer_moisture_slope_buph_per_pct": c.dryer_moisture_slope_buph_per_pct,
            "interior_1c_rate_buph": c.interior_1c_rate_buph,
            "interior_2c_rate_buph": c.interior_2c_rate_buph,
            "interior_2c_1gc_rate_buph": c.interior_2c_1gc_rate_buph,
            "minimum_interior_mode_dwell_min": c.minimum_interior_mode_dwell_h * 60.0,
            "maximum_interior_mode_switches_per_field": c.max_interior_mode_switches,
            "interior_mode_switch_time_min": c.interior_mode_switch_time_h * 60.0,
            "prediction_horizon_h": c.prediction_horizon_h,
            "control_decision_interval_min": c.control_decision_interval_h * 60.0,
            "stop_work_requirement_h": c.stop_work_requirement_h,
            "minimum_stop_h": c.minimum_stop_h,
            "number_of_planned_stops": stop_count,
            "planned_stop_time_h": planned_stop_h,
            "mean_planned_stop_duration_h": mean_stop_h,
            "minimum_observed_planned_stop_duration_h": (
                min(stop_durations) if stop_durations else 0.0),
            "mean_wet_bin_inventory_at_stop_start_bu": mean_stop_start_inventory,
            "mean_wet_bin_inventory_at_stop_end_bu": mean_stop_end_inventory,
            "dryer_operating_time_during_planned_stops_h": dryer_operating_during_stop_h,
            "dryer_idle_time_during_planned_stops_h": max(
                0.0, planned_stop_h - dryer_operating_during_stop_h),
            "semi_unload_time_min": c.semi_unload_time_h * 60.0,
            "combine_tank_capacity_per_combine_bu": c.combine_tank_capacity_per_combine_bu,
            "grain_cart_capacity_bu": c.grain_cart_capacity_bu,
            "semi_count": c.semi_count,
            "semi_capacity_bu": c.semi_capacity_bu,
            "receiving_pit_capacity_bu": c.receiving_pit_capacity_bu,
            "total_completion_time_h": harvest_completion_h,
            "harvest_completion_time_h": harvest_completion_h,
            "system_completion_time_h": d.time_h,
            "post_harvest_time_h": d.time_h - harvest_completion_h,
            "productive_harvest_time_h": productive_h,
            "blocked_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "forced_blocking_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "switching_time_h": switching_h,
            "interior_mode_switching_time_h": mode_switching_h,
            "number_of_field_switches": field_switches,
            "number_of_interior_mode_switches": interior_mode_switches,
            "intentional_slowdown_productive_h": intentional_slowdown_h,
            "intentional_rate_reduction_capacity_bu": intentional_rate_reduction_bu,
            "opening_1c_productive_h": opening_productive_h_by_mode[MODE_1C],
            "opening_2c_productive_h": opening_productive_h_by_mode[MODE_2C],
            "interior_1c_productive_h": productive_h_by_mode[MODE_1C],
            "interior_2c_productive_h": productive_h_by_mode[MODE_2C],
            "interior_2c_1gc_productive_h": productive_h_by_mode[MODE_2C_1GC],
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
            "dried_at_harvest_completion_bu": harvest_end_inventory[
                "cumulative_grain_dried_bu"],
            "combine_tank_at_harvest_completion_bu": harvest_end_inventory[
                "combine_tank_bu"],
            "grain_cart_at_harvest_completion_bu": harvest_end_inventory[
                "grain_cart_inventory_bu"],
            "semi_at_field_grain_at_harvest_completion_bu": harvest_end_inventory[
                "semi_at_field_grain_bu"],
            "in_transit_grain_at_harvest_completion_bu": harvest_end_inventory[
                "in_transit_grain_bu"],
            "semi_at_gc_grain_at_harvest_completion_bu": harvest_end_inventory[
                "semi_at_gc_grain_bu"],
            "wet_bin_at_harvest_completion_bu": harvest_end_inventory[
                "wet_bin_inventory_bu"],
            "maximum_combine_tank_bu": max(row["combine_tank_bu"] for row in series),
            "maximum_grain_cart_inventory_bu": max(
                row["grain_cart_inventory_bu"] for row in series),
            "maximum_receiving_pit_inventory_bu": 0.0,
            "maximum_wet_bin_inventory_bu": max(
                row["wet_bin_inventory_bu"] for row in series),
            "number_of_semi_trips": sum(semi.trips for semi in d.semis),
            "final_unprocessed_grain_bu": d.unprocessed_grain_bu,
            "mass_balance_error_bu": 0.0 if abs(mass_error) <= 1e-6 else mass_error,
        }
        daily_machine_hours = calculate_daily_machine_hours(series, c.semi_count)
        metrics.update(summarize_machine_hours(daily_machine_hours))
        return SimulationResult(self.controller.name, metrics, series,
                                self.fields, d.events)
