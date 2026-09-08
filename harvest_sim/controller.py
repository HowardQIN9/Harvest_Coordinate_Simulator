from __future__ import annotations

from dataclasses import dataclass

from .config import (INTERIOR_MODES, MIN_INTERIOR_PREDICTION_HORIZON_H,
                     MODE_1C, MODE_2C, MODE_2C_1GC,
                     SimulationConfig)
from .field import INTERIOR, Field

TaskRef = tuple[int, str]


class SequentialController:
    name = "sequential"
    allows_interior_mode_switching = False

    def choose(self, fields: list[Field], current: TaskRef | None, **_: float) -> TaskRef | None:
        if current is not None:
            index, task = current
            if task in fields[index].available_tasks():
                return current
        for index, field in enumerate(fields):
            tasks = field.available_tasks()
            if tasks:
                return index, tasks[0]
        return None

    def choose_interior_mode(self, **_: float) -> str:
        return MODE_2C_1GC


@dataclass
class ReactiveController:
    """Fixed field order with mode control based only on current pressure."""

    config: SimulationConfig
    name: str = "reactive"
    allows_interior_mode_switching: bool = True

    def choose(
        self,
        fields: list[Field],
        current: TaskRef | None,
        **_: float,
    ) -> TaskRef | None:
        # Keep the sequential field order so the experiment isolates the value
        # of reacting to current congestion from predictive field selection.
        return SequentialController().choose(fields, current)

    def choose_interior_mode(
        self,
        *,
        receiving_pressure: float,
        system_buffer_free_bu: float,
        wet_bin_inventory_bu: float,
        **_: float,
    ) -> str:
        """Choose intensity from current state without forecasting the future."""
        c = self.config
        system_capacity_bu = (
            c.combine_tank_capacity_bu
            + c.semi_count * c.semi_capacity_bu
            + c.receiving_pit_capacity_bu
            + c.wet_bin_capacity_bu
        )
        system_pressure = 1.0 - system_buffer_free_bu / system_capacity_bu
        wet_bin_pressure = wet_bin_inventory_bu / c.wet_bin_capacity_bu
        pressure = max(
            0.0,
            min(1.0, receiving_pressure),
            min(1.0, system_pressure),
            min(1.0, wet_bin_pressure),
        )
        if pressure >= c.high_receiving_pressure_fraction:
            return MODE_1C
        if pressure >= c.low_receiving_pressure_fraction:
            return MODE_2C
        return MODE_2C_1GC


