"""Rolling predictive control with one-hour root actions and a dynamic tail.

Each real decision commits only the next control interval. Candidate actions
select one interior mode and optionally one boundary STOP. Their future is
evaluated with the real Simulator and a deterministic, fastest-safe reactive
tail that may choose new modes every hour and reconsider STOP at later field
boundaries. This keeps the forecast consistent with receding-horizon execution
without enumerating fixed whole-field mode profiles.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from .config import (
    INTERIOR_MODES,
    OPENING_MODES,
    SimulationConfig,
)
from .controller import ReactiveController
from .field import INTERIOR
from .open_loop import FieldAction, OpenLoopController, OpenLoopPlan
from .simulator import SimulationCheckpoint, SimulationResult, Simulator


@dataclass(frozen=True)
class RollingAction:
    """Immediate action committed by the real controller."""

    interior_mode: str
    stop_duration_h: float = 0.0


@dataclass(frozen=True)
class PlanEvaluation:
    action: RollingAction
    horizon_elapsed_h: float
    raw_forced_block_h: float
    planned_stop_h: float
    stop_starts: int
    mode_switches: int
    terminal_inventory_bu: float
    dryer_idle_during_stop_h: float


class RollingTailController(ReactiveController):
    """Fastest-safe continuation using the shared boundary STOP supervisor."""

    name = "rolling_tail"

    def __init__(
            self, config: SimulationConfig,
            decision_interval_h: float) -> None:
        super().__init__(config)
        self.decision_interval_h = decision_interval_h

class RollingFourFieldPredictive:
    """Receding-horizon search with dynamic future mode decisions."""

    def __init__(
            self, config: SimulationConfig, *,
            completion_tolerance_min_fraction: float = 0.01,
            completion_tolerance_max_fraction: float = 0.04,
            completion_tolerance_pressure_scale: float = 0.30,
            decision_interval_h: float = 1.0,
            horizon_fields: int = 4,
            forecast_simulation_step_h: float = 5.0 / 60.0,
            verbose: bool = False,
            stop_durations_h: tuple[float, ...] | None = None,
            maximum_stop_h: float = 30.0,
            stop_coarse_step_h: float = 4.0,
            stop_refine_step_h: float = 1.0,
            stop_fine_step_h: float = 0.25,
            forecast_yield_multiplier: float = 1.0,
    ) -> None:
        if not (0 <= completion_tolerance_min_fraction
                <= completion_tolerance_max_fraction):
            raise ValueError("completion tolerance fractions are invalid")
        if completion_tolerance_pressure_scale < 0:
            raise ValueError("completion tolerance scale must be nonnegative")
        if decision_interval_h <= 0:
            raise ValueError("decision_interval_h must be positive")
        if horizon_fields <= 0:
            raise ValueError("horizon_fields must be positive")
        if forecast_simulation_step_h <= 0:
            raise ValueError("forecast_simulation_step_h must be positive")
        if maximum_stop_h < config.minimum_stop_h:
            raise ValueError("maximum_stop_h must allow the minimum STOP")
        for value in (
                stop_coarse_step_h, stop_refine_step_h, stop_fine_step_h):
            if value <= 0:
                raise ValueError("STOP search steps must be positive")
        if forecast_yield_multiplier <= 0:
            raise ValueError("forecast_yield_multiplier must be positive")
        self.config = config
        self.completion_tolerance_min_fraction = (
            completion_tolerance_min_fraction)
        self.completion_tolerance_max_fraction = (
            completion_tolerance_max_fraction)
        self.completion_tolerance_pressure_scale = (
            completion_tolerance_pressure_scale)
        self.decision_interval_h = decision_interval_h
        self.horizon_fields = horizon_fields
        self.forecast_simulation_step_h = forecast_simulation_step_h
        self.verbose = verbose
        self.stop_durations_h = (
            None if stop_durations_h is None
            else tuple(sorted(set(float(value) for value in stop_durations_h)))
        )
        self.maximum_stop_h = maximum_stop_h
        self.stop_coarse_step_h = stop_coarse_step_h
        self.stop_refine_step_h = stop_refine_step_h
        self.stop_fine_step_h = stop_fine_step_h
        self.forecast_yield_multiplier = forecast_yield_multiplier
        self.decision_history: list[dict[str, object]] = []

    def _forecast_checkpoint(
            self, checkpoint: SimulationCheckpoint) -> SimulationCheckpoint:
        """Return a forecast-only checkpoint with biased remaining yield."""
        if abs(self.forecast_yield_multiplier - 1.0) <= 1e-12:
            return checkpoint
        fields = deepcopy(checkpoint.fields)
        for field in fields:
            if not field.field_complete:
                field.yield_bu_per_ac *= self.forecast_yield_multiplier
        return replace(checkpoint, fields=fields)

    def _ordered_incomplete_ids(
            self, checkpoint: SimulationCheckpoint) -> tuple[str, ...]:
        return tuple(
            checkpoint.fields[index].field_id
            for index in self.config.field_order_indices()
            if not checkpoint.fields[index].field_complete
        )

    def _active_field_id(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...]) -> str:
        current = checkpoint.state["current"]
        if current is not None:
            return checkpoint.fields[current[0]].field_id
        if not horizon_ids:
            raise ValueError("No unfinished field remains")
        return horizon_ids[0]

    def _at_field_start(self, checkpoint: SimulationCheckpoint) -> bool:
        current = checkpoint.state["current"]
        if current is None:
            return False
        field = checkpoint.fields[current[0]]
        return (
            current[1] == "Opening"
            and abs(field.remaining_area_ac - field.total_area_ac)
            <= self.config.numerical_tolerance
        )

    def _root_modes(
            self, checkpoint: SimulationCheckpoint) -> tuple[str, ...]:
        available = self.config.feasible_modes(INTERIOR_MODES)
        current = checkpoint.state["current"]
        current_mode = checkpoint.state["current_interior_mode"]
        if (current is None or current[1] != INTERIOR
                or current_mode is None):
            return available
        if (int(checkpoint.state["field_interior_mode_switches"])
                >= self.config.max_interior_mode_switches):
            return (str(current_mode),)
        if (float(checkpoint.state["interior_mode_dwell_h"])
                + self.config.numerical_tolerance
                < self.config.minimum_interior_mode_dwell_h):
            return (str(current_mode),)
        return available

    def _make_plan(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...],
            action: RollingAction) -> OpenLoopPlan:
        active_id = self._active_field_id(checkpoint, horizon_ids)
        fastest_opening = max(
            self.config.feasible_modes(OPENING_MODES),
            key=self.config.interior_rate_buph)
        fastest_interior = max(
            self.config.feasible_modes(INTERIOR_MODES),
            key=self.config.interior_rate_buph)
        current = checkpoint.state["current"]
        saved_opening = checkpoint.state["current_opening_mode"]
        actions: list[FieldAction] = []
        for field in checkpoint.fields:
            opening_mode = fastest_opening
            interior_mode = fastest_interior
            if field.field_id == active_id:
                interior_mode = action.interior_mode
                if (current is not None and current[1] == "Opening"
                        and saved_opening is not None):
                    opening_mode = str(saved_opening)
            actions.append(FieldAction(
                field.field_id, opening_mode, interior_mode))
        return OpenLoopPlan(
            tuple(actions),
            active_id if action.stop_duration_h > 0 else None,
            action.stop_duration_h,
        )

    def _advance_root(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...],
            action: RollingAction, *,
            record_trace: bool,
            simulation_step_h: float | None,
            apply_forecast_error: bool = False,
    ) -> SimulationCheckpoint | None:
        resume_checkpoint = (
            self._forecast_checkpoint(checkpoint)
            if apply_forecast_error else checkpoint)
        plan = self._make_plan(checkpoint, horizon_ids, action)
        controller = OpenLoopController(self.config, plan)
        target_h = (
            checkpoint.downstream.time_h
            + action.stop_duration_h
            + self.decision_interval_h)
        try:
            advanced = Simulator(
                self.config, controller=controller).run(
                    resume_from=resume_checkpoint,
                    resume_controller=controller,
                    checkpoint_at_h=target_h,
                    checkpoint_after_completed_fields=len(checkpoint.fields),
                    checkpoint_on_field_change=(
                        checkpoint.state["combine_field"] is not None),
                    record_trace=record_trace,
                    simulation_step_h=simulation_step_h,
                )
        except (RuntimeError, ValueError):
            return None
        if not isinstance(advanced, SimulationCheckpoint):
            return None
        if action.stop_duration_h > 0:
            stop_h = (
                float(advanced.state["planned_stop_h"])
                - float(checkpoint.state["planned_stop_h"]))
            stop_starts = (
                int(advanced.state["stop_count"])
                - int(checkpoint.state["stop_count"]))
            if (stop_starts != 1
                    or stop_h + self.config.numerical_tolerance
                    < action.stop_duration_h):
                return None
        return advanced

    def _forecast(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...], action: RollingAction,
            target_completed: int) -> PlanEvaluation | None:
        root = self._advance_root(
            checkpoint, horizon_ids, action,
            record_trace=False,
            simulation_step_h=self.forecast_simulation_step_h,
            apply_forecast_error=True,
        )
        if root is None:
            return None
        completed = sum(field.field_complete for field in root.fields)
        if completed >= target_completed:
            forecast = root
        else:
            tail = RollingTailController(
                root.controller.config, self.decision_interval_h)
            try:
                forecast = Simulator(
                    root.controller.config, controller=tail).run(
                        resume_from=root,
                        resume_controller=tail,
                        checkpoint_after_completed_fields=target_completed,
                        record_trace=False,
                        simulation_step_h=self.forecast_simulation_step_h,
                    )
            except (RuntimeError, ValueError):
                return None
            if not isinstance(forecast, SimulationCheckpoint):
                return None
        before = checkpoint.state
        return PlanEvaluation(
            action=action,
            horizon_elapsed_h=(
                forecast.downstream.time_h - checkpoint.downstream.time_h),
            raw_forced_block_h=(
                float(forecast.state["blocked_h"])
                - float(before["blocked_h"])),
            planned_stop_h=(
                float(forecast.state["planned_stop_h"])
                - float(before["planned_stop_h"])),
            stop_starts=(
                int(forecast.state["stop_count"])
                - int(before["stop_count"])),
            mode_switches=(
                int(forecast.state["interior_mode_switches"])
                - int(before["interior_mode_switches"])),
            terminal_inventory_bu=forecast.downstream.unprocessed_grain_bu,
            dryer_idle_during_stop_h=max(
                0.0,
                (float(forecast.state["planned_stop_h"])
                 - float(before["planned_stop_h"]))
                - (float(forecast.state["dryer_operating_during_stop_h"])
                   - float(before["dryer_operating_during_stop_h"])),
            ),
        )

    def _selection_context(
            self, evaluated: list[PlanEvaluation],
    ) -> tuple[float, float, float, float]:
        fastest = min(
            evaluated,
            key=lambda item: (
                item.horizon_elapsed_h,
                item.raw_forced_block_h,
            ),
        )
        fastest_h = fastest.horizon_elapsed_h
        forced_ratio = (
            fastest.raw_forced_block_h
            / max(fastest_h, self.config.numerical_tolerance)
        )
        alpha = min(
            self.completion_tolerance_max_fraction,
            max(
                self.completion_tolerance_min_fraction,
                self.completion_tolerance_min_fraction
                + self.completion_tolerance_pressure_scale * forced_ratio,
            ),
        )
        return fastest_h, forced_ratio, alpha, fastest_h * alpha

    def _select(
            self, evaluated: list[PlanEvaluation]) -> PlanEvaluation:
        if not evaluated:
            raise RuntimeError("Rolling Predictive found no feasible action")
        fastest_h, _, _, tolerance_h = self._selection_context(evaluated)
        eligible = [
            item for item in evaluated
            if item.horizon_elapsed_h
            <= (fastest_h + tolerance_h
                + self.config.numerical_tolerance)
        ]
        return min(
            eligible,
            key=lambda item: (
                item.raw_forced_block_h,
                item.dryer_idle_during_stop_h,
                item.planned_stop_h,
                item.stop_starts,
                item.mode_switches,
                item.horizon_elapsed_h,
                item.terminal_inventory_bu,
            ),
        )

    @staticmethod
    def _duration_grid(
            start_h: float, end_h: float,
            step_h: float) -> tuple[float, ...]:
        values: list[float] = []
        value = start_h
        while value <= end_h + 1e-9:
            values.append(round(value, 9))
            value += step_h
        return tuple(values)

    def _coarse_stop_durations(self) -> tuple[float, ...]:
        if self.stop_durations_h is not None:
            return tuple(
                value for value in self.stop_durations_h
                if (value + self.config.numerical_tolerance
                    >= self.config.minimum_stop_h))
        return self._duration_grid(
            self.config.minimum_stop_h,
            self.maximum_stop_h,
            self.stop_coarse_step_h,
        )

    def _bounded_duration_grid(
            self, center_h: float, radius_h: float,
            step_h: float) -> tuple[float, ...]:
        return self._duration_grid(
            max(self.config.minimum_stop_h, center_h - radius_h),
            min(self.maximum_stop_h, center_h + radius_h),
            step_h,
        )

    def _evaluate_actions(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...], target_completed: int,
            modes: tuple[str, ...], durations_h: tuple[float, ...],
            cache: dict[RollingAction, PlanEvaluation | None],
    ) -> None:
        for duration_h in durations_h:
            for mode in modes:
                action = RollingAction(mode, duration_h)
                if action not in cache:
                    cache[action] = self._forecast(
                        checkpoint, horizon_ids, action, target_completed)

    def choose_plan(
            self, checkpoint: SimulationCheckpoint) -> PlanEvaluation:
        incomplete = self._ordered_incomplete_ids(checkpoint)
        horizon_ids = incomplete[:self.horizon_fields]
        if not horizon_ids:
            raise ValueError("No unfinished field remains")
        completed = sum(field.field_complete for field in checkpoint.fields)
        target_completed = min(
            len(checkpoint.fields), completed + len(horizon_ids))
        modes = self._root_modes(checkpoint)
        cache: dict[RollingAction, PlanEvaluation | None] = {}

        self._evaluate_actions(
            checkpoint, horizon_ids, target_completed,
            modes, (0.0,), cache)

        can_stop = (
            self._at_field_start(checkpoint)
            and float(checkpoint.state["productive_since_stop_h"])
            + self.config.numerical_tolerance
            >= self.config.stop_work_requirement_h)
        if can_stop:
            coarse = self._coarse_stop_durations()
            self._evaluate_actions(
                checkpoint, horizon_ids, target_completed,
                modes, coarse, cache)
            coarse_outcomes = [
                cache[RollingAction(mode, duration_h)]
                for duration_h in coarse for mode in modes
                if cache.get(RollingAction(mode, duration_h)) is not None
            ]
            if coarse_outcomes and self.stop_durations_h is None:
                best_coarse = self._select(coarse_outcomes).action.stop_duration_h
                medium = self._bounded_duration_grid(
                    best_coarse, self.stop_coarse_step_h,
                    self.stop_refine_step_h)
                self._evaluate_actions(
                    checkpoint, horizon_ids, target_completed,
                    modes, medium, cache)
                stop_outcomes = [
                    item for action, item in cache.items()
                    if action.stop_duration_h > 0 and item is not None
                ]
                best_medium = self._select(
                    stop_outcomes).action.stop_duration_h
                fine = self._bounded_duration_grid(
                    best_medium, self.stop_refine_step_h,
                    self.stop_fine_step_h)
                self._evaluate_actions(
                    checkpoint, horizon_ids, target_completed,
                    modes, fine, cache)

        evaluated = [item for item in cache.values() if item is not None]
        selected = self._select(evaluated)
        fastest_h, fastest_forced_ratio, alpha, tolerance_h = (
            self._selection_context(evaluated))
        no_stop = [item for item in evaluated
                   if item.action.stop_duration_h == 0]
        stop = [item for item in evaluated
                if item.action.stop_duration_h > 0]
        best_no_stop = self._select(no_stop) if no_stop else None
        best_stop = self._select(stop) if stop else None
        active_id = self._active_field_id(checkpoint, horizon_ids)
        self.decision_history.append({
            "time_h": checkpoint.downstream.time_h,
            "field_id": active_id,
            "horizon_fields": " -> ".join(horizon_ids),
            "candidate_count": len(evaluated),
            "fastest_horizon_h": fastest_h,
            "fastest_forced_ratio": fastest_forced_ratio,
            "completion_tolerance_fraction": alpha,
            "completion_tolerance_h": tolerance_h,
            "dryer_idle_limit_h": self.config.maximum_dryer_idle_episode_h,
            "selected_horizon_h": selected.horizon_elapsed_h,
            "selected_raw_forced_h": selected.raw_forced_block_h,
            "selected_dryer_idle_during_stop_h": (
                selected.dryer_idle_during_stop_h),
            "selected_planned_stop_h": selected.planned_stop_h,
            "selected_stop_starts": selected.stop_starts,
            "selected_root_stop_h": selected.action.stop_duration_h,
            "selected_root_mode": selected.action.interior_mode,
            "best_no_stop_horizon_h": (
                best_no_stop.horizon_elapsed_h
                if best_no_stop is not None else ""),
            "best_no_stop_raw_forced_h": (
                best_no_stop.raw_forced_block_h
                if best_no_stop is not None else ""),
            "best_stop_horizon_h": (
                best_stop.horizon_elapsed_h
                if best_stop is not None else ""),
            "best_stop_raw_forced_h": (
                best_stop.raw_forced_block_h
                if best_stop is not None else ""),
            "best_stop_root_h": (
                best_stop.action.stop_duration_h
                if best_stop is not None else ""),
        })
        if self.verbose:
            print(
                f"t={checkpoint.downstream.time_h:.2f} h "
                f"field={active_id} candidates={len(evaluated)} "
                f"mode={selected.action.interior_mode} "
                f"root_stop={selected.action.stop_duration_h:g} h "
                f"forecast={selected.horizon_elapsed_h:.2f} h "
                f"block={selected.raw_forced_block_h:.2f} h "
                f"alpha={alpha:.1%} "
                f"idle_limit={self.config.maximum_dryer_idle_episode_h:g} h",
                flush=True,
            )
        return selected

    def run(self) -> SimulationResult:
        fastest_opening = max(
            self.config.feasible_modes(OPENING_MODES),
            key=self.config.interior_rate_buph)
        fastest_interior = max(
            self.config.feasible_modes(INTERIOR_MODES),
            key=self.config.interior_rate_buph)
        initial_plan = OpenLoopPlan(tuple(
            FieldAction(field.field_id, fastest_opening, fastest_interior)
            for field in self.config.field_specs))
        initial_controller = OpenLoopController(self.config, initial_plan)
        checkpoint = Simulator(
            self.config, controller=initial_controller).run(checkpoint_at_h=0.0)
        if not isinstance(checkpoint, SimulationCheckpoint):
            raise AssertionError("Initial rolling checkpoint was not created")

        while not all(field.field_complete for field in checkpoint.fields):
            selected = self.choose_plan(checkpoint)
            horizon_ids = self._ordered_incomplete_ids(
                checkpoint)[:self.horizon_fields]
            advanced = self._advance_root(
                checkpoint, horizon_ids, selected.action,
                record_trace=True,
                simulation_step_h=None,
                apply_forecast_error=False,
            )
            if advanced is None:
                raise RuntimeError("Selected rolling action did not execute")
            if (advanced.downstream.time_h
                    <= checkpoint.downstream.time_h
                    + self.config.numerical_tolerance
                    and advanced.state["combine_field"]
                    == checkpoint.state["combine_field"]):
                raise RuntimeError("Rolling execution did not advance")
            checkpoint = advanced

        final = Simulator(
            checkpoint.config, controller=checkpoint.controller).run(
                resume_from=checkpoint)
        if not isinstance(final, SimulationResult):
            raise AssertionError("Rolling simulation did not finish")
        # ``predictive`` is the public strategy name.  Keep implementation
        # identity separate so rolling and legacy results cannot be confused.
        final.policy = "predictive"
        final.metrics["policy"] = "predictive"
        final.metrics["controller_implementation"] = (
            "rolling_four_field_dynamic_tail")
        final.metrics["predictive_search_implementation"] = (
            "one_hour_root_dynamic_tail")
        final.metrics["predictive_active_prefix_h"] = (
            self.decision_interval_h)
        final.metrics["control_decision_interval_min"] = (
            self.decision_interval_h * 60.0)
        final.metrics["predictive_tail_policy"] = (
            "boundary_reactive_fastest_safe")
        final.metrics["rolling_completion_tolerance"] = (
            "adaptive_relative_forced_pressure")
        final.metrics["rolling_completion_tolerance_min_pct"] = (
            self.completion_tolerance_min_fraction * 100.0)
        final.metrics["rolling_completion_tolerance_max_pct"] = (
            self.completion_tolerance_max_fraction * 100.0)
        final.metrics["rolling_completion_tolerance_pressure_scale"] = (
            self.completion_tolerance_pressure_scale)
        final.metrics["rolling_forecast_simulation_step_min"] = (
            self.forecast_simulation_step_h * 60.0)
        final.metrics["rolling_horizon_fields"] = self.horizon_fields
        final.metrics["rolling_stop_search"] = "coarse_bidirectional_refine"
        final.metrics["rolling_stop_coarse_step_h"] = (
            self.stop_coarse_step_h)
        final.metrics["rolling_stop_refine_step_h"] = (
            self.stop_refine_step_h)
        final.metrics["rolling_stop_fine_step_h"] = (
            self.stop_fine_step_h)
        final.metrics["rolling_maximum_stop_h"] = self.maximum_stop_h
        final.metrics["rolling_uses_system_dryer_idle_limit"] = True
        final.metrics["forecast_yield_multiplier"] = (
            self.forecast_yield_multiplier)
        final.metrics["rolling_mean_candidate_count"] = (
            sum(int(row["candidate_count"])
                for row in self.decision_history)
            / len(self.decision_history)
            if self.decision_history else 0.0)
        final.controller_decisions = list(self.decision_history)
        return final
