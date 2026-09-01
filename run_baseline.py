#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.plotting import plot_result


def main() -> None:
    """
    Parse the argument, run the simulation and output results.
    """

    # Define command-line options and defult values
    parser = argparse.ArgumentParser(description="Run one harvest/downstream simulation")
    parser.add_argument("--policy", choices=["sequential", "adaptive"], default="sequential")
    parser.add_argument("--gc-rate", "--downstream-rate", dest="gc_rate", type=float,
                        default=SimulationConfig().grain_center_processing_rate_buph,
                        help="Grain center processing rate in bu/h (default: config value)")
    parser.add_argument("--output-dir", type=Path, default=Path("results/semi_transport"))
    parser.add_argument("--name-suffix", default="", help="Optional safe suffix for output filenames")
    args = parser.parse_args()

    #Create the simulation configuration and run the policy
    config = SimulationConfig(grain_center_processing_rate_buph=args.gc_rate)
    result = Simulator(config, args.policy).run()

    # Create the output  directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Build the output filename from the policy, downstrearm and optional suffix
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    stem = f"{args.policy}_GC{args.gc_rate:g}"
    if suffix:
        stem += f"_{suffix}"
    with (args.output_dir / f"{stem}_metrics.json").open("w") as handle:
        json.dump(result.metrics, handle, indent=2)
    with (args.output_dir / f"{stem}_timeseries.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.time_series[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.time_series)

    with (args.output_dir / f"{stem}_transport_events.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time_h", "event", "semi_id", "grain_bu", "trip"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.transport_events)

    plot_result(result, config, args.output_dir / f"{stem}_plots.svg")
    plot_result(result, config, args.output_dir / f"{stem}_first3h.svg", end_time_h=3.0)

    print(json.dumps(result.metrics, indent=2))


if __name__ == "__main__":
    main()
