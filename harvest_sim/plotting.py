from pathlib import Path

from .config import SimulationConfig
from .simulator import SimulationResult


def plot_result(result: SimulationResult, config: SimulationConfig, output: Path) -> None:
    """Write a dependency-free four-panel SVG diagnostic plot."""
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = result.time_series
    width, height = 1200, 1170
    left, right, panel_h, gap = 90, 30, 190, 80
    plot_w = width - left - right
    max_t = max(float(r["time_h"]) for r in rows) or 1.0

    def x(value: float) -> float:
        return left + value / max_t * plot_w

    def polyline(values: list[float], maximum: float, top: float, color: str) -> str:
        maximum = maximum or 1.0
        points = " ".join(
            f"{x(float(row['time_h'])):.2f},{top + panel_h - value / maximum * panel_h:.2f}"
            for row, value in zip(rows, values)
        )
        return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="34" text-anchor="middle" font-family="sans-serif" font-size="20pt">Harvest simulation — {result.policy}</text>',
    ]
    tops = [70 + i * (panel_h + gap) for i in range(4)]
    for top in tops:
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="#fafafa"/>')
    parts.append(polyline([float(r["buffer_bu"]) for r in rows], config.buffer_capacity_bu, tops[0], "#2563eb"))
    parts.append(f'<line x1="{left}" x2="{left+plot_w}" y1="{tops[0]}" y2="{tops[0]}" stroke="#dc2626" stroke-dasharray="7 5"/>')

    colors = {"Opening": "#f59e0b", "InteriorHarvest": "#16a34a", "SWITCHING": "#64748b", "BLOCKED": "#dc2626", "IDLE": "#e2e8f0"}
    for first, second in zip(rows[:-1], rows[1:]):
        state = str(first["combine_state"])
        label = str(first["current_task"]) if state == "HARVESTING" else state
        rect_w = max(0.2, x(float(second["time_h"])) - x(float(first["time_h"])))
        parts.append(f'<rect x="{x(float(first["time_h"])):.2f}" y="{tops[1]}" width="{rect_w:.2f}" height="{panel_h}" fill="{colors.get(label, "#e2e8f0")}"/>')
    parts.append(polyline([float(r["cumulative_harvested_ac"]) for r in rows], config.total_area_ac, tops[2], "#7c3aed"))
    parts.append(polyline([float(r["cumulative_grain_processed_bu"]) for r in rows], config.total_grain_bu, tops[3], "#0891b2"))
    labels = ["Buffer (bu)", "Combine task/state", "Cumulative acres", "Processed grain (bu)"]
    for top, label in zip(tops, labels):
        label_x, label_y = 24, top + panel_h / 2
        parts.append(
            f'<text x="{label_x}" y="{label_y}" text-anchor="middle" '
            f'dominant-baseline="middle" transform="rotate(-90 {label_x} {label_y})" '
            f'font-family="sans-serif" font-size="20pt">{label}</text>'
        )
        # Draw borders after the data so the state rectangles cannot cover them.
        parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{panel_h}" fill="none" stroke="#888"/>')
    legend_y, legend_x = tops[1] + panel_h + 25, left
    for label, color in colors.items():
        parts.extend([f'<rect x="{legend_x}" y="{legend_y}" width="18" height="18" fill="{color}"/>', f'<text x="{legend_x+25}" y="{legend_y+17}" font-family="sans-serif" font-size="20pt">{label}</text>'])
        legend_x += 210
    axis_y = tops[-1] + panel_h + 32
    parts.extend([
        f'<text x="{left}" y="{axis_y}" font-family="sans-serif" font-size="20pt">0</text>',
        f'<text x="{left+plot_w}" y="{axis_y}" text-anchor="end" font-family="sans-serif" font-size="20pt">{max_t:.1f} h</text>',
        f'<text x="{left+plot_w/2}" y="{axis_y+38}" text-anchor="middle" font-family="sans-serif" font-size="20pt">Time (h)</text>',
    ])
    parts.append('</svg>')
    output.write_text("\n".join(parts), encoding="utf-8")
