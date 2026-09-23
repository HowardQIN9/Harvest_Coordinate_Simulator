from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
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
from .field import (
    INTERIOR,
    OPENING,
    Field,
    FieldGenerator,
    FieldSelector,
    FixedOrderFieldSelector,
)


@dataclass
class SimulationResult:
    policy: str
    metrics: dict[str, float | int | str]
    time_series: list[dict[str, Any]]
    fields: list[Field]
    transport_events: list[dict[str, Any]]
    controller_decisions: list[dict[str, Any]]
    state_snapshots: list[dict[str, Any]]


@dataclass
class SimulationCheckpoint:
    """A forkable, complete state at a simulation step boundary."""

    config: SimulationConfig
    fields: list[Field]
    downstream: Downstream
    controller: Any
    field_selector: FieldSelector
    field_moistures_pct: tuple[float, ...]
    state: dict[str, Any]


class Simulator:
    def __init__(
            self, config: SimulationConfig, policy: str = "fixed",
            field_moistures_pct: tuple[float, ...] | None = None,
            field_selector: FieldSelector | None = None,
            controller: Any | None = None) -> None:
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
        self.fields = FieldGenerator.generate(config, moistures)
        self.field_selector = (
            FixedOrderFieldSelector(config.field_order_indices())
            if field_selector is None else field_selector)
        self.downstream = Downstream(config)
        normalized = {"sequential": "fixed", "adaptive": "predictive"}.get(
            policy, policy)
        controllers = {
            "fixed": FixedController,
            "threshold": ThresholdController,
            "reactive": ReactiveController,
            "predictive": PredictiveController,
        }
        if controller is not None:
            self.controller = controller
        else:
            try:
                self.controller = controllers[normalized](config)
            except KeyError as error:
                raise ValueError(f"Unknown policy: {policy}") from error

    def capacity(self, field: Field, task: str, mode: str = MODE_2C) -> float:
        """Effective capacity in ac/h; no further efficiency deduction applies."""
        rate_buph = (self.config.opening_rate_buph(
                         field.geometry, mode, field.opening_capacity_factor)
                     if task == OPENING
                     else self.config.interior_rate_buph(mode))
        return rate_buph / field.yield_bu_per_ac

    def run(
            self, *, checkpoint_at_h: float | None = None,
            checkpoint_after_completed_fields: int | None = None,
            checkpoint_on_field_change: bool = False,
            record_trace: bool = True,
            simulation_step_h: float | None = None,
            resume_from: SimulationCheckpoint | None = None,
            resume_controller: Any | None = None,
    ) -> SimulationResult | SimulationCheckpoint:
        if resume_controller is not None and resume_from is None:
            raise ValueError("resume_controller requires resume_from")
        if resume_from is not None:
            if resume_from.config != self.config:
                raise ValueError("Checkpoint configuration does not match simulator")
            self.fields = deepcopy(resume_from.fields)
            self.downstream = deepcopy(resume_from.downstream)
            self.field_selector = deepcopy(resume_from.field_selector)
            self.field_moistures_pct = resume_from.field_moistures_pct
            self.controller = deepcopy(
                resume_from.controller if resume_controller is None
                else resume_controller)
        if checkpoint_at_h is not None and checkpoint_at_h < 0:
            raise ValueError("checkpoint_at_h must be nonnegative")
        if (checkpoint_after_completed_fields is not None
                and checkpoint_after_completed_fields < 0):
            raise ValueError(
                "checkpoint_after_completed_fields must be nonnegative")
        if simulation_step_h is not None and simulation_step_h <= 0:
            raise ValueError("simulation_step_h must be positive")
        c, d = self.config, self.downstream
        physical_step_h = (
            c.dt_h if simulation_step_h is None else simulation_step_h)
        productive_h = blocked_h = switching_h = mode_switching_h = 0.0
        planned_stop_h = 0.0
        dryer_operating_during_stop_h = 0.0
        maximum_dryer_idle_episode_during_stop_h = 0.0
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
        planned_stop_used_current_field = False
        stop_request_started_h: float | None = None
        stop_durations: list[float] = []
        current_stop_duration_h = 0.0
        stop_start_inventories: list[float] = []
        stop_end_inventories: list[float] = []

        # Physical state is observed every minute. Predictive performs the
        # expensive rollout on its own 30-minute/event-driven planning clock;
        # Threshold and Reactive use the one-minute control clock.
        controller_decision_interval_h = (
            float(getattr(self.controller, "decision_interval_h"))
            if hasattr(self.controller, "decision_interval_h")
            else (c.predictive_decision_interval_h
                  if self.controller.name == "predictive"
                  else c.control_decision_interval_h))
        next_control_decision_h = 0.0
        cached_schedule_action = HARVEST

        harvest_completion_h: float | None = None
        harvest_end_inventory: dict[str, Any] = {}
        series: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        next_snapshot_h = 0.0

        if resume_from is not None:
            saved = resume_from.state
            productive_h = saved["productive_h"]
            blocked_h = saved["blocked_h"]
            switching_h = saved["switching_h"]
            mode_switching_h = saved["mode_switching_h"]
            planned_stop_h = saved["planned_stop_h"]
            dryer_operating_during_stop_h = saved[
                "dryer_operating_during_stop_h"]
            maximum_dryer_idle_episode_during_stop_h = saved[
                "maximum_dryer_idle_episode_during_stop_h"]
            harvested_ac = saved["harvested_ac"]
            generated_bu = saved["generated_bu"]
            field_switches = saved["field_switches"]
            interior_mode_switches = saved["interior_mode_switches"]
            intentional_slowdown_h = saved["intentional_slowdown_h"]
            intentional_rate_reduction_bu = saved[
                "intentional_rate_reduction_bu"]
            productive_h_by_mode = deepcopy(saved["productive_h_by_mode"])
            opening_productive_h_by_mode = deepcopy(
                saved["opening_productive_h_by_mode"])
            current = saved["current"]
            combine_field = saved["combine_field"]
            field_order = deepcopy(saved["field_order"])
            pending_field = saved["pending_field"]
            field_switch_remaining_h = saved["field_switch_remaining_h"]
            current_opening_mode = saved["current_opening_mode"]
            current_interior_mode = saved["current_interior_mode"]
            pending_interior_mode = saved["pending_interior_mode"]
            interior_mode_dwell_h = saved["interior_mode_dwell_h"]
            interior_mode_switch_remaining_h = saved[
                "interior_mode_switch_remaining_h"]
            field_interior_mode_switches = saved[
                "field_interior_mode_switches"]
            opening_modes = deepcopy(saved["opening_modes"])
            interior_mode_sequences = deepcopy(saved["interior_mode_sequences"])
            in_stop = saved["in_stop"]
            stop_elapsed_h = saved["stop_elapsed_h"]
            productive_since_stop_h = saved["productive_since_stop_h"]
            stop_count = saved["stop_count"]
            planned_stop_used_current_field = saved.get(
                "planned_stop_used_current_field", False)
            stop_request_started_h = saved["stop_request_started_h"]
            stop_durations = deepcopy(saved["stop_durations"])
            current_stop_duration_h = saved["current_stop_duration_h"]
            stop_start_inventories = deepcopy(saved["stop_start_inventories"])
            stop_end_inventories = deepcopy(saved["stop_end_inventories"])
            next_control_decision_h = saved["next_control_decision_h"]
            cached_schedule_action = saved["cached_schedule_action"]
            harvest_completion_h = saved["harvest_completion_h"]
            harvest_end_inventory = deepcopy(saved["harvest_end_inventory"])
            series = deepcopy(saved["series"])
            snapshots = deepcopy(saved["snapshots"])
            next_snapshot_h = saved["next_snapshot_h"]
            if not record_trace:
                series = []
                snapshots = []
                next_snapshot_h = d.time_h
            if (resume_controller is not None
                    and resume_controller is not resume_from.controller):
                if stop_request_started_h is not None:
                    raise ValueError(
                        "Cannot change plan during STOP confirmation")
                next_control_decision_h = d.time_h
                cached_schedule_action = HARVEST

        checkpoint_initial_field = combine_field

        def begin_field(field_index: int) -> None:
            nonlocal current_opening_mode, current_interior_mode
            nonlocal pending_interior_mode, interior_mode_dwell_h
            nonlocal interior_mode_switch_remaining_h
            nonlocal field_interior_mode_switches
            nonlocal planned_stop_used_current_field
            field = self.fields[field_index]
            d.set_field_context(field.field_id, field.one_way_travel_min)
            field_order.append(field.field_id)
            current_opening_mode = None
            current_interior_mode = None
            pending_interior_mode = None
            interior_mode_dwell_h = 0.0
            interior_mode_switch_remaining_h = 0.0
            field_interior_mode_switches = 0
            planned_stop_used_current_field = False
            interior_mode_sequences[field.field_id] = []

        def active_mode_for(task: str | None) -> str:
            if pending_field is not None or task is None:
                if current_interior_mode is not None:
                    return current_interior_mode
                if current_opening_mode is not None:
                    return current_opening_mode
            if task == OPENING:
                if current_opening_mode is not None:
                    return current_opening_mode
                modes = c.feasible_modes(OPENING_MODES)
            else:
                if current_interior_mode is not None:
                    return current_interior_mode
                modes = c.feasible_modes(INTERIOR_MODES)
            return max(modes, key=c.interior_rate_buph)

        def active_storage_context(task: str | None) -> tuple[int, int]:
            mode = active_mode_for(task)
            return (
                c.combine_count_for_mode(mode),
                c.field_buffer_count_for_mode(mode),
            )

        def effective_rate_buph(field: Field, task: str, mode: str) -> float:
            """Current team rate after any individual combine blockage."""
            requested_combines = c.combine_count_for_mode(mode)
            working_combines = min(
                requested_combines, d.working_combine_count)
            if working_combines <= 0:
                return 0.0
            rate = (
                working_combines * c.combine_harvest_capacity_buph
                + c.grain_cart_count_for_mode(mode)
                * c.grain_cart_harvest_capacity_buph
            )
            if task == OPENING:
                rate *= field.opening_capacity_factor
            return rate

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
                        field.opening_remaining_ac * field.yield_bu_per_ac,
                        field.moisture_pct, rate, field.one_way_travel_min,
                        field.opening_capacity_factor,
                    ))
                if field.interior_remaining_ac > c.numerical_tolerance:
                    segments.append(ForecastSegment(
                        field.field_id, INTERIOR, field.geometry,
                        field.interior_remaining_ac * field.yield_bu_per_ac,
                        field.moisture_pct, rate, field.one_way_travel_min,
                    ))
            return tuple(segments)

        def is_field_boundary(field: Field, task: str) -> bool:
            """Whether harvest has not yet begun on the active field."""
            return (
                task == OPENING
                and abs(field.remaining_area_ac - field.total_area_ac)
                <= c.numerical_tolerance
            )

        def control_state(field: Field, task: str) -> dict[str, Any]:
            if task == OPENING:
                available_modes = (
                    (current_opening_mode,) if current_opening_mode
                    else c.feasible_modes(OPENING_MODES))
            elif (current_interior_mode is not None
                  and (field_interior_mode_switches >= c.max_interior_mode_switches
                       or interior_mode_dwell_h + c.numerical_tolerance
                       < c.minimum_interior_mode_dwell_h)):
                available_modes = (current_interior_mode,)
            else:
                available_modes = c.feasible_modes(INTERIOR_MODES)
            active_combines, active_carts = active_storage_context(task)
            system_free_bu = max(0.0, c.total_system_storage_bu - d.unprocessed_grain_bu)
            response_h = max(
                c.dt_h,
                field.one_way_travel_min / 60.0
                + c.semi_unload_time_h
                + c.interior_mode_switch_time_h,
            )
            stop_work_eligible = (
                productive_since_stop_h + c.numerical_tolerance
                >= c.stop_work_requirement_h)
            at_field_start = is_field_boundary(field, task)
            boundary_stop_available = (
                at_field_start and not planned_stop_used_current_field)
            stop_eligible = stop_work_eligible and boundary_stop_available
            hours_until_stop = max(
                0.0, c.stop_work_requirement_h - productive_since_stop_h)
            protection_h = c.reactive_mode_horizon_h
            state = {
                "phase": task,
                "field_id": field.field_id,
                "geometry": field.geometry,
                "opening_capacity_factor": field.opening_capacity_factor,
                "remaining_grain_bu": field.remaining_grain(task),
                "at_field_start": at_field_start,
                "boundary_stop_available": boundary_stop_available,
                "receiving_pressure": d.receiving_pressure,
                "field_mobile_free_bu": d.field_mobile_free_bu(
                    active_combines, active_carts),
                "field_mobile_free_by_mode": {
                    mode: d.field_mobile_free_bu(
                        c.combine_count_for_mode(mode),
                        c.field_buffer_count_for_mode(mode))
                    for mode in available_modes
                },
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
                "stop_work_eligible": stop_work_eligible,
                "planned_stop_used_current_field": (
                    planned_stop_used_current_field),
                "hours_until_stop_eligible_h": hours_until_stop,
                "in_stop": in_stop,
                "stop_elapsed_h": stop_elapsed_h,
                "stop_request_duration_h": (
                    0.0 if stop_request_started_h is None
                    else d.time_h - stop_request_started_h),
                "productive_since_stop_h": productive_since_stop_h,
                "elapsed_h": d.time_h,
                "field_engaged_elapsed_h": d.time_h - planned_stop_h,
                "available_modes": available_modes,
                "mode_switches_used": field_interior_mode_switches,
                "current_mode": active_mode_for(task),
                "mode_initialized": (
                    current_opening_mode is not None
                    if task == OPENING else current_interior_mode is not None),
                "interior_mode_dwell_h": interior_mode_dwell_h,
                "transport_forecast": d,
            }
            if self.controller.name == "predictive":
                state["forecast_segments"] = forecast_segments()
            return state

        def minimum_stop_preserves_dryer(task: str) -> bool:
            """Check the shared six-hour STOP against the dryer-idle limit."""
            forecast = d.fork_for_forecast()
            active_combines, active_carts = active_storage_context(task)
            elapsed_h = 0.0
            while (elapsed_h + c.numerical_tolerance
                   < c.minimum_stop_h):
                forecast.handle_events()
                forecast.unload_field_storage(
                    active_combines, active_carts, force=True)
                forecast.dispatch_partial_loads(
                    active_combines, active_carts)
                if (forecast.current_dryer_idle_episode_h
                        > (c.maximum_dryer_idle_episode_h
                           + c.numerical_tolerance)):
                    return False
                step_h = min(
                    c.forecast_step_h,
                    c.minimum_stop_h - elapsed_h,
                )
                event_gap_h = forecast.next_event_h - forecast.time_h
                if event_gap_h < step_h:
                    step_h = event_gap_h
                if step_h <= c.numerical_tolerance:
                    forecast.handle_events()
                    continue
                forecast.advance_time(step_h)
                elapsed_h += step_h
            return (
                forecast.current_dryer_idle_episode_h
                <= (c.maximum_dryer_idle_episode_h
                    + c.numerical_tolerance)
            )

        def record(
                state: str, active: TaskRef | None,
                duration_h: float = 0.0, event: str = "",
                productive_combine_count: int = 0,
                productive_grain_cart_count: int = 0) -> None:
            nonlocal next_snapshot_h
            mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
            if abs(mass_error) > 1e-6:
                raise AssertionError(f"Mass balance error: {mass_error}")
            for vehicle in d.vehicles:
                if not (-c.numerical_tolerance <= vehicle.load_bu
                        <= vehicle.storage_capacity_bu
                        + c.numerical_tolerance):
                    raise AssertionError(
                        f"{vehicle.vehicle_id} load outside physical bounds")
            if not -c.numerical_tolerance <= d.wet_bin_inventory_bu <= c.wet_bin_capacity_bu + c.numerical_tolerance:
                raise AssertionError("Wet bin outside physical bounds")
            if not record_trace:
                return
            task = active[1] if active else None
            active_field = self.fields[active[0]] if active else None
            at_field_start = bool(
                active_field is not None
                and task is not None
                and is_field_boundary(active_field, task))
            stop_work_eligible = (
                productive_since_stop_h + c.numerical_tolerance
                >= c.stop_work_requirement_h)
            boundary_stop_available = (
                at_field_start and not planned_stop_used_current_field)
            active_mode = active_mode_for(task) if task else ""
            active_rate_buph = 0.0
            if task:
                field = self.fields[active[0]]
                active_rate_buph = effective_rate_buph(
                    field, task, active_mode)
            row = {
                "time_h": d.time_h,
                "duration_h": duration_h,
                "current_field": active_field.field_id if active_field else "",
                "current_task": task or "",
                "operating_mode": active_mode,
                "effective_harvest_rate_buph": active_rate_buph,
                "productive_combine_count": productive_combine_count,
                "productive_grain_cart_count": productive_grain_cart_count,
                "interior_mode_dwell_h": (interior_mode_dwell_h
                                           if task == INTERIOR else 0.0),
                "interior_mode_switches_current_field": field_interior_mode_switches,
                "current_moisture_pct": (
                    active_field.moisture_pct if active_field else ""),
                "combine_state": state,
                "event": event,
                "controller_macro_action": getattr(
                    self.controller, "active_macro_label", ""),
                "productive_since_stop_h": productive_since_stop_h,
                "stop_eligible": int(
                    stop_work_eligible and boundary_stop_available),
                "stop_work_eligible": int(stop_work_eligible),
                "at_field_start": int(at_field_start),
                "boundary_stop_available": int(boundary_stop_available),
                "planned_stop_used_current_field": int(
                    planned_stop_used_current_field),
                "current_stop_elapsed_h": stop_elapsed_h if in_stop else 0.0,
                "stop_request_duration_h": (
                    0.0 if stop_request_started_h is None
                    else d.time_h - stop_request_started_h),
                "cumulative_planned_stop_h": planned_stop_h,
                "cumulative_harvested_ac": harvested_ac,
                "cumulative_grain_generated_bu": generated_bu,
                **d.snapshot(),
            }
            series.append(row)
            if (not snapshots
                    or event
                    or d.time_h + c.numerical_tolerance >= next_snapshot_h
                    or snapshots[-1]["combine_state"] != state):
                snapshots.append(dict(row))
                while next_snapshot_h <= d.time_h + c.numerical_tolerance:
                    next_snapshot_h += c.snapshot_interval_h

        def make_checkpoint() -> SimulationCheckpoint:
            state = {
                "productive_h": productive_h,
                "blocked_h": blocked_h,
                "switching_h": switching_h,
                "mode_switching_h": mode_switching_h,
                "planned_stop_h": planned_stop_h,
                "dryer_operating_during_stop_h": (
                    dryer_operating_during_stop_h),
                "maximum_dryer_idle_episode_during_stop_h": (
                    maximum_dryer_idle_episode_during_stop_h),
                "harvested_ac": harvested_ac,
                "generated_bu": generated_bu,
                "field_switches": field_switches,
                "interior_mode_switches": interior_mode_switches,
                "intentional_slowdown_h": intentional_slowdown_h,
                "intentional_rate_reduction_bu": (
                    intentional_rate_reduction_bu),
                "productive_h_by_mode": productive_h_by_mode,
                "opening_productive_h_by_mode": (
                    opening_productive_h_by_mode),
                "current": current,
                "combine_field": combine_field,
                "field_order": field_order,
                "pending_field": pending_field,
                "field_switch_remaining_h": field_switch_remaining_h,
                "current_opening_mode": current_opening_mode,
                "current_interior_mode": current_interior_mode,
                "pending_interior_mode": pending_interior_mode,
                "interior_mode_dwell_h": interior_mode_dwell_h,
                "interior_mode_switch_remaining_h": (
                    interior_mode_switch_remaining_h),
                "field_interior_mode_switches": (
                    field_interior_mode_switches),
                "opening_modes": opening_modes,
                "interior_mode_sequences": interior_mode_sequences,
                "in_stop": in_stop,
                "stop_elapsed_h": stop_elapsed_h,
                "productive_since_stop_h": productive_since_stop_h,
                "stop_count": stop_count,
                "planned_stop_used_current_field": (
                    planned_stop_used_current_field),
                "stop_request_started_h": stop_request_started_h,
                "stop_durations": stop_durations,
                "current_stop_duration_h": current_stop_duration_h,
                "stop_start_inventories": stop_start_inventories,
                "stop_end_inventories": stop_end_inventories,
                "next_control_decision_h": next_control_decision_h,
                "cached_schedule_action": cached_schedule_action,
                "harvest_completion_h": harvest_completion_h,
                "harvest_end_inventory": harvest_end_inventory,
                "series": series,
                "snapshots": snapshots,
                "next_snapshot_h": next_snapshot_h,
            }
            return SimulationCheckpoint(
                c, deepcopy(self.fields), deepcopy(d),
                deepcopy(self.controller), deepcopy(self.field_selector),
                self.field_moistures_pct, deepcopy(state))

        if resume_from is None:
            record("IDLE", None)
        while True:
            completed_fields = sum(
                field.field_complete for field in self.fields)
            time_checkpoint = (
                checkpoint_at_h is not None
                and d.time_h + c.numerical_tolerance >= checkpoint_at_h)
            completion_checkpoint = (
                checkpoint_after_completed_fields is not None
                and completed_fields >= checkpoint_after_completed_fields)
            field_checkpoint = (
                checkpoint_on_field_change
                and combine_field is not None
                and combine_field != checkpoint_initial_field)
            if time_checkpoint or completion_checkpoint or field_checkpoint:
                return make_checkpoint()
            harvest_finished = all(field.field_complete for field in self.fields)
            if harvest_finished and harvest_completion_h is None:
                harvest_completion_h = d.time_h
                harvest_end_inventory = d.snapshot()
                record("HARVEST_COMPLETE", None)

            event_start = len(d.events)
            d.handle_events()
            task = current[1] if current else None
            active_combines, active_carts = active_storage_context(task)
            if harvest_finished:
                d.dispatch_partial_loads(active_combines, active_carts)
            else:
                d.unload_field_storage(active_combines, active_carts,
                                       force=in_stop)
                if in_stop:
                    d.dispatch_partial_loads(active_combines, active_carts)
            if len(d.events) > event_start:
                names = ";".join(f"{event['semi_id']}:{event['event']}"
                                 for event in d.events[event_start:])
                record("EVENT", current, event=names)
                if (self.controller.name == "predictive"
                        and (in_stop
                             or stop_request_started_h is not None)):
                    next_control_decision_h = d.time_h

            if harvest_finished and d.unprocessed_grain_bu <= c.numerical_tolerance:
                record("DONE", None)
                break
            if d.time_h >= c.max_simulation_h:
                raise RuntimeError("Simulation exceeded maximum duration")

            step = min(physical_step_h, d.next_event_h - d.time_h,
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

            selected_field = self.field_selector.choose_next(
                self.fields, combine_field)
            if selected_field is None:
                choice = None
            else:
                tasks = self.fields[selected_field].available_tasks()
                choice = (selected_field, tasks[0]) if tasks else None
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
                if (checkpoint_on_field_change
                        and combine_field != checkpoint_initial_field):
                    return make_checkpoint()

            field = self.fields[current[0]]
            task = current[1]
            state = control_state(field, task)
            control_due = (
                d.time_h + c.numerical_tolerance >= next_control_decision_h)
            if control_due:
                next_control_decision_h = (
                    d.time_h + controller_decision_interval_h)

            if in_stop:
                if stop_elapsed_h + c.numerical_tolerance < c.minimum_stop_h:
                    action = STOP
                elif (d.current_dryer_idle_episode_h
                      + c.numerical_tolerance
                      >= c.maximum_dryer_idle_episode_h):
                    # The common STOP supervisor overrides a strategy before
                    # a voluntary stop can starve the continuously operated
                    # dryer beyond the configured system-wide limit.
                    action = HARVEST
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
                    stop_elapsed_h = 0.0
                    current_stop_duration_h = 0.0
                    stop_end_inventories.append(d.wet_bin_inventory_bu)
                    productive_since_stop_h = 0.0
                    stop_request_started_h = None
                    cached_schedule_action = HARVEST
                    record("STOP_END", current, event="VOLUNTARY_STOP_END")
                    continue
                step = min(step, max(
                    physical_step_h, c.minimum_stop_h - stop_elapsed_h))
                # A coarse forecast step must not jump across the same dryer
                # continuity boundary that real one-minute execution obeys.
                # Once a starvation episode has started, stop exactly at the
                # remaining system allowance and let the supervisor resume on
                # the next iteration.
                if (d.dryer_has_started
                        and d.current_dryer_idle_episode_h
                        > c.numerical_tolerance):
                    remaining_idle_h = (
                        c.maximum_dryer_idle_episode_h
                        - d.current_dryer_idle_episode_h)
                    if remaining_idle_h > c.numerical_tolerance:
                        step = min(step, remaining_idle_h)
                dryer_before_h = d.total_dryer_operating_h
                d.advance_time(step)
                dryer_operating_during_stop_h += (
                    d.total_dryer_operating_h - dryer_before_h)
                maximum_dryer_idle_episode_during_stop_h = max(
                    maximum_dryer_idle_episode_during_stop_h,
                    d.current_dryer_idle_episode_h,
                )
                stop_elapsed_h += step
                current_stop_duration_h += step
                planned_stop_h += step
                record("PLANNED_STOP", current, step)
                continue

            if not state["stop_eligible"]:
                cached_schedule_action = HARVEST
                stop_request_started_h = None
            elif control_due:
                cached_schedule_action = (
                    self.controller.choose_schedule_action(**state))
            stop_action = cached_schedule_action
            if stop_action == STOP:
                if not (state["at_field_start"]
                        and state["boundary_stop_available"]):
                    raise AssertionError(
                        "Shared STOP supervisor allowed a non-boundary STOP")
                if not minimum_stop_preserves_dryer(task):
                    cached_schedule_action = HARVEST
                    stop_request_started_h = None
                    record(
                        "STOP_REJECTED", current,
                        event="DRYER_CONTINUITY_GUARD")
                    continue
                in_stop = True
                stop_elapsed_h = 0.0
                current_stop_duration_h = 0.0
                cached_schedule_action = STOP
                stop_request_started_h = None
                stop_count += 1
                planned_stop_used_current_field = True
                stop_start_inventories.append(d.wet_bin_inventory_bu)
                d.dispatch_partial_loads(*active_storage_context(task))
                record(
                    "STOP_START", current,
                    event="VOLUNTARY_STOP_START")
                continue
            else:
                stop_request_started_h = None

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

            active_combines = c.combine_count_for_mode(mode)
            productive_carts = c.grain_cart_count_for_mode(mode)
            active_buffers = c.field_buffer_count_for_mode(mode)
            d.unload_field_storage(active_combines, active_buffers)
            free_tank_bu = d.active_combine_free_bu(active_combines)
            if free_tank_bu <= c.numerical_tolerance:
                d.advance_time(step)
                blocked_h += step
                if task == INTERIOR:
                    interior_mode_dwell_h += step
                record("BLOCKED", current, step)
                continue

            working_combine_count = min(
                active_combines, d.working_combine_count)
            active_rate_buph = effective_rate_buph(field, task, mode)
            if active_rate_buph <= c.numerical_tolerance:
                raise AssertionError("Positive combine storage has zero harvest rate")
            capacity_acph = active_rate_buph / field.yield_bu_per_ac
            step = min(step, field.remaining(task) / capacity_acph,
                       free_tank_bu / active_rate_buph)
            acres = capacity_acph * step
            grain = acres * field.yield_bu_per_ac
            d.advance_time(step)
            d.add_harvested_grain(grain, field.moisture_pct, field.field_id)
            field.harvest(task, acres)
            harvested_ac += acres
            generated_bu += grain
            productive_h += step
            productive_since_stop_h += step
            if task == OPENING:
                opening_productive_h_by_mode[mode] += step
                fastest_opening_mode = max(
                    c.feasible_modes(OPENING_MODES),
                    key=c.interior_rate_buph)
                maximum_rate = c.opening_rate_buph(
                    field.geometry,
                    fastest_opening_mode,
                    field.opening_capacity_factor)
                if mode != fastest_opening_mode:
                    intentional_slowdown_h += step
                    intentional_rate_reduction_bu += (
                        maximum_rate
                        - c.opening_rate_buph(
                            field.geometry, mode,
                            field.opening_capacity_factor)) * step
            else:
                interior_mode_dwell_h += step
                productive_h_by_mode[mode] += step
                fastest_interior_mode = max(
                    c.feasible_modes(INTERIOR_MODES),
                    key=c.interior_rate_buph)
                if mode != fastest_interior_mode:
                    intentional_slowdown_h += step
                    intentional_rate_reduction_bu += (
                        c.interior_rate_buph(fastest_interior_mode)
                        - c.interior_rate_buph(mode)) * step
            record(
                "HARVESTING", current, step,
                productive_combine_count=working_combine_count,
                productive_grain_cart_count=productive_carts,
            )
            if task not in field.available_tasks():
                current = None

        if harvest_completion_h is None:
            raise AssertionError("Missing harvest completion time")
        if in_stop and current_stop_duration_h > 0:
            stop_durations.append(current_stop_duration_h)
        mass_error = generated_bu - d.total_dried_bu - d.unprocessed_grain_bu
        if abs(mass_error) > 1e-6:
            raise AssertionError(f"Final mass balance error: {mass_error}")
        if any(not field.field_complete for field in self.fields):
            raise AssertionError("Simulation ended before every field completed")
        if d.unprocessed_grain_bu > c.numerical_tolerance:
            raise AssertionError("Simulation ended with unprocessed grain")
        harvest_time_identity_h = (
            productive_h + blocked_h + mode_switching_h
            + switching_h + planned_stop_h)
        if abs(harvest_completion_h - harvest_time_identity_h) > 1e-6:
            raise AssertionError(
                "Harvest time identity failed: "
                f"{harvest_completion_h} != {harvest_time_identity_h}")
        field_by_id = {field.field_id: field for field in self.fields}
        grain_weighted_moisture = sum(
            field.total_area_ac * field.yield_bu_per_ac * field.moisture_pct
            for field in self.fields) / c.total_grain_bu
        stop_hours = sum(stop_durations)
        mean_stop_h = stop_hours / len(stop_durations) if stop_durations else 0.0
        mean_stop_start_inventory = (
            sum(stop_start_inventories) / len(stop_start_inventories)
            if stop_start_inventories else 0.0)
        mean_stop_end_inventory = (
            sum(stop_end_inventories) / len(stop_end_inventories)
            if stop_end_inventories else 0.0)

        forced_episodes_h: list[float] = []
        current_forced_episode_h = 0.0
        for row in series:
            duration_h = float(row["duration_h"])
            if duration_h <= c.numerical_tolerance:
                continue
            if row["combine_state"] == "BLOCKED":
                current_forced_episode_h += duration_h
            elif current_forced_episode_h > c.numerical_tolerance:
                forced_episodes_h.append(current_forced_episode_h)
                current_forced_episode_h = 0.0
        if current_forced_episode_h > c.numerical_tolerance:
            forced_episodes_h.append(current_forced_episode_h)
        meaningful_forced_episodes_h = [
            duration_h for duration_h in forced_episodes_h
            if duration_h > c.forced_block_ignore_h + c.numerical_tolerance]
        meaningful_forced_h = sum(meaningful_forced_episodes_h)

        controller_implementation = {
            "fixed": "fixed_fastest_feasible",
            "threshold": "static_pressure_threshold",
            "reactive": "eta_aware_current_state",
            "predictive": "legacy_one_step",
        }.get(self.controller.name, self.controller.name)

        metrics: dict[str, float | int | str] = {
            "policy": self.controller.name,
            "controller_implementation": controller_implementation,
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
            "wet_bin_control_limit_fraction": (
                c.wet_bin_control_limit_fraction),
            "wet_bin_control_reserve_bu": c.wet_bin_control_reserve_bu,
            "dryer_intercept_buph": c.dryer_intercept_buph,
            "dryer_moisture_slope_buph_per_pct": c.dryer_moisture_slope_buph_per_pct,
            "interior_1c_rate_buph": c.interior_1c_rate_buph,
            "interior_2c_rate_buph": c.interior_2c_rate_buph,
            "interior_2c_1gc_rate_buph": c.interior_2c_1gc_rate_buph,
            "minimum_interior_mode_dwell_min": c.minimum_interior_mode_dwell_h * 60.0,
            "maximum_interior_mode_switches_per_field": c.max_interior_mode_switches,
            "interior_mode_switch_time_min": c.interior_mode_switch_time_h * 60.0,
            "prediction_horizon_h": c.prediction_horizon_h,
            "predictive_beam_width": c.predictive_beam_width,
            "predictive_search_implementation": (
                "one_step_rollout" if self.controller.name == "predictive"
                else ""),
            "predictive_active_prefix_h": (
                c.predictive_decision_interval_h
                if self.controller.name == "predictive" else 0.0),
            "predictive_tail_policy": (
                "threshold_to_completion"
                if self.controller.name == "predictive" else ""),
            "control_decision_interval_min": controller_decision_interval_h * 60.0,
            "stop_work_requirement_h": c.stop_work_requirement_h,
            "minimum_stop_h": c.minimum_stop_h,
            "stop_signal_confirmation_h": c.stop_signal_confirmation_h,
            "planned_stop_location_rule": "field_boundary_only",
            "maximum_planned_stops_per_field": 1,
            "maximum_dryer_idle_episode_limit_h": (
                c.maximum_dryer_idle_episode_h),
            "forced_block_ignore_h": c.forced_block_ignore_h,
            "harvest_completion_tolerance_h": c.harvest_completion_tolerance_h,
            "minimum_field_engaged_reduction_h": (
                c.minimum_field_engaged_reduction_h),
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
            "maximum_dryer_idle_episode_during_planned_stops_h": (
                maximum_dryer_idle_episode_during_stop_h),
            "semi_unload_time_min": c.semi_unload_time_h * 60.0,
            "combine_tank_capacity_per_combine_bu": c.combine_tank_capacity_per_combine_bu,
            "grain_cart_capacity_bu": c.grain_cart_capacity_bu,
            "n_combines": c.n_combines,
            "n_grain_carts": c.n_grain_carts,
            "use_field_buffer": c.use_field_buffer,
            "n_semis": c.n_semis,
            "semi_count": c.n_semis,
            "semi_capacity_bu": c.semi_capacity_bu,
            "receiving_pit_capacity_bu": c.receiving_pit_capacity_bu,
            "total_completion_time_h": d.time_h,
            "harvest_completion_time_h": harvest_completion_h,
            "system_completion_time_h": d.time_h,
            "post_harvest_time_h": d.time_h - harvest_completion_h,
            "productive_harvest_time_h": productive_h,
            "blocked_time_h": 0.0 if blocked_h <= c.numerical_tolerance else blocked_h,
            "raw_forced_blocking_time_h": (
                0.0 if blocked_h <= c.numerical_tolerance else blocked_h),
            "forced_blocking_time_h": (
                0.0 if meaningful_forced_h <= c.numerical_tolerance
                else meaningful_forced_h),
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
        metrics["field_engaged_calendar_time_h"] = (
            harvest_completion_h - planned_stop_h)
        metrics["field_operation_time_h"] = (
            productive_h + blocked_h + mode_switching_h)
        metrics["average_wet_bin_inventory_bu"] = (
            sum(float(row["wet_bin_inventory_bu"])
                * float(row["duration_h"]) for row in series)
            / d.time_h if d.time_h > c.numerical_tolerance else 0.0)
        metrics["dryer_operating_time_h"] = d.total_dryer_operating_h
        metrics["dryer_idle_time_h"] = max(
            0.0, d.time_h - d.total_dryer_operating_h)
        metrics["dryer_starvation_time_after_start_h"] = (
            d.total_dryer_starvation_h)
        metrics["maximum_dryer_idle_episode_h"] = (
            d.maximum_dryer_idle_episode_h)
        metrics["semi_queue_waiting_time_h"] = sum(
            float(row["duration_h"])
            * sum(row[f"semi_{index}_state"] == "WAITING_UNLOAD"
                  for index in range(1, c.n_semis + 1))
            for row in series)
        metrics["raw_number_of_forced_blocking_events"] = len(forced_episodes_h)
        metrics["number_of_forced_blocking_events"] = len(
            meaningful_forced_episodes_h)
        daily_machine_hours = calculate_daily_machine_hours(series, c.semi_count)
        metrics.update(summarize_machine_hours(daily_machine_hours))
        return SimulationResult(
            self.controller.name,
            metrics,
            series,
            self.fields,
            d.events,
            list(getattr(self.controller, "decision_history", [])),
            snapshots,
        )
