from __future__ import annotations

from dataclasses import dataclass, field
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

    @property
    def field_engaged_h(self) -> float:
        return self.harvest_completion_h - self.planned_stop_h

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
        system_buffer_free_bu: float,
        wet_bin_inventory_bu: float) -> float:
    system_pressure = 1.0 - system_buffer_free_bu / config.total_system_storage_bu
    wet_bin_pressure = wet_bin_inventory_bu / config.wet_bin_capacity_bu
    return max(
        0.0,
        min(1.0, receiving_pressure),
        min(1.0, system_pressure),
        min(1.0, wet_bin_pressure),
    )


def _rate(config: SimulationConfig, phase: str, geometry: str, mode: str) -> float:
    if phase == OPENING:
        return config.opening_rate_buph(geometry, mode)
    return config.interior_rate_buph(mode)


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

    def choose_opening_mode(self, **_: float) -> str:
        return MODE_2C

    def choose_interior_mode(self, **_: float) -> str:
        return MODE_2C_1GC

    def choose_schedule_action(self, **_: float) -> str:
        return HARVEST


@dataclass
class ThresholdController:
    """Conventional occupancy-threshold reference with the common action set."""

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
            float(state["system_buffer_free_bu"]),
            float(state["wet_bin_inventory_bu"]),
        )

    def choose_opening_mode(self, **state: float) -> str:
        return (MODE_1C if self._current_pressure(**state)
                >= self.config.high_pressure_fraction else MODE_2C)

    def choose_interior_mode(self, **state: float) -> str:
        pressure = self._current_pressure(**state)
        if pressure >= self.config.high_pressure_fraction:
            return MODE_1C
        if pressure >= self.config.low_pressure_fraction:
            return MODE_2C
        return MODE_2C_1GC

    def choose_schedule_action(
            self, *, stop_eligible: bool, in_stop: bool,
            stop_elapsed_h: float, **state: float) -> str:
        pressure = self._current_pressure(**state)
        if in_stop:
            if stop_elapsed_h < self.config.minimum_stop_h:
                return STOP
            return (HARVEST if pressure < self.config.high_pressure_fraction
                    else STOP)
        if stop_eligible and pressure >= self.config.stop_pressure_fraction:
            return STOP
        return HARVEST


