#!/usr/bin/env python3
"""Render static per-policy field-action dashboards from simulation CSVs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle


COLORS = {
    "Opening 1C": "#9ECAE1",
    "Opening 2C": "#56B4E9",
    "1C": "#0072B2",
    "2C": "#009E73",
    "2C+1GC": "#CC79A7",
    "STOP": "#E69F00",
    "Switch": "#B3B3B3",
}
HATCHES = {
    "Opening 1C": "..",
    "Opening 2C": "..",
    "STOP": "xx",
    "Switch": "//",
}
DISPLAY = {
    "Opening 1C": "Open 1C",
    "Opening 2C": "Open 2C",
    "1C": "1C",
    "2C": "2C",
    "2C+1GC": "2C+1GC",
    "STOP": "STOP",
    "Switch": "Switch",
}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def interval_action(row: dict[str, str]) -> str | None:
    state = row["combine_state"]
    if state == "PLANNED_STOP":
        return "STOP"
    if state in {"SWITCHING", "MODE_SWITCHING"}:
        return "Switch"
    mode = row["operating_mode"]
    if not mode:
        return None
    if row["current_task"] == "Opening":
        return f"Opening {mode}"
    return mode if mode in {"1C", "2C", "2C+1GC"} else None


def merge_intervals(
        rows: list[dict[str, str]], field_order: list[str],
) -> tuple[dict[str, list[tuple[float, float, str]]],
           dict[str, list[tuple[float, float]]]]:
    actions: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    blocked: dict[str, list[tuple[float, float]]] = defaultdict(list)
    valid_fields = set(field_order)
    previous_time = 0.0
    for row in rows:
        duration = float(row["duration_h"] or 0.0)
        end = float(row["time_h"] or 0.0)
        if duration <= 1e-10 or end <= previous_time + 1e-10:
            continue
        # Baseline CSVs contain five-minute snapshots whose duration_h is
        # still the last one-minute physical step. Reconstruct the represented
        # interval from adjacent positive-duration timestamps. Full predictive
        # traces naturally reduce to their original physical intervals.
        start = previous_time
        previous_time = end
        field_id = row["current_field"]
        if field_id not in valid_fields:
            continue
        action = interval_action(row)
        if action is not None:
            current = actions[field_id]
            if (current and current[-1][2] == action
                    and start <= current[-1][1] + 1e-7):
                previous = current[-1]
                current[-1] = (previous[0], max(previous[1], end), action)
            else:
                current.append((start, end, action))
        if row["combine_state"] == "BLOCKED":
            current_blocked = blocked[field_id]
            if (current_blocked
                    and start <= current_blocked[-1][1] + 1e-7):
                previous = current_blocked[-1]
                current_blocked[-1] = (previous[0], max(previous[1], end))
            else:
                current_blocked.append((start, end))
    return actions, blocked


def policy_inputs(seed_root: Path, policy: str) -> tuple[Path, Path]:
    if policy == "predictive":
        folder = seed_root / "predictive"
    else:
        scenarios = sorted(path for path in seed_root.iterdir()
                           if path.is_dir() and path.name.startswith("fields"))
        if len(scenarios) != 1:
            raise RuntimeError(
                f"Expected one baseline scenario under {seed_root}, "
                f"found {len(scenarios)}")
        folder = scenarios[0] / policy
    return folder / "timeseries.csv", folder / "metrics.json"


def render(seed_root: Path, policy: str, output_dir: Path) -> tuple[Path, Path]:
    timeseries_path, metrics_path = policy_inputs(seed_root, policy)
    rows = load_rows(timeseries_path)
    with metrics_path.open(encoding="utf-8") as handle:
        metrics = json.load(handle)

    field_order = [value.strip() for value in metrics["field_order"].split("->")]
    actions, blocked = merge_intervals(rows, field_order)
    harvest_h = float(metrics["harvest_completion_time_h"])
    system_h = float(metrics["system_completion_time_h"])
    raw_block_h = float(metrics["raw_forced_blocking_time_h"])
    stop_h = float(metrics["planned_stop_time_h"])
    stops = int(metrics["number_of_planned_stops"])
    seed = int(metrics["random_seed"])

    plt.rcParams.update({
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 9,
        "legend.fontsize": 8,
    })
    fig = plt.figure(figsize=(15, 9.0), constrained_layout=True)
    grid = fig.add_gridspec(3, 1, height_ratios=(0.75, 4.4, 1.35))
    header_ax = fig.add_subplot(grid[0])
    ax = fig.add_subplot(grid[1])
    wet_ax = fig.add_subplot(grid[2], sharex=ax)
    header_ax.axis("off")

    bar_h = 0.68
    y_by_field = {field_id: len(field_order) - 1 - index
                  for index, field_id in enumerate(field_order)}
    used_actions: set[str] = set()
    for field_id in field_order:
        y = y_by_field[field_id]
        for start, end, action in actions[field_id]:
            duration = end - start
            used_actions.add(action)
            rect = Rectangle(
                (start, y - bar_h / 2), duration, bar_h,
                facecolor=COLORS.get(action, "#777777"),
                edgecolor="#333333", linewidth=0.45,
                hatch=HATCHES.get(action, ""), zorder=2,
            )
            ax.add_patch(rect)
            if duration >= 2.2:
                label = f"{DISPLAY.get(action, action)}\n{duration:.1f}h"
                ax.text(
                    start + duration / 2, y, label,
                    ha="center", va="center", fontsize=8.0,
                    color="white" if action in {"1C", "2C", "2C+1GC"}
                    else "#222222",
                    fontweight="bold", clip_on=True, zorder=3,
                )
        for start, end in blocked[field_id]:
            ax.add_patch(Rectangle(
                (start, y - bar_h / 2), end - start, bar_h,
                facecolor="none", edgecolor="#D62728", linewidth=0.75,
                hatch="////", zorder=4,
            ))

    ax.set_yticks([y_by_field[field_id] for field_id in field_order])
    ax.set_yticklabels(field_order)
    ax.set_ylim(-0.8, len(field_order) - 0.2)
    ax.set_xlim(0.0, system_h)
    ax.set_ylabel("Field")
    header_ax.text(
        0.0, 0.78, f"{policy.title()} — field actions (seed {seed})",
        transform=header_ax.transAxes, ha="left", va="center",
        fontsize=15, fontweight="bold")
    header_ax.text(
        0.0, 0.28,
        f"Harvest {harvest_h:.2f} h   |   System {system_h:.2f} h   |   "
        f"Raw blocking {raw_block_h:.2f} h   |   "
        f"Planned STOP {stop_h:.2f} h ({stops})",
        transform=header_ax.transAxes, ha="left", va="center",
        fontsize=11, fontweight="bold")
    ax.axvline(
        harvest_h, color="#222222", linestyle="--", linewidth=1.1,
        label=f"Harvest complete ({harvest_h:.2f} h)", zorder=5)
    ax.grid(axis="x", color="#D9D9D9", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)

    legend_order = [
        "Opening 1C", "Opening 2C", "1C", "2C", "2C+1GC",
        "STOP", "Switch",
    ]
    handles = [
        Patch(facecolor=COLORS[action], edgecolor="#333333",
              hatch=HATCHES.get(action, ""), label=DISPLAY[action])
        for action in legend_order if action in used_actions
    ]
    handles.append(Patch(
        facecolor="white", edgecolor="#D62728", hatch="////",
        label="Forced blocking overlay"))
    header_ax.legend(
        handles=handles, ncol=min(8, len(handles)), loc="lower right",
        bbox_to_anchor=(1.0, 0.02), frameon=False,
    )

    times: list[float] = []
    wet: list[float] = []
    capacity = float(metrics["wet_bin_capacity_bu"])
    for row in rows:
        if not row["time_h"]:
            continue
        times.append(float(row["time_h"]))
        wet.append(100.0 * float(row["wet_bin_inventory_bu"] or 0.0)
                   / capacity)
    wet_ax.plot(times, wet, color="#0072B2", linewidth=1.25,
                label="Wet-bin occupancy")
    wet_ax.fill_between(times, wet, color="#56B4E9", alpha=0.28)
    wet_ax.axhline(100.0, color="#D62728", linestyle="--", linewidth=0.9,
                   label="Wet-bin capacity")
    wet_ax.axvline(harvest_h, color="#222222", linestyle="--", linewidth=1.1)
    wet_ax.set_ylim(0.0, 105.0)
    wet_ax.set_ylabel("Wet bin (%)")
    wet_ax.set_xlabel("Simulation time (h)")
    wet_ax.grid(color="#D9D9D9", linewidth=0.6, alpha=0.8)
    wet_ax.legend(loc="upper right", frameon=False, ncol=2)

    fig.text(
        0.01, 0.005,
        "Actions use colour + hatch; red hatching marks forced blocking. "
        "Labels show merged action duration within each field.",
        fontsize=8, color="#444444",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{policy}_action_dashboard"
    png = output_dir / f"{stem}.png"
    svg = output_dir / f"{stem}.svg"
    fig.savefig(png, dpi=180, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return png, svg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--policies", nargs="+",
        default=("fixed", "threshold", "reactive", "predictive"))
    args = parser.parse_args()
    for policy in args.policies:
        png, svg = render(args.seed_root, policy, args.output_dir)
        print(f"Wrote {png}")
        print(f"Wrote {svg}")


if __name__ == "__main__":
    main()
