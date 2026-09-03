from __future__ import annotations

from html import escape
from pathlib import Path

from .config import SimulationConfig
from .simulator import SimulationResult


def plot_result(result: SimulationResult, config: SimulationConfig, output: Path,
                end_time_h: float | None = None) -> None:
    """Write a dependency-free SVG with discrete event jumps preserved."""
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for row in result.time_series
            if end_time_h is None or row["time_h"] <= end_time_h]
    if not rows:
        rows = result.time_series[:1]

    width, height = 1800, 3260
    left, right, panel_h, stride = 210, 60, 220, 330
    plot_w = width - left - right
    tops = [230 + index * stride for index in range(9)]
    max_t = max((float(row["time_h"]) for row in rows), default=0.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text {font-family:Arial,sans-serif;fill:#1e293b;font-size:20pt} '
        '.tick {font-size:20pt;fill:#64748b} .main-title {font-size:28pt;font-weight:bold}</style>',
        f'<text class="main-title" x="{left}" y="38">Harvest / transport / wet-bin simulation — {escape(result.policy)}</text>',
        f'<text x="{left}" y="75">Opening 2C: rect {config.rectangular_opening_2c_rate_buph:g}, irregular {config.irregular_opening_2c_rate_buph:g} bu/h | '
        f'Interior 1C / 2C / 2C+1GC: {config.interior_1c_rate_buph:g} / {config.interior_2c_rate_buph:g} / {config.interior_2c_1gc_rate_buph:g} bu/h</text>',
        f'<text x="{left}" y="112">Wet bin {config.wet_bin_capacity_bu:g} bu | Grain leg {config.grain_leg_rate_buph:g} bu/h | '
        f'Dryer D(M) = {config.dryer_intercept_buph:g} - {config.dryer_moisture_slope_buph_per_pct:g}M bu/h</text>',
        f'<text x="{left}" y="149">Field moisture U({config.moisture_min_pct:g}%, {config.moisture_max_pct:g}%), seed {config.random_seed} | '
        f'Trips use 5, 15, or 20 min outbound + {config.semi_unload_time_h * 60:g} min unload + equal return time</text>',
        f'<text x="{left}" y="186">Harvest schedule from t=0: {config.harvest_work_window_h:g} h active + '
        f'{config.harvest_rest_window_h:g} h scheduled rest; downstream continues during rest</text>',
    ]

    def x(time_h: float) -> float:
        return left + time_h / max_t * plot_w

    def panel(index: int, title: str, maximum: float | None) -> None:
        top = tops[index]
        center = top + panel_h / 2
        parts.append(
            f'<text x="48" y="{center:.2f}" text-anchor="middle" font-weight="bold" '
            f'transform="rotate(-90 48 {center:.2f})">{escape(title)}</text>')
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="#f8fafc"/>')
        for tick in range(6):
            xx = x(max_t * tick / 5)
            parts.append(f'<line x1="{xx:.2f}" x2="{xx:.2f}" y1="{top}" y2="{top + panel_h}" stroke="#e2e8f0"/>')
            parts.append(f'<text class="tick" x="{xx:.2f}" y="{top + panel_h + 31}" text-anchor="middle">{max_t * tick / 5:.1f}</text>')
        if maximum is not None:
            for tick in range(3):
                yy = top + panel_h * (1 - tick / 2)
                parts.append(f'<text class="tick" x="{left - 18}" y="{yy + 8}" text-anchor="end">{maximum * tick / 2:,.0f}</text>')

    def line(index: int, key: str, maximum: float, color: str,
             stroke_width: float = 2.4) -> None:
        points = " ".join(
            f'{x(float(row["time_h"])):.3f},'
            f'{tops[index] + panel_h * (1 - float(row[key]) / maximum):.3f}'
            for row in rows)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="{stroke_width}"/>')

    def legend(index: int, items: list[tuple[str, str]]) -> None:
        xx, yy = left, tops[index] + panel_h + 72
        for name, color in items:
            estimated = len(name) * 14 + 62
            if xx + estimated > width - right:
                xx = left
                yy += 33
            parts.append(f'<rect x="{xx}" y="{yy - 20}" width="20" height="20" fill="{color}"/>')
            parts.append(f'<text x="{xx + 29}" y="{yy}">{escape(name)}</text>')
            xx += estimated

    def state_bars(intervals: list[tuple[float, float, str]], yy: float,
                   bar_h: float, colors: dict[str, str]) -> None:
        spans: list[tuple[float, float, str]] = []
        for start, end, label in intervals:
            if end <= start:
                continue
            if spans and spans[-1][2] == label and abs(spans[-1][1] - start) < 1e-8:
                spans[-1] = (spans[-1][0], end, label)
            else:
                spans.append((start, end, label))
        for start, end, label in spans:
            parts.append(
                f'<rect x="{x(start):.3f}" y="{yy:.2f}" '
                f'width="{(end - start) / max_t * plot_w:.3f}" height="{bar_h:.2f}" '
                f'fill="{colors.get(label, "#cbd5e1")}"/>')

    mobile_capacity = config.combine_tank_capacity_bu + config.semi_count * config.semi_capacity_bu
    panel(0, "Field grain (bu)", mobile_capacity)
    line(0, "field_grain_bu", mobile_capacity, "#7c3aed")
    line(0, "combine_tank_bu", mobile_capacity, "#2563eb")
    legend(0, [("Total at field", "#7c3aed"), ("Combine tank", "#2563eb")])

    panel(1, "Semi loads (bu)", config.semi_capacity_bu)
    palette = ["#d97706", "#0891b2", "#7c3aed", "#dc2626"]
    for index in range(config.semi_count):
        line(1, f"semi_{index + 1}_load_bu", config.semi_capacity_bu,
             palette[index % len(palette)])
    legend(1, [(f"Semi {index + 1}", palette[index % len(palette)])
               for index in range(config.semi_count)])

    panel(2, "Semi states", None)
    truck_colors = {
        "AT_FIELD": "#16a34a",
        "TO_GC": "#d97706",
        "WAITING_UNLOAD": "#dc2626",
        "UNLOADING": "#9333ea",
        "RETURNING": "#2563eb",
    }
    lane_h = panel_h / config.semi_count
    for index in range(config.semi_count):
        yy = tops[2] + index * lane_h
        parts.append(f'<text class="tick" x="{left - 18}" y="{yy + lane_h / 2 + 8}" text-anchor="end">S{index + 1}</text>')
        intervals = [(first["time_h"], second["time_h"], first[f"semi_{index + 1}_state"])
                     for first, second in zip(rows, rows[1:])]
        state_bars(intervals, yy + 8, lane_h - 16, truck_colors)
    legend(2, [("At field", truck_colors["AT_FIELD"]),
               ("To GC", truck_colors["TO_GC"]),
               ("Waiting", truck_colors["WAITING_UNLOAD"]),
               ("Unloading", truck_colors["UNLOADING"]),
               ("Returning", truck_colors["RETURNING"])])

    panel(3, "Receiving pit (bu)", config.receiving_pit_capacity_bu)
    line(3, "receiving_pit_inventory_bu", config.receiving_pit_capacity_bu, "#ea580c")
    legend(3, [("Receiving pit", "#ea580c")])

    panel(4, "Wet bin (bu)", config.wet_bin_capacity_bu)
    line(4, "wet_bin_inventory_bu", config.wet_bin_capacity_bu, "#0891b2", 3.0)
    legend(4, [("FIFO wet-bin inventory", "#0891b2")])

    dryer_max = config.dryer_rate_for_moisture(config.moisture_min_pct)
    panel(5, "Dryer rate (bu/h)", dryer_max)
    line(5, "dryer_rate_buph", dryer_max, "#9333ea")
    legend(5, [("Active dryer rate", "#9333ea")])

    panel(6, "Harvest mode/state", None)
    combine_colors = {
        "Opening-2C": "#d97706",
        "1C": "#16a34a",
        "2C": "#0891b2",
        "2C+1GC": "#7c3aed",
        "SWITCHING": "#64748b",
        "MODE_SWITCHING": "#db2777",
        "BLOCKED": "#dc2626",
        "HARVEST_REST": "#334155",
        "POST_HARVEST": "#cbd5e1",
    }
    intervals = []
    for row in rows:
        if row["duration_h"] <= 0:
            continue
        label = (row["operating_mode"] if row["combine_state"] == "HARVESTING"
                 else row["combine_state"])
        intervals.append((row["time_h"] - row["duration_h"], row["time_h"], label))
    state_bars(intervals, tops[6], panel_h, combine_colors)
    legend(6, [("Opening 2C", combine_colors["Opening-2C"]),
               ("1C", combine_colors["1C"]),
               ("2C", combine_colors["2C"]),
               ("2C+1GC", combine_colors["2C+1GC"]),
               ("Field switch", combine_colors["SWITCHING"]),
               ("Mode switch", combine_colors["MODE_SWITCHING"]),
               ("Blocked", combine_colors["BLOCKED"]),
               ("Scheduled rest", combine_colors["HARVEST_REST"]),
               ("Post-harvest", combine_colors["POST_HARVEST"])])

    area_max = (max(row["cumulative_harvested_ac"] for row in rows)
                if end_time_h is not None else config.total_area_ac) or 1.0
    panel(7, "Harvested area (ac)", area_max)
    line(7, "cumulative_harvested_ac", area_max, "#7c3aed")
    legend(7, [("Harvested area", "#7c3aed")])

    grain_max = (max(row["cumulative_grain_generated_bu"] for row in rows)
                 if end_time_h is not None else config.total_grain_bu) or 1.0
    panel(8, "Cumulative grain (bu)", grain_max)
    grain_lines = [
        ("cumulative_grain_generated_bu", "Harvested", "#7c3aed"),
        ("cumulative_grain_unloaded_bu", "Unloaded", "#d97706"),
        ("cumulative_grain_to_wet_bin_bu", "Sent by grain leg", "#2563eb"),
        ("cumulative_grain_dried_bu", "Dried", "#0891b2"),
    ]
    for key, _, color in grain_lines:
        line(8, key, grain_max, color)
    legend(8, [(name, color) for _, name, color in grain_lines])

    harvest_end = float(result.metrics["harvest_completion_time_h"])
    for top in tops:
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="none" stroke="#94a3b8"/>')
        if 0 < harvest_end <= max_t:
            parts.append(f'<line x1="{x(harvest_end):.2f}" x2="{x(harvest_end):.2f}" y1="{top}" y2="{top + panel_h}" stroke="#0f172a" stroke-width="2" stroke-dasharray="7 7"/>')
    parts.append(f'<text x="{left}" y="3240">Horizontal axes: time (h). Dashed vertical line: harvest complete; drying may continue afterward.</text>')
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")
