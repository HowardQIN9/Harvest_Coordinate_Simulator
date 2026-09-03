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
    parser.add_argument("--dryer-high-rate", type=float,
                        default=defaults.dryer_high_moisture_rate_buph)
    parser.add_argument("--dryer-low-rate", type=float,
                        default=defaults.dryer_low_moisture_rate_buph)
    parser.add_argument("--output-dir", type=Path, default=Path("results/wetbin_dryer"))
    parser.add_argument("--name-suffix", default="",
                        help="Optional safe suffix for output filenames")
    args = parser.parse_args()

    config = SimulationConfig(
        grain_leg_rate_buph=args.grain_leg_rate,
        wet_bin_capacity_bu=args.wet_bin_capacity,
        dryer_high_moisture_rate_buph=args.dryer_high_rate,
        dryer_low_moisture_rate_buph=args.dryer_low_rate,
    )
    result = Simulator(config, args.policy).run()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    stem = (f"{args.policy}_wetbin{args.wet_bin_capacity:g}_leg{args.grain_leg_rate:g}"
            f"_dryer{args.dryer_high_rate:g}-{args.dryer_low_rate:g}")
    if suffix:
        stem += f"_{suffix}"

    with (args.output_dir / f"{stem}_metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
    with (args.output_dir / f"{stem}_timeseries.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.time_series[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.time_series)
    with (args.output_dir / f"{stem}_transport_events.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(result.transport_events[0].keys()) if result.transport_events else []
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.transport_events)

    plot_result(result, config, args.output_dir / f"{stem}_plots_20pt.svg")
    plot_result(result, config, args.output_dir / f"{stem}_first3h_20pt.svg", end_time_h=3.0)
    print(json.dumps(result.metrics, indent=2))


if __name__ == "__main__":
    main()
