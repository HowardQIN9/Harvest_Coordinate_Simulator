#!/usr/bin/env python3
import csv
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.plotting import plot_result


def main() -> None:
    output = Path("results/wetbin_dryer")
    output.mkdir(parents=True, exist_ok=True)
    config = SimulationConfig()
    results = [Simulator(config, policy).run()
               for policy in ("sequential", "adaptive")]
    rows = [result.metrics for result in results]

    with (output / "wetbin_dryer_policy_comparison.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    for result in results:
        plot_result(
            result,
            config,
            output / f"{result.policy}_wetbin10000_leg3000_dryer500-800_plots_20pt.svg",
        )

    keys = [
        "policy", "field_order", "harvest_completion_time_h",
        "system_completion_time_h", "blocked_time_h", "combine_utilization",
        "maximum_wet_bin_inventory_bu", "number_of_semi_trips",
    ]
    print("\t".join(keys))
    for row in rows:
        print("\t".join(f"{row[key]:.4f}" if isinstance(row[key], float)
                        else str(row[key]) for key in keys))


if __name__ == "__main__":
    main()
