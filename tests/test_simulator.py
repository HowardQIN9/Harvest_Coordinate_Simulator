import unittest
from dataclasses import replace

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.controller import AdaptiveController
from harvest_sim.downstream import AT_FIELD, RETURNING, TO_GC, Downstream
from harvest_sim.field import INTERIOR, OPENING, Field


def fast_config(**changes):
    values = dict(field_geometries=("rectangular",), opening_area_ac=15.0,
                  interior_area_ac=0.0, rectangular_opening_capacity_acph=30.0,
                  semi_count=2, grain_center_processing_rate_buph=2000.0)
    values.update(changes)
    return replace(SimulationConfig(), **values)


class DownstreamTests(unittest.TestCase):
    def test_gc_arrival_at_20_minutes_and_empty_return_at_45(self):
        d = Downstream(replace(SimulationConfig(), semi_count=2,
                               grain_center_processing_rate_buph=2000.0))
        self.assertEqual([semi.state for semi in d.semis], [AT_FIELD, AT_FIELD])
        for _ in range(2):
            d.combine_tank_bu = 500.0
            d.unload_combine()
        self.assertEqual(d.semis[0].state, TO_GC)
        self.assertEqual(d.semis[1].state, AT_FIELD)
        self.assertEqual(d.in_transit_grain_bu, 1000.0)
        self.assertEqual(d.grain_center_inventory_bu, 0.0)

        d.advance_time(20 / 60)
        self.assertEqual(d.total_processed_bu, 0.0)
        self.assertEqual(d.total_received_bu, 0.0)
        d.handle_events()
        self.assertEqual(d.grain_center_inventory_bu, 1000.0)
        self.assertEqual(d.semis[0].state, RETURNING)
        self.assertEqual(d.semis[0].load_bu, 0.0)
        d.advance_time(25 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, AT_FIELD)
        self.assertAlmostEqual(d.grain_center_inventory_bu, 1000 - 2000 * 25 / 60)
        self.assertAlmostEqual(d.total_processed_bu, 2000 * 25 / 60)

    def test_no_ground_storage_when_both_semis_are_away(self):
        d = Downstream(replace(SimulationConfig(), semi_count=2))
        for _ in range(4):
            d.combine_tank_bu = 500.0
            d.unload_combine()
        d.combine_tank_bu = 500.0
        d.unload_combine()
        self.assertEqual(d.combine_tank_bu, 500.0)
        self.assertEqual(d.semi_at_field_grain_bu, 0.0)
        self.assertEqual(d.in_transit_grain_bu, 2000.0)
        self.assertEqual(d.receiving_pressure, 1.0)

    def test_simultaneous_deliveries_and_gc_emptying(self):
        d = Downstream(replace(SimulationConfig(), semi_count=2,
                               grain_center_processing_rate_buph=2000.0))
        for _ in range(4):
            d.combine_tank_bu = 500.0
            d.unload_combine()
        d.advance_time(20 / 60)
        d.handle_events()
        self.assertEqual(d.grain_center_inventory_bu, 2000.0)
        d.advance_time(25 / 60)
        d.handle_events()
        d.advance_time(d.next_event_h - d.time_h)
        self.assertAlmostEqual(d.grain_center_inventory_bu, 0.0)
        self.assertAlmostEqual(d.total_processed_bu, 2000.0)
        d.advance_time(1.0)
        self.assertAlmostEqual(d.total_processed_bu, 2000.0)


