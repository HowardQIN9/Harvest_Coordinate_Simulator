#!/usr/bin/env python3
"""Plot the Predictive downstream-capacity sensitivity experiment."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path("results/validation/summary")
INPUT = ROOT / "downstream_capacity.csv"
OUTPUT_STEM = ROOT / "downstream_capacity_predictive"


def values(rows: list[dict[str, str]], key: str) -> list[float]:
    return [float(row[key]) for row in rows]


def main() -> None:
    with INPUT.open(newline="", encoding="utf-8") as handle:
        rows = sorted(
            (row for row in csv.DictReader(handle)
             if row["policy"] == "predictive"),
            key=lambda row: float(row["downstream_capacity_scale"]),
        )

    scale = values(rows, "downstream_capacity_scale")
    harvest = values(rows, "harvest_completion_time_h")
    system = values(rows, "system_completion_time_h")
    stop_h = values(rows, "planned_stop_time_h")
    stop_count = [int(float(row["number_of_planned_stops"])) for row in rows]
    raw_block = values(rows, "raw_forced_blocking_time_h")
    one_c = values(rows, "interior_1c_productive_h")
    two_c = values(rows, "interior_2c_productive_h")
    two_c_gc = values(rows, "interior_2c_1gc_productive_h")

    plt.rcParams.update({
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 10,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "lines.linewidth": 1.8,
        "lines.markersize": 5,
    })
    fig, axes = plt.subplots(2, 2, figsize=(8.0, 5.8), sharex=True)
    ax_time, ax_stop, ax_block, ax_mode = axes.flat

    ax_time.plot(scale, harvest, color="#0072B2", marker="o",
                 linestyle="-", label="Harvest completion")
    ax_time.plot(scale, system, color="#E69F00", marker="s",
                 linestyle="--", label="System completion")
    ax_time.set_title("(a) Completion time")
    ax_time.set_ylabel("Time (h)")
    ax_time.set_ylim(bottom=0)
    ax_time.legend(frameon=False)

    ax_stop.plot(scale, stop_h, color="#CC79A7", marker="D",
                 linestyle="-", label="Planned STOP")
    for x_value, y_value, count in zip(scale, stop_h, stop_count):
        ax_stop.annotate(
            f"{count} stops", (x_value, y_value),
            xytext=(0, 6), textcoords="offset points",
            ha="center", va="bottom", fontsize=7,
        )
    ax_stop.set_title("(b) Boundary STOP intervention")
    ax_stop.set_ylabel("Planned STOP (h)")
    ax_stop.set_ylim(bottom=0, top=max(stop_h) * 1.16)

    ax_block.plot(scale, raw_block, color="#D55E00", marker="^",
                  linestyle="-", label="Raw forced blocking")
    for x_value, y_value in zip(scale, raw_block):
        ax_block.annotate(
            f"{y_value:.2f}", (x_value, y_value),
            xytext=(0, 6), textcoords="offset points",
            ha="center", va="bottom", fontsize=7,
        )
    ax_block.set_title("(c) Residual forced blocking")
    ax_block.set_xlabel("Joint downstream capacity scale (× baseline)")
    ax_block.set_ylabel("Raw blocking (h)")
    ax_block.set_ylim(0, max(raw_block) * 1.28)

    ax_mode.plot(scale, one_c, color="#009E73", marker="o",
                 linestyle="-", label="1C")
    ax_mode.plot(scale, two_c, color="#0072B2", marker="s",
                 linestyle="--", label="2C")
    ax_mode.plot(scale, two_c_gc, color="#E69F00", marker="^",
                 linestyle=":", label="2C+1GC")
    ax_mode.set_title("(d) Interior productive time by mode")
    ax_mode.set_xlabel("Joint downstream capacity scale (× baseline)")
    ax_mode.set_ylabel("Productive time (h)")
    ax_mode.set_ylim(bottom=0)
    ax_mode.legend(frameon=False, ncol=3, loc="upper right")

    for axis in axes.flat:
        axis.set_xticks(scale)
        axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    fig.suptitle(
        "Predictive control reduces STOP intervention as downstream capacity grows",
        fontsize=11,
    )
    fig.text(
        0.5, 0.005,
        "S2 fixed; 10,000-bu physical wet bin; 500-bu controller reserve "
        "(95% control limit); "
        "dryer, grain leg, and semi unloading scaled jointly.",
        ha="center", va="bottom", fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    for suffix in ("png", "svg"):
        fig.savefig(
            OUTPUT_STEM.with_suffix(f".{suffix}"),
            dpi=220 if suffix == "png" else None,
            bbox_inches="tight",
        )
    plt.close(fig)


if __name__ == "__main__":
    main()
