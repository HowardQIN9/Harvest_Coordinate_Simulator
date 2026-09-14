#!/usr/bin/env python3
import argparse
import csv
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from harvest_sim import (
    DAILY_MACHINE_HOUR_FIELDS,
    SimulationConfig,
    Simulator,
    calculate_daily_machine_hours,
)
from harvest_sim.plotting import plot_result


POLICIES = ("fixed", "threshold", "reactive", "predictive")


def scenario_name(config: SimulationConfig, tag: str = "") -> str:
    name = (
        f"fields{len(config.field_specs)}_seed{config.random_seed}"
        f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
        f"_wetbin{config.wet_bin_capacity_bu:g}_leg{config.grain_leg_rate_buph:g}"
        f"_semis{config.semi_count}_order{config.default_field_order_rule}"
        f"_work{config.stop_work_requirement_h:g}h_stop{config.minimum_stop_h:g}h"
        f"_decision{config.control_decision_interval_h * 60:g}min"
        f"_forecast{config.prediction_horizon_h:g}h")
    return f"{name}_{tag}" if tag else name


def run_matched_scenario(config: SimulationConfig, output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    field_moistures = config.sample_field_moistures()
    results = [Simulator(config, policy, field_moistures).run()
               for policy in POLICIES]
    rows = [result.metrics for result in results]

    with (output / "scenario_config.json").open(
            "w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)

    with (output / "field_scenario.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        fieldnames = ["field_id", "area_ac", "opening_area_ac", "geometry",
                      "one_way_travel_min", "moisture_pct", "dryer_rate_buph"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for spec, moisture in zip(config.field_specs, field_moistures):
            writer.writerow({
                "field_id": spec.field_id,
                "area_ac": spec.area_ac,
                "opening_area_ac": config.opening_area_ac(spec.area_ac),
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

    machine_fields = list(DAILY_MACHINE_HOUR_FIELDS[3:])
    with (output / "machine_hours_summary.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["policy", *machine_fields],
                                lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({"policy": row["policy"],
                             **{key: row[key] for key in machine_fields}})

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
        with (policy_output / "metrics.json").open(
                "w", encoding="utf-8") as handle:
            json.dump(result.metrics, handle, indent=2)
        with (policy_output / "timeseries.csv").open(
                "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=result.time_series[0].keys(),
                lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.time_series)
        if result.transport_events:
            with (policy_output / "transport_events.csv").open(
                    "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=result.transport_events[0].keys(),
                    lineterminator="\n")
                writer.writeheader()
                writer.writerows(result.transport_events)
        daily_rows = calculate_daily_machine_hours(
            result.time_series, config.semi_count)
        with (policy_output / "daily_machine_hours.csv").open(
                "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=DAILY_MACHINE_HOUR_FIELDS,
                lineterminator="\n")
            writer.writeheader()
            writer.writerows(daily_rows)
        plot_result(result, config, policy_output / "overview.svg")

    keys = [
        "policy", "harvest_completion_time_h", "system_completion_time_h",
        "forced_blocking_time_h", "planned_stop_time_h",
        "number_of_planned_stops", "total_occupied_machine_h",
        "dryer_idle_time_during_planned_stops_h",
        "number_of_interior_mode_switches", "field_order",
    ]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(f"{row[key]:.4f}" if isinstance(row[key], float)
                        else str(row[key]) for key in keys))
    return rows


def main() -> None:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(description="Compare harvest policies")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/current"))
    parser.add_argument("--semi-count", type=int, default=defaults.semi_count)
    parser.add_argument("--prediction-horizon-h", type=float,
                        default=defaults.prediction_horizon_h)
    parser.add_argument("--decision-interval-min", type=float,
                        default=defaults.control_decision_interval_h * 60.0)
    parser.add_argument("--work-before-stop-h", type=float,
                        default=defaults.stop_work_requirement_h)
    parser.add_argument("--minimum-stop-h", type=float,
                        default=defaults.minimum_stop_h)
    parser.add_argument("--field-order-rule",
                        choices=["far_to_near", "near_to_far", "listed"],
                        default=defaults.default_field_order_rule)
    parser.add_argument(
        "--mechanism", action="store_true",
        help="Run the small capacity/buffer map plus a working-window check")
    parser.add_argument(
        "--joint-capacity-mechanism", action="store_true",
        help=("Run a separate map that scales dryer, grain leg, and nominal "
              "unload service together and preserves complete traces"))
    args = parser.parse_args()

    if args.mechanism and args.joint_capacity_mechanism:
        parser.error("Choose only one mechanism experiment")

    base = SimulationConfig(
        semi_count=args.semi_count,
        prediction_horizon_h=args.prediction_horizon_h,
        control_decision_interval_h=args.decision_interval_min / 60.0,
        stop_work_requirement_h=args.work_before_stop_h,
        minimum_stop_h=args.minimum_stop_h,
        default_field_order_rule=args.field_order_rule,
    )
    if not args.mechanism and not args.joint_capacity_mechanism:
        run_matched_scenario(base, args.output_dir / scenario_name(base))
        return

    if args.joint_capacity_mechanism:
        experiment_root = args.output_dir / "joint_capacity_mechanism"
        experiment_root.mkdir(parents=True, exist_ok=True)
        experiment_rows: list[dict] = []
        for capacity_scale in (1.0, 4.0, 10.0):
            for buffer_scale in (0.5, 1.0, 2.0):
                config = replace(
                    base,
                    dryer_intercept_buph=(
                        base.dryer_intercept_buph * capacity_scale),
                    dryer_moisture_slope_buph_per_pct=(
                        base.dryer_moisture_slope_buph_per_pct
                        * capacity_scale),
                    grain_leg_rate_buph=(
                        base.grain_leg_rate_buph * capacity_scale),
                    semi_unload_time_h=(
                        base.semi_unload_time_h / capacity_scale),
                    wet_bin_capacity_bu=(
                        base.wet_bin_capacity_bu * buffer_scale),
                )
                transport_capacities = [
                    min(
                        config.semi_count * config.semi_capacity_bu
                        / (2.0 * field.one_way_travel_min / 60.0
                           + config.semi_unload_time_h),
                        config.semi_capacity_bu / config.semi_unload_time_h,
                        config.grain_leg_rate_buph,
                    )
                    for field in config.field_specs
                ]
                minimum_transport_rate = min(transport_capacities)
                mean_moisture = (
                    config.moisture_min_pct + config.moisture_max_pct) / 2.0
                mean_dryer_rate = config.dryer_rate_for_moisture(mean_moisture)
                worst_dryer_rate = config.dryer_rate_for_moisture(
                    config.moisture_max_pct)
                effective_mean_capacity = min(
                    minimum_transport_rate,
                    config.grain_leg_rate_buph,
                    mean_dryer_rate,
                )
                effective_worst_capacity = min(
                    minimum_transport_rate,
                    config.grain_leg_rate_buph,
                    worst_dryer_rate,
                )
                tag = (f"jointscale{capacity_scale:g}"
                       f"_bufferscale{buffer_scale:g}")
                rows = run_matched_scenario(
                    config, experiment_root / scenario_name(config, tag))
                for row in rows:
                    experiment_rows.append({
                        "case_family": "joint_downstream_capacity_map",
                        "joint_capacity_scale": capacity_scale,
                        "buffer_scale": buffer_scale,
                        "mean_dryer_rate_buph": mean_dryer_rate,
                        "worst_dryer_rate_buph": worst_dryer_rate,
                        "grain_leg_rate_buph_design": config.grain_leg_rate_buph,
                        "nominal_semi_unload_min": (
                            config.semi_unload_time_h * 60.0),
                        "minimum_field_transport_rate_buph": (
                            minimum_transport_rate),
                        "effective_capacity_at_mean_moisture_buph": (
                            effective_mean_capacity),
                        "effective_capacity_at_max_moisture_buph": (
                            effective_worst_capacity),
                        "max_harvest_to_effective_mean_ratio": (
                            config.interior_2c_1gc_rate_buph
                            / effective_mean_capacity),
                        "max_harvest_to_effective_worst_ratio": (
                            config.interior_2c_1gc_rate_buph
                            / effective_worst_capacity),
                        **row,
                    })

        summary_path = experiment_root / "joint_capacity_summary.csv"
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=experiment_rows[0].keys(),
                lineterminator="\n")
            writer.writeheader()
            writer.writerows(experiment_rows)
        with (experiment_root / "run_manifest.json").open(
                "w", encoding="utf-8") as handle:
            json.dump({
                "generated_at": datetime.now().astimezone().isoformat(),
                "command": "python run_experiments.py --joint-capacity-mechanism",
                "policies": list(POLICIES),
                "joint_capacity_scales": [1.0, 4.0, 10.0],
                "wet_bin_buffer_scales": [0.5, 1.0, 2.0],
                "paired_random_seed": base.random_seed,
                "notes": [
                    "The earlier dryer-only mechanism outputs were preserved.",
                    "Dryer curve and grain-leg rate are multiplied by the same scale.",
                    "Nominal semi unload time is divided by that scale because unloading occurs through the grain leg.",
                    "The 10x case is nonbinding for the realized field moistures; transport capacity is reported explicitly.",
                ],
            }, handle, indent=2)
        print(f"Wrote joint-capacity summary to {summary_path}")
        return

    # Exploratory mechanism map, not a calibrated farm sweep.  Scaling both
    # dryer parameters preserves the provisional moisture-curve shape.
    mechanism_rows: list[dict] = []
    cases: list[tuple[str, float, float, float]] = [
        ("capacity_buffer_map", dryer_scale, buffer_scale,
         base.stop_work_requirement_h)
        for dryer_scale in (1.0, 4.0, 8.0)
        for buffer_scale in (0.5, 1.0, 2.0)
    ]
    # A small one-factor check of the work-before-STOP constraint is added at
    # the middle capacity/buffer condition; duplicate cases are omitted.
    for work_h in (8.0, 12.0, 16.0):
        case = ("working_window_check", 4.0, 1.0, work_h)
        if not any(existing[1:] == case[1:] for existing in cases):
            cases.append(case)

    for family, dryer_scale, buffer_scale, work_h in cases:
        config = replace(
            base,
            dryer_intercept_buph=base.dryer_intercept_buph * dryer_scale,
            dryer_moisture_slope_buph_per_pct=(
                base.dryer_moisture_slope_buph_per_pct * dryer_scale),
            wet_bin_capacity_bu=base.wet_bin_capacity_bu * buffer_scale,
            stop_work_requirement_h=work_h,
        )
        tag = (f"dryerscale{dryer_scale:g}_bufferscale{buffer_scale:g}"
               f"_work{work_h:g}h")
        rows = run_matched_scenario(
            config, args.output_dir / "mechanism" / scenario_name(config, tag))
        mean_dryer_rate = config.dryer_rate_for_moisture(
            (config.moisture_min_pct + config.moisture_max_pct) / 2)
        for row in rows:
            mechanism_rows.append({
                "case_family": family,
                "dryer_scale": dryer_scale,
                "buffer_scale": buffer_scale,
                "work_before_stop_h": work_h,
                "mean_dryer_rate_buph": mean_dryer_rate,
                "max_harvest_to_mean_dryer_ratio": (
                    config.interior_2c_1gc_rate_buph / mean_dryer_rate),
                **row,
            })

    summary = args.output_dir / "mechanism" / "mechanism_summary.csv"
    summary.parent.mkdir(parents=True, exist_ok=True)
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=mechanism_rows[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(mechanism_rows)
    print(f"Wrote mechanism summary to {summary}")


if __name__ == "__main__":
    main()
