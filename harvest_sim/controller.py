from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, inf
from typing import Iterable

from .config import (
    INTERIOR_MODES,
    MODE_1C,
    MODE_2C,
    MODE_2C_1GC,
    OPENING_MODES,
    SimulationConfig,
)
from .field import INTERIOR, OPENING, Field
from .transport import TransportForecast

TaskRef = tuple[int, str]
HARVEST = "HARVEST"
STOP = "STOP"


@dataclass(frozen=True)
class ForecastLot:
    amount_bu: float
    moisture_pct: float


@dataclass(frozen=True)
class ForecastSegment:
    field_id: str
    phase: str
    geometry: str
    remaining_grain_bu: float
    moisture_pct: float
    transport_rate_buph: float
    one_way_travel_min: float = 0.0
    opening_capacity_factor: float | None = None


@dataclass(frozen=True)
class ForecastOutcome:
    harvest_completion_h: float
    system_completion_h: float
    forced_block_h: float
    forced_block_starts: int
    planned_stop_h: float
    planned_stop_starts: int
    meaningful_forced_block_h: float | None = None
    meaningful_forced_block_starts: int | None = None
    dryer_idle_during_planned_stop_h: float = 0.0
    dryer_operating_during_planned_stop_h: float = 0.0
    maximum_dryer_idle_episode_h: float = 0.0
    first_stop_wet_bin_drawdown_bu: float = 0.0
    field_switching_h: float = 0.0

    @property
    def field_engaged_h(self) -> float:
        return self.harvest_completion_h - self.planned_stop_h

    @property
    def field_operation_h(self) -> float:
        return self.field_engaged_h - self.field_switching_h

    @property
    def benefit_forced_block_h(self) -> float:
        if self.meaningful_forced_block_h is None:
            return self.forced_block_h
        return self.meaningful_forced_block_h


@dataclass(frozen=True)
class MacroAction:
    """One committed wait-then-resume decision evaluated as a unit."""

    mode: str
    wait_h: float = 0.0

    @property
    def label(self) -> str:
        if self.wait_h <= 0.0:
            return f"RESUME_NOW->{self.mode}"
        return f"WAIT_{self.wait_h:g}H->{self.mode}"


def _fixed_choice(
        config: SimulationConfig,
        fields: list[Field],
        current: TaskRef | None) -> TaskRef | None:
    if current is not None:
        index, task = current
        if task in fields[index].available_tasks():
            return current
    for index in config.field_order_indices():
        tasks = fields[index].available_tasks()
        if tasks:
            return index, tasks[0]
    return None


def _pressure(
        config: SimulationConfig,
        receiving_pressure: float,
        wet_bin_inventory_bu: float) -> float:
    wet_bin_pressure = wet_bin_inventory_bu / config.wet_bin_capacity_bu
    return max(
        0.0,
        min(1.0, receiving_pressure),
        min(1.0, wet_bin_pressure),
    )


def _rate(
        config: SimulationConfig, phase: str, geometry: str, mode: str,
        opening_capacity_factor: float | None = None) -> float:
    if phase == OPENING:
        return config.opening_rate_buph(
            geometry, mode, opening_capacity_factor)
    return config.interior_rate_buph(mode)


def _available_modes(
        config: SimulationConfig, defaults: tuple[str, ...],
        state: dict[str, object]) -> tuple[str, ...]:
    supplied = tuple(state.get("available_modes", ()))
    return supplied or config.feasible_modes(defaults)


class FixedController:
    """Pre-specified reference: shared field order and no state adaptation."""

    name = "fixed"
    allows_interior_mode_switching = False

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config

    def choose(
            self, fields: list[Field], current: TaskRef | None,
            **_: float) -> TaskRef | None:
        return _fixed_choice(self.config, fields, current)

    def choose_opening_mode(self, **state: float) -> str:
        modes = _available_modes(self.config, OPENING_MODES, state)
        return max(modes, key=self.config.interior_rate_buph)

    def choose_interior_mode(self, **state: float) -> str:
        modes = _available_modes(self.config, INTERIOR_MODES, state)
        return max(modes, key=self.config.interior_rate_buph)

    def choose_schedule_action(self, **_: float) -> str:
        return HARVEST


@dataclass
class ThresholdController:
    """Reference using only field-storage and wet-bin occupancy pressure."""

    config: SimulationConfig
    name: str = "threshold"
    allows_interior_mode_switching: bool = True

    def choose(
            self, fields: list[Field], current: TaskRef | None,
            **_: float) -> TaskRef | None:
        return _fixed_choice(self.config, fields, current)

    def _current_pressure(self, **state: float) -> float:
        return _pressure(
            self.config,
            float(state["receiving_pressure"]),
            float(state["wet_bin_inventory_bu"]),
        )

    def choose_opening_mode(self, **state: float) -> str:
        modes = _available_modes(self.config, OPENING_MODES, state)
        chooser = min if (self._current_pressure(**state)
                          >= self.config.high_pressure_fraction) else max
        return chooser(modes, key=self.config.interior_rate_buph)

    def choose_interior_mode(self, **state: float) -> str:
        modes = _available_modes(self.config, INTERIOR_MODES, state)
        pressure = self._current_pressure(**state)
        if pressure >= self.config.high_pressure_fraction:
            target = MODE_1C
        elif pressure >= self.config.low_pressure_fraction:
            target = MODE_2C
        else:
            target = MODE_2C_1GC
        target_rate = self.config.interior_rate_buph(target)
        return min(
            modes,
            key=lambda mode: (
                abs(self.config.interior_rate_buph(mode) - target_rate),
                -self.config.interior_rate_buph(mode),
            ),
        )

    def choose_schedule_action(
            self, *, stop_eligible: bool, in_stop: bool,
            stop_elapsed_h: float, **state: float) -> str:
        pressure = self._current_pressure(**state)
        if in_stop:
            if stop_elapsed_h < self.config.minimum_stop_h:
                return STOP
            return (HARVEST if pressure < self.config.high_pressure_fraction
                    else STOP)
        if (stop_eligible
                and pressure >= self.config.stop_pressure_fraction):
            return STOP
        return HARVEST


