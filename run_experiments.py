#!/usr/bin/env python3
import csv
from dataclasses import replace
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator


def main() -> None:
    output = Path("results")
    output.mkdir(exist_ok=True)
    rates = [600, 800, 900, 1000, 1100, 1200]
    rows = []
    baseline = SimulationConfig()
    for rate in rates:
        config = replace(baseline, downstream_rate_buph=float(rate))
        for policy in ("sequential", "adaptive"):
            rows.append(Simulator(config, policy).run().metrics)
    with (output / "sensitivity_comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    keys = ["policy", "downstream_rate_bu_per_h", "total_completion_time_h", "blocked_time_h", "switching_time_h", "number_of_field_switches", "combine_utilization"]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(f"{row[k]:.4f}" if isinstance(row[k], float) else str(row[k]) for k in keys))


if __name__ == "__main__":
    main()
