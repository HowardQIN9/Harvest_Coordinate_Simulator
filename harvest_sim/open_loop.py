"""Prescribed two-field actions for validating a physical forecast.

This controller makes no optimization decisions.  A plan is replayed by the
ordinary Simulator, so a forecast uses the same vehicle, STOP, and dryer
physics as an observed run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import OPENING_MODES, INTERIOR_MODES, SimulationConfig
from .controller import HARVEST, STOP, FixedController, TaskRef
from .field import OPENING, Field
from .simulator import SimulationCheckpoint, SimulationResult, Simulator


@dataclass(frozen=True)
class FieldAction:
    field_id: str
    opening_mode: str
    interior_mode: str


@dataclass(frozen=True)
class OpenLoopPlan:
    fields: tuple[FieldAction, ...]
    stop_on_field_id: str | None = None
    stop_duration_h: float = 0.0


class OpenLoopController:
    """Replay field modes and, optionally, one planned STOP request.

    STOP is requested when the named field becomes active.  The common
    simulator supervisor still applies field-boundary eligibility, the
    one-STOP-per-field limit, dryer protection, and minimum duration. The
    resulting trace records the *actual* start and end times; a request is not
    treated as an executed STOP.
    """

    name = "open_loop"
    allows_interior_mode_switching = True

    def __init__(self, config: SimulationConfig, plan: OpenLoopPlan) -> None:
        self.config = config
        self.plan = plan
        self._actions = {action.field_id: action for action in plan.fields}
        expected = {field.field_id for field in config.field_specs}
        if set(self._actions) != expected or len(plan.fields) != len(expected):
            raise ValueError("Plan must specify every configured field once")
        for action in plan.fields:
            if action.opening_mode not in config.feasible_modes(OPENING_MODES):
                raise ValueError(f"Invalid opening mode for {action.field_id}")
            if action.interior_mode not in config.feasible_modes(INTERIOR_MODES):
                raise ValueError(f"Invalid interior mode for {action.field_id}")
        if plan.stop_on_field_id is None:
            if plan.stop_duration_h != 0.0:
                raise ValueError("STOP duration requires a STOP field")
        elif (plan.stop_on_field_id not in expected
              or plan.stop_duration_h < config.minimum_stop_h):
            raise ValueError("STOP field or duration is invalid")
        self._stop_started = False
        self._stop_done = False

    def choose(
            self, fields: list[Field], current: TaskRef | None,
            **_: float) -> TaskRef | None:
        return FixedController(self.config).choose(fields, current)

    def _planned_mode(self, field_id: str, phase: str,
                      available_modes: tuple[str, ...]) -> str:
        action = self._actions[field_id]
        mode = (action.opening_mode if phase == OPENING
                else action.interior_mode)
        if mode not in available_modes:
            raise ValueError(
                f"Planned mode {mode} is unavailable for {field_id} {phase}")
        return mode

    def choose_opening_mode(self, *, field_id: str, phase: str,
                            available_modes: tuple[str, ...], **_: float) -> str:
        return self._planned_mode(field_id, phase, available_modes)

    def choose_interior_mode(self, *, field_id: str, phase: str,
                             available_modes: tuple[str, ...], **_: float) -> str:
        return self._planned_mode(field_id, phase, available_modes)

    def choose_schedule_action(
            self, *, field_id: str, in_stop: bool,
            stop_elapsed_h: float, **_: float) -> str:
        if self._stop_done or self.plan.stop_on_field_id is None:
            return HARVEST
        if in_stop:
            self._stop_started = True
            if (stop_elapsed_h + self.config.numerical_tolerance
                    >= self.plan.stop_duration_h):
                self._stop_done = True
                return HARVEST
            return STOP
        if field_id == self.plan.stop_on_field_id and not self._stop_started:
            return STOP
        return HARVEST


def forecast_plan(
        checkpoint: SimulationCheckpoint,
        plan: OpenLoopPlan) -> SimulationResult:
    """Fork a checkpoint and replay a prescribed plan with real physics."""
    current = checkpoint.state["current"]
    if current is not None and checkpoint.state["current_opening_mode"]:
        field_id = checkpoint.fields[current[0]].field_id
        if current[1] == OPENING:
            planned = next(
                action.opening_mode for action in plan.fields
                if action.field_id == field_id)
            if planned != checkpoint.state["current_opening_mode"]:
                raise ValueError("Cannot change an opening mode already in use")
    same_plan = (isinstance(checkpoint.controller, OpenLoopController)
                 and checkpoint.controller.plan == plan)
    if (checkpoint.state["stop_count"]
            and plan.stop_on_field_id is not None and not same_plan):
        raise ValueError("A second planned STOP is not supported yet")
    if same_plan:
        controller = checkpoint.controller
    else:
        controller = OpenLoopController(checkpoint.config, plan)
        if checkpoint.state["stop_count"]:
            controller._stop_started = True
            controller._stop_done = not checkpoint.state["in_stop"]
    simulator = Simulator(checkpoint.config, controller=controller)
    result = simulator.run(
        resume_from=checkpoint, resume_controller=controller)
    if not isinstance(result, SimulationResult):
        raise AssertionError("Plan forecast did not finish")
    if (plan.stop_on_field_id is not None
            and checkpoint.state["stop_count"] == 0
            and result.metrics["number_of_planned_stops"] == 0):
        raise ValueError("Requested STOP was not physically feasible")
    if plan.stop_on_field_id is not None:
        starts = [float(row["time_h"]) for row in result.time_series
                  if row["event"] == "VOLUNTARY_STOP_START"]
        ends = [float(row["time_h"]) for row in result.time_series
                if row["event"] == "VOLUNTARY_STOP_END"]
        if (len(starts) != 1 or len(ends) != 1
                or ends[0] - starts[0] + checkpoint.config.numerical_tolerance
                < plan.stop_duration_h):
            raise ValueError("Requested STOP duration was not executed")
    return result