@dataclass
class ReactiveController:
    """Current-state persistence controller with a physical safety margin."""

    config: SimulationConfig
    name: str = "reactive"
    allows_interior_mode_switching: bool = True

    def _with_known_semi_returns(
            self, state: dict[str, object]) -> dict[str, object]:
        if "known_semi_return_relief" in state:
            return state
        transport = state.get("transport_forecast")
        if transport is None:
            return state
        enriched = dict(state)
        enriched["known_semi_return_relief"] = (
            transport.known_semi_return_relief(
                self.config.reactive_mode_horizon_h))
        return enriched

    def choose(
            self, fields: list[Field], current: TaskRef | None,
            **_: float) -> TaskRef | None:
        return _fixed_choice(self.config, fields, current)

    def physical_margin_bu(
            self, mode: str, *, phase: str, geometry: str,
            wet_bin_free_bu: float, committed_grain_bu: float,
            field_mobile_free_bu: float, system_buffer_free_bu: float,
            field_mobile_free_by_mode: dict[str, float] | None = None,
            transport_rate_buph: float,
            active_dryer_rate_buph: float, protection_h: float,
            known_semi_return_relief: tuple[tuple[float, float], ...] = (),
            opening_capacity_factor: float | None = None,
            **_: float) -> float:
        """Conservative current-state balance over the next response interval.

        Existing grain is already counted as occupied capacity.  The margin
        therefore protects the complete finite buffer chain (combine, cart,
        semis, and wet bin) without requiring every committed bushel to fit in
        the wet bin immediately.
        """
        c = self.config
        harvest_rate = _rate(
            c, phase, geometry, mode, opening_capacity_factor)
        horizon = max(protection_h, c.dt_h)
        wet_bin_control_reserve_bu = c.wet_bin_control_reserve_bu
        inventory_based_free_bu = max(
            0.0,
            c.total_system_storage_bu
            - (c.wet_bin_capacity_bu - wet_bin_free_bu)
            - committed_grain_bu
            - wet_bin_control_reserve_bu,
        )
        effective_free_bu = min(
            max(0.0, system_buffer_free_bu - wet_bin_control_reserve_bu),
            inventory_based_free_bu,
        )
        total_storage_margin = (
            effective_free_bu
            - max(0.0, harvest_rate - active_dryer_rate_buph) * horizon
        )
        candidate_field_free_bu = (
            field_mobile_free_bu
            if not field_mobile_free_by_mode
            else field_mobile_free_by_mode.get(mode, field_mobile_free_bu)
        )
        del transport_rate_buph  # Reactive uses committed per-semi ETAs.
        field_storage_margin = candidate_field_free_bu
        returned_capacity_bu = 0.0
        for eta_h, capacity_bu in known_semi_return_relief:
            if eta_h > horizon + c.numerical_tolerance:
                break
            # Check the instant immediately before the known return creates
            # new field receiving space.
            field_storage_margin = min(
                field_storage_margin,
                candidate_field_free_bu + returned_capacity_bu
                - harvest_rate * eta_h,
            )
            returned_capacity_bu += capacity_bu
        field_storage_margin = min(
            field_storage_margin,
            candidate_field_free_bu + returned_capacity_bu
            - harvest_rate * horizon,
        )
        return min(total_storage_margin, field_storage_margin)

    def _choose_mode(
            self, modes: Iterable[str], **state: float) -> str:
        state = self._with_known_semi_returns(state)
        candidates = _available_modes(
            self.config, tuple(modes), state)
        safe = [mode for mode in candidates
                if self.physical_margin_bu(mode, **state)
                >= -self.config.numerical_tolerance]
        selected = safe or [min(
            candidates, key=lambda mode: _rate(
                self.config, str(state["phase"]),
                str(state["geometry"]), mode,
                state.get("opening_capacity_factor")))]
        return max(
            selected,
            key=lambda mode: _rate(
                self.config, str(state["phase"]), str(state["geometry"]), mode,
                state.get("opening_capacity_factor")),
        )

    def choose_opening_mode(self, **state: float) -> str:
        return self._choose_mode(
            _available_modes(self.config, OPENING_MODES, state), **state)

    def choose_interior_mode(self, **state: float) -> str:
        return self._choose_mode(
            _available_modes(self.config, INTERIOR_MODES, state), **state)

    def choose_schedule_action(
            self, *, stop_eligible: bool, in_stop: bool,
            stop_elapsed_h: float, available_modes: tuple[str, ...],
            **state: float) -> str:
        c = self.config
        state = self._with_known_semi_returns(state)
        if in_stop:
            if stop_elapsed_h < c.minimum_stop_h:
                return STOP
            # The work-clock rule governs when another voluntary STOP may begin;
            # it must not make restart impossible under a hard bottleneck.
            resume_state = dict(state)
            resume_state["protection_h"] = c.reactive_mode_horizon_h
            safe_to_resume = any(
                self.physical_margin_bu(mode, **resume_state)
                >= -c.numerical_tolerance
                for mode in available_modes
            )
            return HARVEST if safe_to_resume else STOP
        if not stop_eligible:
            return HARVEST
        unsafe = all(
            self.physical_margin_bu(mode, **state)
            < -c.numerical_tolerance
            for mode in available_modes
        )
        return STOP if unsafe else HARVEST


