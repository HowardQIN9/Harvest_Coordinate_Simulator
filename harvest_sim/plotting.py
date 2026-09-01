from html import escape
from pathlib import Path

from .config import SimulationConfig
from .simulator import SimulationResult


def plot_result(result: SimulationResult, config: SimulationConfig, output: Path,
                end_time_h: float | None = None) -> None:
    """Dependency-free SVG, with duplicate event timestamps preserving jumps."""
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for row in result.time_series if end_time_h is None or row["time_h"] <= end_time_h]
    width, height = 1440, 2050
    left, right, panel_h, stride = 100, 40, 165, 275
    plot_w = width - left - right
    tops = [140 + i * stride for i in range(7)]
    max_t = max((float(row["time_h"]) for row in rows), default=0.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text {font-family:Arial,sans-serif;fill:#1e293b} .tick {font-size:14px;fill:#64748b}</style>',
        f'<text x="{left}" y="38" font-size="26" font-weight="bold">Harvest and semi transport / {escape(result.policy)}</text>',
        f'<text x="{left}" y="68" font-size="16">Combine: {config.combine_tank_capacity_bu:g} bu | '
        f'{config.semi_count} semis x {config.semi_capacity_bu:g} bu | GC arrival: '
        f'{config.semi_gc_arrival_h * 60:g} min | Return: {config.semi_round_trip_h * 60:g} min | '
        f'GC: {config.grain_center_processing_rate_buph:g} bu/h</text>',
    ]

    def x(time_h: float) -> float:
        return left + time_h / max_t * plot_w

    def panel(index: int, title: str, maximum: float | None) -> None:
        top = tops[index]
        parts.append(f'<text x="{left}" y="{top - 24}" font-size="19" font-weight="bold">{title}</text>')
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="#f8fafc"/>')
        for tick in range(6):
            xx = x(max_t * tick / 5)
            parts.append(f'<line x1="{xx:.2f}" x2="{xx:.2f}" y1="{top}" y2="{top + panel_h}" stroke="#e2e8f0"/>')
            parts.append(f'<text class="tick" x="{xx:.2f}" y="{top + panel_h + 23}" text-anchor="middle">{max_t * tick / 5:.2f}</text>')
        if maximum is not None:
            for tick in range(3):
                yy = top + panel_h * (1 - tick / 2)
                parts.append(f'<text class="tick" x="{left - 12}" y="{yy + 5}" text-anchor="end">{maximum * tick / 2:,.0f}</text>')
        parts.append(f'<text class="tick" x="{left + plot_w}" y="{top + panel_h + 46}" text-anchor="end">Time (h)</text>')

    def line(index: int, key: str, maximum: float, color: str) -> None:
        points = " ".join(f'{x(float(row["time_h"])):.3f},{tops[index] + panel_h * (1 - float(row[key]) / maximum):.3f}' for row in rows)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8"/>')

    def legend(index: int, items: list[tuple[str, str]]) -> None:
        xx, yy = left, tops[index] + panel_h + 48
        for name, color in items:
            parts.append(f'<rect x="{xx}" y="{yy - 12}" width="14" height="14" fill="{color}"/>')
            parts.append(f'<text x="{xx + 21}" y="{yy}" font-size="14">{escape(name)}</text>')
            xx += len(name) * 8 + 56

    def state_bars(intervals: list[tuple[float, float, str]], yy: float, bar_h: float,
                   colors: dict[str, str]) -> None:
        # Merge adjacent equal states, avoiding seams between minute-wide bars.
        spans: list[tuple[float, float, str]] = []
        for start, end, label in intervals:
            if end <= start:
                continue
            if spans and spans[-1][2] == label and abs(spans[-1][1] - start) < 1e-8:
                spans[-1] = (spans[-1][0], end, label)
            else:
                spans.append((start, end, label))
        for start, end, label in spans:
            parts.append(f'<rect x="{x(start):.3f}" y="{yy:.2f}" width="{(end - start) / max_t * plot_w:.3f}" height="{bar_h:.2f}" fill="{colors[label]}"/>')

    mobile_capacity = config.combine_tank_capacity_bu + config.semi_count * config.semi_capacity_bu
    panel(0, "1 / Grain at field (bu): combine + on-site semis; no ground storage", mobile_capacity)
    line(0, "field_grain_bu", mobile_capacity, "#7c3aed")
    line(0, "combine_tank_bu", mobile_capacity, "#2563eb")
    tank_y = tops[0] + panel_h * (1 - config.combine_tank_capacity_bu / mobile_capacity)
    parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{tank_y}" y2="{tank_y}" stroke="#2563eb" stroke-dasharray="5 5" opacity="0.5"/>')
    legend(0, [("Total at field", "#7c3aed"), ("Combine tank", "#2563eb")])

    panel(1, "2 / Semi grain loads (bu): load drops at GC arrival", config.semi_capacity_bu)
    palette = ["#d97706", "#0891b2", "#7c3aed", "#dc2626"]
    for index in range(config.semi_count):
        line(1, f"semi_{index + 1}_load_bu", config.semi_capacity_bu, palette[index % len(palette)])
    legend(1, [(f"Semi {i + 1}", palette[i % len(palette)]) for i in range(config.semi_count)])

    panel(2, "3 / Semi transport states", None)
    truck_colors = {"AT_FIELD": "#16a34a", "TO_GC": "#d97706", "RETURNING": "#2563eb"}
    lane_h = panel_h / config.semi_count
    for index in range(config.semi_count):
        yy = tops[2] + index * lane_h
        parts.append(f'<text class="tick" x="{left - 12}" y="{yy + lane_h / 2 + 5}" text-anchor="end">Semi {index + 1}</text>')
        intervals = [(first["time_h"], second["time_h"], first[f"semi_{index + 1}_state"])
                     for first, second in zip(rows, rows[1:])]
        state_bars(intervals, yy + 6, lane_h - 12, truck_colors)
    legend(2, [("At field", truck_colors["AT_FIELD"]), ("To GC (loaded)", truck_colors["TO_GC"]), ("Returning (empty)", truck_colors["RETURNING"])])

    gc_max = max(config.semi_capacity_bu, max(row["grain_center_inventory_bu"] for row in rows)) * 1.05
    panel(3, "4 / Grain center inventory (bu): batch arrivals, continuous processing", gc_max)
    line(3, "grain_center_inventory_bu", gc_max, "#0891b2")
    legend(3, [("Waiting for processing", "#0891b2")])

    panel(4, "5 / Combine task and state", None)
    colors = {"Opening": "#d97706", "InteriorHarvest": "#16a34a", "SWITCHING": "#64748b",
              "BLOCKED": "#dc2626", "POST_HARVEST": "#cbd5e1"}
    intervals = []
    for row in rows:
        if row["duration_h"] <= 0:
            continue
        # The row describes the interval ENDING at its timestamp.
        label = row["current_task"] if row["combine_state"] == "HARVESTING" else row["combine_state"]
        start = row["time_h"] - row["duration_h"]
        intervals.append((start, row["time_h"], label))
    state_bars(intervals, tops[4], panel_h, colors)
    legend(4, [("Opening", colors["Opening"]), ("Interior", colors["InteriorHarvest"]),
               ("Field switching", colors["SWITCHING"]), ("Blocked", colors["BLOCKED"]),
               ("After harvest", colors["POST_HARVEST"])])

    area_max = (max(row["cumulative_harvested_ac"] for row in rows)
                if end_time_h is not None else config.total_area_ac) or 1.0
    panel(5, "6 / Cumulative harvested area (ac)", area_max)
    line(5, "cumulative_harvested_ac", area_max, "#7c3aed")
    legend(5, [("Harvested area", "#7c3aed")])

    grain_max = (max(row["cumulative_grain_generated_bu"] for row in rows)
                 if end_time_h is not None else config.total_grain_bu) or 1.0
    panel(6, "7 / Cumulative grain (bu)", grain_max)
    grain_lines = [("cumulative_grain_generated_bu", "Harvested", "#7c3aed"),
                   ("cumulative_grain_received_bu", "Received at GC", "#d97706"),
                   ("cumulative_grain_processed_bu", "Processed at GC", "#0891b2")]
    for key, _, color in grain_lines:
        line(6, key, grain_max, color)
    legend(6, [(name, color) for _, name, color in grain_lines])

    harvest_end = float(result.metrics["harvest_completion_time_h"])
    for top in tops:
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="none" stroke="#94a3b8"/>')
        if 0 < harvest_end <= max_t:
            parts.append(f'<line x1="{x(harvest_end):.2f}" x2="{x(harvest_end):.2f}" y1="{top}" y2="{top + panel_h}" stroke="#0f172a" stroke-dasharray="5 5"/>')
    parts.append(f'<text x="{left}" y="2028" font-size="14">Dashed vertical line: harvest complete. Final deliveries and GC processing continue afterward.</text>')
    parts.append('</svg>')
    output.write_text("\n".join(parts), encoding="utf-8")
