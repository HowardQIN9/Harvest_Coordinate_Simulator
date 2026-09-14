from dataclasses import dataclass
from math import isfinite
from random import Random


MODE_1C = "1C"
MODE_2C = "2C"
MODE_2C_1GC = "2C+1GC"
MODE_STOP = "STOP"

OPENING_MODES = (MODE_1C, MODE_2C)
INTERIOR_MODES = (MODE_1C, MODE_2C, MODE_2C_1GC)
MIN_PREDICTION_HORIZON_H = 8.0


@dataclass(frozen=True)
class FieldSpec:
    field_id: str
    area_ac: float
    geometry: str
    one_way_travel_min: float


DEFAULT_FIELDS = (
    FieldSpec("F1", 50.0, "irregular", 5.0),
    FieldSpec("F2", 80.0, "rectangular", 15.0),
    FieldSpec("F3", 50.0, "irregular", 20.0),
    FieldSpec("F4", 80.0, "rectangular", 5.0),
    FieldSpec("F5", 50.0, "irregular", 15.0),
    FieldSpec("F6", 80.0, "rectangular", 20.0),
    FieldSpec("F7", 50.0, "irregular", 15.0),
    FieldSpec("F8", 80.0, "rectangular", 15.0),
    FieldSpec("F9", 50.0, "irregular", 15.0),
    FieldSpec("F10", 80.0, "rectangular", 15.0),
)