@dataclass
class PredictiveController(ReactiveController):
    """Pressure-guided rolling rollout over joint STOP/resume macro actions.

    Every retained candidate commits one immediate action, then uses a
    Threshold-controlled physical continuation to system completion.  The
    reusable multi-layer Beam state described in the README is a deliberate
    next refactor; this class currently searches a bounded one-step frontier.
    """

    name: str = "predictive"
    _committed_stop_until_h: float | None = field(
        default=None, init=False, repr=False)
    _committed_resume_mode: str | None = field(
        default=None, init=False, repr=False)
    _scheduled_mode: str | None = field(default=None, init=False, repr=False)
    active_macro_label: str = field(default="", init=False)
    decision_history: list[dict[str, float | int | str]] = field(
        default_factory=list, init=False, repr=False)
    _decision_counter: int = field(default=0, init=False, repr=False)

    def _rollout(
            self, first_action: MacroAction | str, *,
            forecast_segments: tuple[ForecastSegment, ...],
            productive_since_stop_h: float,
            stop_elapsed_h: float, in_stop: bool,
            mode_switches_used: int,
            response_time_h: float,
            current_mode: str,
            mode_initialized: bool = False,
            interior_mode_dwell_h: float = 0.0,
            transport_forecast: TransportForecast | None = None,
            tail_strategy: str = "coordinated",
            **_: float) -> ForecastOutcome:
        c = self.config
        step_h = c.forecast_step_h
        if transport_forecast is None:
            raise ValueError("Predictive rollout requires an exact transport state")
        transport = transport_forecast.fork_for_forecast()
        segments = [[segment, segment.remaining_grain_bu]
                    for segment in forecast_segments]
        if isinstance(first_action, str):
            macro = MacroAction(
                MODE_1C if first_action == STOP else first_action,
                c.minimum_stop_h if first_action == STOP else 0.0,
            )
        else:
            macro = first_action
        forced_block_h = 0.0
        forced_block_starts = 0
        forced_blocking = False
        forced_episode_h = 0.0
        meaningful_forced_block_h = 0.0
        meaningful_forced_block_starts = 0
        planned_stop_h = 0.0
        planned_stop_starts = int(macro.wait_h > 0.0 and not in_stop)
        dryer_idle_during_planned_stop_h = 0.0
        dryer_operating_during_planned_stop_h = 0.0
        maximum_dryer_idle_episode_h = 0.0
        dryer_has_started = (
            transport.total_dryer_operating_h > c.numerical_tolerance)
        current_dryer_idle_episode_h = (
            transport.current_dryer_idle_episode_h
            if dryer_has_started else 0.0)
        dryer_continuity_tracking = planned_stop_starts > 0
        first_stop_start_wet_bin_bu = transport.wet_bin_inventory_bu
        first_stop_wet_bin_drawdown_bu = 0.0
        field_switching_h = 0.0
        elapsed_h = 0.0
        initial_wait_remaining_h = macro.wait_h
        stopping = initial_wait_remaining_h > c.numerical_tolerance
        stop_clock = stop_elapsed_h if in_stop else 0.0
        tail_stop_request_h = 0.0
        work_clock = productive_since_stop_h
        active_segment = (
            (segments[0][0].field_id, segments[0][0].phase)
            if segments else ("", ""))
        switches_used = mode_switches_used
        selected_mode = macro.mode
        initial_stop_storage_mode = current_mode
        mode_dwell_h = interior_mode_dwell_h
        initial_mode_switch_pending = (
            mode_initialized
            and bool(segments)
            and segments[0][0].phase == INTERIOR
            and selected_mode != current_mode)

        def storage_context(mode: str) -> tuple[int, int]:
            return (
                c.combine_count_for_mode(mode),
                c.field_buffer_count_for_mode(mode),
            )

        def rollout_rate(segment: ForecastSegment, mode: str) -> float:
            requested = c.combine_count_for_mode(mode)
            working = min(requested, transport.working_combine_count)
            if working <= 0:
                return 0.0
            rate = (
                working * c.combine_harvest_capacity_buph
                + c.grain_cart_count_for_mode(mode)
                * c.grain_cart_harvest_capacity_buph
            )
            if segment.phase == OPENING:
                rate *= (
                    c.opening_capacity_factor(segment.geometry)
                    if segment.opening_capacity_factor is None
                    else segment.opening_capacity_factor)
            return rate

        def pressure_target_mode(
                segment: ForecastSegment,
                allowed_modes: tuple[str, ...]) -> str:
            """Threshold fallback action for one fully simulated state."""
            pressure = _pressure(
                c,
                transport.receiving_pressure,
                transport.wet_bin_inventory_bu,
            )
            if segment.phase == OPENING:
                chooser = min if pressure >= c.high_pressure_fraction else max
                return chooser(
                    allowed_modes,
                    key=lambda mode: _rate(
                        c, segment.phase, segment.geometry, mode,
                        segment.opening_capacity_factor),
                )
            if pressure >= c.high_pressure_fraction:
                target = MODE_1C
            elif pressure >= c.low_pressure_fraction:
                target = MODE_2C
            else:
                target = MODE_2C_1GC
            target_rate = c.interior_rate_buph(target)
            return min(
                allowed_modes,
                key=lambda mode: (
                    abs(c.interior_rate_buph(mode) - target_rate),
                    -c.interior_rate_buph(mode),
                ),
            )

        def bounded_duration(maximum_h: float) -> float:
            """Respect the next exact semi/unload event, as the simulator does."""
            transport.handle_events()
            event_gap_h = transport.next_event_h - transport.time_h
            if event_gap_h < -c.numerical_tolerance:
                raise RuntimeError("Forecast transport event is in the past")
            if event_gap_h <= c.numerical_tolerance:
                transport.handle_events()
                event_gap_h = transport.next_event_h - transport.time_h
                if event_gap_h <= c.numerical_tolerance:
                    raise RuntimeError("Forecast transport event did not advance")
            return min(maximum_h, event_gap_h) if event_gap_h < inf else maximum_h

        def advance_transport(duration_h: float) -> float:
            nonlocal maximum_dryer_idle_episode_h
            nonlocal current_dryer_idle_episode_h
            nonlocal dryer_has_started
            operating_before_h = transport.total_dryer_operating_h
            transport.advance_time(duration_h)
            operating_h = transport.total_dryer_operating_h - operating_before_h
            if dryer_continuity_tracking:
                idle_h = max(0.0, duration_h - operating_h)
                if operating_h > c.numerical_tolerance:
                    dryer_has_started = True
                    current_dryer_idle_episode_h = idle_h
                elif dryer_has_started:
                    current_dryer_idle_episode_h += idle_h
                maximum_dryer_idle_episode_h = max(
                    maximum_dryer_idle_episode_h,
                    current_dryer_idle_episode_h,
                )
            return operating_h

        def advance_nonharvest(
                duration_h: float, mode: str, *, dispatch: bool) -> float:
            """Advance an idle interval while retaining exact vehicle events."""
            advanced_h = 0.0
            active_combines, active_carts = storage_context(mode)
            while advanced_h + c.numerical_tolerance < duration_h:
                transport.handle_events()
                transport.unload_field_storage(
                    active_combines, active_carts, force=dispatch)
                if dispatch:
                    transport.dispatch_partial_loads(
                        active_combines, active_carts)
                part_h = bounded_duration(min(
                    step_h, duration_h - advanced_h))
                advance_transport(part_h)
                advanced_h += part_h
            return advanced_h

        def close_forced_episode() -> None:
            nonlocal forced_episode_h
            nonlocal meaningful_forced_block_h
            nonlocal meaningful_forced_block_starts
            if forced_episode_h > c.forced_block_ignore_h + c.numerical_tolerance:
                meaningful_forced_block_h += forced_episode_h
                meaningful_forced_block_starts += 1
            forced_episode_h = 0.0

        def minimum_stop_preserves_dryer(mode: str) -> bool:
            """Apply the same minimum-STOP dryer guard inside a forecast."""
            forecast = transport.fork_for_forecast()
            active_combines, active_carts = storage_context(mode)
            guarded_h = 0.0
            while (guarded_h + c.numerical_tolerance
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
                guard_step_h = min(
                    step_h, c.minimum_stop_h - guarded_h)
                event_gap_h = forecast.next_event_h - forecast.time_h
                if event_gap_h < guard_step_h:
                    guard_step_h = event_gap_h
                if guard_step_h <= c.numerical_tolerance:
                    forecast.handle_events()
                    continue
                forecast.advance_time(guard_step_h)
                guarded_h += guard_step_h
            return (
                forecast.current_dryer_idle_episode_h
                <= (c.maximum_dryer_idle_episode_h
                    + c.numerical_tolerance)
            )

        # Current implementation is a correct one-step rollout: commit the
        # candidate for one Predictive decision interval, then let the frozen
        # Threshold fallback control every later decision to completion.  A
        # future reusable Beam node can extend this actively planned prefix to
        # H_P without changing the physical continuation below.
        planned_prefix_h = min(
            c.predictive_decision_interval_h, c.max_simulation_h)
        while segments:
            if elapsed_h >= c.max_simulation_h:
                raise RuntimeError(
                    "Predictive completion rollout exceeded maximum duration")
            duration_h = min(
                step_h,
                c.max_simulation_h - elapsed_h,
            )
            segment = segments[0][0]
            remaining_bu = float(segments[0][1])
            in_threshold_tail = (
                elapsed_h + c.numerical_tolerance >= planned_prefix_h)
            segment_key = (segment.field_id, segment.phase)
            if segment_key != active_segment:
                if segment.field_id != active_segment[0]:
                    switches_used = 0
                elif segment.phase == INTERIOR:
                    switches_used = 0
                active_segment = segment_key
                mode_dwell_h = 0.0
                initial_mode_switch_pending = False
                segment_modes = c.feasible_modes(
                    OPENING_MODES if segment.phase == OPENING
                    else INTERIOR_MODES)
                selected_mode = (
                    pressure_target_mode(segment, segment_modes)
                    if in_threshold_tail
                    else max(
                        segment_modes,
                        key=lambda mode: _rate(
                            c, segment.phase, segment.geometry, mode,
                            segment.opening_capacity_factor),
                    )
                )
            transport.set_field_context(
                segment.field_id, segment.one_way_travel_min)

            if stopping:
                dryer_continuity_tracking = True
                close_forced_episode()
                if initial_wait_remaining_h > c.numerical_tolerance:
                    duration_h = min(duration_h, initial_wait_remaining_h)
                stop_storage_mode = (
                    initial_stop_storage_mode
                    if initial_wait_remaining_h > c.numerical_tolerance
                    else selected_mode)
                active_combines, active_carts = storage_context(
                    stop_storage_mode)
                transport.unload_field_storage(
                    active_combines, active_carts, force=True)
                transport.dispatch_partial_loads(
                    active_combines, active_carts)
                duration_h = bounded_duration(duration_h)
                operating = advance_transport(duration_h)
                dryer_operating_during_planned_stop_h += operating
                dryer_idle_during_planned_stop_h += duration_h - operating
                planned_stop_h += duration_h
                stop_clock += duration_h
                elapsed_h += duration_h
                forced_blocking = False
                if initial_wait_remaining_h > c.numerical_tolerance:
                    initial_wait_remaining_h -= duration_h
                    if initial_wait_remaining_h <= c.numerical_tolerance:
                        first_stop_wet_bin_drawdown_bu = (
                            first_stop_start_wet_bin_bu
                            - transport.wet_bin_inventory_bu)
                        stopping = False
                        stop_clock = 0.0
                        work_clock = 0.0
                    continue
                if stop_clock + c.numerical_tolerance >= c.minimum_stop_h:
                    pressure = _pressure(
                        c,
                        transport.receiving_pressure,
                        transport.wet_bin_inventory_bu,
                    )
                    dryer_must_resume = (
                        transport.current_dryer_idle_episode_h
                        + c.numerical_tolerance
                        >= c.maximum_dryer_idle_episode_h)
                    if in_threshold_tail:
                        safe_to_resume = (
                            pressure < c.high_pressure_fraction
                            or dryer_must_resume)
                    else:
                        inventory_bu = transport.unprocessed_grain_bu
                        free_bu = c.total_system_storage_bu - inventory_bu
                        lowest_rate = _rate(
                            c, segment.phase, segment.geometry, MODE_1C,
                            segment.opening_capacity_factor)
                        current_dryer = (
                            transport.current_dryer_rate_buph
                            or c.dryer_rate_for_moisture(
                                segment.moisture_pct))
                        required = max(
                            0.0,
                            (lowest_rate - current_dryer)
                            * max(c.dt_h, response_time_h),
                        )
                        safe_to_resume = (
                            free_bu + c.numerical_tolerance >= required)
                    if safe_to_resume:
                        stopping = False
                        stop_clock = 0.0
                        work_clock = 0.0
                        allowed_after_stop = c.feasible_modes(
                            OPENING_MODES if segment.phase == OPENING
                            else INTERIOR_MODES)
                        selected_mode = (
                            pressure_target_mode(
                                segment, allowed_after_stop)
                            if in_threshold_tail
                            else max(
                                allowed_after_stop,
                                key=lambda mode: _rate(
                                    c, segment.phase, segment.geometry, mode,
                                    segment.opening_capacity_factor),
                            )
                        )
                continue

            allowed_modes = c.feasible_modes(
                OPENING_MODES if segment.phase == OPENING
                else INTERIOR_MODES)
            if selected_mode not in allowed_modes:
                selected_mode = (
                    pressure_target_mode(segment, allowed_modes)
                    if in_threshold_tail
                    else max(
                        allowed_modes,
                        key=lambda mode: _rate(
                            c, segment.phase, segment.geometry, mode,
                            segment.opening_capacity_factor),
                    )
                )

            if initial_mode_switch_pending:
                close_forced_episode()
                switch_h = advance_nonharvest(
                    min(c.interior_mode_switch_time_h,
                        c.max_simulation_h - elapsed_h),
                    current_mode,
                    dispatch=False,
                )
                elapsed_h += switch_h
                work_clock += switch_h
                switches_used += 1
                mode_dwell_h = 0.0
                initial_mode_switch_pending = False
                continue

            active_combines, active_carts = storage_context(selected_mode)
            transport.unload_field_storage(active_combines, active_carts)

            if in_threshold_tail:
                threshold_mode = pressure_target_mode(segment, allowed_modes)
                may_switch = (
                    segment.phase == INTERIOR
                    and threshold_mode != selected_mode
                    and switches_used < c.max_interior_mode_switches
                    and mode_dwell_h + c.numerical_tolerance
                    >= c.minimum_interior_mode_dwell_h)
                if may_switch:
                    close_forced_episode()
                    switch_h = advance_nonharvest(
                        min(c.interior_mode_switch_time_h,
                            c.max_simulation_h - elapsed_h),
                        selected_mode,
                        dispatch=False,
                    )
                    elapsed_h += switch_h
                    work_clock += switch_h
                    selected_mode = threshold_mode
                    switches_used += 1
                    mode_dwell_h = 0.0
                    tail_stop_request_h = 0.0
                    continue

            inventory_bu = transport.unprocessed_grain_bu
            dryer_rate = (
                transport.current_dryer_rate_buph
                or c.dryer_rate_for_moisture(segment.moisture_pct))
            current_rate = rollout_rate(segment, selected_mode)
            projected_free = c.total_system_storage_bu - inventory_bu
            projected_net = max(0.0, current_rate - dryer_rate) * duration_h
            projected_field_net = max(
                0.0, current_rate - segment.transport_rate_buph) * duration_h
            field_free_bu = transport.field_mobile_free_bu(
                active_combines, active_carts)

            if ((projected_net > projected_free + c.numerical_tolerance
                 or projected_field_net
                 > field_free_bu + c.numerical_tolerance)
                    and segment.phase == INTERIOR
                    and switches_used < c.max_interior_mode_switches
                    and mode_dwell_h + c.numerical_tolerance
                    >= c.minimum_interior_mode_dwell_h
                    and tail_strategy != "fastest_unregulated"
                    and not in_threshold_tail):
                lower_modes = [mode for mode in c.feasible_modes(INTERIOR_MODES)
                               if c.interior_rate_buph(mode) < current_rate]
                if lower_modes:
                    next_mode = max(
                        lower_modes, key=c.interior_rate_buph)
                    close_forced_episode()
                    switch_h = advance_nonharvest(
                        min(c.interior_mode_switch_time_h,
                            c.max_simulation_h - elapsed_h),
                        selected_mode,
                        dispatch=False,
                    )
                    elapsed_h += switch_h
                    work_clock += switch_h
                    selected_mode = next_mode
                    switches_used += 1
                    mode_dwell_h = 0.0
                    continue

            if ((projected_net > projected_free + c.numerical_tolerance
                    or projected_field_net
                    > field_free_bu + c.numerical_tolerance)
                    and work_clock + c.numerical_tolerance
                    >= c.stop_work_requirement_h
                    and tail_strategy != "fastest_unregulated"
                    and not in_threshold_tail):
                stopping = True
                dryer_continuity_tracking = True
                stop_clock = 0.0
                planned_stop_starts += 1
                continue

            tail_stop_risk = False
            if in_threshold_tail:
                pressure = _pressure(
                    c,
                    transport.receiving_pressure,
                    transport.wet_bin_inventory_bu,
                )
                slowest_mode = min(
                    allowed_modes,
                    key=lambda mode: _rate(
                        c, segment.phase, segment.geometry, mode,
                        segment.opening_capacity_factor),
                )
                tail_stop_risk = (
                    work_clock + c.numerical_tolerance
                    >= c.stop_work_requirement_h
                    and selected_mode == slowest_mode
                    and pressure >= c.stop_pressure_fraction)
                if tail_stop_risk:
                    if (tail_stop_request_h + c.numerical_tolerance
                            >= c.stop_signal_confirmation_h):
                        if minimum_stop_preserves_dryer(selected_mode):
                            stopping = True
                            dryer_continuity_tracking = True
                            stop_clock = 0.0
                            tail_stop_request_h = 0.0
                            planned_stop_starts += 1
                            continue
                        tail_stop_request_h = 0.0
                    else:
                        duration_h = min(
                            duration_h,
                            c.stop_signal_confirmation_h
                            - tail_stop_request_h,
                        )
                else:
                    tail_stop_request_h = 0.0

            free_tank_bu = transport.active_combine_free_bu(active_combines)
            if free_tank_bu <= c.numerical_tolerance:
                duration_h = bounded_duration(duration_h)
                advance_transport(duration_h)
                forced_block_h += duration_h
                forced_episode_h += duration_h
                if not forced_blocking:
                    forced_block_starts += 1
                forced_blocking = True
                elapsed_h += duration_h
                work_clock += duration_h
                if segment.phase == INTERIOR:
                    mode_dwell_h += duration_h
                if tail_stop_risk:
                    tail_stop_request_h += duration_h
                continue

            close_forced_episode()
            forced_blocking = False
            duration_h = min(
                bounded_duration(duration_h),
                remaining_bu / current_rate,
                free_tank_bu / current_rate,
            )
            accepted_bu = current_rate * duration_h
            advance_transport(duration_h)
            transport.add_harvested_grain(
                accepted_bu, segment.moisture_pct, segment.field_id)
            work_clock += duration_h
            if segment.phase == INTERIOR:
                mode_dwell_h += duration_h
            if tail_stop_risk:
                tail_stop_request_h += duration_h
            segments[0][1] = remaining_bu - accepted_bu
            elapsed_h += duration_h
            if float(segments[0][1]) <= c.numerical_tolerance:
                finished = segments.pop(0)[0]
                if segments and segments[0][0].field_id != finished.field_id:
                    transition_h = min(
                        c.field_switch_time_h,
                        c.max_simulation_h - elapsed_h)
                    elapsed_h += advance_nonharvest(
                        transition_h, selected_mode, dispatch=False)
                    field_switching_h += transition_h

        close_forced_episode()
        harvest_completion_h = elapsed_h
        tail_mode = selected_mode
        while transport.unprocessed_grain_bu > c.numerical_tolerance:
            if elapsed_h >= c.max_simulation_h:
                raise RuntimeError(
                    "Predictive downstream tail exceeded maximum duration")
            active_combines, active_carts = storage_context(tail_mode)
            transport.handle_events()
            transport.dispatch_partial_loads(
                active_combines, active_carts)
            duration_h = bounded_duration(min(
                step_h, c.max_simulation_h - elapsed_h))
            advance_transport(duration_h)
            elapsed_h += duration_h

        return ForecastOutcome(
            harvest_completion_h=harvest_completion_h,
            system_completion_h=elapsed_h,
            forced_block_h=forced_block_h,
            forced_block_starts=forced_block_starts,
            planned_stop_h=planned_stop_h,
            planned_stop_starts=planned_stop_starts,
            meaningful_forced_block_h=meaningful_forced_block_h,
            meaningful_forced_block_starts=meaningful_forced_block_starts,
            dryer_idle_during_planned_stop_h=(
                dryer_idle_during_planned_stop_h),
            dryer_operating_during_planned_stop_h=(
                dryer_operating_during_planned_stop_h),
            maximum_dryer_idle_episode_h=maximum_dryer_idle_episode_h,
            first_stop_wet_bin_drawdown_bu=first_stop_wet_bin_drawdown_bu,
            field_switching_h=field_switching_h,
        )

    def _preferred_macro_action(
            self, actions: Iterable[MacroAction], **state: float) -> MacroAction:
        """Apply the documented constrained, hierarchical plan ranking."""
        candidates = tuple(actions)[:self.config.predictive_beam_width]
        outcomes = {action: self._rollout(action, **state) for action in candidates}
        immediate = tuple(action for action in candidates
                          if action.wait_h <= self.config.numerical_tolerance)
        best_immediate_forced_h = min(
            (outcomes[action].benefit_forced_block_h for action in immediate),
            default=inf,
        )
        physically_eligible = tuple(
            action for action in candidates
            if (action.wait_h <= self.config.numerical_tolerance
                or (outcomes[action].benefit_forced_block_h
                    < best_immediate_forced_h - self.config.numerical_tolerance
                    and outcomes[action].maximum_dryer_idle_episode_h
                    <= (self.config.maximum_dryer_idle_episode_h
                        + self.config.numerical_tolerance)))
        )
        if not physically_eligible:
            physically_eligible = immediate
        best_completion_h = min(
            outcomes[action].harvest_completion_h
            for action in physically_eligible)
        eligible = tuple(
            action for action in physically_eligible
            if outcomes[action].harvest_completion_h
            <= (best_completion_h
                + self.config.harvest_completion_tolerance_h
                + self.config.numerical_tolerance)
        )

        def preference(action: MacroAction) -> tuple[float, ...]:
            outcome = outcomes[action]
            return (
                outcome.benefit_forced_block_h,
                outcome.harvest_completion_h,
                outcome.planned_stop_starts,
                outcome.system_completion_h,
                -_rate(self.config, str(state["phase"]),
                       str(state["geometry"]), action.mode,
                       state.get("opening_capacity_factor")),
                action.wait_h,
            )

        selected = min(eligible, key=preference)
        self._decision_counter += 1
        for action in candidates:
            outcome = outcomes[action]
            self.decision_history.append({
                "decision_id": self._decision_counter,
                "time_h": float(state.get("elapsed_h", 0.0)),
                "field_id": str(state.get("field_id", "")),
                "phase": str(state.get("phase", "")),
                "decision_context": (
                    "schedule" if "stop_eligible" in state else "mode"),
                "action": action.label,
                "eligible": int(action in eligible),
                "selected": int(action == selected),
                "selection_rule": (
                    "hard_dryer_limit_then_completion_band_then_"
                    "forced_then_stop_count"),
                "forecast_search_implementation": "one_step_rollout",
                "forecast_active_prefix_h": (
                    self.config.predictive_decision_interval_h),
                "forecast_tail_policy": "threshold_to_completion",
                "forecast_meaningful_forced_h": (
                    outcome.benefit_forced_block_h),
                "forecast_raw_forced_h": outcome.forced_block_h,
                "forecast_field_engaged_h": outcome.field_engaged_h,
                "forecast_field_operation_h": outcome.field_operation_h,
                "forecast_harvest_completion_h": (
                    outcome.harvest_completion_h),
                "forecast_system_completion_h": outcome.system_completion_h,
                "forecast_planned_stop_h": outcome.planned_stop_h,
                "forecast_dryer_operating_during_stop_h": (
                    outcome.dryer_operating_during_planned_stop_h),
                "forecast_dryer_idle_during_stop_h": (
                    outcome.dryer_idle_during_planned_stop_h),
                "forecast_max_dryer_idle_episode_h": (
                    outcome.maximum_dryer_idle_episode_h),
                "forecast_first_stop_wet_bin_drawdown_bu": (
                    outcome.first_stop_wet_bin_drawdown_bu),
            })
        return selected

    def _relief_wait_h(self, mode: str, **state: float) -> float | None:
        """Earliest crew-release duration that makes ``mode`` safe to resume.

        No new grain is produced while the copied transport system advances.
        Existing field grain is dispatched, semis retain their exact event
        clocks, and the dryer drains the wet bin.  The returned time is aligned
        to a control decision boundary so the simulator can honor it exactly.
        """
        c = self.config
        transport_source = state.get("transport_forecast")
        segments = tuple(state.get("forecast_segments", ()))
        if transport_source is None or not segments:
            return None
        transport = transport_source.fork_for_forecast()
        segment = segments[0]
        transport.set_field_context(
            segment.field_id, segment.one_way_travel_min)
        stop_mode = str(state.get("current_mode", mode))
        stop_combine_count = c.combine_count_for_mode(stop_mode)
        stop_cart_count = c.field_buffer_count_for_mode(stop_mode)
        resume_combine_count = c.combine_count_for_mode(mode)
        resume_cart_count = c.field_buffer_count_for_mode(mode)
        minimum_h = c.minimum_stop_h
        maximum_h = min(c.prediction_horizon_h, c.max_simulation_h)
        elapsed_h = 0.0

        while elapsed_h <= maximum_h + c.numerical_tolerance:
            transport.handle_events()
            transport.unload_field_storage(
                stop_combine_count, stop_cart_count, force=True)
            transport.dispatch_partial_loads(
                stop_combine_count, stop_cart_count)

            if elapsed_h + c.numerical_tolerance >= minimum_h:
                margin = self.physical_margin_bu(
                    mode,
                    phase=str(state["phase"]),
                    geometry=str(state["geometry"]),
                    wet_bin_free_bu=(
                        c.wet_bin_capacity_bu
                        - transport.wet_bin_inventory_bu),
                    committed_grain_bu=transport.committed_grain_bu,
                    field_mobile_free_bu=transport.field_mobile_free_bu(
                        resume_combine_count, resume_cart_count),
                    system_buffer_free_bu=max(
                        0.0,
                        c.total_system_storage_bu
                        - transport.unprocessed_grain_bu),
                    transport_rate_buph=float(state["transport_rate_buph"]),
                    active_dryer_rate_buph=(
                        transport.current_dryer_rate_buph),
                    protection_h=max(
                        c.dt_h, float(state["response_time_h"])),
                )
                if (margin >= -c.numerical_tolerance
                        and transport.current_dryer_idle_episode_h
                        <= (c.maximum_dryer_idle_episode_h
                            + c.numerical_tolerance)):
                    interval_h = c.control_decision_interval_h
                    return max(
                        minimum_h,
                        ceil((elapsed_h - c.numerical_tolerance)
                             / interval_h) * interval_h,
                    )

            remaining_h = maximum_h - elapsed_h
            if remaining_h <= c.numerical_tolerance:
                break
            step_h = min(c.forecast_step_h, remaining_h)
            if elapsed_h < minimum_h:
                step_h = min(step_h, minimum_h - elapsed_h)
            event_gap_h = transport.next_event_h - transport.time_h
            if event_gap_h < inf:
                step_h = min(step_h, event_gap_h)
            if step_h <= c.numerical_tolerance:
                transport.handle_events()
                continue
            transport.advance_time(step_h)
            elapsed_h += step_h
            if (transport.current_dryer_idle_episode_h
                    > (c.maximum_dryer_idle_episode_h
                       + c.numerical_tolerance)):
                return None
        return None

    def _macro_candidates(
            self, modes: Iterable[str], *, in_stop: bool,
            stop_elapsed_h: float, stop_eligible: bool,
            **state: float) -> tuple[MacroAction, ...]:
        allowed = tuple(modes)
        if ("receiving_pressure" in state
                and "wet_bin_inventory_bu" in state
                and len(allowed) > 1):
            ordered = tuple(sorted(
                allowed, key=self.config.interior_rate_buph))
            pressure = _pressure(
                self.config,
                float(state["receiving_pressure"]),
                float(state["wet_bin_inventory_bu"]),
            )
            if pressure < self.config.low_pressure_fraction:
                # Low pressure: explore the fastest action and its neighbour.
                allowed = ordered[-2:]
            elif pressure >= self.config.high_pressure_fraction:
                # High pressure: explore the slowest action and one recovery
                # action so the search cannot prune dryer-protection capacity.
                allowed = ordered[:2]
            else:
                # In the transition region, retain the current mode and its
                # adjacent rates. With the three Version-1 modes this normally
                # retains all modes.
                current = str(state.get("current_mode", ""))
                if current in ordered:
                    index = ordered.index(current)
                    allowed = ordered[
                        max(0, index - 1):min(len(ordered), index + 2)]
        candidates = [MacroAction(mode) for mode in allowed]
        if stop_eligible and not in_stop:
            minimum_h = self.config.minimum_stop_h
            for mode in allowed:
                candidates.append(MacroAction(mode, minimum_h))
                relief_h = self._relief_wait_h(mode, **state)
                if (relief_h is not None
                        and relief_h > minimum_h + self.config.numerical_tolerance):
                    candidates.append(MacroAction(mode, relief_h))
        return tuple(candidates)

    def _predictive_mode(self, modes: Iterable[str], **state: float) -> str:
        candidates = tuple(modes)
        if self._scheduled_mode in candidates:
            selected = self._scheduled_mode
            self._scheduled_mode = None
            return selected
        action = self._preferred_macro_action(
            tuple(MacroAction(mode) for mode in candidates), **state)
        self.active_macro_label = action.label
        return action.mode

    def choose_opening_mode(self, **state: float) -> str:
        return self._predictive_mode(
            _available_modes(self.config, OPENING_MODES, state), **state)

    def choose_interior_mode(self, **state: float) -> str:
        return self._predictive_mode(
            _available_modes(self.config, INTERIOR_MODES, state), **state)

    def choose_schedule_action(
            self, *, stop_eligible: bool, in_stop: bool,
            stop_elapsed_h: float, available_modes: tuple[str, ...],
            **state: float) -> str:
        c = self.config
        if in_stop and stop_elapsed_h < c.minimum_stop_h:
            return STOP
        elapsed_h = float(state.get("elapsed_h", 0.0))
        if self._committed_stop_until_h is not None:
            if elapsed_h + c.numerical_tolerance < self._committed_stop_until_h:
                return STOP
            self._scheduled_mode = self._committed_resume_mode
            self._committed_stop_until_h = None
            self._committed_resume_mode = None
            return HARVEST

        candidates = self._macro_candidates(
            available_modes,
            in_stop=in_stop,
            stop_elapsed_h=stop_elapsed_h,
            stop_eligible=stop_eligible,
            **state,
        )
        best = self._preferred_macro_action(
            candidates,
            stop_eligible=stop_eligible,
            in_stop=in_stop,
            stop_elapsed_h=stop_elapsed_h,
            **state,
        )
        self.active_macro_label = best.label
        if best.wait_h > c.numerical_tolerance:
            self._committed_stop_until_h = elapsed_h + best.wait_h
            self._committed_resume_mode = best.mode
            return STOP
        self._scheduled_mode = best.mode
        return HARVEST


# Compatibility aliases for older imports and command lines.
SequentialController = FixedController
AdaptiveController = PredictiveController
