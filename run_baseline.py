#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.plotting import plot_result


def main() -> None:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(description="Run one harvest/downstream simulation")
    parser.add_argument("--policy", choices=["sequential", "adaptive"], default="sequential")
    parser.add_argument("--grain-leg-rate", type=float, default=defaults.grain_leg_rate_buph)
    parser.add_argument("--wet-bin-capacity", type=float, default=defaults.wet_bin_capacity_bu)
    parser.add_argument("--seed", type=int, default=defaults.random_seed)
    parser.add_argument("--moisture-min", type=float, default=defaults.moisture_min_pct)
    parser.add_argument("--moisture-max", type=float, default=defaults.moisture_max_pct)
    parser.add_argument("--dryer-intercept", type=float, default=defaults.dryer_intercept_buph)
    parser.add_argument("--dryer-slope", type=float,
                        default=defaults.dryer_moisture_slope_buph_per_pct)
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
    )
    field_moistures = config.sample_field_moistures()
    result = Simulator(config, args.policy, field_moistures).run()
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    scenario = (f"fields{len(config.field_specs)}_seed{config.random_seed}"
                f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
                f"_wetbin{args.wet_bin_capacity:g}_leg{args.grain_leg_rate:g}")
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
