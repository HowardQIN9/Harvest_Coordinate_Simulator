from __future__ import annotations

from dataclasses import dataclass
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
    """Transparent 48-hour aggregate rolling-horizon controller.

    It shares field order, actions, STOP constraints, and switch limits with
    Reactive.  The sole treatment difference is that it scores each immediate
    action using known future field segments and FIFO moisture lots.
    """

    name: str = "predictive"

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
            self, first_action: str, *, forecast_segments: tuple[ForecastSegment, ...],
            forecast_lots: tuple[ForecastLot, ...], productive_since_stop_h: float,
            stop_elapsed_h: float, in_stop: bool,
            mode_switches_used: int, wet_bin_inventory_bu: float,
            response_time_h: float, **_: float) -> tuple[float, float, float, float]:
        c = self.config
        horizon = c.prediction_horizon_h
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
        initial_target_bu = sum(float(item[1]) for item in segments)
        harvested_bu = 0.0
        forced_block_h = 0.0
        planned_stop_h = 0.0
        dryer_idle_h = 0.0
        elapsed_h = 0.0
        stopping = in_stop or first_action == STOP
        stop_clock = stop_elapsed_h if in_stop else 0.0
        work_clock = productive_since_stop_h
        first_mode = first_action if first_action != STOP else MODE_1C
        active_field_id = segments[0][0].field_id if segments else ""
        switches_used = mode_switches_used
        selected_mode = first_mode

        while elapsed_h < horizon - c.numerical_tolerance and segments:
            duration_h = min(step_h, horizon - elapsed_h)
            segment = segments[0][0]
            remaining_bu = float(segments[0][1])
            if segment.field_id != active_field_id:
                active_field_id = segment.field_id
                switches_used = 0
                selected_mode = (MODE_2C if segment.phase == OPENING
                                 else MODE_2C_1GC)

            if stopping:
                _, operating = self._advance_forecast_downstream(
                    dryer_queue, upstream_queue,
                    segment.transport_rate_buph, duration_h)
                dryer_idle_h += duration_h - operating
                planned_stop_h += duration_h
                stop_clock += duration_h
                elapsed_h += duration_h
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
                    and switches_used < c.max_interior_mode_switches):
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
                    >= c.stop_work_requirement_h):
                stopping = True
                stop_clock = 0.0
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
                forced_block_h += (possible_bu - accepted_bu) / current_rate
            if accepted_bu > c.numerical_tolerance:
                self._append_forecast_lot(
                    upstream_queue, accepted_bu, segment.moisture_pct)
                harvested_bu += accepted_bu
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
                    transition_h = min(c.field_switch_time_h,
                                       horizon - elapsed_h)
                    _, operating = self._advance_forecast_downstream(
                        dryer_queue, upstream_queue,
                        segment.transport_rate_buph, transition_h)
                    dryer_idle_h += transition_h - operating
                    elapsed_h += transition_h

        remaining_target_bu = max(0.0, initial_target_bu - harvested_bu)
        terminal_inventory_bu = (self._queue_amount(dryer_queue)
                                 + self._queue_amount(upstream_queue))
        # A fixed horizon must reward useful progress before distinguishing
        # how that progress was obtained; otherwise an endless STOP has zero
        # forced blocking and incorrectly dominates every productive action.
        return (remaining_target_bu, forced_block_h,
                dryer_idle_h, terminal_inventory_bu)

    def _predictive_mode(self, modes: Iterable[str], **state: float) -> str:
        candidates = tuple(modes)
        return min(
            candidates,
            key=lambda mode: (
                self._rollout(mode, **state),
                -_rate(self.config, str(state["phase"]),
                       str(state["geometry"]), mode),
            ),
        )

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
        candidates = list(available_modes)
        if in_stop or stop_eligible:
            candidates.append(STOP)
        best = min(
            candidates,
            key=lambda action: (
                self._rollout(
                    action,
                    stop_eligible=stop_eligible,
                    in_stop=in_stop,
                    stop_elapsed_h=stop_elapsed_h,
                    **state,
                ),
                1 if action == STOP else 0,
            ),
        )
        return STOP if best == STOP else HARVEST


# Compatibility aliases for older imports and command lines.
SequentialController = FixedController
AdaptiveController = PredictiveController
