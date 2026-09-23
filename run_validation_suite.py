#!/usr/bin/env python3
"""Run field, downstream-pressure, and forecast-error validation suites."""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import replace
from pathlib import Path

from harvest_sim import FieldScenarioGenerator, SimulationConfig, Simulator
from harvest_sim.config import INTERIOR_MODES
from harvest_sim.rolling_predictive import RollingFourFieldPredictive


SCENARIOS = {"s1": 101, "s2": 202, "s3": 303}
CAPACITY_SCALES = (1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0)
FORECAST_YIELD_MULTIPLIERS = (0.95, 1.0, 1.05)
POLICIES = ("fixed", "threshold", "reactive", "predictive")

SUMMARY_METRICS = (
    "controller_implementation",
    "wet_bin_capacity_bu",
    "wet_bin_control_limit_fraction",
    "wet_bin_control_reserve_bu",
    "grain_weighted_average_moisture_pct",
    "effective_dryer_rate_buph",
    "harvest_completion_time_h",
    "system_completion_time_h",
    "raw_forced_blocking_time_h",
    "forced_blocking_time_h",
    "number_of_planned_stops",
    "planned_stop_time_h",
    "mean_planned_stop_duration_h",
    "dryer_idle_time_h",
    "maximum_dryer_idle_episode_h",
    "number_of_interior_mode_switches",
    "interior_1c_productive_h",
    "interior_2c_productive_h",
    "interior_2c_1gc_productive_h",
    "rolling_mean_candidate_count",
    "control_decision_interval_min",
    "rolling_horizon_fields",
    "rolling_forecast_simulation_step_min",
    "rolling_completion_tolerance_min_pct",
    "rolling_completion_tolerance_max_pct",
    "rolling_completion_tolerance_pressure_scale",
    "rolling_stop_search",
    "rolling_stop_coarse_step_h",
    "rolling_stop_refine_step_h",
    "rolling_stop_fine_step_h",
    "rolling_maximum_stop_h",
    "total_harvested_grain_bu",
    "mass_balance_error_bu",
    "wall_clock_seconds",
)


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def base_config() -> SimulationConfig:
    return replace(
        SimulationConfig(),
        stop_work_requirement_h=1.0,
        use_field_buffer=True,
        maximum_dryer_idle_episode_h=2.0,
        wet_bin_control_limit_fraction=0.95,
    )


def scenario_config(name: str) -> SimulationConfig:
    return FieldScenarioGenerator().generate_config(
        SCENARIOS[name], base_config())


def effective_dryer_rate(config: SimulationConfig) -> float:
    total_bu = 0.0
    total_h = 0.0
    for field in config.field_specs:
        field_yield = (
            config.yield_bu_per_ac
            if field.yield_bu_per_ac is None else field.yield_bu_per_ac)
        moisture = (
            (config.moisture_min_pct + config.moisture_max_pct) / 2.0
            if field.moisture_pct is None else field.moisture_pct)
        volume = field.area_ac * field_yield
        total_bu += volume
        total_h += volume / config.dryer_rate_for_moisture(moisture)
    return total_bu / total_h


def config_for_capacity_scale(
        config: SimulationConfig, capacity_scale: float,
) -> tuple[SimulationConfig, dict[str, float]]:
    if capacity_scale <= 0:
        raise ValueError("downstream capacity scale must be positive")
    max_harvest_rate = max(
        config.interior_rate_buph(mode)
        for mode in config.feasible_modes(INTERIOR_MODES))
    base_effective = effective_dryer_rate(config)
    adjusted = replace(
        config,
        dryer_intercept_buph=(
            config.dryer_intercept_buph * capacity_scale),
        dryer_moisture_slope_buph_per_pct=(
            config.dryer_moisture_slope_buph_per_pct * capacity_scale),
        grain_leg_rate_buph=config.grain_leg_rate_buph * capacity_scale,
        semi_unload_time_h=config.semi_unload_time_h / capacity_scale,
    )
    realized_effective = effective_dryer_rate(adjusted)
    return adjusted, {
        "downstream_capacity_scale": capacity_scale,
        "base_effective_dryer_rate_buph": base_effective,
        "maximum_harvest_rate_buph": max_harvest_rate,
        "effective_dryer_rate_buph": realized_effective,
        "maximum_harvest_to_effective_downstream_ratio": (
            max_harvest_rate / realized_effective),
        "wet_bin_control_limit_fraction": (
            config.wet_bin_control_limit_fraction),
    }


