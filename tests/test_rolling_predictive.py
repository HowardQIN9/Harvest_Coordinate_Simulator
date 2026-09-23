import unittest
from dataclasses import replace

from harvest_sim.config import FieldSpec, SimulationConfig
from harvest_sim.rolling_predictive import (
    PlanEvaluation,
    RollingAction,
    RollingFourFieldPredictive,
)


class RollingPredictiveTests(unittest.TestCase):
    def test_small_run_replans_with_one_hour_actions_and_dynamic_tail(self):
        base = SimulationConfig()
        config = replace(
            base,
            field_specs=(
                FieldSpec("A", 2.0, "rectangular", 2.0),
                FieldSpec("B", 2.0, "irregular", 3.0),
            ),
            fixed_field_order_ids=("A", "B"),
            stop_work_requirement_h=1.0,
            max_simulation_h=30.0,
        )
        result = RollingFourFieldPredictive(
            config,
            horizon_fields=2,
            decision_interval_h=1.0,
            stop_durations_h=(6.0,),
        ).run()

        self.assertEqual(result.policy, "predictive")
        self.assertEqual(result.metrics["policy"], "predictive")
        self.assertEqual(
            result.metrics["controller_implementation"],
            "rolling_four_field_dynamic_tail",
        )
        self.assertEqual(result.metrics["field_order"], "A -> B")
        self.assertEqual(
            result.metrics["interior_mode_sequence"],
            "A:2C+1GC; B:2C+1GC",
        )
        self.assertEqual(
            result.metrics["predictive_search_implementation"],
            "one_hour_root_dynamic_tail",
        )
        self.assertEqual(
            result.metrics["predictive_tail_policy"],
            "boundary_reactive_fastest_safe",
        )
        self.assertEqual(
            result.metrics["rolling_completion_tolerance"],
            "adaptive_relative_forced_pressure",
        )
        self.assertEqual(
            result.metrics["rolling_completion_tolerance_min_pct"], 1.0)
        self.assertEqual(
            result.metrics["rolling_completion_tolerance_max_pct"], 4.0)
        self.assertEqual(result.metrics["control_decision_interval_min"], 60.0)
        self.assertEqual(result.metrics["rolling_horizon_fields"], 2)
        self.assertEqual(len(result.controller_decisions), 1)
        self.assertTrue(all(
            row["selected_root_mode"] == "2C+1GC"
            for row in result.controller_decisions))

    @staticmethod
    def evaluation(elapsed_h: float, forced_h: float) -> PlanEvaluation:
        return PlanEvaluation(
            action=RollingAction("1C"),
            horizon_elapsed_h=elapsed_h,
            raw_forced_block_h=forced_h,
            planned_stop_h=0.0,
            stop_starts=0,
            mode_switches=0,
            terminal_inventory_bu=0.0,
            dryer_idle_during_stop_h=0.0,
        )

    def test_adaptive_completion_band_expands_with_forced_pressure(self):
        planner = RollingFourFieldPredictive(SimulationConfig())
        fast = self.evaluation(100.0, 10.0)
        lower_block = self.evaluation(103.5, 1.0)
        outside_band = self.evaluation(104.1, 0.0)

        self.assertIs(planner._select([fast, lower_block]), lower_block)
        self.assertIs(planner._select([fast, outside_band]), fast)
        _, ratio, alpha, tolerance_h = planner._selection_context([fast])
        self.assertAlmostEqual(ratio, 0.10)
        self.assertAlmostEqual(alpha, 0.04)
        self.assertAlmostEqual(tolerance_h, 4.0)

    def test_stop_refinement_is_bidirectional_at_minimum(self):
        planner = RollingFourFieldPredictive(SimulationConfig())
        self.assertEqual(
            planner._bounded_duration_grid(6.0, 4.0, 1.0),
            (6.0, 7.0, 8.0, 9.0, 10.0),
        )
        self.assertEqual(
            planner._bounded_duration_grid(8.0, 1.0, 0.25),
            (7.0, 7.25, 7.5, 7.75, 8.0, 8.25, 8.5, 8.75, 9.0),
        )

    def test_dryer_idle_limit_is_owned_by_system_config(self):
        config = replace(
            SimulationConfig(), maximum_dryer_idle_episode_h=2.5)
        planner = RollingFourFieldPredictive(config)
        self.assertEqual(
            planner.config.maximum_dryer_idle_episode_h, 2.5)
        self.assertFalse(hasattr(planner, "normal_dryer_idle_limit_h"))
        self.assertFalse(hasattr(planner, "high_pressure_dryer_idle_limit_h"))

    def test_yield_forecast_error_does_not_change_real_harvest_mass(self):
        config = replace(
            SimulationConfig(),
            field_specs=(
                FieldSpec("A", 2.0, "rectangular", 2.0),
                FieldSpec("B", 2.0, "irregular", 3.0),
            ),
            fixed_field_order_ids=("A", "B"),
            stop_work_requirement_h=1.0,
            max_simulation_h=30.0,
        )
        result = RollingFourFieldPredictive(
            config,
            horizon_fields=2,
            decision_interval_h=1.0,
            stop_durations_h=(6.0,),
            forecast_yield_multiplier=0.95,
        ).run()

        self.assertEqual(result.metrics["forecast_yield_multiplier"], 0.95)
        self.assertAlmostEqual(
            result.metrics["total_harvested_grain_bu"],
            config.total_grain_bu,
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
