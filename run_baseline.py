#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path

from harvest_sim import (DAILY_MACHINE_HOUR_FIELDS, SimulationConfig, Simulator,
                         calculate_daily_machine_hours)
from harvest_sim.plotting import plot_result


def main() -> None:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(description="Run one harvest/downstream simulation")
    parser.add_argument(
        "--policy", choices=["sequential", "reactive", "adaptive"],
        default="sequential")
    parser.add_argument("--grain-leg-rate", type=float, default=defaults.grain_leg_rate_buph)
    parser.add_argument("--wet-bin-capacity", type=float, default=defaults.wet_bin_capacity_bu)
    parser.add_argument("--seed", type=int, default=defaults.random_seed)
    parser.add_argument("--moisture-min", type=float, default=defaults.moisture_min_pct)
    parser.add_argument("--moisture-max", type=float, default=defaults.moisture_max_pct)
    parser.add_argument("--dryer-intercept", type=float, default=defaults.dryer_intercept_buph)
    parser.add_argument("--dryer-slope", type=float,
                        default=defaults.dryer_moisture_slope_buph_per_pct)
    parser.add_argument("--opening-rect-2c-rate", type=float,
                        default=defaults.rectangular_opening_2c_rate_buph)
    parser.add_argument("--opening-irregular-2c-rate", type=float,
                        default=defaults.irregular_opening_2c_rate_buph)
    parser.add_argument("--interior-1c-rate", type=float,
                        default=defaults.interior_1c_rate_buph)
    parser.add_argument("--interior-2c-rate", type=float,
                        default=defaults.interior_2c_rate_buph)
    parser.add_argument("--interior-2c-1gc-rate", type=float,
                        default=defaults.interior_2c_1gc_rate_buph)
    parser.add_argument("--mode-dwell-min", type=float,
                        default=defaults.minimum_interior_mode_dwell_h * 60.0)
    parser.add_argument("--max-mode-switches", type=int,
                        default=defaults.max_interior_mode_switches)
    parser.add_argument("--mode-switch-time-min", type=float,
                        default=defaults.interior_mode_switch_time_h * 60.0)
    parser.add_argument("--prediction-horizon-h", type=float,
                        default=defaults.interior_prediction_horizon_h)
    parser.add_argument("--rest-shortage-weight", type=float,
                        default=defaults.rest_inventory_shortage_weight)
    parser.add_argument("--rest-coverage-transport-fraction", type=float,
                        default=defaults.rest_coverage_transport_fraction)
    parser.add_argument("--harvest-work-h", type=float,
                        default=defaults.harvest_work_window_h)
    parser.add_argument("--harvest-rest-h", type=float,
                        default=defaults.harvest_rest_window_h)
    parser.add_argument("--output-dir", type=Path, default=Path("results/current"))
    parser.add_argument("--name-suffix", default="",
                        help="Optional safe suffix for output filenames")
    args = parser.parse_args()

    config = SimulationConfig(
        grain_leg_rate_buph=args.grain_leg_rate,
        wet_bin_capacity_bu=args.wet_bin_capacity,
        random_seed=args.seed,
        moisture_min_pct=args.moisture_min,
        moisture_max_pct=args.moisture_max,
        dryer_intercept_buph=args.dryer_intercept,
        dryer_moisture_slope_buph_per_pct=args.dryer_slope,
        rectangular_opening_2c_rate_buph=args.opening_rect_2c_rate,
        irregular_opening_2c_rate_buph=args.opening_irregular_2c_rate,
        interior_1c_rate_buph=args.interior_1c_rate,
        interior_2c_rate_buph=args.interior_2c_rate,
        interior_2c_1gc_rate_buph=args.interior_2c_1gc_rate,
        minimum_interior_mode_dwell_h=args.mode_dwell_min / 60.0,
        max_interior_mode_switches=args.max_mode_switches,
        interior_mode_switch_time_h=args.mode_switch_time_min / 60.0,
        interior_prediction_horizon_h=args.prediction_horizon_h,
        rest_inventory_shortage_weight=args.rest_shortage_weight,
        rest_coverage_transport_fraction=args.rest_coverage_transport_fraction,
        harvest_work_window_h=args.harvest_work_h,
        harvest_rest_window_h=args.harvest_rest_h,
    )
    field_moistures = config.sample_field_moistures()
    result = Simulator(config, args.policy, field_moistures).run()
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    scenario = (f"fields{len(config.field_specs)}_seed{config.random_seed}"
                f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
                f"_wetbin{args.wet_bin_capacity:g}_leg{args.grain_leg_rate:g}"
                f"_harvest{args.harvest_work_h:g}h_rest{args.harvest_rest_h:g}h"
                f"_forecast{args.prediction_horizon_h:g}h"
                f"_restcoverage{args.rest_shortage_weight:g}")
    if suffix:
        scenario += f"_{suffix}"
    scenario_dir = args.output_dir / scenario
    result_dir = scenario_dir / args.policy
    result_dir.mkdir(parents=True, exist_ok=True)

    with (scenario_dir / "field_scenario.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        fieldnames = ["field_id", "area_ac", "geometry", "one_way_travel_min",
                      "moisture_pct", "dryer_rate_buph"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for spec, moisture in zip(config.field_specs, field_moistures):
            writer.writerow({
                "field_id": spec.field_id,
                "area_ac": spec.area_ac,
                "geometry": spec.geometry,
                "one_way_travel_min": spec.one_way_travel_min,
                "moisture_pct": moisture,
                "dryer_rate_buph": config.dryer_rate_for_moisture(moisture),
            })

    with (result_dir / "metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
    with (result_dir / "timeseries.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.time_series[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.time_series)
    daily_machine_hours = calculate_daily_machine_hours(
        result.time_series, config.semi_count)
    with (result_dir / "daily_machine_hours.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_MACHINE_HOUR_FIELDS,
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(daily_machine_hours)
    with (result_dir / "transport_events.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(result.transport_events[0].keys()) if result.transport_events else []
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.transport_events)

    plot_result(result, config, result_dir / "overview.svg")
    plot_result(result, config, result_dir / "first_3h.svg", end_time_h=3.0)
    print(json.dumps(result.metrics, indent=2))


if __name__ == "__main__":
    main()
