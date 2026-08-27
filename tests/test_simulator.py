import unittest
from dataclasses import replace

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.downstream import Downstream


class SimulatorTests(unittest.TestCase):
    def test_sanity_checks_for_both_policies(self):
        for policy in ("sequential", "adaptive"):
            result = Simulator(SimulationConfig(), policy).run()
            m = result.metrics
            self.assertAlmostEqual(m["total_harvested_ac"], 300.0, places=6)
            self.assertAlmostEqual(m["total_harvested_grain_bu"], 60000.0, places=5)
            self.assertAlmostEqual(m["mass_balance_error_bu"], 0.0, places=6)
            self.assertLessEqual(m["maximum_buffer_bu"], 1000.0 + 1e-8)
            accounted = (m["productive_harvest_time_h"] + m["blocked_time_h"]
                         + m["switching_time_h"])
            self.assertAlmostEqual(m["total_completion_time_h"], accounted, places=7)
            for row in result.time_series:
                self.assertAlmostEqual(
                    row["cumulative_grain_generated_bu"],
                    row["cumulative_grain_processed_bu"] + row["buffer_bu"],
                    places=6,
                )

    def test_high_capacity_has_no_blocking(self):
        config = replace(SimulationConfig(), downstream_rate_buph=2000.0)
        for policy in ("sequential", "adaptive"):
            metrics = Simulator(config, policy).run().metrics
            self.assertAlmostEqual(metrics["blocked_time_h"], 0.0, places=8)
            self.assertAlmostEqual(metrics["final_buffer_bu"], 0.0, places=8)

    def test_low_capacity_blocks_more(self):
        low = Simulator(replace(SimulationConfig(), downstream_rate_buph=600.0), "sequential").run()
        high = Simulator(replace(SimulationConfig(), downstream_rate_buph=1200.0), "sequential").run()
        self.assertGreater(low.metrics["blocked_time_h"], high.metrics["blocked_time_h"])

    def test_sequential_switch_cost(self):
        result = Simulator(SimulationConfig(), "sequential").run()
        self.assertEqual(result.metrics["number_of_field_switches"], 5)
        self.assertAlmostEqual(result.metrics["switching_time_h"], 5 * 20 / 60, places=8)

    def test_current_inflow_is_processed_in_same_step(self):
        dt = 1 / 60
        downstream = Downstream(1000.0, 1000.0)
        accepted, processed = downstream.step_with_inflow(1500.0 * dt, dt)
        self.assertAlmostEqual(accepted, 1500.0 * dt)
        self.assertAlmostEqual(processed, 1000.0 * dt)
        self.assertAlmostEqual(downstream.buffer_bu, 500.0 * dt)

        fast_downstream = Downstream(1000.0, 2000.0)
        fast_downstream.step_with_inflow(1500.0 * dt, dt)
        self.assertAlmostEqual(fast_downstream.buffer_bu, 0.0)


if __name__ == "__main__":
    unittest.main()