class SimulatorTests(unittest.TestCase):
    def assert_mass_balance(self, result, config):
        for row in result.time_series:
            stored = (row["combine_tank_bu"] + row["semi_at_field_grain_bu"]
                      + row["in_transit_grain_bu"] + row["grain_center_inventory_bu"])
            self.assertAlmostEqual(row["cumulative_grain_generated_bu"],
                                   row["cumulative_grain_processed_bu"] + stored, places=6)
            self.assertGreaterEqual(row["combine_tank_bu"], -1e-8)
            self.assertLessEqual(row["combine_tank_bu"], config.combine_tank_capacity_bu + 1e-8)
            for index in range(1, config.semi_count + 1):
                self.assertGreaterEqual(row[f"semi_{index}_load_bu"], -1e-8)
                self.assertLessEqual(row[f"semi_{index}_load_bu"], config.semi_capacity_bu + 1e-8)
            if row["combine_state"] == "BLOCKED":
                self.assertAlmostEqual(row["combine_tank_bu"], config.combine_tank_capacity_bu)
                self.assertEqual(row["semis_at_field"], 0)

    def test_sanity_checks_for_both_policies(self):
        c = SimulationConfig()
        for policy in ("sequential", "adaptive"):
            with self.subTest(policy=policy):
                result = Simulator(c, policy).run()
                m = result.metrics
                self.assertAlmostEqual(m["total_harvested_ac"], c.total_area_ac, places=6)
                self.assertAlmostEqual(m["total_processed_grain_bu"], c.total_grain_bu, places=5)
                self.assertAlmostEqual(m["mass_balance_error_bu"], 0.0, places=6)
                accounted = m["productive_harvest_time_h"] + m["blocked_time_h"] + m["switching_time_h"]
                self.assertAlmostEqual(m["harvest_completion_time_h"], accounted, places=7)
                self.assertAlmostEqual(m["system_completion_time_h"], accounted + m["post_harvest_time_h"], places=7)
                self.assertEqual(m["number_of_semi_trips"], 60)
                self.assertAlmostEqual(m["final_unprocessed_grain_bu"], 0.0, places=6)
                self.assert_mass_balance(result, c)

    def test_blocks_only_after_2500_bu_and_resumes_on_return(self):
        # 6000 bu/h: departures at 10 and 20 min, tank full at 25 min.
        # First return at 55 min. The remaining 500 bu takes five minutes.
        c = fast_config()
        r = Simulator(c).run()
        self.assertAlmostEqual(r.metrics["blocked_time_h"], 30 / 60)
        self.assertAlmostEqual(r.metrics["harvest_completion_time_h"], 1.0)
        blocked = [row for row in r.time_series if row["combine_state"] == "BLOCKED"]
        self.assertAlmostEqual(blocked[0]["time_h"] - blocked[0]["duration_h"], 25 / 60)
        self.assertAlmostEqual(blocked[-1]["time_h"], 55 / 60)
        self.assertTrue(all(abs(row["cumulative_grain_generated_bu"] - 2500.0) < 1e-6 for row in blocked))
        self.assert_mass_balance(r, c)

    def test_event_timing_is_independent_of_time_step(self):
        for dt in (1 / 120, 1 / 60, 0.7):
            with self.subTest(dt=dt):
                r = Simulator(fast_config(dt_h=dt)).run()
                self.assertAlmostEqual(r.metrics["harvest_completion_time_h"], 1.0)
                self.assertAlmostEqual(r.metrics["system_completion_time_h"], 2.0)
                departures = {(e["semi_id"], e["trip"]): e for e in r.transport_events if e["event"] == "DEPART_FIELD"}
                for event in r.transport_events:
                    if event["event"] not in ("ARRIVE_GC", "RETURN_FIELD"):
                        continue
                    departure = departures[event["semi_id"], event["trip"]]
                    delay = 20 / 60 if event["event"] == "ARRIVE_GC" else 45 / 60
                    self.assertAlmostEqual(event["time_h"] - departure["time_h"], delay, places=9)
                    if event["event"] == "ARRIVE_GC":
                        self.assertAlmostEqual(event["grain_bu"], departure["grain_bu"])

    def test_gc_speed_does_not_block_combine_or_delay_returns(self):
        slow = Simulator(fast_config(grain_center_processing_rate_buph=500.0)).run()
        fast = Simulator(fast_config(grain_center_processing_rate_buph=5000.0)).run()
        for key in ("blocked_time_h", "harvest_completion_time_h"):
            self.assertAlmostEqual(slow.metrics[key], fast.metrics[key])
        self.assertGreater(slow.metrics["system_completion_time_h"], fast.metrics["system_completion_time_h"])
        self.assertGreater(slow.metrics["maximum_grain_center_inventory_bu"], fast.metrics["maximum_grain_center_inventory_bu"])
        for r in (slow, fast):
            first_return = next(e for e in r.transport_events if e["event"] == "RETURN_FIELD")
            self.assertAlmostEqual(first_return["time_h"], 55 / 60)

    def test_final_partial_shipment_and_no_fake_blocking_after_harvest(self):
        for acres in (0.5, 2.5, 7.3, 12.5):
            with self.subTest(acres=acres):
                c = fast_config(opening_area_ac=acres)
                r = Simulator(c).run()
                self.assertAlmostEqual(r.metrics["total_processed_grain_bu"], acres * 200)
                self.assertAlmostEqual(r.metrics["blocked_time_h"], 0.0)
                shipments = [e["grain_bu"] for e in r.transport_events if e["event"] == "DEPART_FIELD"]
                self.assertAlmostEqual(sum(shipments), acres * 200)
                self.assertLessEqual(max(shipments), 1000 + 1e-8)
                self.assert_mass_balance(r, c)

    def test_fields_finish_both_phases_before_switching(self):
        for policy in ("sequential", "adaptive"):
            for rate in (600.0, 2000.0):
                with self.subTest(policy=policy, gc_rate=rate):
                    c = replace(SimulationConfig(), grain_center_processing_rate_buph=rate,
                                interior_capacity_acph=30.0, rectangular_opening_capacity_acph=30.0,
                                irregular_opening_capacity_acph=20.0)
                    r = Simulator(c, policy).run()
                    phases, acres_by_phase = [], {}
                    previous_acres = 0.0
                    for row in r.time_series:
                        acres = row["cumulative_harvested_ac"] - previous_acres
                        previous_acres = row["cumulative_harvested_ac"]
                        if row["combine_state"] == "SWITCHING":
                            self.assertAlmostEqual(previous_acres, (len(acres_by_phase) // 2) * 50.0)
                        if row["combine_state"] not in ("HARVESTING", "BLOCKED"):
                            continue
                        phase = (row["current_field"], row["current_task"])
                        if not phases or phases[-1] != phase:
                            phases.append(phase)
                        acres_by_phase[phase] = acres_by_phase.get(phase, 0.0) + acres
                    order = list(dict.fromkeys(field_id for field_id, _ in phases))
                    self.assertEqual(len(order), len(c.field_geometries))
                    self.assertEqual(phases, [(field_id, task) for field_id in order for task in (OPENING, INTERIOR)])
                    for field_id in order:
                        self.assertAlmostEqual(acres_by_phase[field_id, OPENING], c.opening_area_ac)
                        self.assertAlmostEqual(acres_by_phase[field_id, INTERIOR], c.interior_area_ac)
                    self.assertEqual(r.metrics["number_of_field_switches"], 5)
                    self.assertAlmostEqual(r.metrics["switching_time_h"], 5 * 20 / 60)

    def test_transport_and_processing_continue_during_field_switch(self):
        c = fast_config(field_geometries=("rectangular", "rectangular"), opening_area_ac=5.0)
        r = Simulator(c).run()
        first_arrival = next(e for e in r.transport_events if e["event"] == "ARRIVE_GC")
        self.assertAlmostEqual(first_arrival["time_h"], 0.5)
        self.assertAlmostEqual(r.metrics["switching_time_h"], 20 / 60)
        self.assert_mass_balance(r, c)

    def test_zero_switch_time_and_empty_fields(self):
        c = fast_config(field_geometries=("rectangular", "irregular"), switch_time_h=0.0)
        r = Simulator(c).run()
        self.assertEqual(r.metrics["number_of_field_switches"], 1)
        self.assertEqual(r.metrics["switching_time_h"], 0.0)
        empty = Simulator(replace(c, field_geometries=())).run()
        self.assertEqual(empty.metrics["system_completion_time_h"], 0.0)
        self.assertEqual(empty.metrics["combine_utilization"], 0.0)

    def test_adaptive_uses_configured_rates(self):
        c = replace(SimulationConfig(), rectangular_opening_capacity_acph=3.0,
                    irregular_opening_capacity_acph=8.0)
        fields = [Field.create(1, "rectangular", c), Field.create(2, "irregular", c)]
        controller = AdaptiveController(c)
        self.assertEqual(controller.choose(fields, None, receiving_pressure=1.0), (0, OPENING))
        self.assertEqual(controller.choose(fields, None, receiving_pressure=0.0), (1, OPENING))

    def test_invalid_transport_configuration(self):
        for values in ({"semi_count": 0}, {"semi_gc_arrival_h": 1.0},
                       {"grain_center_processing_rate_buph": 0.0}, {"combine_tank_capacity_bu": -1.0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(SimulationConfig(), **values)


if __name__ == "__main__":
    unittest.main()