def write_scenario(
        directory: Path, config: SimulationConfig,
        metadata: dict[str, object],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for field in config.field_specs:
        moisture = (
            config.sample_field_moistures()[len(rows)]
            if field.moisture_pct is None else field.moisture_pct)
        field_yield = (
            config.yield_bu_per_ac
            if field.yield_bu_per_ac is None else field.yield_bu_per_ac)
        rows.append({
            "field_id": field.field_id,
            "area_ac": field.area_ac,
            "geometry": field.geometry,
            "yield_bu_per_ac": field_yield,
            "moisture_pct": moisture,
            "one_way_travel_min": field.one_way_travel_min,
            "opening_area_fraction": (
                config.opening_area_fraction
                if field.opening_area_fraction is None
                else field.opening_area_fraction),
            "opening_capacity_factor": config.opening_capacity_factor(
                field.geometry)
            if field.opening_capacity_factor is None
            else field.opening_capacity_factor,
            "grain_bu": field.area_ac * field_yield,
            "dryer_rate_buph": config.dryer_rate_for_moisture(moisture),
        })
    write_rows(directory / "field_scenario.csv", rows)
    predictive = RollingFourFieldPredictive(config)
    payload = {
        "random_seed": config.random_seed,
        "field_count": len(config.field_specs),
        "total_area_ac": config.total_area_ac,
        "total_grain_bu": config.total_grain_bu,
        "use_field_buffer": config.use_field_buffer,
        "wet_bin_capacity_bu": config.wet_bin_capacity_bu,
        "wet_bin_control_limit_fraction": (
            config.wet_bin_control_limit_fraction),
        "wet_bin_control_reserve_bu": config.wet_bin_control_reserve_bu,
        "maximum_dryer_idle_episode_h": (
            config.maximum_dryer_idle_episode_h),
        "stop_work_requirement_h": config.stop_work_requirement_h,
        "predictive_controller_implementation": (
            "rolling_four_field_dynamic_tail"),
        "predictive_decision_interval_min": (
            predictive.decision_interval_h * 60.0),
        "predictive_horizon_fields": predictive.horizon_fields,
        "predictive_forecast_step_min": (
            predictive.forecast_simulation_step_h * 60.0),
        "predictive_completion_tolerance_min_pct": (
            predictive.completion_tolerance_min_fraction * 100.0),
        "predictive_completion_tolerance_max_pct": (
            predictive.completion_tolerance_max_fraction * 100.0),
        "predictive_completion_tolerance_pressure_scale": (
            predictive.completion_tolerance_pressure_scale),
        "predictive_stop_search": "coarse_bidirectional_refine",
        "predictive_stop_minimum_h": config.minimum_stop_h,
        "predictive_stop_maximum_h": predictive.maximum_stop_h,
        "predictive_stop_coarse_step_h": predictive.stop_coarse_step_h,
        "predictive_stop_refine_step_h": predictive.stop_refine_step_h,
        "predictive_stop_fine_step_h": predictive.stop_fine_step_h,
        **metadata,
    }
    with (directory / "scenario_metadata.json").open(
            "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def run_policy(
        config: SimulationConfig, policy: str, output_dir: Path, *,
        experiment_metadata: dict[str, object],
        forecast_yield_multiplier: float = 1.0,
        skip_existing: bool = False,
) -> dict[str, object]:
    metrics_path = output_dir / "metrics.json"
    if skip_existing and metrics_path.exists():
        with metrics_path.open(encoding="utf-8") as handle:
            metrics = json.load(handle)
        print(f"SKIP {output_dir}", flush=True)
        return metrics

    print(f"START {output_dir}", flush=True)
    started = time.perf_counter()
    if policy == "predictive":
        result = RollingFourFieldPredictive(
            config,
            forecast_yield_multiplier=forecast_yield_multiplier,
        ).run()
    else:
        result = Simulator(
            config, policy, config.sample_field_moistures()).run()
    wall_clock_s = time.perf_counter() - started
    result.metrics.update({
        **experiment_metadata,
        "wall_clock_seconds": wall_clock_s,
        "forecast_yield_multiplier": forecast_yield_multiplier,
    })

    output_dir.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)
        handle.write("\n")
    write_rows(output_dir / "timeseries.csv", result.time_series)
    write_rows(
        output_dir / "controller_decisions.csv",
        result.controller_decisions)
    print(
        f"DONE {output_dir} harvest="
        f"{result.metrics['harvest_completion_time_h']:.3f}h raw_block="
        f"{result.metrics['raw_forced_blocking_time_h']:.3f}h runtime="
        f"{wall_clock_s:.1f}s",
        flush=True,
    )
    return result.metrics


