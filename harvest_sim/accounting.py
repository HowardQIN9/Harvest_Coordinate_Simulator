from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import MODE_1C, MODE_2C, MODE_2C_1GC


DAILY_MACHINE_HOUR_FIELDS = (
    "day",
    "start_h",
    "end_h",
    "combine_productive_machine_h",
    "combine_switching_machine_h",
    "combine_active_machine_h",
    "combine_blocked_machine_h",
    "combine_mode_switch_occupied_machine_h",
    "combine_occupied_machine_h",
    "grain_cart_productive_machine_h",
    "grain_cart_active_machine_h",
    "grain_cart_blocked_machine_h",
    "grain_cart_mode_switch_occupied_machine_h",
    "grain_cart_occupied_machine_h",
    "semi_driving_machine_h",
    "semi_unloading_machine_h",
    "semi_gc_waiting_machine_h",
    "semi_field_standby_machine_h",
    "semi_active_machine_h",
    "semi_occupied_machine_h",
    "total_active_machine_h",
    "total_occupied_machine_h",
)


def _field_resource_counts(row: dict[str, Any]) -> tuple[int, int]:
    mode = row["operating_mode"]
    if mode == MODE_1C:
        return 1, 0
    if mode in ("Opening-2C", MODE_2C):
        return 2, 0
    if mode == MODE_2C_1GC:
        return 2, 1
    return 0, 0


def _interval_values(row: dict[str, Any], semi_count: int) -> dict[str, float]:
    state = row["combine_state"]
    combine_count, grain_cart_count = _field_resource_counts(row)
    values: defaultdict[str, float] = defaultdict(float)

    if state == "HARVESTING":
        values["combine_productive_machine_h"] = combine_count
        values["grain_cart_productive_machine_h"] = grain_cart_count
    elif state == "SWITCHING":
        # Both combines relocate because every new field starts with Opening 2C.
        values["combine_switching_machine_h"] = 2
    elif state == "BLOCKED":
        values["combine_blocked_machine_h"] = combine_count
        values["grain_cart_blocked_machine_h"] = grain_cart_count
    elif state == "MODE_SWITCHING":
        values["combine_mode_switch_occupied_machine_h"] = combine_count
        values["grain_cart_mode_switch_occupied_machine_h"] = grain_cart_count

    values["combine_active_machine_h"] = (
        values["combine_productive_machine_h"]
        + values["combine_switching_machine_h"]
    )
    values["combine_occupied_machine_h"] = (
        values["combine_active_machine_h"]
        + values["combine_blocked_machine_h"]
        + values["combine_mode_switch_occupied_machine_h"]
    )
    values["grain_cart_active_machine_h"] = values[
        "grain_cart_productive_machine_h"]
    values["grain_cart_occupied_machine_h"] = (
        values["grain_cart_active_machine_h"]
        + values["grain_cart_blocked_machine_h"]
        + values["grain_cart_mode_switch_occupied_machine_h"]
    )

    for index in range(1, semi_count + 1):
        semi_state = row[f"semi_{index}_state"]
        if semi_state in ("TO_GC", "RETURNING"):
            values["semi_driving_machine_h"] += 1
        elif semi_state == "UNLOADING":
            values["semi_unloading_machine_h"] += 1
        elif semi_state == "WAITING_UNLOAD":
            values["semi_gc_waiting_machine_h"] += 1
        elif semi_state == "AT_FIELD" and state not in (
                "HARVEST_REST", "POST_HARVEST", "HARVEST_COMPLETE",
                "DONE", "IDLE"):
            values["semi_field_standby_machine_h"] += 1

    values["semi_active_machine_h"] = (
        values["semi_driving_machine_h"]
        + values["semi_unloading_machine_h"]
    )
    values["semi_occupied_machine_h"] = (
        values["semi_active_machine_h"]
        + values["semi_gc_waiting_machine_h"]
        + values["semi_field_standby_machine_h"]
    )
    values["total_active_machine_h"] = (
        values["combine_active_machine_h"]
        + values["grain_cart_active_machine_h"]
        + values["semi_active_machine_h"]
    )
    values["total_occupied_machine_h"] = (
        values["combine_occupied_machine_h"]
        + values["grain_cart_occupied_machine_h"]
        + values["semi_occupied_machine_h"]
    )
    return dict(values)


def calculate_daily_machine_hours(
        time_series: list[dict[str, Any]], semi_count: int) -> list[dict[str, float | int]]:
    """Split every state interval at 24-hour boundaries and total machine-hours."""
    if not time_series:
        return []
    final_h = max(float(row["time_h"]) for row in time_series)
    daily: dict[int, defaultdict[str, float]] = {}
    for row in time_series:
        duration_h = float(row["duration_h"])
        if duration_h <= 0:
            continue
        start_h = float(row["time_h"]) - duration_h
        end_h = float(row["time_h"])
        rates = _interval_values(row, semi_count)
        cursor_h = start_h
        while cursor_h < end_h - 1e-12:
            day = int(cursor_h // 24.0) + 1
            boundary_h = day * 24.0
            segment_end_h = min(end_h, boundary_h)
            segment_h = segment_end_h - cursor_h
            bucket = daily.setdefault(day, defaultdict(float))
            for key, rate in rates.items():
                bucket[key] += rate * segment_h
            cursor_h = segment_end_h

    rows: list[dict[str, float | int]] = []
    for day in sorted(daily):
        row: dict[str, float | int] = {
            "day": day,
            "start_h": (day - 1) * 24.0,
            "end_h": min(day * 24.0, final_h),
        }
        row.update({key: daily[day][key]
                    for key in DAILY_MACHINE_HOUR_FIELDS[3:]})
        rows.append(row)
    return rows


def summarize_machine_hours(
        daily_rows: list[dict[str, float | int]]) -> dict[str, float]:
    return {
        key: sum(float(row[key]) for row in daily_rows)
        for key in DAILY_MACHINE_HOUR_FIELDS[3:]
    }
