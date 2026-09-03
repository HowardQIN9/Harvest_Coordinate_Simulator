import unittest
from dataclasses import replace

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.config import FieldSpec, HIGH, LOW
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
    def full_departure(self, downstream: Downstream, moisture: str = HIGH) -> None:
        for _ in range(2):
            downstream.add_harvested_grain(500.0, moisture, "F1")
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
        d.receiving_pit_lots.append(GrainLot(1000.0, HIGH, "F1"))
        d.advance_time(10 / 60)
        self.assertAlmostEqual(d.total_leg_transferred_bu, 500.0)
        self.assertAlmostEqual(d.total_dried_bu, 500.0 * 10 / 60)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 500.0 - 500.0 * 10 / 60)

    def test_fifo_dryer_changes_rate_with_moisture(self):
        d = Downstream(SimulationConfig())
        d.wet_bin_lots.extend([
            GrainLot(100.0, HIGH, "F1"),
            GrainLot(100.0, LOW, "F3"),
        ])
        d.advance_time(0.2)
        self.assertAlmostEqual(d.dried_by_moisture_bu[HIGH], 100.0)
        self.assertAlmostEqual(d.dried_by_moisture_bu[LOW], 0.0)
        self.assertEqual(d.current_dryer_rate_buph, 800.0)
        d.advance_time(0.125)
        self.assertAlmostEqual(d.dried_by_moisture_bu[LOW], 100.0)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 0.0)

    def test_full_downstream_makes_semi_wait(self):
        d = Downstream(SimulationConfig())
        d.receiving_pit_lots.append(GrainLot(1000.0, HIGH, "F1"))
        d.wet_bin_lots.append(GrainLot(10000.0, HIGH, "F1"))
        semi = d.semis[0]
        semi.lots.append(GrainLot(1000.0, HIGH, "F1"))
        semi.state = WAITING_UNLOAD
        d.handle_events()
        self.assertEqual(semi.state, WAITING_UNLOAD)
        d.advance_time(2.0)
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
        ])
        self.assertEqual(c.total_area_ac, 390.0)
        self.assertEqual(c.total_grain_bu, 78000.0)

    def test_both_policies_finish_with_conservation(self):
        c = SimulationConfig()
        for policy in ("sequential", "adaptive"):
            with self.subTest(policy=policy):
                result = Simulator(c, policy).run()
                metrics = result.metrics
                self.assertAlmostEqual(metrics["total_harvested_ac"], 390.0)
                self.assertAlmostEqual(metrics["total_dried_grain_bu"], 78000.0,
                                       places=5)
                order = metrics["field_order"].split(" -> ")
                area_by_id = {field.field_id: field.area_ac for field in c.field_specs}
                expected_high = sum(area_by_id[field_id] for field_id in order[:2]) * c.yield_bu_per_ac
                self.assertAlmostEqual(metrics["total_dried_high_moisture_bu"],
                                       expected_high, places=5)
                self.assertAlmostEqual(metrics["total_dried_low_moisture_bu"],
                                       c.total_grain_bu - expected_high, places=5)
                field_by_id = {field.field_id: field for field in result.fields}
                self.assertTrue(all(field_by_id[field_id].moisture == HIGH
                                    for field_id in order[:2]))
                self.assertTrue(all(field_by_id[field_id].moisture == LOW
                                    for field_id in order[2:]))
                accounted = (metrics["productive_harvest_time_h"]
                             + metrics["blocked_time_h"]
                             + metrics["switching_time_h"])
                self.assertAlmostEqual(metrics["harvest_completion_time_h"],
                                       accounted, places=6)
                self.assertAlmostEqual(metrics["mass_balance_error_bu"], 0.0)
                self.assert_mass_balance(result, c)

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
        self.assertEqual(len(field_order), 6)
        self.assertEqual(result.metrics["number_of_field_switches"], 5)
        self.assertAlmostEqual(result.metrics["switching_time_h"], 100 / 60)

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

    def test_invalid_configurations(self):
        cases = [
            {"semi_count": 0},
            {"grain_leg_rate_buph": 0.0},
            {"wet_bin_capacity_bu": -1.0},
            {"receiving_pit_capacity_bu": 999.0},
            {"field_specs": (FieldSpec("F1", 5.0, "irregular", 5.0),)},
            {"field_specs": (FieldSpec("F1", 50.0, "round", 5.0),)},
            {"high_moisture_fields_by_order": 7},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(SimulationConfig(), **values)


if __name__ == "__main__":
    unittest.main()