@dataclass
class ReactiveController:
    """Current-state persistence controller with a physical safety margin."""

    config: SimulationConfig
    name: str = "reactive"
    allows_interior_mode_switching: bool = True

    def choose(
            self, fields: list[Field], current: TaskRef | None,
            **_: float) -> TaskRef | None:
        return _fixed_choice(self.config, fields, current)

    def physical_margin_bu(
            self, mode: str, *, phase: str, geometry: str,
            wet_bin_free_bu: float, committed_grain_bu: float,
            field_mobile_free_bu: float, system_buffer_free_bu: float,
            transport_rate_buph: float,
            active_dryer_rate_buph: float, protection_h: float,
            **_: float) -> float:
        """Conservative current-state balance over the next response interval.

        Existing grain is already counted as occupied capacity.  The margin
        therefore protects the complete finite buffer chain (combine, cart,
        semis, and wet bin) without requiring every committed bushel to fit in
        the wet bin immediately.
        """
        c = self.config
        harvest_rate = _rate(c, phase, geometry, mode)
        horizon = max(protection_h, c.dt_h)
        inventory_based_free_bu = max(
            0.0,
            c.total_system_storage_bu
            - (c.wet_bin_capacity_bu - wet_bin_free_bu)
            - committed_grain_bu,
        )
        effective_free_bu = min(system_buffer_free_bu, inventory_based_free_bu)
        total_storage_margin = (
            effective_free_bu
            - max(0.0, harvest_rate - active_dryer_rate_buph) * horizon
        )
        field_storage_margin = (
            field_mobile_free_bu
            - max(0.0, harvest_rate - transport_rate_buph) * horizon
        )
        return min(total_storage_margin, field_storage_margin)

    def _choose_mode(
            self, modes: Iterable[str], **state: float) -> str:
        candidates = tuple(modes)
        safe = [mode for mode in candidates
                if self.physical_margin_bu(mode, **state)
                >= -self.config.numerical_tolerance]
        selected = safe or [MODE_1C]
        return max(
            selected,
            key=lambda mode: _rate(
                self.config, str(state["phase"]), str(state["geometry"]), mode),
        )

    def choose_opening_mode(self, **state: float) -> str:
        return self._choose_mode(OPENING_MODES, **state)

    def choose_interior_mode(self, **state: float) -> str:
        return self._choose_mode(INTERIOR_MODES, **state)

    def choose_schedule_action(
            self, *, stop_eligible: bool, in_stop: bool,
            stop_elapsed_h: float, available_modes: tuple[str, ...],
            **state: float) -> str:
        c = self.config
        if in_stop:
            if stop_elapsed_h < c.minimum_stop_h:
                return STOP
            # The 12-hour rule governs when another voluntary STOP may begin;
            # it must not make restart impossible under a hard bottleneck.
            resume_state = dict(state)
            resume_state["protection_h"] = max(
                c.dt_h, float(state.get("response_time_h", c.dt_h)))
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
    """Completion-aware multiobjective macro-action controller.

    It shares field order, physical constraints, and switch limits with the
    references.  At each eligible decision it jointly evaluates how long to
    wait and which mode to use after the wait.  Candidate rollouts are compared
    on harvest completion, meaningful forced blocking, and field-engaged time.
    """

    name: str = "predictive"
    _committed_stop_until_h: float | None = field(
        default=None, init=False, repr=False)
    _committed_resume_mode: str | None = field(
        default=None, init=False, repr=False)
    _scheduled_mode: str | None = field(default=None, init=False, repr=False)
    active_macro_label: str = field(default="", init=False)

    def _dryer_step(
            self, queue: list[list[float]], duration_h: float) -> tuple[float, float]:
        remaining_h = duration_h
        dried_bu = 0.0
        operating_h = 0.0
        while queue and remaining_h > self.config.numerical_tolerance:
            amount_bu, moisture_pct = queue[0]
            rate = self.config.dryer_rate_for_moisture(moisture_pct)
            step_h = min(remaining_h, amount_bu / rate)
            amount = rate * step_h
            queue[0][0] -= amount
            dried_bu += amount
            operating_h += step_h
            remaining_h -= step_h
            if queue[0][0] <= self.config.numerical_tolerance:
                queue.pop(0)
        return dried_bu, operating_h

    @staticmethod
    def _queue_amount(queue: list[list[float]]) -> float:
        return sum(lot[0] for lot in queue)

    @staticmethod
    def _append_forecast_lot(
            queue: list[list[float]], amount_bu: float,
            moisture_pct: float) -> None:
        if amount_bu <= 1e-12:
            return
        if queue and queue[-1][1] == moisture_pct:
            queue[-1][0] += amount_bu
        else:
            queue.append([amount_bu, moisture_pct])

    def _move_forecast_lots(
            self, source: list[list[float]], destination: list[list[float]],
            amount_bu: float) -> float:
        moved_bu = 0.0
        while source and moved_bu < amount_bu - self.config.numerical_tolerance:
            take = min(source[0][0], amount_bu - moved_bu)
            self._append_forecast_lot(destination, take, source[0][1])
            source[0][0] -= take
            moved_bu += take
            if source[0][0] <= self.config.numerical_tolerance:
                source.pop(0)
        return moved_bu

    def _advance_forecast_downstream(
            self, dryer_queue: list[list[float]],
            upstream_queue: list[list[float]], transport_rate_buph: float,
            duration_h: float) -> tuple[float, float]:
        """Advance a transparent aggregate transport/dryer approximation."""
        c = self.config
        transport_budget = transport_rate_buph * duration_h
        pre_move = min(
            self._queue_amount(upstream_queue),
            transport_budget / 2.0,
            max(0.0, c.wet_bin_capacity_bu - self._queue_amount(dryer_queue)),
        )
        moved = self._move_forecast_lots(
            upstream_queue, dryer_queue, pre_move)
        dried_bu, operating_h = self._dryer_step(dryer_queue, duration_h)
        post_move = min(
            self._queue_amount(upstream_queue),
            transport_budget - moved,
            max(0.0, c.wet_bin_capacity_bu - self._queue_amount(dryer_queue)),
        )
        self._move_forecast_lots(upstream_queue, dryer_queue, post_move)
        return dried_bu, operating_h

    def _rollout(
            self, first_action: MacroAction | str, *,
            forecast_segments: tuple[ForecastSegment, ...],
            forecast_lots: tuple[ForecastLot, ...], productive_since_stop_h: float,
            stop_elapsed_h: float, in_stop: bool,
            mode_switches_used: int, wet_bin_inventory_bu: float,
            response_time_h: float, tail_strategy: str = "coordinated",
            **_: float) -> ForecastOutcome:
        c = self.config
        step_h = c.forecast_step_h
        dryer_queue: list[list[float]] = []
        upstream_queue: list[list[float]] = []
        wet_remaining_bu = wet_bin_inventory_bu
        for lot in forecast_lots:
            wet_amount = min(lot.amount_bu, max(0.0, wet_remaining_bu))
            self._append_forecast_lot(
                dryer_queue, wet_amount, lot.moisture_pct)
            self._append_forecast_lot(
                upstream_queue, lot.amount_bu - wet_amount,
                lot.moisture_pct)
            wet_remaining_bu -= wet_amount
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
        dryer_idle_h = 0.0
        dryer_idle_during_planned_stop_h = 0.0
        elapsed_h = 0.0
        initial_wait_remaining_h = macro.wait_h
        stopping = initial_wait_remaining_h > c.numerical_tolerance
        stop_clock = stop_elapsed_h if in_stop else 0.0
        work_clock = productive_since_stop_h
        active_segment = (
            (segments[0][0].field_id, segments[0][0].phase)
            if segments else ("", ""))
        switches_used = mode_switches_used
        selected_mode = macro.mode

        def close_forced_episode() -> None:
            nonlocal forced_episode_h
            nonlocal meaningful_forced_block_h
            nonlocal meaningful_forced_block_starts
            if forced_episode_h > c.forced_block_ignore_h + c.numerical_tolerance:
                meaningful_forced_block_h += forced_episode_h
                meaningful_forced_block_starts += 1
            forced_episode_h = 0.0

        while segments:
            if elapsed_h >= c.max_simulation_h:
                raise RuntimeError(
                    "Predictive completion rollout exceeded maximum duration")
            duration_h = min(step_h, c.max_simulation_h - elapsed_h)
            segment = segments[0][0]
            remaining_bu = float(segments[0][1])
            segment_key = (segment.field_id, segment.phase)
            if segment_key != active_segment:
                if segment.field_id != active_segment[0]:
                    switches_used = 0
                elif segment.phase == INTERIOR:
                    switches_used = 0
                active_segment = segment_key
                selected_mode = (MODE_2C if segment.phase == OPENING
                                 else MODE_2C_1GC)

            if stopping:
                close_forced_episode()
                if initial_wait_remaining_h > c.numerical_tolerance:
                    duration_h = min(duration_h, initial_wait_remaining_h)
                _, operating = self._advance_forecast_downstream(
                    dryer_queue, upstream_queue,
                    segment.transport_rate_buph, duration_h)
                dryer_idle_h += duration_h - operating
                dryer_idle_during_planned_stop_h += duration_h - operating
                planned_stop_h += duration_h
                stop_clock += duration_h
                elapsed_h += duration_h
                forced_blocking = False
                if initial_wait_remaining_h > c.numerical_tolerance:
                    initial_wait_remaining_h -= duration_h
                    if initial_wait_remaining_h <= c.numerical_tolerance:
                        stopping = False
                        stop_clock = 0.0
                        work_clock = 0.0
                    continue
                if stop_clock + c.numerical_tolerance >= c.minimum_stop_h:
                    inventory_bu = (self._queue_amount(dryer_queue)
                                    + self._queue_amount(upstream_queue))
                    free_bu = c.total_system_storage_bu - inventory_bu
                    lowest_rate = _rate(c, segment.phase, segment.geometry, MODE_1C)
                    head_queue = dryer_queue or upstream_queue
                    current_dryer = (c.dryer_rate_for_moisture(head_queue[0][1])
                                     if head_queue else c.dryer_rate_for_moisture(
                                         segment.moisture_pct))
                    required = max(
                        0.0,
                        (lowest_rate - current_dryer)
                        * max(c.dt_h, response_time_h),
                    )
                    if free_bu + c.numerical_tolerance >= required:
                        stopping = False
                        stop_clock = 0.0
                        work_clock = 0.0
                        allowed_after_stop = (
                            OPENING_MODES if segment.phase == OPENING
                            else INTERIOR_MODES)
                        selected_mode = max(
                            allowed_after_stop,
                            key=lambda mode: _rate(
                                c, segment.phase, segment.geometry, mode),
                        )
                continue

            allowed_modes = (OPENING_MODES if segment.phase == OPENING
                             else INTERIOR_MODES)
            if selected_mode not in allowed_modes:
                selected_mode = MODE_2C if segment.phase == OPENING else MODE_2C_1GC

            dryer_inventory_bu = self._queue_amount(dryer_queue)
            upstream_inventory_bu = self._queue_amount(upstream_queue)
            inventory_bu = dryer_inventory_bu + upstream_inventory_bu
            head_queue = dryer_queue or upstream_queue
            dryer_rate = (c.dryer_rate_for_moisture(head_queue[0][1])
                          if head_queue else c.dryer_rate_for_moisture(
                              segment.moisture_pct))
            current_rate = _rate(c, segment.phase, segment.geometry, selected_mode)
            projected_free = c.total_system_storage_bu - inventory_bu
            projected_net = max(0.0, current_rate - dryer_rate) * duration_h
            projected_field_net = max(
                0.0, current_rate - segment.transport_rate_buph) * duration_h
            field_capacity_bu = (
                c.maximum_combine_storage_bu + c.grain_cart_capacity_bu
                + c.semi_count * c.semi_capacity_bu)

            if ((projected_net > projected_free + c.numerical_tolerance
                 or projected_field_net
                 > field_capacity_bu - upstream_inventory_bu
                 + c.numerical_tolerance)
                    and segment.phase == INTERIOR
                    and switches_used < c.max_interior_mode_switches
                    and tail_strategy != "fastest_unregulated"):
                lower_modes = [mode for mode in INTERIOR_MODES
                               if c.interior_rate_buph(mode) < current_rate]
                if lower_modes:
                    selected_mode = max(
                        lower_modes, key=c.interior_rate_buph)
                    switches_used += 1
                    current_rate = c.interior_rate_buph(selected_mode)
                    projected_net = max(0.0, current_rate - dryer_rate) * duration_h

            if (projected_net > projected_free + c.numerical_tolerance
                    and work_clock + c.numerical_tolerance
                    >= c.stop_work_requirement_h
                    and tail_strategy != "fastest_unregulated"):
                stopping = True
                stop_clock = 0.0
                planned_stop_starts += 1
                continue

            possible_bu = min(remaining_bu, current_rate * duration_h)
            possible_dried_bu = min(
                dryer_inventory_bu
                + min(upstream_inventory_bu + possible_bu,
                      segment.transport_rate_buph * duration_h),
                dryer_rate * duration_h,
            )
            transport_out_bu = min(
                upstream_inventory_bu + possible_bu,
                segment.transport_rate_buph * duration_h,
                max(0.0, c.wet_bin_capacity_bu - dryer_inventory_bu)
                + possible_dried_bu,
            )
            accepted_bu = min(
                possible_bu,
                max(0.0, c.total_system_storage_bu - inventory_bu)
                + possible_dried_bu,
                max(0.0, field_capacity_bu - upstream_inventory_bu)
                + transport_out_bu,
            )
            if possible_bu > accepted_bu + c.numerical_tolerance:
                blocked_step_h = (possible_bu - accepted_bu) / current_rate
                forced_block_h += blocked_step_h
                forced_episode_h += blocked_step_h
                if not forced_blocking:
                    forced_block_starts += 1
                forced_blocking = True
            else:
                close_forced_episode()
                forced_blocking = False
            if accepted_bu > c.numerical_tolerance:
                self._append_forecast_lot(
                    upstream_queue, accepted_bu, segment.moisture_pct)
                work_clock += accepted_bu / current_rate
                segments[0][1] = remaining_bu - accepted_bu

            _, operating = self._advance_forecast_downstream(
                dryer_queue, upstream_queue,
                segment.transport_rate_buph, duration_h)
            dryer_idle_h += duration_h - operating
            elapsed_h += duration_h
            if float(segments[0][1]) <= c.numerical_tolerance:
                finished = segments.pop(0)[0]
                if segments and segments[0][0].field_id != finished.field_id:
                    transition_h = c.field_switch_time_h
                    _, operating = self._advance_forecast_downstream(
                        dryer_queue, upstream_queue,
                        segment.transport_rate_buph, transition_h)
                    dryer_idle_h += transition_h - operating
                    elapsed_h += transition_h

        close_forced_episode()
        harvest_completion_h = elapsed_h
        last_transport_rate = (
            forecast_segments[-1].transport_rate_buph
            if forecast_segments else c.grain_leg_rate_buph)
        while dryer_queue or upstream_queue:
            if elapsed_h >= c.max_simulation_h:
                raise RuntimeError(
                    "Predictive downstream tail exceeded maximum duration")
            duration_h = min(step_h, c.max_simulation_h - elapsed_h)
            _, operating = self._advance_forecast_downstream(
                dryer_queue, upstream_queue, last_transport_rate, duration_h)
            dryer_idle_h += duration_h - operating
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
        )

    @staticmethod
    def _objective(outcome: ForecastOutcome) -> tuple[float, float, float]:
        return (
            outcome.harvest_completion_h,
            outcome.benefit_forced_block_h,
            outcome.field_engaged_h,
        )

    @classmethod
    def _dominates(cls, left: ForecastOutcome, right: ForecastOutcome) -> bool:
        left_values = cls._objective(left)
        right_values = cls._objective(right)
        return (all(a <= b + 1e-9 for a, b in zip(left_values, right_values))
                and any(a < b - 1e-9 for a, b in zip(left_values, right_values)))

    def _preferred_macro_action(
            self, actions: Iterable[MacroAction], **state: float) -> MacroAction:
        """Return a balanced representative of the three-dimensional frontier."""
        candidates = tuple(actions)
        outcomes = {action: self._rollout(action, **state) for action in candidates}
        feasible = [
            action for action in candidates
            if (outcomes[action].dryer_idle_during_planned_stop_h
                <= self.config.numerical_tolerance)]
        if feasible:
            candidates = tuple(feasible)
        frontier = [
            action for action in candidates
            if not any(
                other != action
                and self._dominates(outcomes[other], outcomes[action])
                for other in candidates)
        ]
        columns = list(zip(*(self._objective(outcomes[action])
                             for action in frontier)))
        minima = tuple(min(column) for column in columns)
        maxima = tuple(max(column) for column in columns)

        def normalized_distance(action: MacroAction) -> tuple[float, ...]:
            values = self._objective(outcomes[action])
            normalized = tuple(
                0.0 if maximum - minimum <= 1e-9
                else (value - minimum) / (maximum - minimum)
                for value, minimum, maximum in zip(values, minima, maxima)
            )
            # Euclidean distance supplies one neutral representative for
            # execution; the complete frontier remains the scientific result.
            distance = sum(value * value for value in normalized) ** 0.5
            return (
                distance,
                max(normalized),
                outcomes[action].system_completion_h,
                outcomes[action].forced_block_h,
                outcomes[action].planned_stop_starts,
                -_rate(self.config, str(state["phase"]),
                       str(state["geometry"]), action.mode),
                action.wait_h,
            )

        return min(frontier, key=normalized_distance)

    def _macro_candidates(
            self, modes: Iterable[str], *, in_stop: bool,
            stop_elapsed_h: float, stop_eligible: bool) -> tuple[MacroAction, ...]:
        allowed = tuple(modes)
        candidates = [MacroAction(mode) for mode in allowed]
        if in_stop or stop_eligible:
            minimum_remaining_h = (
                max(0.0, self.config.minimum_stop_h - stop_elapsed_h)
                if in_stop else self.config.minimum_stop_h)
            waits = [minimum_remaining_h]
            waits.extend(
                minimum_remaining_h + extension
                for extension in self.config.planned_stop_extension_options_h)
            for wait_h in sorted(set(waits)):
                if wait_h <= self.config.numerical_tolerance:
                    continue
                candidates.extend(MacroAction(mode, wait_h) for mode in allowed)
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
        return self._predictive_mode(OPENING_MODES, **state)

    def choose_interior_mode(self, **state: float) -> str:
        return self._predictive_mode(INTERIOR_MODES, **state)

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
