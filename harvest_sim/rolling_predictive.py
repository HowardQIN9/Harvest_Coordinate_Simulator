"""A small, transparent rolling predictive controller.

The planner evaluates four unfinished fields with the real Simulator, executes
only the next control interval, and then plans again.  Interior modes may
change subject to the Simulator's dwell and two-switch limits.  A planned STOP
is considered only at a field boundary and lasts at least six hours.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import MODE_1C, MODE_2C, MODE_2C_1GC, SimulationConfig
from .open_loop import FieldAction, OpenLoopController, OpenLoopPlan
from .simulator import SimulationCheckpoint, SimulationResult, Simulator


@dataclass(frozen=True)
class PlanEvaluation:
    plan: OpenLoopPlan
    horizon_elapsed_h: float
    raw_forced_block_h: float
    planned_stop_h: float
    stop_starts: int
    mode_switches: int
    terminal_inventory_bu: float


class RollingFourFieldPredictive:
    """Receding-horizon search with a four-field active horizon."""

    def __init__(
            self, config: SimulationConfig, *,
            completion_tolerance_h: float = 2.0,
            decision_interval_h: float = 1.0,
            horizon_fields: int = 4,
            forecast_simulation_step_h: float = 5.0 / 60.0,
            verbose: bool = False,
            stop_durations_h: tuple[float, ...] = (
                6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0),
    ) -> None:
        self.config = config
        self.completion_tolerance_h = completion_tolerance_h
        self.decision_interval_h = decision_interval_h
        self.horizon_fields = horizon_fields
        self.forecast_simulation_step_h = forecast_simulation_step_h
        self.verbose = verbose
        self.stop_durations_h = stop_durations_h
        self.decision_history: list[dict[str, object]] = []

    def _ordered_incomplete_ids(
            self, checkpoint: SimulationCheckpoint) -> tuple[str, ...]:
        return tuple(
            checkpoint.fields[index].field_id
            for index in self.config.field_order_indices()
            if not checkpoint.fields[index].field_complete
        )

    def _at_field_start(
            self, checkpoint: SimulationCheckpoint) -> bool:
        current = checkpoint.state["current"]
        if current is None:
            return False
        field = checkpoint.fields[current[0]]
        return (
            current[1] == "Opening"
            and abs(field.remaining_area_ac - field.total_area_ac)
            <= self.config.numerical_tolerance
        )

    def _profiles(
            self, length: int, *, force_first_full: bool = False,
    ) -> tuple[tuple[str, ...], ...]:
        full = MODE_2C_1GC
        raw = (
            (full,) * length,
            (MODE_2C,) * length,
            (MODE_1C,) * length,
            (MODE_1C,) + (full,) * max(0, length - 1),
            (MODE_2C,) + (full,) * max(0, length - 1),
            (MODE_1C,) * min(2, length)
            + (full,) * max(0, length - 2),
        )
        if force_first_full:
            raw = tuple(
                (full,) + profile[1:] for profile in raw)
        return tuple(dict.fromkeys(raw))

    def _make_plan(
            self, checkpoint: SimulationCheckpoint,
            horizon_ids: tuple[str, ...], profile: tuple[str, ...],
            stop_field_id: str | None = None,
            stop_duration_h: float = 0.0) -> OpenLoopPlan:
        modes = dict(zip(horizon_ids, profile))
        actions = tuple(
            FieldAction(
                field.field_id,
                MODE_2C,
                modes.get(field.field_id, MODE_2C_1GC),
            )
            for field in checkpoint.fields
        )
        return OpenLoopPlan(actions, stop_field_id, stop_duration_h)

    def _forecast(
            self, checkpoint: SimulationCheckpoint,
            plan: OpenLoopPlan, target_completed: int) -> PlanEvaluation | None:
        controller = OpenLoopController(self.config, plan)
        before = checkpoint.state
        try:
            forecast = Simulator(
                self.config, controller=controller).run(
                    resume_from=checkpoint,
                    resume_controller=controller,
                    checkpoint_after_completed_fields=target_completed,
                    record_trace=False,
                    simulation_step_h=self.forecast_simulation_step_h,
                )
        except (RuntimeError, ValueError):
            return None
        if not isinstance(forecast, SimulationCheckpoint):
            return None
        stop_h = (
            float(forecast.state["planned_stop_h"])
            - float(before["planned_stop_h"]))
        stop_starts = (
            int(forecast.state["stop_count"])
            - int(before["stop_count"]))
        if plan.stop_on_field_id is not None:
            if (stop_starts != 1
                    or stop_h + self.config.numerical_tolerance
                    < plan.stop_duration_h):
                return None
        return PlanEvaluation(
            plan=plan,
            horizon_elapsed_h=(
                forecast.downstream.time_h - checkpoint.downstream.time_h),
            raw_forced_block_h=(
                float(forecast.state["blocked_h"])
                - float(before["blocked_h"])),
            planned_stop_h=stop_h,
            stop_starts=stop_starts,
            mode_switches=(
                int(forecast.state["interior_mode_switches"])
                - int(before["interior_mode_switches"])),
            terminal_inventory_bu=forecast.downstream.unprocessed_grain_bu,
        )

    def choose_plan(
            self, checkpoint: SimulationCheckpoint) -> PlanEvaluation:
        incomplete = self._ordered_incomplete_ids(checkpoint)
        horizon_ids = incomplete[:self.horizon_fields]
        if not horizon_ids:
            raise ValueError("No unfinished field remains")
        completed = sum(field.field_complete for field in checkpoint.fields)
        target_completed = min(
            len(checkpoint.fields), completed + len(horizon_ids))
        stop_options: tuple[tuple[str | None, float], ...] = ((None, 0.0),)
        if (self._at_field_start(checkpoint)
                and float(checkpoint.state["productive_since_stop_h"])
                + self.config.numerical_tolerance
                >= self.config.stop_work_requirement_h):
            stop_options += tuple(
                (horizon_ids[0], duration_h)
                for duration_h in self.stop_durations_h
                if duration_h + self.config.numerical_tolerance
                >= self.config.minimum_stop_h
            )

        evaluated: list[PlanEvaluation] = []
        for profile in self._profiles(
                len(horizon_ids),
                force_first_full=self._at_field_start(checkpoint)):
            for stop_field, stop_h in stop_options:
                plan = self._make_plan(
                    checkpoint, horizon_ids, profile, stop_field, stop_h)
                outcome = self._forecast(
                    checkpoint, plan, target_completed)
                if outcome is not None:
                    evaluated.append(outcome)
        if not evaluated:
            raise RuntimeError("Rolling Predictive found no feasible plan")
        fastest_h = min(item.horizon_elapsed_h for item in evaluated)
        eligible = [
            item for item in evaluated
            if item.horizon_elapsed_h
            <= (fastest_h + self.completion_tolerance_h
                + self.config.numerical_tolerance)
        ]
        selected = min(
            eligible,
            key=lambda item: (
                item.raw_forced_block_h,
                item.stop_starts,
                item.mode_switches,
                item.horizon_elapsed_h,
                item.terminal_inventory_bu,
            ),
        )
        current = checkpoint.state["current"]
        self.decision_history.append({
            "time_h": checkpoint.downstream.time_h,
            "field_id": (
                checkpoint.fields[current[0]].field_id
                if current is not None else ""),
            "horizon_fields": " -> ".join(horizon_ids),
            "candidate_count": len(evaluated),
            "fastest_horizon_h": fastest_h,
            "selected_horizon_h": selected.horizon_elapsed_h,
            "selected_raw_forced_h": selected.raw_forced_block_h,
            "selected_stop_field": selected.plan.stop_on_field_id or "",
            "selected_stop_h": selected.plan.stop_duration_h,
            "selected_modes": ";".join(
                f"{action.field_id}:{action.interior_mode}"
                for action in selected.plan.fields
                if action.field_id in horizon_ids),
        })
        if self.verbose:
            current_field = (
                checkpoint.fields[current[0]].field_id
                if current is not None else "transition")
            chosen_modes = ",".join(
                action.interior_mode for action in selected.plan.fields
                if action.field_id in horizon_ids)
            print(
                f"t={checkpoint.downstream.time_h:.2f} h "
                f"field={current_field} candidates={len(evaluated)} "
                f"modes={chosen_modes} "
                f"stop={selected.plan.stop_duration_h:g} h "
                f"forecast_block={selected.raw_forced_block_h:.2f} h",
                flush=True,
            )
        return selected

    def _can_replan(self, checkpoint: SimulationCheckpoint) -> bool:
        if checkpoint.state["in_stop"]:
            return False
        if self._at_field_start(checkpoint):
            return True
        current = checkpoint.state["current"]
        if current is None or checkpoint.state["pending_field"] is not None:
            return False
        if current[1] != "InteriorHarvest":
            return False
        if checkpoint.state["current_interior_mode"] is None:
            return True
        return (
            int(checkpoint.state["field_interior_mode_switches"])
            < self.config.max_interior_mode_switches
            and float(checkpoint.state["interior_mode_dwell_h"])
            + self.config.numerical_tolerance
            >= self.config.minimum_interior_mode_dwell_h
        )

    def run(self) -> SimulationResult:
        full_plan = OpenLoopPlan(tuple(
            FieldAction(field.field_id, MODE_2C, MODE_2C_1GC)
            for field in self.config.field_specs))
        controller = OpenLoopController(self.config, full_plan)
        checkpoint = Simulator(
            self.config, controller=controller).run(checkpoint_at_h=0.0)
        if not isinstance(checkpoint, SimulationCheckpoint):
            raise AssertionError("Initial rolling checkpoint was not created")

        active_plan = full_plan
        while not all(field.field_complete for field in checkpoint.fields):
            if checkpoint.state["in_stop"]:
                controller = checkpoint.controller
                if not isinstance(controller, OpenLoopController):
                    raise AssertionError("STOP checkpoint lost its plan")
                remaining_stop_h = max(
                    0.0,
                    controller.plan.stop_duration_h
                    - float(checkpoint.state["stop_elapsed_h"]))
                target_h = (
                    checkpoint.downstream.time_h + remaining_stop_h
                    + self.config.dt_h)
            elif self._can_replan(checkpoint):
                selected = self.choose_plan(checkpoint)
                active_plan = selected.plan
                controller = OpenLoopController(self.config, active_plan)
                target_h = (
                    checkpoint.downstream.time_h + self.decision_interval_h)
            else:
                controller = checkpoint.controller
                target_h = (
                    checkpoint.downstream.time_h + self.decision_interval_h)

            advanced = Simulator(
                self.config, controller=controller).run(
                    resume_from=checkpoint,
                    resume_controller=controller,
                    checkpoint_at_h=target_h,
                    checkpoint_on_field_change=True,
                )
            if not isinstance(advanced, SimulationCheckpoint):
                raise AssertionError("Rolling execution ended without checkpoint")
            if (advanced.downstream.time_h
                    <= checkpoint.downstream.time_h
                    + self.config.numerical_tolerance
                    and advanced.state["combine_field"]
                    == checkpoint.state["combine_field"]):
                raise RuntimeError("Rolling execution did not advance")
            checkpoint = advanced

        final = Simulator(
            self.config, controller=checkpoint.controller).run(
                resume_from=checkpoint)
        if not isinstance(final, SimulationResult):
            raise AssertionError("Rolling simulation did not finish")
        final.policy = "rolling_predictive"
        final.metrics["policy"] = "rolling_predictive"
        final.metrics["predictive_search_implementation"] = (
            "rolling_four_field_profiles")
        final.metrics["predictive_active_prefix_h"] = (
            self.decision_interval_h)
        final.metrics["predictive_tail_policy"] = "four_field_physical_forecast"
        final.metrics["rolling_candidate_profiles"] = 6
        final.metrics["rolling_completion_tolerance_h"] = (
            self.completion_tolerance_h)
        final.metrics["rolling_forecast_simulation_step_min"] = (
            self.forecast_simulation_step_h * 60.0)
        final.controller_decisions = list(self.decision_history)
        return final
