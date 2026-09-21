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
        default=Path("results/current/rolling_four_field_predictive"))
    parser.add_argument("--decision-interval-h", type=float, default=1.0)
    parser.add_argument("--forecast-step-min", type=float, default=5.0)
    parser.add_argument("--horizon-fields", type=int, default=4)
    parser.add_argument("--completion-tolerance-h", type=float, default=2.0)
    parser.add_argument("--work-before-stop-h", type=float, default=1.0)
    parser.add_argument(
        "--stop-durations-h", default="6,10,14,18",
        help="Comma-separated planned STOP candidates")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    stop_durations = tuple(
        float(value) for value in args.stop_durations_h.split(",")
        if value.strip())
    config = replace(
        SimulationConfig(),
        stop_work_requirement_h=args.work_before_stop_h,
    )
    planner = RollingFourFieldPredictive(
        config,
        completion_tolerance_h=args.completion_tolerance_h,
        decision_interval_h=args.decision_interval_h,
        horizon_fields=args.horizon_fields,
        forecast_simulation_step_h=args.forecast_step_min / 60.0,
        stop_durations_h=stop_durations,
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
    print(json.dumps(result.metrics, indent=2))
    print(f"Wrote results to {args.output_dir}")


if __name__ == "__main__":
    main()
