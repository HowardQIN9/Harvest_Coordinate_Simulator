#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.plotting import plot_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one harvest/downstream simulation")
    parser.add_argument("--policy", choices=["sequential", "adaptive"], default="sequential")
    parser.add_argument("--downstream-rate", type=float, default=1000.0)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--name-suffix", default="", help="Optional safe suffix for output filenames")
    args = parser.parse_args()
    config = SimulationConfig(downstream_rate_buph=args.downstream_rate)
    result = Simulator(config, args.policy).run()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", args.name_suffix).strip("_")
    stem = f"{args.policy}_D{args.downstream_rate:g}"
    if suffix:
        stem += f"_{suffix}"
    with (args.output_dir / f"{stem}_metrics.json").open("w") as handle:
        json.dump(result.metrics, handle, indent=2)
    with (args.output_dir / f"{stem}_timeseries.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.time_series[0].keys())
        writer.writeheader()
        writer.writerows(result.time_series)
    plot_result(result, config, args.output_dir / f"{stem}_plots.svg")
    print(json.dumps(result.metrics, indent=2))


if __name__ == "__main__":
    main()