@dataclass
class AdaptiveController:
    config: SimulationConfig
    name: str = "adaptive"
    allows_interior_mode_switching: bool = True

    def choose(
        self,
        fields: list[Field],
        current: TaskRef | None,
        receiving_pressure: float,
        downstream_inventory_bu: float = 0.0,
        field_mobile_free_bu: float = 0.0,
        system_buffer_free_bu: float = 0.0,
        current_dryer_rate_buph: float = 0.0,
        wet_bin_inventory_bu: float = 0.0,
        hours_to_harvest_rest_h: float = float("inf"),
        rest_inventory_target_bu: float = 0.0,
        **_: float,
    ) -> TaskRef | None:
        feasible = [(i, task) for i, field in enumerate(fields) for task in field.available_tasks()]
        if not feasible:
            return None
        c = self.config
        if current in feasible:
            return current

        def forecast(task: TaskRef) -> tuple[float, float, int]:
            """Estimate the whole candidate field, including future backpressure.

            This intentionally evaluates through the end of the field because the
            combine is not allowed to switch again during that field.
            """
            field = fields[task[0]]
            productive_h = (
                field.opening_remaining_ac * c.yield_bu_per_ac
                / c.opening_rate_buph(field.geometry)
                + field.interior_remaining_ac * c.yield_bu_per_ac
                / c.interior_2c_rate_buph
            )
            grain_bu = (field.opening_remaining_ac + field.interior_remaining_ac) * c.yield_bu_per_ac
            travel_h = field.one_way_travel_min / 60.0
            truck_cycle_h = 2.0 * travel_h + c.semi_unload_time_h
            truck_rate = c.semi_count * c.semi_capacity_bu / truck_cycle_h
            unload_dock_rate = c.semi_capacity_bu / c.semi_unload_time_h
            transport_rate = min(truck_rate, unload_dock_rate, c.grain_leg_rate_buph)
            dryer_rate = c.dryer_rate_for_moisture(field.moisture_pct)
            active_dryer_rate = current_dryer_rate_buph or dryer_rate
            candidate_rest_inventory_target_bu = max(
                rest_inventory_target_bu,
                c.rest_dryer_inventory_target_bu(dryer_rate),
            )

            transport_shortfall = max(
                0.0, grain_bu - field_mobile_free_bu - transport_rate * productive_h)
            transport_delay_h = transport_shortfall / transport_rate
            dryer_shortfall = max(
                0.0, grain_bu - system_buffer_free_bu - active_dryer_rate * productive_h)
            dryer_delay_h = dryer_shortfall / active_dryer_rate
            elapsed_h = productive_h + max(transport_delay_h, dryer_delay_h)

            projected_inventory = max(
                0.0, downstream_inventory_bu + grain_bu - active_dryer_rate * elapsed_h)
            # Retaining too much grain after this field reduces the capacity
            # available to every later choice. Increase that look-ahead weight as
            # the field receiving system becomes pressured.
            look_ahead_weight = 0.10 + 0.40 * receiving_pressure
            future_clearance_h = projected_inventory / dryer_rate
            score = elapsed_h + look_ahead_weight * future_clearance_h
            if (c.harvest_rest_window_h > c.numerical_tolerance
                    and hours_to_harvest_rest_h
                    <= c.interior_prediction_horizon_h + c.numerical_tolerance):
                time_to_rest_h = max(0.0, hours_to_harvest_rest_h)
                average_harvest_rate = grain_bu / max(
                    productive_h, c.numerical_tolerance)
                grain_before_rest_bu = min(
                    grain_bu, average_harvest_rate * time_to_rest_h)
                inflow_before_rest_bu = min(
                    grain_before_rest_bu,
                    c.rest_coverage_transport_fraction
                    * transport_rate * time_to_rest_h,
                    c.grain_leg_rate_buph * time_to_rest_h,
                )
                projected_wet_bin_bu = max(
                    0.0,
                    wet_bin_inventory_bu + inflow_before_rest_bu
                    - active_dryer_rate * time_to_rest_h,
                )
                rest_shortfall_h = (
                    max(0.0, candidate_rest_inventory_target_bu
                        - projected_wet_bin_bu)
                    / active_dryer_rate
                )
                score += c.rest_inventory_shortage_weight * rest_shortfall_h
            return score, elapsed_h, task[0]

        return min(feasible, key=forecast)

    def choose_interior_mode(
        self,
        *,
        remaining_grain_bu: float,
        field_mobile_free_bu: float,
        system_buffer_free_bu: float,
        transport_rate_buph: float,
        active_dryer_rate_buph: float,
        wet_bin_inventory_bu: float = 0.0,
        hours_to_harvest_rest_h: float = float("inf"),
        rest_inventory_target_bu: float | None = None,
        **_: float,
    ) -> str:
        """Select production intensity using a fixed-work rolling forecast.

        Every candidate is asked to process the same grain volume over a
        look-ahead of at least 12 hours (or all remaining field grain when that
        is smaller).
        Its score includes nominal production time and predicted forced
        blocking. During the final forecast horizon before a scheduled rest,
        it also treats the grain needed to keep the dryer running through that
        rest as a minimum coverage requirement. Only predicted shortage is
        penalized; excess inventory does not trigger intentional waiting.
        """
        c = self.config
        max_rate = c.interior_2c_1gc_rate_buph
        horizon_h = max(c.interior_prediction_horizon_h,
                        MIN_INTERIOR_PREDICTION_HORIZON_H)
        target_bu = min(remaining_grain_bu,
                        max_rate * horizon_h)
        dryer_rate = max(active_dryer_rate_buph, c.numerical_tolerance)
        transport_rate = max(transport_rate_buph, c.numerical_tolerance)
        target_rest_inventory_bu = (
            c.rest_dryer_inventory_target_bu(dryer_rate)
            if rest_inventory_target_bu is None
            else rest_inventory_target_bu
        )
        target_rest_active = (
            c.harvest_rest_window_h > c.numerical_tolerance
            and hours_to_harvest_rest_h <= horizon_h + c.numerical_tolerance
        )
        time_to_rest_h = max(0.0, hours_to_harvest_rest_h)

        def forecast(mode: str) -> tuple[float, float, float, float]:
            rate = c.interior_rate_buph(mode)
            nominal_h = target_bu / rate
            transport_overflow = max(
                0.0,
                target_bu - field_mobile_free_bu - transport_rate * nominal_h,
            )
            downstream_overflow = max(
                0.0,
                target_bu - system_buffer_free_bu - dryer_rate * nominal_h,
            )
            forced_block_h = max(
                transport_overflow / transport_rate,
                downstream_overflow / dryer_rate,
            )
            rest_target_error_h = 0.0
            if target_rest_active:
                new_grain_before_rest_bu = min(
                    remaining_grain_bu,
                    rate * time_to_rest_h,
                )
                wet_bin_inflow_before_rest_bu = min(
                    new_grain_before_rest_bu,
                    c.rest_coverage_transport_fraction
                    * transport_rate * time_to_rest_h,
                    c.grain_leg_rate_buph * time_to_rest_h,
                )
                projected_wet_bin_bu = max(
                    0.0,
                    wet_bin_inventory_bu
                    + wet_bin_inflow_before_rest_bu
                    - dryer_rate * time_to_rest_h,
                )
                # Express terminal inventory error as equivalent dryer hours
                # so it is comparable with the other time terms in the score.
                rest_target_error_h = (
                    max(0.0, target_rest_inventory_bu - projected_wet_bin_bu)
                    / dryer_rate
                )
            score = (
                nominal_h
                + (1.0 + c.predictive_blocking_penalty) * forced_block_h
                + c.rest_inventory_shortage_weight * rest_target_error_h
            )
            # Prefer less rest-coverage shortage, less blocking, then higher
            # production, on otherwise close scores.
            return score, rest_target_error_h, forced_block_h, -rate

        return min(INTERIOR_MODES, key=forecast)
