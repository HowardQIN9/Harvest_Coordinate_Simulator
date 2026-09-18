import unittest
from dataclasses import replace
from unittest.mock import patch

from harvest_sim import (
    MIN_PREDICTION_HORIZON_H,
    MODE_1C,
    MODE_2C,
    MODE_2C_1GC,
    FieldSpec,
    SimulationConfig,
    Simulator,
    calculate_daily_machine_hours,
    summarize_machine_hours,
)
from harvest_sim.config import INTERIOR_MODES
from harvest_sim.controller import (
    HARVEST,
    STOP,
    ForecastLot,
    ForecastOutcome,
    ForecastSegment,
    MacroAction,
    PredictiveController,
    ReactiveController,
    ThresholdController,
)
from harvest_sim.downstream import AT_FIELD, RETURNING, UNLOADING, Downstream, GrainLot
from harvest_sim.field import INTERIOR, OPENING


class DownstreamTests(unittest.TestCase):
    def dispatch_full_semi(self, downstream: Downstream) -> None:
        # Grain is first created in independent combine tanks. Two transfers
        # fill one semi without ever exceeding either combine's 300-bu tank.
        downstream.add_harvested_grain(600.0, 18.0, "F1")
        downstream.unload_field_storage(2, 0)
        downstream.add_harvested_grain(400.0, 18.0, "F1")
        downstream.unload_field_storage(2, 0)

    def test_five_plus_twenty_plus_five_minute_trip_without_pit(self):
        d = Downstream(SimulationConfig())
        d.set_field_context("F1", 5.0)
        self.dispatch_full_semi(d)

        d.advance_time(5 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, UNLOADING)
        d.advance_time(20 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, RETURNING)
        self.assertEqual(d.receiving_pit_inventory_bu, 0.0)
        self.assertAlmostEqual(d.total_unloaded_bu, 1000.0)
        self.assertAlmostEqual(d.total_dried_bu, 800.0 * 20 / 60)
        self.assertAlmostEqual(
            d.wet_bin_inventory_bu, 1000.0 - 800.0 * 20 / 60)
        d.advance_time(5 / 60)
        d.handle_events()
        self.assertEqual(d.semis[0].state, AT_FIELD)

        times = {event["event"]: event["time_h"] for event in d.events
                 if event["event"] != "LOAD_SEMI"}
        self.assertAlmostEqual(times["ARRIVE_GC"], 5 / 60)
        self.assertAlmostEqual(times["START_UNLOAD"], 5 / 60)
        self.assertAlmostEqual(times["FINISH_UNLOAD"], 25 / 60)
        self.assertAlmostEqual(times["RETURN_FIELD"], 30 / 60)

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

    def test_full_wet_bin_throttles_direct_unloading_to_dryer_rate(self):
        d = Downstream(SimulationConfig())
        d.wet_bin_lots.append(GrainLot(10000.0, 22.0, "F0"))
        semi = d.semis[0]
        semi.lots.append(GrainLot(1000.0, 22.0, "F1"))
        semi.state = "WAITING_UNLOAD"
        semi.route_travel_h = 5 / 60
        d.handle_events()
        self.assertEqual(semi.state, UNLOADING)

        d.advance_time(1.0)
        d.handle_events()
        self.assertEqual(semi.state, UNLOADING)
        self.assertAlmostEqual(semi.load_bu, 600.0)
        self.assertAlmostEqual(d.wet_bin_inventory_bu, 10000.0)
        d.advance_time(1.5)
        d.handle_events()
        self.assertEqual(semi.state, RETURNING)
        self.assertAlmostEqual(semi.load_bu, 0.0)

    def test_combine_inventories_remain_independent(self):
        d = Downstream(SimulationConfig())
        d.combines[0].lots.append(GrainLot(300.0, 18.0, "F1"))
        self.assertEqual(d.working_combine_count, 1)
        self.assertAlmostEqual(d.combines[0].load_bu, 300.0)
        self.assertAlmostEqual(d.combines[1].load_bu, 0.0)
        d.combines[1].lots.append(GrainLot(300.0, 18.0, "F1"))
        self.assertEqual(d.working_combine_count, 0)

    def test_forecast_fork_preserves_vehicle_events_without_mutating_reality(self):
        d = Downstream(SimulationConfig())
        d.set_field_context("F1", 5.0)
        self.dispatch_full_semi(d)

        forecast = d.fork_for_forecast()
        self.assertEqual(forecast.semis[0].state, d.semis[0].state)
        self.assertAlmostEqual(forecast.next_event_h, d.next_event_h)

        forecast.advance_time(5 / 60)
        forecast.handle_events()
        self.assertEqual(forecast.semis[0].state, UNLOADING)
        self.assertEqual(d.semis[0].state, "TO_GC")
        self.assertAlmostEqual(d.time_h, 0.0)

    def test_single_unload_slot_uses_arrival_fifo(self):
        d = Downstream(SimulationConfig())
        first, second = d.semis[:2]
        for semi, arrival in ((first, 2.0), (second, 1.0)):
            semi.lots.append(GrainLot(100.0, 18.0, "F1"))
            semi.state = "WAITING_UNLOAD"
            semi.gc_arrival_h = arrival
        d._try_start_unloading()
        self.assertEqual(second.state, UNLOADING)
        self.assertEqual(first.state, "WAITING_UNLOAD")

    def test_committed_return_eta_reports_field_relief(self):
        d = Downstream(SimulationConfig())
        semi = d.semis[0]
        semi.state = RETURNING
        semi.field_return_h = 0.25
        relief = d.known_semi_return_relief(1.0)
        self.assertEqual(relief, ((0.25, 1000.0),))
        self.assertEqual(semi.state, RETURNING)
        self.assertAlmostEqual(d.time_h, 0.0)

    def test_dryer_starvation_clock_excludes_startup_and_tracks_episodes(self):
        d = Downstream(SimulationConfig())
        d.advance_time(2.0)
        self.assertFalse(d.dryer_has_started)
        self.assertAlmostEqual(d.total_dryer_starvation_h, 0.0)
        d.wet_bin_lots.append(GrainLot(400.0, 22.0, "F1"))
        d.advance_time(0.5)
        self.assertTrue(d.dryer_has_started)
        d.advance_time(1.0)
        self.assertAlmostEqual(d.current_dryer_idle_episode_h, 0.5)
        d.advance_time(0.5)
        self.assertAlmostEqual(d.maximum_dryer_idle_episode_h, 1.0)


