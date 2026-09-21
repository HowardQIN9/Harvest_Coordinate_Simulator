import unittest
from dataclasses import replace

from harvest_sim.config import FieldSpec, SimulationConfig
from harvest_sim.rolling_predictive import RollingFourFieldPredictive


class RollingPredictiveTests(unittest.TestCase):
    def test_small_run_replans_and_starts_each_field_at_full_speed(self):
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

        self.assertEqual(result.policy, "rolling_predictive")
        self.assertEqual(result.metrics["field_order"], "A -> B")
        self.assertEqual(
            result.metrics["interior_mode_sequence"],
            "A:2C+1GC; B:2C+1GC",
        )
        self.assertEqual(result.metrics["rolling_candidate_profiles"], 6)
        self.assertEqual(len(result.controller_decisions), 2)


if __name__ == "__main__":
    unittest.main()
