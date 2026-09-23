#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path

from harvest_sim import (
    DAILY_MACHINE_HOUR_FIELDS,
    SimulationConfig,
    Simulator,
    calculate_daily_machine_hours,
)
from harvest_sim.plotting import plot_result


POLICIES = ("fixed", "threshold", "reactive", "predictive",
            "sequential", "adaptive")


def main() -> None:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(
        description="Run one harvest/downstream coordination simulation")
    parser.add_argument("--policy", choices=POLICIES, default="fixed")
    parser.add_argument("--grain-leg-rate", type=float,
                        default=defaults.grain_leg_rate_buph)
    parser.add_argument("--wet-bin-capacity", type=float,
                        default=defaults.wet_bin_capacity_bu)
    parser.add_argument("--combine-count", type=int, default=defaults.n_combines)
    parser.add_argument("--grain-cart-count", type=int,
                        default=defaults.n_grain_carts)
    parser.add_argument(
        "--use-field-buffer", action=argparse.BooleanOptionalAction,
        default=defaults.use_field_buffer,
        help="System-wide field-buffer availability for all policies")
    parser.add_argument("--semi-count", type=int, default=defaults.n_semis)
    parser.add_argument("--combine-capacity", type=float,
                        default=defaults.combine_harvest_capacity_buph,
                        help="Harvest contribution per combine (bu/h)")
    parser.add_argument("--grain-cart-capacity", type=float,
                        default=defaults.grain_cart_harvest_capacity_buph,
                        help="Team harvest contribution per active cart (bu/h)")
    parser.add_argument("--seed", type=int, default=defaults.random_seed)
    parser.add_argument("--moisture-min", type=float,
                        default=defaults.moisture_min_pct)
    parser.add_argument("--moisture-max", type=float,
                        default=defaults.moisture_max_pct)
    parser.add_argument("--dryer-intercept", type=float,
                        default=defaults.dryer_intercept_buph)
    parser.add_argument("--dryer-slope", type=float,
                        default=defaults.dryer_moisture_slope_buph_per_pct)
    parser.add_argument("--opening-fraction", type=float,
                        default=defaults.opening_area_fraction)
    parser.add_argument("--mode-dwell-min", type=float,
                        default=defaults.minimum_interior_mode_dwell_h * 60.0)
    parser.add_argument("--max-mode-switches", type=int,
                        default=defaults.max_interior_mode_switches)
    parser.add_argument("--mode-switch-time-min", type=float,
                        default=defaults.interior_mode_switch_time_h * 60.0)
    parser.add_argument("--prediction-horizon-h", type=float,
                        default=defaults.prediction_horizon_h)
    parser.add_argument("--predictive-decision-interval-min", type=float,
                        default=(
                            defaults.predictive_decision_interval_h * 60.0))
    parser.add_argument("--predictive-beam-width", type=int,
                        default=defaults.predictive_beam_width)
    parser.add_argument("--decision-interval-min", type=float,
                        default=defaults.control_decision_interval_h * 60.0)
    parser.add_argument("--work-before-stop-h", type=float,
                        default=defaults.stop_work_requirement_h)
    parser.add_argument("--minimum-stop-h", type=float,
                        default=defaults.minimum_stop_h)
    parser.add_argument("--stop-confirmation-min", type=float,
                        default=defaults.stop_signal_confirmation_h * 60.0)
    parser.add_argument("--maximum-dryer-idle-h", type=float,
                        default=defaults.maximum_dryer_idle_episode_h)
    parser.add_argument("--forced-block-ignore-h", type=float,
                        default=defaults.forced_block_ignore_h)
    parser.add_argument("--harvest-tolerance-h", type=float,
                        default=defaults.harvest_completion_tolerance_h)
    parser.add_argument("--minimum-field-reduction-h", type=float,
                        default=defaults.minimum_field_engaged_reduction_h)
    parser.add_argument("--field-order-rule",
                        choices=["far_to_near", "near_to_far", "listed"],
                        default=defaults.default_field_order_rule)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/current"))
    parser.add_argument("--name-suffix", default="",
                        help="Optional safe suffix for output filenames")
    args = parser.parse_args()

    config = SimulationConfig(
        grain_leg_rate_buph=args.grain_leg_rate,
        wet_bin_capacity_bu=args.wet_bin_capacity,
        n_combines=args.combine_count,
        n_grain_carts=args.grain_cart_count,
        use_field_buffer=args.use_field_buffer,
        n_semis=args.semi_count,
        combine_harvest_capacity_buph=args.combine_capacity,
        grain_cart_harvest_capacity_buph=args.grain_cart_capacity,
        random_seed=args.seed,
        moisture_min_pct=args.moisture_min,
        moisture_max_pct=args.moisture_max,
        dryer_intercept_buph=args.dryer_intercept,
        dryer_moisture_slope_buph_per_pct=args.dryer_slope,
        opening_area_fraction=args.opening_fraction,
        minimum_interior_mode_dwell_h=args.mode_dwell_min / 60.0,
        max_interior_mode_switches=args.max_mode_switches,
        interior_mode_switch_time_h=args.mode_switch_time_min / 60.0,
        prediction_horizon_h=args.prediction_horizon_h,
        predictive_decision_interval_h=(
            args.predictive_decision_interval_min / 60.0),
        predictive_beam_width=args.predictive_beam_width,
        control_decision_interval_h=args.decision_interval_min / 60.0,
        stop_work_requirement_h=args.work_before_stop_h,
        minimum_stop_h=args.minimum_stop_h,
        stop_signal_confirmation_h=args.stop_confirmation_min / 60.0,
        maximum_dryer_idle_episode_h=args.maximum_dryer_idle_h,
        forced_block_ignore_h=args.forced_block_ignore_h,
        harvest_completion_tolerance_h=args.harvest_tolerance_h,
        minimum_field_engaged_reduction_h=args.minimum_field_reduction_h,
        default_field_order_rule=args.field_order_rule,
    )
    field_moistures = config.sample_field_moistures()
    result = Simulator(config, args.policy, field_moistures).run()
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    scenario = (
        f"fields{len(config.field_specs)}_seed{config.random_seed}"
        f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
        f"_wetbin{config.wet_bin_capacity_bu:g}_leg{config.grain_leg_rate_buph:g}"
        f"_fleet{config.n_combines}C-{config.n_grain_carts}GC-"
        f"{config.n_semis}S_order{config.default_field_order_rule}"
        f"_work{config.stop_work_requirement_h:g}h_stop{config.minimum_stop_h:g}h"
        f"_decision{config.control_decision_interval_h * 60:g}min"
        f"_preddecision{config.predictive_decision_interval_h * 60:g}min"
        f"_forecast{config.prediction_horizon_h:g}h")
    if suffix:
        scenario += f"_{suffix}"
    scenario_dir = args.output_dir / scenario
    result_dir = scenario_dir / result.policy
    result_dir.mkdir(parents=True, exist_ok=True)

    with (scenario_dir / "field_scenario.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "field_id", "area_ac", "opening_area_ac", "opening_area_fraction",
            "geometry", "opening_capacity_factor", "yield_bu_per_ac",
            "one_way_travel_min", "moisture_pct", "dryer_rate_buph",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for field in result.fields:
            writer.writerow({
                "field_id": field.field_id,
                "area_ac": field.area_ac,
                "opening_area_ac": field.opening_area_ac,
                "opening_area_fraction": field.opening_area_fraction,
                "geometry": field.geometry,
                "opening_capacity_factor": field.opening_capacity_factor,
                "yield_bu_per_ac": field.yield_bu_per_ac,
                "one_way_travel_min": field.one_way_travel_min,
                "moisture_pct": field.moisture_pct,
                "dryer_rate_buph": config.dryer_rate_for_moisture(
                    field.moisture_pct),
            })

    with (result_dir / "metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
    with (result_dir / "timeseries.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.state_snapshots[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.state_snapshots)
    daily = calculate_daily_machine_hours(result.time_series, config.n_semis)
    with (result_dir / "daily_machine_hours.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_MACHINE_HOUR_FIELDS,
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(daily)
    if result.transport_events:
        with (result_dir / "transport_events.csv").open(
                "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=result.transport_events[0].keys(),
                lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.transport_events)
    if result.controller_decisions:
        with (result_dir / "controller_decisions.csv").open(
                "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=result.controller_decisions[0].keys(),
                lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.controller_decisions)

    plot_result(result, config, result_dir / "overview.svg")
    plot_result(result, config, result_dir / "first_3h.svg", end_time_h=3.0)
    print(json.dumps(result.metrics, indent=2))
    print(f"Wrote results to {result_dir}")


if __name__ == "__main__":
    main()
