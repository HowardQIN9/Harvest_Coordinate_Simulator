import unittest
from dataclasses import replace

from harvest_sim import FieldSpec, SimulationConfig, Simulator
from harvest_sim.costs import CostConfig, MachineCost, calculate_costs


class CostTests(unittest.TestCase):
    def test_capital_recovery_zero_interest(self):
        machine = MachineCost(10000, 2000, 10, 0, 0)
        self.assertAlmostEqual(machine.annual_ownership(0), 860)

    def test_cost_breakdown_and_waiting(self):
        config = SimulationConfig(field_specs=(FieldSpec("F", 2, "rectangular", 5,
                                                        moisture_pct=20),))
        result = Simulator(config).run()
        metrics = result.metrics
        parts = sum(value for key, value in metrics.items()
                    if key.startswith("cost_") and key.endswith("_usd"))
        self.assertAlmostEqual(parts, metrics["total_cost_usd"])
        self.assertAlmostEqual(metrics["cost_drying_usd"], 2 * 200 * 5 * .055)
        changed = dict(metrics)
        changed["combine_occupied_machine_h"] += 1
        new = calculate_costs(changed, config, result.fields, config.cost_config)
        self.assertGreater(new["total_cost_usd"], metrics["total_cost_usd"])
        self.assertEqual(new["cost_combine_ownership_usd"], metrics["cost_combine_ownership_usd"])
        extra = replace(config, n_combines=3)
        new = calculate_costs(metrics, extra, result.fields, config.cost_config)
        self.assertGreater(new["cost_combine_ownership_usd"], metrics["cost_combine_ownership_usd"])

    def test_invalid_inputs(self):
        for kwargs in ({"diesel_usd_per_gal": -1}, {"idle_fuel_fraction": 2},
                       {"interest_rate": float("nan")}):
            with self.assertRaises(ValueError):
                CostConfig(**kwargs)


if __name__ == "__main__":
    unittest.main()
