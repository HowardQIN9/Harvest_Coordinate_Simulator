#!/usr/bin/env python3
import csv
from dataclasses import replace
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator


def main() -> None:
    output = Path("results/semi_transport")
    output.mkdir(parents=True, exist_ok=True)
    rates = [600, 1000, 1500, 2000, 2500, 3000]
    rows = []
    baseline = SimulationConfig()
    for rate in rates:
        config = replace(baseline, grain_center_processing_rate_buph=float(rate))
        for policy in ("sequential", "adaptive"):
            rows.append(Simulator(config, policy).run().metrics)
    with (output / "sensitivity_comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    keys = ["policy", "grain_center_processing_rate_buph", "harvest_completion_time_h",
            "system_completion_time_h", "blocked_time_h", "maximum_grain_center_inventory_bu",
            "number_of_semi_trips", "combine_utilization"]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(f"{row[k]:.4f}" if isinstance(row[k], float) else str(row[k]) for k in keys))


if __name__ == "__main__":
    main()
