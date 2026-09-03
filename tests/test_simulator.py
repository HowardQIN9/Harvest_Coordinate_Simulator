import unittest
from dataclasses import replace

from harvest_sim import (SimulationConfig, Simulator,
                         calculate_daily_machine_hours,
                         summarize_machine_hours)
from harvest_sim.config import (INTERIOR_MODES, MODE_1C, MODE_2C,
                                MODE_2C_1GC, FieldSpec)
from harvest_sim.downstream import (
    AT_FIELD,
    RETURNING,
    UNLOADING,
    WAITING_UNLOAD,
    Downstream,
    GrainLot,
)
from harvest_sim.field import INTERIOR, OPENING


class DownstreamTests(unittest.TestCase):
    def full_departure(self, downstream: Downstream,
                       moisture_pct: float = 18.0) -> None:
        for _ in range(2):
            downstream.add_harvested_grain(500.0, moisture_pct, "F1")
            downstream.unload_combine()

    def test_five_plus_ten_plus_five_minute_trip(self):
        d = Downstream(SimulationConfig())
        d.set_field_context("F1", 5.0)
        self.full_departure(d)

        d.advance_time(5 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, UNLOADING)
        d.advance_time(10 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, RETURNING)
        self.assertAlmostEqual(d.receiving_pit_inventory_bu, 1000.0)
        d.advance_time(5 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, AT_FIELD)

        times = {event["event"]: event["time_h"] for event in d.events
                 if event["event"] != "LOAD_SEMI"}
        self.assertAlmostEqual(times["ARRIVE_GC"], 5 / 60)
        self.assertAlmostEqual(times["START_UNLOAD"], 5 / 60)
        self.assertAlmostEqual(times["FINISH_UNLOAD"], 15 / 60)
        self.assertAlmostEqual(times["RETURN_FIELD"], 20 / 60)

    def test_grain_leg_feeds_wet_bin_at_3000_buph(self):
        d = Downstream(SimulationConfig())
        d.receiving_pit_lots.append(GrainLot(1000.0, 18.0, "F1"))
        d.advance_time(10 / 60)
        self.assertAlmostEqual(d.total_leg_transferred_bu, 500.0)
        self.assertAlmostEqual(d.total_dried_bu, 800.0 * 10 / 60)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 500.0 - 800.0 * 10 / 60)

    def test_fifo_dryer_changes_rate_with_moisture(self):
        d = Downstream(SimulationConfig())
        d.wet_bin_lots.extend([
            GrainLot(100.0, 18.0, "F1"),
            GrainLot(100.0, 22.0, "F2"),
        ])
        d.advance_time(0.125)
        self.assertAlmostEqual(d.dried_by_field_bu["F1"], 100.0)
        self.assertNotIn("F2", d.dried_by_field_bu)
        self.assertEqual(d.current_dryer_rate_buph, 400.0)
        d.advance_time(0.25)
        self.assertAlmostEqual(d.dried_by_field_bu["F2"], 100.0)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 0.0)

    def test_full_downstream_makes_semi_wait(self):
        d = Downstream(SimulationConfig())
        d.receiving_pit_lots.append(GrainLot(1000.0, 22.0, "F1"))
        d.wet_bin_lots.append(GrainLot(10000.0, 22.0, "F1"))
        semi = d.semis[0]
        semi.lots.append(GrainLot(1000.0, 22.0, "F1"))
        semi.state = WAITING_UNLOAD
        d.handle_events()
        self.assertEqual(semi.state, WAITING_UNLOAD)
        d.advance_time(2.5)
        d.handle_events()
        self.assertEqual(semi.state, UNLOADING)
        self.assertAlmostEqual(d.receiving_pit_inventory_bu, 0.0)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 10000.0)


