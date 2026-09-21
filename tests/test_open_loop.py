import unittest
from dataclasses import replace

from harvest_sim.config import SimulationConfig
from harvest_sim.open_loop import (
    FieldAction,
    OpenLoopController,
    OpenLoopPlan,
    forecast_plan,
)
from harvest_sim.simulator import SimulationCheckpoint, SimulationResult, Simulator


class OpenLoopForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        base = SimulationConfig()
        self.config = replace(
            base,
            field_specs=(base.field_specs[2], base.field_specs[5]),
            fixed_field_order_ids=("F3", "F6"),
            stop_work_requirement_h=1.0,
        )
        self.plan = OpenLoopPlan(
            fields=(
                FieldAction("F3", "2C", "2C"),
                FieldAction("F6", "2C", "2C+1GC"),
            ),
            stop_on_field_id="F6",
            stop_duration_h=6.0,
        )

    def simulator(self) -> Simulator:
        return Simulator(
            self.config,
            controller=OpenLoopController(self.config, self.plan),
        )

    def test_same_plan_matches_reality_from_harvest_stop_and_resume(self):
        real = self.simulator().run()
        self.assertIsInstance(real, SimulationResult)
        self.assertEqual(real.metrics["field_order"], "F3 -> F6")
        self.assertEqual(real.metrics["number_of_planned_stops"], 1)
        self.assertGreaterEqual(
            real.metrics["planned_stop_time_h"]
            + self.config.numerical_tolerance,
            6.0,
        )
        stop_start = next(
            row for row in real.time_series
            if row["event"] == "VOLUNTARY_STOP_START")
        self.assertEqual(stop_start["current_field"], "F6")
        self.assertAlmostEqual(
            stop_start["stop_request_duration_h"], 0.0, places=9)

        for requested_h in (2.0, 5.0, 11.0):
            with self.subTest(requested_h=requested_h):
                checkpoint = self.simulator().run(
                    checkpoint_at_h=requested_h)
                self.assertIsInstance(checkpoint, SimulationCheckpoint)
                self.assertGreaterEqual(
                    checkpoint.downstream.time_h + self.config.numerical_tolerance,
                    requested_h,
                )
                forecast = forecast_plan(checkpoint, self.plan)
                self.assertEqual(real.time_series, forecast.time_series)
                self.assertEqual(real.transport_events, forecast.transport_events)
                self.assertEqual(real.metrics, forecast.metrics)
                self.assertEqual(
                    [f.remaining_area_ac for f in real.fields],
                    [f.remaining_area_ac for f in forecast.fields],
                )

    def test_branching_changes_future_without_changing_checkpoint(self):
        checkpoint = self.simulator().run(checkpoint_at_h=2.0)
        self.assertIsInstance(checkpoint, SimulationCheckpoint)
        checkpoint_time = checkpoint.downstream.time_h
        checkpoint_grain = checkpoint.downstream.unprocessed_grain_bu
        checkpoint_remaining = tuple(
            field.remaining_area_ac for field in checkpoint.fields)

        slower = OpenLoopPlan(
            fields=(
                FieldAction("F3", "2C", "1C"),
                FieldAction("F6", "2C", "1C"),
            ),
        )
        slow_forecast = forecast_plan(checkpoint, slower)
        original_forecast = forecast_plan(checkpoint, self.plan)

        self.assertNotEqual(
            slow_forecast.metrics["harvest_completion_time_h"],
            original_forecast.metrics["harvest_completion_time_h"],
        )
        self.assertEqual(checkpoint.downstream.time_h, checkpoint_time)
        self.assertEqual(
            checkpoint.downstream.unprocessed_grain_bu, checkpoint_grain)
        self.assertEqual(
            tuple(field.remaining_area_ac for field in checkpoint.fields),
            checkpoint_remaining,
        )

    def test_forecast_rejects_a_stop_the_supervisor_ends_early(self):
        checkpoint = self.simulator().run(checkpoint_at_h=2.0)
        self.assertIsInstance(checkpoint, SimulationCheckpoint)
        too_long = replace(self.plan, stop_duration_h=20.0)
        with self.assertRaisesRegex(ValueError, "duration was not executed"):
            forecast_plan(checkpoint, too_long)

    def test_plan_can_start_from_an_existing_fixed_policy_state(self):
        checkpoint = Simulator(self.config, policy="fixed").run(
            checkpoint_at_h=2.0)
        self.assertIsInstance(checkpoint, SimulationCheckpoint)
        original_time = checkpoint.downstream.time_h
        forecast = forecast_plan(checkpoint, self.plan)
        self.assertEqual(forecast.metrics["number_of_planned_stops"], 1)
        self.assertEqual(checkpoint.downstream.time_h, original_time)


if __name__ == "__main__":
    unittest.main()