class ControllerTests(unittest.TestCase):
    def current_state(self, config: SimulationConfig, **updates):
        state = {
            "phase": INTERIOR,
            "geometry": "rectangular",
            "receiving_pressure": 0.0,
            "field_mobile_free_bu": 4600.0,
            "system_buffer_free_bu": 10000.0,
            "wet_bin_inventory_bu": 0.0,
            "wet_bin_free_bu": config.wet_bin_capacity_bu,
            "committed_grain_bu": 0.0,
            "transport_rate_buph": 5000.0,
            "active_dryer_rate_buph": 800.0,
            "protection_h": 1.0,
            "current_mode": MODE_2C_1GC,
        }
        state.update(updates)
        return state

    def test_static_threshold_is_a_separate_reference(self):
        c = SimulationConfig()
        controller = ThresholdController(c)
        common = {
            "system_buffer_free_bu": c.total_system_storage_bu,
            "wet_bin_inventory_bu": 0.0,
        }
        self.assertEqual(controller.choose_interior_mode(
            receiving_pressure=0.2, **common), MODE_2C_1GC)
        self.assertEqual(controller.choose_interior_mode(
            receiving_pressure=0.5, **common), MODE_2C)
        self.assertEqual(controller.choose_interior_mode(
            receiving_pressure=0.9, **common), MODE_1C)
        # Total-system inventory is a diagnostic, not a Threshold input.
        self.assertEqual(controller.choose_interior_mode(
            receiving_pressure=0.2,
            system_buffer_free_bu=0.0,
            wet_bin_inventory_bu=0.0), MODE_2C_1GC)

    def test_reactive_rule_uses_physical_headroom_and_current_rates(self):
        c = SimulationConfig()
        controller = ReactiveController(c)
        self.assertEqual(
            controller.choose_interior_mode(**self.current_state(c)),
            MODE_2C_1GC,
        )
        constrained = self.current_state(
            c, system_buffer_free_bu=2000.0, field_mobile_free_bu=2000.0)
        self.assertEqual(controller.choose_interior_mode(**constrained), MODE_1C)
        transport_constrained = self.current_state(
            c, transport_rate_buph=1000.0, field_mobile_free_bu=100.0)
        self.assertEqual(
            controller.choose_interior_mode(**transport_constrained), MODE_1C)

    def test_reactive_is_invariant_to_future_trajectory(self):
        c = SimulationConfig()
        controller = ReactiveController(c)
        state = self.current_state(c)
        dry_future = (ForecastSegment(
            "F1", INTERIOR, "rectangular", 10000.0, 18.0, 5000.0),)
        wet_future = (ForecastSegment(
            "F1", INTERIOR, "rectangular", 10000.0, 22.0, 1000.0),)
        self.assertEqual(
            controller.choose_interior_mode(
                forecast_segments=dry_future, **state),
            controller.choose_interior_mode(
                forecast_segments=wet_future, **state),
        )

    def test_reactive_uses_committed_semi_return_etas(self):
        c = SimulationConfig()
        controller = ReactiveController(c)
        state = self.current_state(
            c,
            field_mobile_free_bu=1000.0,
            system_buffer_free_bu=c.total_system_storage_bu,
            active_dryer_rate_buph=4000.0,
            protection_h=1.0,
        )
        self.assertEqual(
            controller.choose_interior_mode(**state), MODE_1C)
        state["known_semi_return_relief"] = (
            (0.25, 1000.0),
            (0.50, 1000.0),
            (0.75, 1000.0),
        )
        self.assertEqual(
            controller.choose_interior_mode(**state), MODE_2C_1GC)

    def test_predictive_uses_forced_time_inside_completion_band(self):
        controller = PredictiveController(SimulationConfig())
        resume = MacroAction(MODE_2C_1GC)
        wait = MacroAction(MODE_2C_1GC, 6.0)
        outcomes = {
            resume: ForecastOutcome(
                100.0, 110.0, 10.0, 3, 0.0, 0, 10.0, 3),
            wait: ForecastOutcome(
                100.0, 110.0, 2.0, 1, 6.0, 1, 2.0, 1),
        }
        with patch.object(
                controller, "_rollout",
                side_effect=lambda action, **_: outcomes[action]):
            self.assertEqual(controller._preferred_macro_action(
                (resume, wait), phase=INTERIOR,
                geometry="rectangular"), wait)

    def test_predictive_builds_minimum_and_relief_stop_candidates(self):
        c = SimulationConfig()
        controller = PredictiveController(c)
        relief = {
            MODE_1C: 6.0,
            MODE_2C: 7.25,
            MODE_2C_1GC: None,
        }
        with patch.object(
                controller, "_relief_wait_h",
                side_effect=lambda mode, **_: relief[mode]):
            candidates = controller._macro_candidates(
                INTERIOR_MODES, in_stop=False, stop_elapsed_h=0.0,
                stop_eligible=True)
        self.assertIn(MacroAction(MODE_1C, 6.0), candidates)
        self.assertIn(MacroAction(MODE_2C, 7.25), candidates)
        self.assertIn(MacroAction(MODE_2C_1GC, 6.0), candidates)
        self.assertNotIn(MacroAction(MODE_2C, 6.5), candidates)

    def test_predictive_candidate_modes_are_pressure_guided(self):
        c = SimulationConfig()
        controller = PredictiveController(c)
        low = controller._macro_candidates(
            INTERIOR_MODES, in_stop=False, stop_elapsed_h=0.0,
            stop_eligible=False, receiving_pressure=0.1,
            wet_bin_inventory_bu=0.0,
        )
        high = controller._macro_candidates(
            INTERIOR_MODES, in_stop=False, stop_elapsed_h=0.0,
            stop_eligible=False, receiving_pressure=0.9,
            wet_bin_inventory_bu=0.0,
        )
        self.assertEqual(
            {action.mode for action in low}, {MODE_2C, MODE_2C_1GC})
        self.assertEqual(
            {action.mode for action in high}, {MODE_1C, MODE_2C})

    def test_predictive_tail_is_physically_simulated_past_horizon(self):
        c = replace(
            SimulationConfig(),
            field_specs=(FieldSpec("F1", 100.0, "rectangular", 5.0),),
            prediction_horizon_h=MIN_PREDICTION_HORIZON_H,
            max_simulation_h=200.0,
        )
        controller = PredictiveController(c)
        downstream = Downstream(c)
        segment = ForecastSegment(
            "F1", INTERIOR, "rectangular", 50000.0, 22.0, 3000.0,
            one_way_travel_min=5.0,
        )
        outcome = controller._rollout(
            MacroAction(MODE_2C_1GC),
            forecast_segments=(segment,),
            productive_since_stop_h=0.0,
            stop_elapsed_h=0.0,
            in_stop=False,
            mode_switches_used=0,
            response_time_h=0.5,
            current_mode=MODE_2C_1GC,
            transport_forecast=downstream,
        )
        self.assertGreater(
            outcome.harvest_completion_h, c.prediction_horizon_h)
        self.assertGreaterEqual(
            outcome.system_completion_h, outcome.harvest_completion_h)
        self.assertAlmostEqual(downstream.time_h, 0.0)
        self.assertAlmostEqual(downstream.unprocessed_grain_bu, 0.0)

    def test_threshold_tail_matches_real_threshold_simulation(self):
        spec = FieldSpec(
            "F1", 50.0, "rectangular", 5.0,
            moisture_pct=18.0,
        )
        c = replace(
            SimulationConfig(),
            field_specs=(spec,),
            prediction_horizon_h=MIN_PREDICTION_HORIZON_H,
            max_simulation_h=100.0,
        )
        real = Simulator(c, "threshold", (18.0,)).run()
        factor = c.opening_capacity_factor("rectangular")
        opening_bu = (
            spec.area_ac * c.opening_area_fraction * c.yield_bu_per_ac)
        segments = (
            ForecastSegment(
                "F1", OPENING, "rectangular", opening_bu, 18.0,
                6000.0, spec.one_way_travel_min, factor),
            ForecastSegment(
                "F1", INTERIOR, "rectangular",
                spec.area_ac * c.yield_bu_per_ac - opening_bu,
                18.0, 6000.0, spec.one_way_travel_min),
        )
        forecast = PredictiveController(c)._rollout(
            MacroAction(MODE_2C),
            forecast_segments=segments,
            productive_since_stop_h=0.0,
            stop_elapsed_h=0.0,
            in_stop=False,
            mode_switches_used=0,
            response_time_h=0.5,
            current_mode=MODE_2C,
            transport_forecast=Downstream(c),
        )
        self.assertAlmostEqual(
            forecast.harvest_completion_h,
            float(real.metrics["harvest_completion_time_h"]),
            delta=c.forecast_step_h,
        )
        self.assertAlmostEqual(
            forecast.system_completion_h,
            float(real.metrics["system_completion_time_h"]),
            delta=c.forecast_step_h,
        )

    def test_predictive_rejects_stop_exceeding_dryer_idle_limit(self):
        controller = PredictiveController(SimulationConfig())
        resume = MacroAction(MODE_2C)
        wait = MacroAction(MODE_2C, 6.0)
        outcomes = {
            resume: ForecastOutcome(
                100.0, 110.0, 10.0, 3, 0.0, 0, 10.0, 3),
            wait: ForecastOutcome(
                100.0, 110.0, 0.0, 0, 6.0, 1, 0.0, 0,
                dryer_idle_during_planned_stop_h=2.0,
                maximum_dryer_idle_episode_h=1.01),
        }
        with patch.object(
                controller, "_rollout",
                side_effect=lambda action, **_: outcomes[action]):
            self.assertEqual(controller._preferred_macro_action(
                (resume, wait), phase=INTERIOR,
                geometry="rectangular"), resume)
        selected_rows = [row for row in controller.decision_history
                         if row["selected"]]
        self.assertEqual(selected_rows[-1]["action"], resume.label)
        self.assertEqual(
            next(row for row in controller.decision_history
                 if row["action"] == wait.label)["eligible"], 0)

    def test_predictive_accepts_one_hour_dryer_idle_boundary(self):
        controller = PredictiveController(SimulationConfig())
        resume = MacroAction(MODE_2C)
        wait = MacroAction(MODE_2C, 6.0)
        outcomes = {
            resume: ForecastOutcome(
                100.0, 110.0, 10.0, 3, 0.0, 0, 10.0, 3),
            wait: ForecastOutcome(
                100.0, 110.0, 0.0, 0, 6.0, 1, 0.0, 0,
                maximum_dryer_idle_episode_h=1.0),
        }
        with patch.object(
                controller, "_rollout",
                side_effect=lambda action, **_: outcomes[action]):
            self.assertEqual(controller._preferred_macro_action(
                (resume, wait), phase=INTERIOR,
                geometry="rectangular"), wait)

    def test_predictive_does_not_stop_without_forced_reduction(self):
        controller = PredictiveController(SimulationConfig())
        resume = MacroAction(MODE_2C)
        wait = MacroAction(MODE_2C, 6.0)
        outcomes = {
            resume: ForecastOutcome(
                100.0, 110.0, 0.0, 0, 0.0, 0, 0.0, 0),
            wait: ForecastOutcome(
                106.0, 110.0, 0.0, 0, 6.0, 1, 0.0, 0),
        }
        with patch.object(
                controller, "_rollout",
                side_effect=lambda action, **_: outcomes[action]):
            self.assertEqual(controller._preferred_macro_action(
                (resume, wait), phase=INTERIOR,
                geometry="rectangular"), resume)
        wait_row = next(row for row in controller.decision_history
                        if row["action"] == wait.label)
        self.assertEqual(wait_row["eligible"], 0)


