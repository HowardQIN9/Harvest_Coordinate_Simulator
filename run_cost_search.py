"""Enumerate feasible fleet/policy choices and minimize completed-campaign USD."""
import argparse
import csv
import json
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path
from html import escape

from harvest_sim import SimulationConfig, Simulator
from harvest_sim.costs import CostConfig, MachineCost
from harvest_sim.open_loop import FieldAction, OpenLoopController, OpenLoopPlan
from harvest_sim.plotting import plot_result


def write_cost_chart(rows, output):
    shown = rows[:12]
    maximum = max(r["total_cost_usd"] for r in shown)
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="620" viewBox="0 0 1100 620">',
             '<rect width="1100" height="620" fill="white"/>',
             '<g font-family="Arial" font-size="16" fill="#19324a">',
             '<text x="30" y="32" font-size="23">Lowest-cost candidates (illustrative USD assumptions)</text>']
    for index, row in enumerate(shown):
        y = 65 + index * 43
        label = f'{row["n_combines"]}C/{row["n_grain_carts"]}GC/{row["n_semis"]}S {row["candidate"]}'
        width = 540 * row["total_cost_usd"] / maximum
        lines.extend([f'<text x="20" y="{y+20}">{escape(label)}</text>',
                      f'<rect x="420" y="{y}" width="{width}" height="28" fill="{"#167d8d" if index == 0 else "#8bbcc5"}"/>',
                      f'<text x="{430+width}" y="{y+20}">${row["total_cost_usd"]:,.0f}</text>'])
    lines.extend(['<text x="20" y="605">Minimum within enumerated choices; equipment prices require farm calibration.</text>', '</g></svg>'])
    output.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cost-config", type=Path)
    parser.add_argument("--write-default-config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("results/current/cost_search"))
    parser.add_argument("--max-harvest-h", type=float, default=1500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.write_default_config:
        args.write_default_config.parent.mkdir(parents=True, exist_ok=True)
        args.write_default_config.write_text(json.dumps(asdict(CostConfig()), indent=2), encoding="utf-8")
        return
    inputs = json.loads(args.cost_config.read_text(encoding="utf-8")) if args.cost_config else {}
    for key in ("combine", "grain_cart", "semi"):
        if key in inputs:
            inputs[key] = MachineCost(**inputs[key])
    costs = CostConfig(**inputs)
    base = SimulationConfig(random_seed=args.seed, cost_config=costs)
    moistures = base.sample_field_moistures()
    rows = []
    rejected = []
    best_result = None
    best_config = None
    for combines, carts, semis in product((1, 2), (0, 1), (1, 2, 3, 4)):
        config = replace(base, n_combines=combines, n_grain_carts=carts, n_semis=semis)
        candidates = [(p, None) for p in ("fixed", "threshold", "reactive")]
        for opening, interior in product(config.feasible_modes(("1C", "2C")),
                                         config.feasible_modes(("1C", "2C", "2C+1GC"))):
            plan = OpenLoopPlan(tuple(FieldAction(f.field_id, opening, interior) for f in config.field_specs))
            candidates.append((f"constant_{opening}_{interior}", OpenLoopController(config, plan)))
        for label, controller in candidates:
            result = Simulator(config, label if controller is None else "fixed",
                               moistures, controller=controller).run()
            if float(result.metrics["harvest_completion_time_h"]) > args.max_harvest_h:
                rejected.append({"candidate": label, "fleet": [combines, carts, semis], "reason": "harvest deadline"})
                continue
            row = {"candidate": label, **result.metrics}
            rows.append(row)
            if best_result is None or row["total_cost_usd"] < best_result.metrics["total_cost_usd"]:
                best_result, best_config = result, config
        print(f"Completed fleet {combines}C/{carts}GC/{semis}S", flush=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: r["total_cost_usd"])
    if rows:
        write_cost_chart(rows, args.output_dir / "cost_ranking.svg")
        plot_result(best_result, best_config, args.output_dir / "best_dashboard.svg")
        with (args.output_dir / "ranking.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with (args.output_dir / "best_time_series.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(best_result.time_series[0]))
            writer.writeheader()
            writer.writerows(best_result.time_series)
    summary = {"scope": "Minimum among enumerated candidates; not a global schedule optimum",
               "seed": args.seed, "max_harvest_h": args.max_harvest_h,
               "cost_config": asdict(costs), "feasible_candidates": len(rows),
               "rejected": rejected, "best": rows[0] if rows else None,
               "best_simulation_config": asdict(best_config) if best_config else None}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["best"], indent=2))


if __name__ == "__main__":
    main()
