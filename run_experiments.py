#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

from harvest_sim import (DAILY_MACHINE_HOUR_FIELDS, SimulationConfig, Simulator,
                         calculate_daily_machine_hours)
from harvest_sim.plotting import plot_result


def main() -> None:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(description="Compare harvest policies")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/current"))
    parser.add_argument("--harvest-work-h", type=float,
                        default=defaults.harvest_work_window_h)
    parser.add_argument("--harvest-rest-h", type=float,
                        default=defaults.harvest_rest_window_h)
    parser.add_argument("--prediction-horizon-h", type=float,
                        default=defaults.interior_prediction_horizon_h)
    parser.add_argument("--rest-shortage-weight", type=float,
                        default=defaults.rest_inventory_shortage_weight)
    parser.add_argument("--rest-coverage-transport-fraction", type=float,
                        default=defaults.rest_coverage_transport_fraction)
    args = parser.parse_args()
    config = SimulationConfig(
        harvest_work_window_h=args.harvest_work_h,
        harvest_rest_window_h=args.harvest_rest_h,
        interior_prediction_horizon_h=args.prediction_horizon_h,
        rest_inventory_shortage_weight=args.rest_shortage_weight,
        rest_coverage_transport_fraction=args.rest_coverage_transport_fraction,
    )
    field_moistures = config.sample_field_moistures()
    scenario = (f"fields{len(config.field_specs)}_seed{config.random_seed}"
                f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
                f"_wetbin{config.wet_bin_capacity_bu:g}"
                f"_leg{config.grain_leg_rate_buph:g}"
                f"_harvest{config.harvest_work_window_h:g}h"
                f"_rest{config.harvest_rest_window_h:g}h"
                f"_forecast{config.interior_prediction_horizon_h:g}h"
                f"_restcoverage{config.rest_inventory_shortage_weight:g}")
    output = args.output_dir / scenario
    output.mkdir(parents=True, exist_ok=True)
    results = [Simulator(config, policy, field_moistures).run()
               for policy in ("sequential", "reactive", "adaptive")]
    rows = [result.metrics for result in results]

    with (output / "field_scenario.csv").open(
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

    with (output / "policy_comparison.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    machine_hour_fields = [
        "combine_productive_machine_h", "combine_switching_machine_h",
        "combine_active_machine_h", "combine_blocked_machine_h",
        "combine_occupied_machine_h", "grain_cart_productive_machine_h",
        "grain_cart_active_machine_h", "grain_cart_blocked_machine_h",
        "grain_cart_occupied_machine_h", "semi_driving_machine_h",
        "semi_unloading_machine_h", "semi_gc_waiting_machine_h",
        "semi_field_standby_machine_h", "semi_active_machine_h",
        "semi_occupied_machine_h", "total_active_machine_h",
        "total_occupied_machine_h",
    ]
    with (output / "machine_hours_summary.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["policy", *machine_hour_fields],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows({"policy": row["policy"], **{
            key: row[key] for key in machine_hour_fields}}
            for row in rows)

    with (output / "daily_machine_hours_comparison.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["policy", *DAILY_MACHINE_HOUR_FIELDS],
            lineterminator="\n")
        writer.writeheader()
        for result in results:
            for daily_row in calculate_daily_machine_hours(
                    result.time_series, config.semi_count):
                writer.writerow({"policy": result.policy, **daily_row})

    for result in results:
        policy_output = output / result.policy
        policy_output.mkdir(parents=True, exist_ok=True)
        plot_result(
            result,
            config,
            policy_output / "overview.svg",
        )

    keys = [
        "policy", "field_order", "harvest_completion_time_h",
        "system_completion_time_h", "blocked_time_h",
        "scheduled_harvest_rest_time_h",
        "total_active_machine_h", "total_occupied_machine_h",
        "intentional_slowdown_productive_h", "number_of_interior_mode_switches",
        "interior_mode_sequence", "combine_utilization",
        "mean_wet_bin_inventory_at_rest_start_bu",
        "mean_rest_dryer_inventory_target_bu",
        "mean_rest_inventory_target_shortfall_bu",
        "mean_rest_inventory_target_excess_bu",
        "mean_wet_bin_inventory_at_rest_end_bu",
        "dryer_idle_time_during_sampled_rests_h",
        "dryer_utilization_during_sampled_rests",
        "maximum_wet_bin_inventory_bu", "number_of_semi_trips",
    ]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(f"{row[key]:.4f}" if isinstance(row[key], float)
                        else str(row[key]) for key in keys))


if __name__ == "__main__":
    main()