def run_field_suite(root: Path, skip_existing: bool) -> None:
    for name in SCENARIOS:
        config = scenario_config(name)
        scenario_dir = root / "field_configurations" / name
        write_scenario(scenario_dir, config, {
            "scenario_id": name,
            "experiment_family": "field_configuration",
        })
        for policy in POLICIES:
            run_policy(
                config, policy, scenario_dir / policy,
                experiment_metadata={
                    "scenario_id": name,
                    "experiment_family": "field_configuration",
                },
                skip_existing=skip_existing,
            )


def run_capacity_suite(root: Path, skip_existing: bool) -> None:
    source = scenario_config("s2")
    for scale in CAPACITY_SCALES:
        config, capacity_metadata = config_for_capacity_scale(source, scale)
        tag = f"scale{scale:g}".replace(".", "p")
        scenario_dir = root / "downstream_capacity_safety95" / tag
        metadata: dict[str, object] = {
            "scenario_id": "s2",
            "experiment_family": "downstream_capacity",
            **capacity_metadata,
        }
        write_scenario(scenario_dir, config, metadata)
        for policy in POLICIES:
            run_policy(
                config, policy, scenario_dir / policy,
                experiment_metadata=metadata,
                skip_existing=skip_existing,
            )


def run_error_suite(root: Path, skip_existing: bool) -> None:
    config = scenario_config("s2")
    for multiplier in FORECAST_YIELD_MULTIPLIERS:
        error_pct = round((multiplier - 1.0) * 100.0, 10)
        tag = forecast_error_tag(multiplier)
        scenario_dir = root / "forecast_error" / tag
        metadata: dict[str, object] = {
            "scenario_id": "s2",
            "experiment_family": "forecast_yield_error",
            "forecast_yield_error_pct": error_pct,
        }
        write_scenario(scenario_dir, config, metadata)
        run_policy(
            config, "predictive", scenario_dir / "predictive",
            experiment_metadata=metadata,
            forecast_yield_multiplier=multiplier,
            skip_existing=skip_existing,
        )


def read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def forecast_error_tag(multiplier: float) -> str:
    error_pct = round((multiplier - 1.0) * 100.0, 10)
    if abs(error_pct) <= 1e-12:
        return "yield_0pct"
    sign = "m" if error_pct < 0 else "p"
    magnitude = f"{abs(error_pct):g}".replace(".", "p")
    return f"yield_{sign}{magnitude}pct"


def summary_row(
        metrics_path: Path, *, scenario_id: str, policy: str,
        downstream_capacity_scale: float | str = "",
        harvest_to_downstream_ratio: float | str = "",
        forecast_yield_error_pct: float | str = "",
) -> dict[str, object]:
    metrics = read_json(metrics_path)
    row: dict[str, object] = {
        "scenario_id": scenario_id,
        "policy": policy,
        "downstream_capacity_scale": downstream_capacity_scale,
        "maximum_harvest_to_effective_downstream_ratio": (
            harvest_to_downstream_ratio),
        "forecast_yield_error_pct": forecast_yield_error_pct,
    }
    row.update({key: metrics.get(key, "") for key in SUMMARY_METRICS})
    return row