@dataclass(frozen=True)
class SimulationConfig:
    field_specs: tuple[FieldSpec, ...] = DEFAULT_FIELDS

    # The observed planting order is not available yet. The default is an
    # explicitly provisional far-to-near scenario shared by every policy.
    fixed_field_order_ids: tuple[str, ...] = ()
    default_field_order_rule: str = "far_to_near"

    # Opening occupies 15% of each field. Its geometry multipliers preserve
    # the ratios in the previous model and apply to either 1C or 2C.
    opening_area_fraction: float = 0.15
    rectangular_opening_rate_fraction: float = 3538.4615384615386 / 4000.0
    irregular_opening_rate_fraction: float = 2692.3076923076924 / 4000.0

    # Effective field capacities are the earlier values multiplied by 80%.
    # They already include routine in-field inefficiencies.
    interior_1c_rate_buph: float = 1600.0
    interior_2c_rate_buph: float = 3200.0
    interior_2c_1gc_rate_buph: float = 4000.0

    # One production-mode switch is allowed per field interior. The five-
    # minute cost is an explicit exploratory assumption for later sensitivity.
    max_interior_mode_switches: int = 1
    minimum_interior_mode_dwell_h: float = 30.0 / 60.0
    interior_mode_switch_time_h: float = 5.0 / 60.0
    field_switch_time_h: float = 20.0 / 60.0

    # STOP is a system scheduling action, not a field mode. The eligibility
    # clock accumulates productive harvest time and pauses during other delays.
    stop_work_requirement_h: float = 12.0
    minimum_stop_h: float = 8.0
    prediction_horizon_h: float = 48.0
    forecast_step_h: float = 15.0 / 60.0
    control_decision_interval_h: float = 15.0 / 60.0

    # Static-threshold reference policy. These are legacy heuristic values,
    # not farmer-confirmed physical constants.
    low_pressure_fraction: float = 0.40
    high_pressure_fraction: float = 0.80
    stop_pressure_fraction: float = 0.95

    header_width_ft: float = 30.0
    yield_bu_per_ac: float = 200.0

    # Mobile storage and transport. Combine capacity is per combine. The
    # grain cart is represented as one aggregate 1,000-bu field buffer.
    combine_tank_capacity_per_combine_bu: float = 300.0
    grain_cart_capacity_bu: float = 1000.0
    semi_count: int = 3
    semi_capacity_bu: float = 1000.0
    semi_unload_time_h: float = 20.0 / 60.0

    # Aaron's system has no receiving pit: one semi unloads directly through
    # the 3,000-bu/h grain leg into the finite wet bin.
    receiving_pit_capacity_bu: float = 0.0
    grain_leg_rate_buph: float = 3000.0
    wet_bin_capacity_bu: float = 10000.0

    # Provisional dryer representation retained from the previous version.
    # It remains a downstream process, never a controller action. The
    # moisture-throughput relationship still needs confirmation.
    random_seed: int = 42
    moisture_min_pct: float = 18.0
    moisture_max_pct: float = 22.0
    dryer_intercept_buph: float = 2600.0
    dryer_moisture_slope_buph_per_pct: float = 100.0

    dt_h: float = 1.0 / 60.0
    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1500.0

    def __post_init__(self) -> None:
        positive = (
            "interior_1c_rate_buph", "interior_2c_rate_buph",
            "interior_2c_1gc_rate_buph", "minimum_interior_mode_dwell_h",
            "stop_work_requirement_h", "minimum_stop_h",
            "prediction_horizon_h", "forecast_step_h",
            "control_decision_interval_h", "yield_bu_per_ac",
            "combine_tank_capacity_per_combine_bu", "grain_cart_capacity_bu",
            "semi_capacity_bu", "semi_unload_time_h", "grain_leg_rate_buph",
            "wet_bin_capacity_bu", "dryer_intercept_buph",
            "dryer_moisture_slope_buph_per_pct", "dt_h",
            "numerical_tolerance", "max_simulation_h",
        )
        for name in positive:
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        nonnegative = (
            "interior_mode_switch_time_h", "field_switch_time_h",
            "receiving_pit_capacity_bu",
        )
        for name in nonnegative:
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not 0 < self.opening_area_fraction < 1:
            raise ValueError("opening_area_fraction must be in (0, 1)")
        for name in (
                "rectangular_opening_rate_fraction",
                "irregular_opening_rate_fraction"):
            value = getattr(self, name)
            if not isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.prediction_horizon_h < MIN_PREDICTION_HORIZON_H:
            raise ValueError(
                f"prediction_horizon_h must be at least {MIN_PREDICTION_HORIZON_H:g}")
        if not isinstance(self.semi_count, int) or isinstance(self.semi_count, bool) or self.semi_count < 1:
            raise ValueError("semi_count must be a positive integer")
        if (not isinstance(self.max_interior_mode_switches, int)
                or isinstance(self.max_interior_mode_switches, bool)
                or self.max_interior_mode_switches < 0):
            raise ValueError("max_interior_mode_switches must be a nonnegative integer")
        if not (self.interior_1c_rate_buph < self.interior_2c_rate_buph
                < self.interior_2c_1gc_rate_buph):
            raise ValueError("Interior rates must satisfy 1C < 2C < 2C+1GC")
        if not 0 <= self.low_pressure_fraction < self.high_pressure_fraction < self.stop_pressure_fraction <= 1:
            raise ValueError("Pressure thresholds must satisfy 0 <= low < high < stop <= 1")
        if self.default_field_order_rule not in ("far_to_near", "near_to_far", "listed"):
            raise ValueError("Unknown default_field_order_rule")
        if not isinstance(self.random_seed, int) or isinstance(self.random_seed, bool):
            raise ValueError("random_seed must be an integer")
        if (not isfinite(self.moisture_min_pct)
                or not isfinite(self.moisture_max_pct)
                or self.moisture_min_pct >= self.moisture_max_pct):
            raise ValueError("moisture bounds must be finite and satisfy min < max")
        if self.dryer_rate_for_moisture(self.moisture_max_pct) <= 0:
            raise ValueError("dryer throughput must remain positive over the moisture range")
        ids = [field.field_id for field in self.field_specs]
        if len(ids) != len(set(ids)):
            raise ValueError("field_id values must be unique")
        for field in self.field_specs:
            if field.geometry not in ("rectangular", "irregular"):
                raise ValueError("Field geometry must be rectangular or irregular")
            if not isfinite(field.area_ac) or field.area_ac <= 0:
                raise ValueError("Each field area must be finite and positive")
            if not isfinite(field.one_way_travel_min) or field.one_way_travel_min < 0:
                raise ValueError("Field travel time must be finite and nonnegative")
        if self.fixed_field_order_ids:
            if len(self.fixed_field_order_ids) != len(ids) or set(self.fixed_field_order_ids) != set(ids):
                raise ValueError("fixed_field_order_ids must contain every field id exactly once")

    @property
    def total_area_ac(self) -> float:
        return sum(field.area_ac for field in self.field_specs)

    @property
    def total_grain_bu(self) -> float:
        return self.total_area_ac * self.yield_bu_per_ac

    @property
    def maximum_combine_storage_bu(self) -> float:
        return 2.0 * self.combine_tank_capacity_per_combine_bu

    @property
    def total_system_storage_bu(self) -> float:
        return (self.maximum_combine_storage_bu
                + self.grain_cart_capacity_bu
                + self.semi_count * self.semi_capacity_bu
                + self.wet_bin_capacity_bu)

    def field_order_indices(self) -> tuple[int, ...]:
        if self.fixed_field_order_ids:
            index_by_id = {
                field.field_id: index for index, field in enumerate(self.field_specs)
            }
            return tuple(index_by_id[field_id] for field_id in self.fixed_field_order_ids)
        if self.default_field_order_rule == "listed":
            return tuple(range(len(self.field_specs)))
        reverse = self.default_field_order_rule == "far_to_near"
        return tuple(sorted(
            range(len(self.field_specs)),
            key=lambda index: (
                -self.field_specs[index].one_way_travel_min
                if reverse else self.field_specs[index].one_way_travel_min,
                index,
            ),
        ))

    def sample_field_moistures(self) -> tuple[float, ...]:
        rng = Random(self.random_seed)
        return tuple(rng.uniform(self.moisture_min_pct, self.moisture_max_pct)
                     for _ in self.field_specs)

    def dryer_rate_for_moisture(self, moisture_pct: float) -> float:
        return (self.dryer_intercept_buph
                - self.dryer_moisture_slope_buph_per_pct * moisture_pct)

    def opening_area_ac(self, field_area_ac: float) -> float:
        return self.opening_area_fraction * field_area_ac

    def opening_rate_buph(self, geometry: str, mode: str) -> float:
        if mode not in OPENING_MODES:
            raise ValueError(f"Opening does not allow mode {mode}")
        base_rate = self.interior_rate_buph(mode)
        fraction = (self.rectangular_opening_rate_fraction
                    if geometry == "rectangular"
                    else self.irregular_opening_rate_fraction)
        return base_rate * fraction

    def interior_rate_buph(self, mode: str) -> float:
        rates = {
            MODE_1C: self.interior_1c_rate_buph,
            MODE_2C: self.interior_2c_rate_buph,
            MODE_2C_1GC: self.interior_2c_1gc_rate_buph,
        }
        try:
            return rates[mode]
        except KeyError as error:
            raise ValueError(f"Unknown interior mode: {mode}") from error

    @staticmethod
    def combine_count_for_mode(mode: str) -> int:
        return 1 if mode == MODE_1C else 2

    @staticmethod
    def grain_cart_enabled_for_mode(mode: str) -> bool:
        return mode == MODE_2C_1GC
