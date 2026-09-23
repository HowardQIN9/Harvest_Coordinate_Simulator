#!/usr/bin/env python3
"""Run the four-field rolling predictive prototype on all configured fields."""

import argparse
import csv
import json
import time
from dataclasses import replace
from pathlib import Path

from harvest_sim.config import SimulationConfig
from harvest_sim.rolling_predictive import RollingFourFieldPredictive


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the four-field rolling predictive prototype")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/current/rolling_dynamic_predictive"))
    parser.add_argument("--decision-interval-h", type=float, default=1.0)
    parser.add_argument("--forecast-step-min", type=float, default=5.0)
    parser.add_argument("--horizon-fields", type=int, default=4)
    parser.add_argument(
        "--seed", type=int, default=SimulationConfig().random_seed,
        help="System field-scenario random seed")
    parser.add_argument(
        "--completion-tolerance-min-pct", type=float, default=1.0)
    parser.add_argument(
        "--completion-tolerance-max-pct", type=float, default=4.0)
    parser.add_argument(
        "--completion-tolerance-pressure-scale", type=float, default=0.30)
    parser.add_argument("--work-before-stop-h", type=float, default=1.0)
    parser.add_argument(
        "--stop-durations-h", default="auto",
        help="Comma-separated fixed STOP candidates, or 'auto' for coarse-to-fine search")
    parser.add_argument("--maximum-stop-h", type=float, default=30.0)
    parser.add_argument("--stop-coarse-step-h", type=float, default=4.0)
    parser.add_argument("--stop-refine-step-h", type=float, default=1.0)
    parser.add_argument("--stop-fine-step-h", type=float, default=0.25)
    parser.add_argument(
        "--maximum-dryer-idle-h", type=float,
        default=SimulationConfig().maximum_dryer_idle_episode_h,
        help="System-wide maximum continuous dryer idle episode")
    parser.add_argument(
        "--use-field-buffer", action=argparse.BooleanOptionalAction,
        default=SimulationConfig().use_field_buffer,
        help="System-wide field-buffer availability for all policies")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    stop_durations = (
        None if args.stop_durations_h.strip().lower() == "auto"
        else tuple(
            float(value) for value in args.stop_durations_h.split(",")
            if value.strip()))
    config = replace(
        SimulationConfig(),
        random_seed=args.seed,
        stop_work_requirement_h=args.work_before_stop_h,
        use_field_buffer=args.use_field_buffer,
        maximum_dryer_idle_episode_h=args.maximum_dryer_idle_h,
    )
    planner = RollingFourFieldPredictive(
        config,
        completion_tolerance_min_fraction=(
            args.completion_tolerance_min_pct / 100.0),
        completion_tolerance_max_fraction=(
            args.completion_tolerance_max_pct / 100.0),
        completion_tolerance_pressure_scale=(
            args.completion_tolerance_pressure_scale),
        decision_interval_h=args.decision_interval_h,
        horizon_fields=args.horizon_fields,
        forecast_simulation_step_h=args.forecast_step_min / 60.0,
        stop_durations_h=stop_durations,
        maximum_stop_h=args.maximum_stop_h,
        stop_coarse_step_h=args.stop_coarse_step_h,
        stop_refine_step_h=args.stop_refine_step_h,
        stop_fine_step_h=args.stop_fine_step_h,
        verbose=args.verbose,
    )

    started = time.perf_counter()
    result = planner.run()
    result.metrics["wall_clock_seconds"] = time.perf_counter() - started

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "metrics.json").open(
            "w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
        handle.write("\n")
    write_rows(args.output_dir / "controller_decisions.csv",
               result.controller_decisions)
    write_rows(args.output_dir / "timeseries.csv", result.time_series)
    write_rows(args.output_dir / "field_scenario.csv", [{
        "field_id": field.field_id,
        "area_ac": field.area_ac,
        "geometry": field.geometry,
        "one_way_travel_min": field.one_way_travel_min,
        "yield_bu_per_ac": field.yield_bu_per_ac,
        "moisture_pct": field.moisture_pct,
        "dryer_rate_buph": config.dryer_rate_for_moisture(
            field.moisture_pct),
    } for field in result.fields])
    print(json.dumps(result.metrics, indent=2))
    print(f"Wrote results to {args.output_dir}")


if __name__ == "__main__":
    main()
