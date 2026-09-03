#!/usr/bin/env python3
import csv
from pathlib import Path

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.plotting import plot_result


def main() -> None:
    config = SimulationConfig()
    field_moistures = config.sample_field_moistures()
    scenario = (f"fields{len(config.field_specs)}_seed{config.random_seed}"
                f"_moisture{config.moisture_min_pct:g}-{config.moisture_max_pct:g}"
                f"_wetbin{config.wet_bin_capacity_bu:g}"
                f"_leg{config.grain_leg_rate_buph:g}")
    output = Path("results/current") / scenario
    output.mkdir(parents=True, exist_ok=True)
    results = [Simulator(config, policy, field_moistures).run()
               for policy in ("sequential", "adaptive")]
    rows = [result.metrics for result in results]

    with (output / "field_scenario.csv").open(
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

    with (output / "policy_comparison.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    for result in results:
        policy_output = output / result.policy
        policy_output.mkdir(parents=True, exist_ok=True)
        plot_result(
            result,
            config,
            policy_output / "overview.svg",
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