class SimulatorTests(unittest.TestCase):
    def compact_config(self, **updates):
        values = {
            "field_specs": (
                FieldSpec("A", 10.0, "rectangular", 2.0),
                FieldSpec("B", 12.0, "irregular", 3.0),
            ),
            "prediction_horizon_h": MIN_PREDICTION_HORIZON_H,
            "stop_work_requirement_h": 1.0,
            "minimum_stop_h": 1.0,
            "minimum_interior_mode_dwell_h": 0.1,
            "field_switch_time_h": 0.05,
            "max_simulation_h": 100.0,
        }
        values.update(updates)
        return replace(SimulationConfig(), **values)

    def assert_mass_balance(self, result, config):
        for row in result.time_series:
            semi_grain = sum(row[f"semi_{index}_load_bu"]
                             for index in range(1, config.semi_count + 1))
            stored = (row["combine_tank_bu"]
                      + row["grain_cart_inventory_bu"]
                      + semi_grain
                      + row["wet_bin_inventory_bu"])
            self.assertAlmostEqual(
                row["cumulative_grain_generated_bu"],
                row["cumulative_grain_dried_bu"] + stored,
                places=5,
            )
            self.assertEqual(row["receiving_pit_inventory_bu"], 0.0)
            self.assertLessEqual(row["grain_cart_inventory_bu"],
                                 config.total_grain_cart_storage_bu + 1e-6)
            self.assertLessEqual(row["wet_bin_inventory_bu"],
                                 config.wet_bin_capacity_bu + 1e-6)

    def test_partial_combine_block_counts_blocked_machine_time(self):
        rows = [{
            "time_h": 1.0,
            "duration_h": 1.0,
            "combine_state": "HARVESTING",
            "operating_mode": MODE_2C,
            "productive_combine_count": 1,
            "productive_grain_cart_count": 0,
        }]
        daily = calculate_daily_machine_hours(rows, semi_count=0)
        self.assertAlmostEqual(
            daily[0]["combine_productive_machine_h"], 1.0)
        self.assertAlmostEqual(
            daily[0]["combine_blocked_machine_h"], 1.0)
        self.assertAlmostEqual(
            daily[0]["combine_occupied_machine_h"], 2.0)

    def test_revised_baseline_parameters_and_provisional_order(self):
        c = SimulationConfig()
        self.assertEqual(c.opening_area_fraction, 0.15)
        self.assertEqual(c.interior_rate_buph(MODE_1C), 1600.0)
        self.assertEqual(c.interior_rate_buph(MODE_2C), 3200.0)
        self.assertEqual(c.interior_rate_buph(MODE_2C_1GC), 4000.0)
        self.assertEqual(c.wet_bin_capacity_bu, 10000.0)
        self.assertEqual(c.grain_cart_capacity_bu, 1000.0)
        self.assertEqual(c.semi_count, 3)
        self.assertEqual(c.n_combines, 2)
        self.assertEqual(c.n_grain_carts, 1)
        self.assertEqual(c.max_interior_mode_switches, 2)
        self.assertAlmostEqual(c.control_decision_interval_h, 1 / 60)
        self.assertAlmostEqual(c.predictive_decision_interval_h, 0.5)
        self.assertEqual(c.predictive_beam_width, 50)
        self.assertEqual(c.receiving_pit_capacity_bu, 0.0)
        self.assertEqual(c.prediction_horizon_h, 48.0)
        self.assertEqual(c.stop_work_requirement_h, 6.0)
        self.assertEqual(c.minimum_stop_h, 6.0)
        self.assertEqual(c.stop_signal_confirmation_h, 0.5)
        self.assertEqual(c.maximum_dryer_idle_episode_h, 1.0)
        self.assertEqual(c.harvest_completion_tolerance_h, 0.5)
        self.assertEqual(c.minimum_field_engaged_reduction_h, 5.0)
        self.assertEqual(
            tuple(c.field_specs[index].field_id for index in c.field_order_indices()),
            ("F3", "F6", "F2", "F5", "F7", "F8", "F9", "F10", "F1", "F4"),
        )
        simulator = Simulator(c)
        self.assertTrue(all(
            abs(field.opening_remaining_ac - 0.15 * field.total_area_ac) < 1e-9
            for field in simulator.fields))

    def test_opening_modes_preserve_geometry_ratios(self):
        c = SimulationConfig()
        simulator = Simulator(c)
        irregular = simulator.fields[0]
        rectangular = simulator.fields[1]
        for mode in (MODE_1C, MODE_2C):
            self.assertAlmostEqual(
                simulator.capacity(irregular, OPENING, mode) * c.yield_bu_per_ac,
                c.interior_rate_buph(mode) * c.irregular_opening_rate_fraction,
            )
            self.assertAlmostEqual(
                simulator.capacity(rectangular, OPENING, mode) * c.yield_bu_per_ac,
                c.interior_rate_buph(mode) * c.rectangular_opening_rate_fraction,
            )

    def test_fleet_counts_control_mode_feasibility(self):
        c = self.compact_config(n_combines=1, n_grain_carts=0)
        self.assertEqual(c.feasible_modes((MODE_1C, MODE_2C)), (MODE_1C,))
        self.assertEqual(c.feasible_modes(INTERIOR_MODES), (MODE_1C,))
        result = Simulator(c, "fixed").run()
        self.assertAlmostEqual(
            result.metrics["intentional_slowdown_productive_h"], 0.0)
        self.assertTrue(all(
            mode == MODE_1C
            for sequence in result.metrics["interior_mode_sequence"].split("; ")
            for mode in sequence.split(":", 1)[1].split("->")
        ))

    def test_field_specific_properties_override_generator_defaults(self):
        field = FieldSpec(
            "custom", 10.0, "rectangular", 4.0,
            opening_area_fraction=0.2,
            opening_capacity_factor=0.75,
            yield_bu_per_ac=180.0,
            moisture_pct=19.0,
        )
        c = self.compact_config(field_specs=(field,))
        generated = Simulator(c).fields[0]
        self.assertAlmostEqual(generated.opening_area_ac, 2.0)
        self.assertAlmostEqual(generated.opening_capacity_factor, 0.75)
        self.assertAlmostEqual(generated.yield_bu_per_ac, 180.0)
        self.assertAlmostEqual(generated.moisture_pct, 19.0)

    def test_field_selector_interface_can_change_order(self):
        class ReverseSelector:
            def choose_next(self, fields, current_index):
                if (current_index is not None
                        and not fields[current_index].field_complete):
                    return current_index
                return next(
                    (index for index in reversed(range(len(fields)))
                     if not fields[index].field_complete),
                    None,
                )

        c = self.compact_config(default_field_order_rule="listed")
        result = Simulator(
            c, "fixed", field_selector=ReverseSelector()).run()
        self.assertEqual(result.metrics["field_order"], "B -> A")

    def test_four_policies_share_scenario_finish_and_conserve_mass(self):
        c = self.compact_config()
        moistures = c.sample_field_moistures()
        expected_order = " -> ".join(
            c.field_specs[index].field_id for index in c.field_order_indices())
        for policy in ("fixed", "threshold", "reactive", "predictive"):
            with self.subTest(policy=policy):
                result = Simulator(c, policy, moistures).run()
                metrics = result.metrics
                self.assertEqual(metrics["field_order"], expected_order)
                self.assertAlmostEqual(metrics["total_harvested_ac"], c.total_area_ac)
                self.assertAlmostEqual(
                    metrics["total_dried_grain_bu"], c.total_grain_bu, places=5)
                accounted = (
                    metrics["productive_harvest_time_h"]
                    + metrics["blocked_time_h"]
                    + metrics["switching_time_h"]
                    + metrics["interior_mode_switching_time_h"]
                    + metrics["planned_stop_time_h"]
                )
                self.assertAlmostEqual(
                    metrics["harvest_completion_time_h"], accounted, places=6)
                self.assertAlmostEqual(
                    metrics["field_engaged_calendar_time_h"],
                    metrics["harvest_completion_time_h"]
                    - metrics["planned_stop_time_h"],
                    places=6,
                )
                self.assertAlmostEqual(
                    metrics["field_operation_time_h"],
                    metrics["productive_harvest_time_h"]
                    + metrics["blocked_time_h"]
                    + metrics["interior_mode_switching_time_h"],
                    places=6,
                )
                self.assertAlmostEqual(metrics["mass_balance_error_bu"], 0.0)
                self.assert_mass_balance(result, c)

    def test_field_sequence_finishes_opening_then_interior(self):
        result = Simulator(self.compact_config(), "predictive").run()
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
        self.assertEqual(result.metrics["number_of_field_switches"], 1)

    def test_switch_limit_penalty_and_opening_transition_definition(self):
        c = self.compact_config()
        result = Simulator(c, "predictive").run()
        switches = result.metrics["number_of_interior_mode_switches"]
        self.assertLessEqual(
            switches, len(c.field_specs) * c.max_interior_mode_switches)
        self.assertAlmostEqual(
            result.metrics["interior_mode_switching_time_h"],
            switches * c.interior_mode_switch_time_h,
        )
        for field_id in (field.field_id for field in result.fields):
            starts = [row for row in result.time_series
                      if row["current_field"] == field_id
                      and str(row["event"]).startswith("MODE_SWITCH_START")]
            self.assertLessEqual(
                len(starts), c.max_interior_mode_switches)

    def test_stop_respects_minimum_productive_work_and_stop_duration(self):
        c = SimulationConfig()
        result = Simulator(c, "threshold").run()
        starts = [row for row in result.time_series
                  if row["combine_state"] == "STOP_START"]
        self.assertTrue(starts)
        self.assertTrue(all(
            row["productive_since_stop_h"] + c.numerical_tolerance
            >= c.stop_work_requirement_h for row in starts))
        self.assertTrue(all(
            row["stop_request_duration_h"] + c.numerical_tolerance
            >= c.stop_signal_confirmation_h for row in starts))
        self.assertGreaterEqual(
            result.metrics["minimum_observed_planned_stop_duration_h"]
            + c.numerical_tolerance,
            c.minimum_stop_h,
        )
        self.assertNotIn(
            "HARVEST_REST",
            {row["combine_state"] for row in result.time_series},
        )
        self.assertLessEqual(
            result.metrics[
                "maximum_dryer_idle_episode_during_planned_stops_h"],
            c.maximum_dryer_idle_episode_h + c.numerical_tolerance,
        )

    def test_hard_bottleneck_does_not_create_permanent_voluntary_stop(self):
        c = self.compact_config(
            field_specs=(
                FieldSpec("A", 30.0, "rectangular", 2.0),
                FieldSpec("B", 30.0, "irregular", 3.0),
            ),
            wet_bin_capacity_bu=5000.0,
            max_simulation_h=100.0,
        )
        moistures = c.sample_field_moistures()
        for policy in ("reactive", "predictive"):
            with self.subTest(policy=policy):
                result = Simulator(c, policy, moistures).run()
                self.assertAlmostEqual(
                    result.metrics["total_harvested_ac"], c.total_area_ac)
                self.assertLess(
                    result.metrics["harvest_completion_time_h"],
                    c.max_simulation_h,
                )

    def test_transport_event_times_use_each_field_distance(self):
        result = Simulator(SimulationConfig(), "fixed").run()
        departures = {(e["semi_id"], e["trip"]): e
                      for e in result.transport_events
                      if e["event"] == "DEPART_FIELD"}
        arrivals = {(e["semi_id"], e["trip"]): e
                    for e in result.transport_events
                    if e["event"] == "ARRIVE_GC"}
        finishes = {(e["semi_id"], e["trip"]): e
                    for e in result.transport_events
                    if e["event"] == "FINISH_UNLOAD"}
        returns = {(e["semi_id"], e["trip"]): e
                   for e in result.transport_events
                   if e["event"] == "RETURN_FIELD"}
        for key, departure in departures.items():
            travel_h = departure["one_way_travel_min"] / 60.0
            self.assertAlmostEqual(
                arrivals[key]["time_h"] - departure["time_h"], travel_h)
            self.assertAlmostEqual(
                returns[key]["time_h"] - finishes[key]["time_h"], travel_h)

    def test_machine_hour_accounting(self):
        c = SimulationConfig()
        result = Simulator(c, "fixed").run()
        daily = calculate_daily_machine_hours(result.time_series, c.semi_count)
        summary = summarize_machine_hours(daily)
        self.assertGreater(len(daily), 1)
        self.assertAlmostEqual(
            summary["combine_productive_machine_h"],
            sum(
                row["duration_h"] * row["productive_combine_count"]
                for row in result.time_series
                if row["combine_state"] == "HARVESTING"),
        )
        self.assertAlmostEqual(
            summary["grain_cart_productive_machine_h"],
            result.metrics["interior_2c_1gc_productive_h"],
        )
        for resource in ("combine", "grain_cart", "semi", "total"):
            self.assertLessEqual(summary[f"{resource}_active_machine_h"],
                                 summary[f"{resource}_occupied_machine_h"])
        for key, value in summary.items():
            self.assertAlmostEqual(value, result.metrics[key])

    def test_invalid_configurations(self):
        cases = [
            {"n_semis": 0},
            {"n_combines": 0},
            {"n_grain_carts": -1},
            {"grain_leg_rate_buph": 0.0},
            {"wet_bin_capacity_bu": -1.0},
            {"receiving_pit_capacity_bu": -1.0},
            {"opening_area_fraction": 0.0},
            {"random_seed": True},
            {"moisture_min_pct": 22.0},
            {"dryer_intercept_buph": 2200.0},
            {"combine_harvest_capacity_buph": 0.0},
            {"max_interior_mode_switches": -1},
            {"minimum_interior_mode_dwell_h": 0.0},
            {"interior_mode_switch_time_h": -0.1},
            {"forced_block_ignore_h": -0.1},
            {"planned_stop_extension_options_h": ()},
            {"prediction_horizon_h": MIN_PREDICTION_HORIZON_H - 0.01},
            {"predictive_decision_interval_h": 0.0},
            {"predictive_beam_width": 0},
            {"stop_signal_confirmation_h": 0.0},
            {"maximum_dryer_idle_episode_h": 0.0},
            {"control_decision_interval_h": 0.0},
            {"reactive_mode_horizon_h": 0.0},
            {"default_field_order_rule": "unknown"},
            {"field_specs": ()},
            {"field_specs": (FieldSpec("F1", 50.0, "round", 5.0),)},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(SimulationConfig(), **values)


if __name__ == "__main__":
    unittest.main()
