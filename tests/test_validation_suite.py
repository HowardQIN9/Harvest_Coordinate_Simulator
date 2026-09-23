import unittest

from run_validation_suite import (
    FORECAST_YIELD_MULTIPLIERS,
    base_config,
    forecast_error_tag,
)


class ValidationSuiteTests(unittest.TestCase):
    def test_all_validation_families_share_wet_bin_reserve(self):
        config = base_config()
        self.assertEqual(config.wet_bin_control_limit_fraction, 0.95)
        self.assertAlmostEqual(
            config.wet_bin_control_reserve_bu,
            config.wet_bin_capacity_bu * 0.05,
        )

    def test_forecast_error_suite_has_independent_zero_baseline(self):
        self.assertEqual(
            FORECAST_YIELD_MULTIPLIERS,
            (0.95, 1.0, 1.05),
        )
        self.assertEqual(forecast_error_tag(0.95), "yield_m5pct")
        self.assertEqual(forecast_error_tag(1.0), "yield_0pct")
        self.assertEqual(forecast_error_tag(1.05), "yield_p5pct")


if __name__ == "__main__":
    unittest.main()