def write_summaries(root: Path) -> None:
    """Build compact, machine-readable tables from completed runs."""
    summary_dir = root / "summary"

    scenario_rows: list[dict[str, object]] = []
    field_rows: list[dict[str, object]] = []
    for scenario_id in SCENARIOS:
        scenario_dir = root / "field_configurations" / scenario_id
        scenario_path = scenario_dir / "field_scenario.csv"
        if scenario_path.exists():
            with scenario_path.open(newline="", encoding="utf-8") as handle:
                fields = list(csv.DictReader(handle))
            grain = sum(float(field["grain_bu"]) for field in fields)
            scenario_rows.append({
                "scenario_id": scenario_id,
                "field_count": len(fields),
                "total_area_ac": sum(float(field["area_ac"])
                                     for field in fields),
                "total_grain_bu": grain,
                "area_min_ac": min(float(field["area_ac"])
                                   for field in fields),
                "area_max_ac": max(float(field["area_ac"])
                                   for field in fields),
                "yield_min_bu_per_ac": min(
                    float(field["yield_bu_per_ac"]) for field in fields),
                "yield_max_bu_per_ac": max(
                    float(field["yield_bu_per_ac"]) for field in fields),
                "grain_weighted_moisture_pct": sum(
                    float(field["grain_bu"]) * float(field["moisture_pct"])
                    for field in fields) / grain,
                "travel_min_min": min(float(field["one_way_travel_min"])
                                      for field in fields),
                "travel_min_max": max(float(field["one_way_travel_min"])
                                      for field in fields),
            })
        for policy in POLICIES:
            metrics_path = scenario_dir / policy / "metrics.json"
            if metrics_path.exists():
                field_rows.append(summary_row(
                    metrics_path, scenario_id=scenario_id, policy=policy))

    capacity_rows: list[dict[str, object]] = []
    for scale in CAPACITY_SCALES:
        tag = f"scale{scale:g}".replace(".", "p")
        for policy in POLICIES:
            metrics_path = (
                root / "downstream_capacity_safety95" / tag
                / policy / "metrics.json")
            if metrics_path.exists():
                metrics = read_json(metrics_path)
                capacity_rows.append(summary_row(
                    metrics_path, scenario_id="s2", policy=policy,
                    downstream_capacity_scale=scale,
                    harvest_to_downstream_ratio=metrics.get(
                        "maximum_harvest_to_effective_downstream_ratio", "")))

    error_rows: list[dict[str, object]] = []
    for multiplier in FORECAST_YIELD_MULTIPLIERS:
        metrics_path = (
            root / "forecast_error" / forecast_error_tag(multiplier)
            / "predictive" / "metrics.json")
        if metrics_path.exists():
            error_rows.append(summary_row(
                metrics_path, scenario_id="s2", policy="predictive",
                forecast_yield_error_pct=round(
                    (multiplier - 1.0) * 100.0, 10)))

    write_rows(summary_dir / "scenarios.csv", scenario_rows)
    write_rows(summary_dir / "field_configurations.csv", field_rows)
    write_rows(summary_dir / "downstream_capacity.csv", capacity_rows)
    write_rows(summary_dir / "forecast_error.csv", error_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=(
            "generate", "field", "pressure", "capacity", "error", "all"),
        default="all")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/validation"))
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    if args.phase in {"generate", "field", "all"}:
        if args.phase == "generate":
            for name in SCENARIOS:
                config = scenario_config(name)
                write_scenario(
                    args.output_dir / "field_configurations" / name,
                    config,
                    {"scenario_id": name,
                     "experiment_family": "field_configuration"})
        else:
            run_field_suite(args.output_dir, args.skip_existing)
    if args.phase in {"pressure", "capacity", "all"}:
        run_capacity_suite(args.output_dir, args.skip_existing)
    if args.phase in {"error", "all"}:
        run_error_suite(args.output_dir, args.skip_existing)
    write_summaries(args.output_dir)


if __name__ == "__main__":
    main()