class SimulatorTests(unittest.TestCase):
    def assert_mass_balance(self, result, config):
        for row in result.time_series:
            semi_grain = sum(row[f"semi_{index}_load_bu"]
                             for index in range(1, config.semi_count + 1))
            stored = (row["combine_tank_bu"] + semi_grain
                      + row["receiving_pit_inventory_bu"]
                      + row["wet_bin_inventory_bu"])
            self.assertAlmostEqual(
                row["cumulative_grain_generated_bu"],
                row["cumulative_grain_dried_bu"] + stored,
                places=5,
            )
            self.assertLessEqual(row["receiving_pit_inventory_bu"],
                                 config.receiving_pit_capacity_bu + 1e-7)
            self.assertLessEqual(row["wet_bin_inventory_bu"],
                                 config.wet_bin_capacity_bu + 1e-6)

    def test_default_field_table(self):
        c = SimulationConfig()
        actual = [(f.field_id, f.area_ac, f.geometry,
                   f.one_way_travel_min) for f in c.field_specs]
        self.assertEqual(actual, [
            ("F1", 50.0, "irregular", 5.0),
            ("F2", 80.0, "rectangular", 15.0),
            ("F3", 50.0, "irregular", 20.0),
            ("F4", 80.0, "rectangular", 5.0),
            ("F5", 50.0, "irregular", 15.0),
            ("F6", 80.0, "rectangular", 20.0),
            ("F7", 50.0, "irregular", 15.0),
            ("F8", 80.0, "rectangular", 15.0),
            ("F9", 50.0, "irregular", 15.0),
            ("F10", 80.0, "rectangular", 15.0),
        ])
        added_fields = c.field_specs[6:]
        self.assertEqual(sum(f.geometry == "rectangular" for f in added_fields), 2)
        self.assertEqual(sum(f.geometry == "irregular" for f in added_fields), 2)
        self.assertTrue(all(f.one_way_travel_min == 15.0 for f in added_fields))
        self.assertEqual(c.total_area_ac, 650.0)
        self.assertEqual(c.total_grain_bu, 130000.0)

    def test_both_policies_finish_with_conservation(self):
        c = SimulationConfig()
        field_moistures = c.sample_field_moistures()
        for policy in ("sequential", "adaptive"):
            with self.subTest(policy=policy):
                result = Simulator(c, policy, field_moistures).run()
                metrics = result.metrics
                self.assertAlmostEqual(metrics["total_harvested_ac"], 650.0)
                self.assertAlmostEqual(metrics["total_dried_grain_bu"], 130000.0,
                                       places=5)
                self.assertEqual(tuple(field.moisture_pct for field in result.fields),
                                 field_moistures)
                expected_average = sum(
                    spec.area_ac * moisture
                    for spec, moisture in zip(c.field_specs, field_moistures)
                ) / c.total_area_ac
                self.assertAlmostEqual(
                    metrics["total_dried_average_moisture_pct"],
                    expected_average,
                )
                accounted = (metrics["productive_harvest_time_h"]
                             + metrics["blocked_time_h"]
                             + metrics["switching_time_h"]
                             + metrics["interior_mode_switching_time_h"]
                             + metrics["scheduled_harvest_rest_time_h"])
                self.assertAlmostEqual(metrics["harvest_completion_time_h"],
                                       accounted, places=6)
                self.assertAlmostEqual(metrics["mass_balance_error_bu"], 0.0)
                self.assert_mass_balance(result, c)

    def test_field_moisture_is_reproducible_and_policy_independent(self):
        c = SimulationConfig(random_seed=42)
        self.assertEqual(c.dryer_rate_for_moisture(18.0), 800.0)
        self.assertEqual(c.dryer_rate_for_moisture(22.0), 400.0)
        first = c.sample_field_moistures()
        second = c.sample_field_moistures()
        different_seed = replace(c, random_seed=43).sample_field_moistures()
        self.assertEqual(first, second)
        self.assertNotEqual(first, different_seed)
        self.assertTrue(all(c.moisture_min_pct <= value <= c.moisture_max_pct
                            for value in first))
        sequential = Simulator(c, "sequential", first)
        adaptive = Simulator(c, "adaptive", first)
        self.assertEqual(tuple(field.moisture_pct for field in sequential.fields),
                         tuple(field.moisture_pct for field in adaptive.fields))

    def test_every_field_finishes_before_switch(self):
        result = Simulator(SimulationConfig(), "adaptive").run()
        phase_order = []
        for row in result.time_series:
            if row["combine_state"] != "HARVESTING":
                continue
            phase = (row["current_field"], row["current_task"])
            if not phase_order or phase_order[-1] != phase:
                phase_order.append(phase)
        field_order = list(dict.fromkeys(field_id for field_id, _ in phase_order))
        self.assertEqual(phase_order, [
            (field_id, phase)
            for field_id in field_order
            for phase in (OPENING, INTERIOR)
        ])
        self.assertEqual(len(field_order), 10)
        self.assertEqual(result.metrics["number_of_field_switches"], 9)
        self.assertAlmostEqual(result.metrics["switching_time_h"], 180 / 60)

    def test_transport_event_times_use_each_field_minutes(self):
        result = Simulator(SimulationConfig(), "sequential").run()
        departures = {(e["semi_id"], e["trip"]): e for e in result.transport_events
                      if e["event"] == "DEPART_FIELD"}
        starts = {(e["semi_id"], e["trip"]): e for e in result.transport_events
                  if e["event"] == "START_UNLOAD"}
        finishes = {(e["semi_id"], e["trip"]): e for e in result.transport_events
                    if e["event"] == "FINISH_UNLOAD"}
        returns = {(e["semi_id"], e["trip"]): e for e in result.transport_events
                   if e["event"] == "RETURN_FIELD"}
        for key, departure in departures.items():
            arrival = next(e for e in result.transport_events
                           if e["event"] == "ARRIVE_GC"
                           and (e["semi_id"], e["trip"]) == key)
            travel_h = departure["one_way_travel_min"] / 60.0
            self.assertAlmostEqual(arrival["time_h"] - departure["time_h"],
                                   travel_h, places=8)
            self.assertAlmostEqual(finishes[key]["time_h"] - starts[key]["time_h"],
                                   10 / 60, places=8)
            self.assertAlmostEqual(returns[key]["time_h"] - finishes[key]["time_h"],
                                   travel_h, places=8)

    def test_adaptive_forecast_reduces_harvest_time(self):
        c = SimulationConfig()
        sequential = Simulator(c, "sequential").run()
        adaptive = Simulator(c, "adaptive").run()
        self.assertNotEqual(adaptive.metrics["field_order"],
                            sequential.metrics["field_order"])
        self.assertLess(adaptive.metrics["harvest_completion_time_h"],
                        sequential.metrics["harvest_completion_time_h"])
        self.assertLess(adaptive.metrics["blocked_time_h"],
                        sequential.metrics["blocked_time_h"])

    def test_effective_opening_and_interior_rates(self):
        c = SimulationConfig()
        simulator = Simulator(c)
        irregular = simulator.fields[0]
        rectangular = simulator.fields[1]
        self.assertAlmostEqual(
            simulator.capacity(irregular, OPENING) * c.yield_bu_per_ac,
            c.irregular_opening_2c_rate_buph,
        )
        self.assertAlmostEqual(
            simulator.capacity(rectangular, OPENING) * c.yield_bu_per_ac,
            c.rectangular_opening_2c_rate_buph,
        )
        for mode in INTERIOR_MODES:
            self.assertAlmostEqual(
                simulator.capacity(irregular, INTERIOR, mode) * c.yield_bu_per_ac,
                c.interior_rate_buph(mode),
            )
        self.assertEqual(c.interior_rate_buph(MODE_1C), 2000.0)
        self.assertEqual(c.interior_rate_buph(MODE_2C), 4000.0)
        self.assertEqual(c.interior_rate_buph(MODE_2C_1GC), 5000.0)

    def test_interior_mode_control_respects_switch_and_dwell_limits(self):
        c = SimulationConfig()
        sequential = Simulator(c, "sequential").run()
        sequential_modes = {
            row["operating_mode"] for row in sequential.time_series
            if row["current_task"] == INTERIOR
            and row["combine_state"] in ("HARVESTING", "BLOCKED")
        }
        self.assertEqual(sequential_modes, {MODE_2C_1GC})
        self.assertEqual(sequential.metrics["number_of_interior_mode_switches"], 0)

        adaptive = Simulator(c, "adaptive").run()
        self.assertGreater(adaptive.metrics["intentional_slowdown_productive_h"], 0.0)
        self.assertLessEqual(
            adaptive.metrics["number_of_interior_mode_switches"],
            len(c.field_specs) * c.max_interior_mode_switches,
        )
        events_by_field = {}
        for row in adaptive.time_series:
            if not str(row["event"]).startswith("MODE_"):
                continue
            events_by_field.setdefault(row["current_field"], []).append(row)
        for field_id, events in events_by_field.items():
            switches = [row for row in events if "SWITCH" in row["event"]]
            self.assertLessEqual(len(switches), c.max_interior_mode_switches,
                                 field_id)
            for first, second in zip(events, events[1:]):
                self.assertGreaterEqual(
                    second["time_h"] - first["time_h"] + c.numerical_tolerance,
                    c.minimum_interior_mode_dwell_h,
                    field_id,
                )

    def test_optional_mode_switch_cost_is_counted(self):
        c = replace(
            SimulationConfig(),
            field_specs=(FieldSpec("F1", 50.0, "irregular", 5.0),),
            interior_mode_switch_time_h=5 / 60,
        )
        result = Simulator(c, "adaptive", (20.0,)).run()
        switches = result.metrics["number_of_interior_mode_switches"]
        self.assertGreater(switches, 0)
        self.assertAlmostEqual(
            result.metrics["interior_mode_switching_time_h"],
            switches * 5 / 60,
        )
        accounted = (result.metrics["productive_harvest_time_h"]
                     + result.metrics["blocked_time_h"]
                     + result.metrics["switching_time_h"]
                     + result.metrics["interior_mode_switching_time_h"]
                     + result.metrics["scheduled_harvest_rest_time_h"])
        self.assertAlmostEqual(result.metrics["harvest_completion_time_h"],
                               accounted)

    def test_fourteen_hour_harvest_then_ten_hour_rest(self):
        c = replace(
            SimulationConfig(),
            field_specs=(FieldSpec("F1", 500.0, "rectangular", 0.0),),
            combine_tank_capacity_bu=200000.0,
            wet_bin_capacity_bu=200000.0,
            grain_leg_rate_buph=100000.0,
            dryer_intercept_buph=100000.0,
            dryer_moisture_slope_buph_per_pct=1.0,
        )
        result = Simulator(c, "sequential", (20.0,)).run()
        self.assertAlmostEqual(
            result.metrics["scheduled_harvest_rest_time_h"], 10.0, places=6)
        rest_rows = [row for row in result.time_series
                     if row["combine_state"] == "HARVEST_REST"]
        self.assertTrue(rest_rows)
        self.assertAlmostEqual(rest_rows[0]["time_h"] - rest_rows[0]["duration_h"],
                               14.0, places=6)
        self.assertAlmostEqual(rest_rows[-1]["time_h"], 24.0, places=6)
        for row in result.time_series:
            if row["combine_state"] != "HARVESTING":
                continue
            midpoint_h = row["time_h"] - row["duration_h"] / 2.0
            self.assertLess(midpoint_h % 24.0, 14.0)

    def test_machine_hour_accounting(self):
        c = SimulationConfig()
        result = Simulator(c, "sequential").run()
        daily = calculate_daily_machine_hours(result.time_series, c.semi_count)
        summary = summarize_machine_hours(daily)
        self.assertGreater(len(daily), 1)
        self.assertAlmostEqual(
            summary["combine_productive_machine_h"],
            2.0 * result.metrics["productive_harvest_time_h"],
        )
        self.assertAlmostEqual(
            summary["combine_switching_machine_h"],
            2.0 * result.metrics["switching_time_h"],
        )
        self.assertAlmostEqual(
            summary["grain_cart_productive_machine_h"],
            result.metrics["interior_2c_1gc_productive_h"],
        )
        self.assertAlmostEqual(
            summary["semi_active_machine_h"],
            summary["semi_driving_machine_h"]
            + summary["semi_unloading_machine_h"],
        )
        for resource in ("combine", "grain_cart", "semi", "total"):
            self.assertLessEqual(summary[f"{resource}_active_machine_h"],
                                 summary[f"{resource}_occupied_machine_h"])
        for key, value in summary.items():
            self.assertAlmostEqual(value, result.metrics[key])

    def test_invalid_configurations(self):
        cases = [
            {"semi_count": 0},
            {"grain_leg_rate_buph": 0.0},
            {"wet_bin_capacity_bu": -1.0},
            {"receiving_pit_capacity_bu": 999.0},
            {"field_specs": (FieldSpec("F1", 5.0, "irregular", 5.0),)},
            {"field_specs": (FieldSpec("F1", 50.0, "round", 5.0),)},
            {"random_seed": True},
            {"moisture_min_pct": 22.0},
            {"dryer_intercept_buph": 2200.0},
            {"interior_1c_rate_buph": 4000.0},
            {"max_interior_mode_switches": -1},
            {"minimum_interior_mode_dwell_h": -0.1},
            {"interior_mode_switch_time_h": -0.1},
            {"harvest_work_window_h": 0.0},
            {"harvest_rest_window_h": -0.1},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(SimulationConfig(), **values)


if __name__ == "__main__":
    unittest.main()
